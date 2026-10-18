from __future__ import annotations

import ctypes
import json
import logging
import os
import sqlite3
import sys
import threading
import uuid
from contextlib import closing, contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Protocol

LOGGER = logging.getLogger(__name__)


class ShutdownTarget(Protocol):
    should_exit: bool


class WorkspaceBusy(RuntimeError):
    pass


@contextmanager
def workspace_lock(root: Path, name: str = "application.lock"):
    import msvcrt

    if Path(name).name != name or name in (".", ".."):
        raise ValueError("A workspace lock requires a simple local filename.")
    directory = root / "var" / "run"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise WorkspaceBusy(
                "The workspace is still in use by another Clean Air process. "
                "Run Stop-CleanAir.cmd before upgrading, syncing, restoring or packaging."
            ) from error
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def process_start_ticks() -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(
        kernel.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited),
        ctypes.byref(kernel_time), ctypes.byref(user_time),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


@contextmanager
def shutdown_control(root: Path, server: ShutdownTarget):
    directory = root / "var" / "run"
    directory.mkdir(parents=True, exist_ok=True)
    instance_id = uuid.uuid4().hex
    state = directory / "server.json"
    temporary = directory / "server.json.tmp"
    request = directory / f"stop-{instance_id}.request"
    temporary.write_text(
        json.dumps(
            {
                "version": 1,
                "pid": os.getpid(),
                "process_start_ticks": str(process_start_ticks()),
                "executable": str(Path(sys.executable).resolve()),
                "workspace": str(root.resolve()),
                "instance_id": instance_id,
            }
        ),
        encoding="utf-8",
    )
    temporary.replace(state)
    finished = threading.Event()

    def watch() -> None:
        while not finished.wait(0.2):
            if request.is_file():
                LOGGER.info("Workspace stop request received; shutting down the server.")
                server.should_exit = True
                return

    thread = threading.Thread(target=watch, name="shutdown-control", daemon=True)
    thread.start()
    try:
        yield
    finally:
        finished.set()
        thread.join(timeout=1)
        request.unlink(missing_ok=True)
        state.unlink(missing_ok=True)


def release_storage(root: Path) -> None:
    directory = root / "var" / "run"
    with workspace_lock(root):
        databases = list((root / "var" / "data").glob("*.sqlite3"))
        if databases:
            for database in databases:
                with closing(sqlite3.connect(database, timeout=10)) as connection:
                    checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    if checkpoint[0]:
                        raise RuntimeError(f"SQLite still has an active reader or writer in {database.name}.")
                    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                    if integrity != "ok":
                        raise RuntimeError(f"Database integrity check failed in {database.name}: {integrity}")
                print(f"Database writes flushed; {database.name} integrity: ok.", flush=True)
        else:
            print("No local database to checkpoint.", flush=True)
        (directory / "server.json").unlink(missing_ok=True)
        (directory / "server.json.tmp").unlink(missing_ok=True)
