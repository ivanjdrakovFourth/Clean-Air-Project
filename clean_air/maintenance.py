from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .alerts import alert_connection, initialize_alerts
from .archive import archive_old, restore
from .database import backup
from .operations import benchmark
from .provider_store import for_provider
from .settings import Provider, Settings, StrictModel, timestamp_now
from .shutdown_control import WorkspaceBusy, workspace_lock

LOGGER = logging.getLogger(__name__)


class JobRequest(StrictModel):
    source: Provider = "eea"
    action: Literal["backup", "archive", "restore", "diagnostics", "benchmark"]
    artifact: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def safe_artifact(self):
        if self.action == "restore":
            if (
                not self.artifact or Path(self.artifact).name != self.artifact
                or not self.artifact.endswith(".jsonl.gz")
            ):
                raise ValueError("Choose a verified archive filename from the selected source.")
        elif self.artifact is not None:
            raise ValueError("Only archive restoration accepts an artifact filename.")
        return self


def initialize_jobs(settings: Settings) -> None:
    initialize_alerts(settings)
    with alert_connection(settings) as connection, connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS maintenance_jobs (
                id INTEGER PRIMARY KEY,
                request TEXT NOT NULL,
                status TEXT NOT NULL,
                created INTEGER NOT NULL,
                finished INTEGER,
                message TEXT NOT NULL,
                result TEXT,
                artifact TEXT
            )
        """)
        connection.execute(
            "UPDATE maintenance_jobs SET status='interrupted',message=? WHERE status='running'",
            ("The app stopped during this job. No success is assumed; retry after checking its output.",),
        )


def submit_job(settings: Settings, request: JobRequest) -> int:
    with alert_connection(settings) as connection, connection:
        if connection.execute(
            "SELECT COUNT(*) FROM maintenance_jobs WHERE status IN ('queued','waiting','running')"
        ).fetchone()[0] >= 20:
            raise ValueError("Too many queued operations; wait for an existing job to finish.")
        return connection.execute(
            "INSERT INTO maintenance_jobs(request,status,created,message) VALUES (?,'queued',?,?)",
            (request.model_dump_json(), timestamp_now(), "Queued; no source data has been changed yet."),
        ).lastrowid


def job_list(settings: Settings) -> list[dict]:
    with alert_connection(settings) as connection:
        return [
            {
                **dict(row), "request": json.loads(row["request"]),
                "result": json.loads(row["result"]) if row["result"] else None,
            }
            for row in connection.execute("SELECT * FROM maintenance_jobs ORDER BY id DESC LIMIT 50")
        ]


class MaintenanceWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stop_event = threading.Event()
        self.thread = None
        self.process = None
        initialize_jobs(settings)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="data-maintenance", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
        if self.thread:
            self.thread.join(timeout=5)

    def _record(self, identity: int, status: str, message: str, result=None, artifact=None):
        with alert_connection(self.settings) as connection, connection:
            connection.execute(
                "UPDATE maintenance_jobs SET status=?,message=?,result=?,artifact=?,finished=? WHERE id=?",
                (status, message, json.dumps(result) if result is not None else None,
                 str(artifact) if artifact else None,
                 timestamp_now() if status in ("completed", "failed") else None, identity),
            )

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            with alert_connection(self.settings) as connection:
                rows = connection.execute(
                    "SELECT * FROM maintenance_jobs WHERE status IN ('queued','waiting') ORDER BY id LIMIT 20"
                ).fetchall()
            if not rows:
                self.stop_event.wait(1)
                continue
            for row in rows:
                if self.stop_event.is_set():
                    break
                try:
                    self.run_job(row["id"], JobRequest.model_validate_json(row["request"]))
                except WorkspaceBusy:
                    self._record(row["id"], "waiting", "Waiting for this source's collector to release its maintenance lock.")
                except (OSError, sqlite3.Error, ValueError, RuntimeError):
                    LOGGER.exception("Maintenance job %s failed; no success is reported", row["id"])
                    self._record(row["id"], "failed", "Operation failed. See application logs; any existing verified data/backup is retained.")
            self.stop_event.wait(1)

    def run_job(self, identity: int, request: JobRequest):
        configured = for_provider(self.settings, request.source)
        if request.action == "backup":
            self._record(identity, "running", "Taking a consistent source-specific SQLite snapshot.")
            output = self.settings.var / "backups" / f"{request.source}-{timestamp_now()}-{identity}.sqlite3"
            backup(configured.database, output)
            self._record(identity, "completed", "Backup integrity verified; download is ready.", {"source": request.source}, output)
            return
        with workspace_lock(self.settings.root, f"{request.source}-collector.lock"):
            self._record(identity, "running", "Running the requested local operation.")
            if request.action == "archive":
                result = archive_old(configured)
                self._record(identity, "completed", "Verified retention operation finished; no data is invented when nothing is old enough.", result or {"archived": 0})
            elif request.action == "restore":
                result = restore(configured, configured.archive_directory / request.artifact)
                self._record(identity, "completed", "Archive identity, checksum and restored data verified.", result)
            elif request.action == "benchmark":
                result = benchmark(configured, 30)
                self._record(
                    identity, "completed" if result["all_targets_met"] else "failed",
                    "Measured comparison-service latency. Inspect the real result and machine/load context.", result,
                )
            else:
                output = self.settings.var / "reports" / f"diagnostics-{identity}.txt"
                environment = {key: value for key, value in os.environ.items() if key.upper() != "OPENAQ_API_KEY"}
                with output.open("w", encoding="utf-8") as handle:
                    self.process = subprocess.Popen(
                        [sys.executable, "-B", "-m", "pytest", "-q",
                         "--basetemp", str(self.settings.var / "tmp" / f"diagnostics-{identity}"),
                         "-o", f"cache_dir={self.settings.var / 'tmp' / f'diagnostics-cache-{identity}'}"],
                        cwd=self.settings.root, stdout=handle, stderr=subprocess.STDOUT, env=environment,
                    )
                    code = self.process.wait()
                    self.process = None
                self._record(
                    identity, "completed" if code == 0 else "failed",
                    "Automated local tests passed." if code == 0 else "One or more local tests failed; inspect the diagnostic output.",
                    {"exit_code": code, "output": output.read_text("utf-8")[-24000:]},
                )
