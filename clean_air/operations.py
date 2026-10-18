from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import platform
import socket
import sqlite3
import subprocess
import threading
import time
import webbrowser
import zipfile
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from statistics import median

import httpx

from . import __version__
from .comparison import ComparisonError, Search, compare, status_snapshot
from .database import backup
from .settings import Settings, months_before, timestamp_now, utc_today
from .shutdown_control import workspace_lock

LOGGER = logging.getLogger(__name__)


@contextmanager
def instance_lock(settings: Settings):
    with workspace_lock(settings.root):
        yield


def ensure_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(
                f"Local port {port} is already in use. Stop the owning application or "
                "choose another port in config\\settings.toml."
            ) from error


@contextmanager
def monitoring(settings: Settings):
    if not settings.options.monitoring.enabled:
        yield
        return
    port = settings.options.monitoring.port
    ensure_port_available(port)
    executable = settings.root / "runtime" / "prometheus" / "prometheus.exe"
    if not executable.is_file():
        raise RuntimeError(
            "The bundled Prometheus executable is missing. Rebuild the portable runtime; "
            "or explicitly use --no-monitoring for an application-only diagnostic run."
        )
    template = (settings.root / "config" / "prometheus.yml").read_text("utf-8")
    generated = settings.var / "run" / "prometheus.yml"
    rules = str(settings.root / "config" / "alerts.yml").replace("\\", "\\\\")
    generated.write_text(
        template.replace("${RULES_PATH}", rules).replace("${APP_PORT}", str(settings.options.app.port)),
        encoding="utf-8",
    )
    log_path = settings.var / "logs" / "prometheus.log"
    if log_path.exists() and log_path.stat().st_size > 10_000_000:
        previous = log_path.with_suffix(".previous.log")
        if previous.exists():
            previous.unlink()
        log_path.replace(previous)
    with log_path.open("ab") as output:
        process = subprocess.Popen(
            [
                str(executable),
                f"--config.file={generated}",
                f"--storage.tsdb.path={settings.var / 'monitoring'}",
                f"--storage.tsdb.retention.time={settings.options.monitoring.retention_days}d",
                f"--web.listen-address=127.0.0.1:{port}",
            ],
            cwd=settings.root,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 25
            with httpx.Client(trust_env=False, timeout=1) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(
                            "Prometheus exited during startup. Inspect var\\logs\\prometheus.log."
                        )
                    try:
                        if client.get(f"http://127.0.0.1:{port}/-/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.25)
                else:
                    raise RuntimeError(
                        "Prometheus did not become ready within 25 seconds. "
                        "Inspect var\\logs\\prometheus.log."
                    )
            LOGGER.info("Prometheus is ready at http://127.0.0.1:%s", port)
            yield
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    LOGGER.error("Prometheus did not stop gracefully; terminating the owned process.")
                    process.kill()
                    process.wait(timeout=5)


def open_browser_when_ready(port: int) -> None:
    def open_page() -> None:
        with httpx.Client(trust_env=False, timeout=1) as client:
            for _ in range(100):
                try:
                    if client.get(f"http://127.0.0.1:{port}/health/live").status_code == 200:
                        if not webbrowser.open(f"http://127.0.0.1:{port}"):
                            LOGGER.warning("Could not open a browser. Open http://127.0.0.1:%s manually.", port)
                        return
                except httpx.TransportError:
                    time.sleep(0.2)
        LOGGER.error("The browser was not opened because application startup did not complete.")

    threading.Thread(target=open_page, name="open-browser", daemon=True).start()


def make_handover(settings: Settings, include_git: bool = True) -> Path:
    runtime = settings.root / "runtime"
    for required in (Path("python") / "python.exe", Path("prometheus") / "prometheus.exe", Path("manifest.json")):
        if not (runtime / required).is_file():
            raise RuntimeError(f"The portable runtime is incomplete: missing {required}")
    destination = settings.root / "dist"
    destination.mkdir(exist_ok=True)
    database_snapshot = settings.var / "backups" / "handover.sqlite3"
    backup(settings.database, database_snapshot)
    output = destination / "CleanAirFinder-Windows-x64.zip"
    temporary = output.with_suffix(".zip.part")
    directories = ("clean_air", "config", "tools", "tests", "docs", "runtime", "The Task")
    if include_git:
        directories += (".git",)
    root_files = [
        path for path in settings.root.iterdir()
        if path.is_file() and (
            path.suffix.lower() in (".md", ".txt", ".toml", ".lock", ".cmd")
            or path.name in (".gitignore", ".gitattributes", "LICENSE", "NOTICE")
        )
    ]
    paths = list(root_files)
    for name in directories:
        folder = settings.root / name
        if folder.exists():
            paths.extend(path for path in folder.rglob("*") if path.is_file())
    archives = settings.var / "archives"
    paths.extend(path for path in archives.iterdir() if path.is_file() and not path.name.endswith(".part"))
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(paths):
            if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
                continue
            relative = path.relative_to(settings.root)
            bundle.write(path, str(Path("CleanAirFinder") / relative))
        bundle.write(database_snapshot, str(Path("CleanAirFinder") / "var" / "data" / "clean_air.sqlite3"))
        bundle.writestr(
            "CleanAirFinder/handover-manifest.json",
            json.dumps(
                {"version": __version__, "created_utc_epoch": timestamp_now(), "history": status_snapshot(settings)},
                indent=2,
            ),
        )
    with zipfile.ZipFile(temporary) as bundle:
        invalid = bundle.testzip()
        if invalid:
            raise RuntimeError(f"Handover ZIP integrity verification failed: {invalid}")
    temporary.replace(output)
    with output.open("rb") as handle:
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()
    output.with_suffix(".zip.sha256").write_text(checksum + "\n", encoding="ascii")
    LOGGER.info("Created verified portable ZIP: %s (%s bytes)", output, output.stat().st_size)
    return output


def benchmark(settings: Settings, iterations: int) -> dict:
    snapshot = status_snapshot(settings)
    if not snapshot["ready"]:
        raise RuntimeError("Import the real history before benchmarking.")
    end = min(utc_today() - timedelta(days=1), date.fromisoformat(snapshot["available_end"]))
    earliest = date.fromisoformat(snapshot["available_start"])
    periods = {
        "one_day": end,
        "thirty_days": max(earliest, end - timedelta(days=29)),
        "twelve_months": max(earliest, months_before(end + timedelta(days=1), 12)),
    }
    results = []
    for pollutant in ("PM2.5", "PM10"):
        for name, start in periods.items():
            search = Search(start=start, end=end, pollutant=pollutant)
            timings = []
            shape = None
            for _ in range(iterations):
                began = time.perf_counter()
                try:
                    response = compare(settings, search)
                    shape = {
                        "view": response["view"],
                        "eligible_locations": response["eligible_location_count"],
                        "winner": response["winners"][0]["name"],
                    }
                except ComparisonError as error:
                    if error.code != "NO_ELIGIBLE_CITY":
                        raise
                    shape = {"expected_error": error.code}
                timings.append((time.perf_counter() - began) * 1000)
            ordered = sorted(timings)
            p95 = ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]
            results.append(
                {
                    "pollutant": pollutant, "period": name, "start": start.isoformat(),
                    "end": end.isoformat(), "iterations": iterations,
                    "p50_ms": round(median(ordered), 3), "p95_ms": round(p95, 3),
                    "maximum_ms": round(max(ordered), 3), "target_met": p95 < 300,
                    "result": shape,
                }
            )
    report = {
        "kind": "in-process complete comparison service, real stored data",
        "platform": platform.platform(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "target_p95_ms": 300,
        "all_targets_met": all(item["target_met"] for item in results),
        "results": results,
    }
    (settings.var / "reports" / "benchmark.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report
