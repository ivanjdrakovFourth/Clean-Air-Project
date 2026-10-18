from __future__ import annotations

import os
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from test_sources import source_day

from clean_air.database import OBSERVATION_DEFINITION, connect, initialize
from clean_air.health import prometheus_summary, source_health
from clean_air.maintenance import JobRequest, MaintenanceWorker, job_list, submit_job
from clean_air.provider_store import prepare_sources
from clean_air.settings import ROOT, Options
from clean_air.shutdown_control import WorkspaceBusy, workspace_lock
from clean_air.source_workers import SourceWorkers


@pytest.fixture
def maintenance(settings):
    prepare_sources(settings)
    source_day(settings, "eea", "Alpha", "A1", "2025-01-01", 3)
    source_day(settings, "openaq", "Alpha", "OA1", "2025-01-01", 90)
    return settings, MaintenanceWorker(settings)


def test_backup_job_contains_only_selected_source_and_verifies_integrity(maintenance):
    settings, worker = maintenance
    request = JobRequest(source="openaq", action="backup")
    identity = submit_job(settings, request)
    worker.run_job(identity, request)
    row = job_list(settings)[0]
    assert row["status"] == "completed"
    with connect(Path(row["artifact"])) as connection:
        assert connection.execute("SELECT mean FROM station_days").fetchone()[0] == 90
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_source_maintenance_lock_does_not_silently_run_during_collection(maintenance):
    settings, worker = maintenance
    request = JobRequest(source="openaq", action="archive")
    identity = submit_job(settings, request)
    with workspace_lock(settings.root, "openaq-collector.lock"), pytest.raises(WorkspaceBusy):
        worker.run_job(identity, request)
    assert job_list(settings)[0]["status"] == "queued"


def test_retention_ui_job_reports_no_old_records_without_inventing_archive(maintenance):
    settings, worker = maintenance
    request = JobRequest(action="archive")
    identity = submit_job(settings, request)
    worker.run_job(identity, request)
    row = job_list(settings)[0]
    assert row["status"] == "completed"
    assert row["result"] == {"archived": 0}


def test_empty_and_unsupported_sources_are_not_reported_as_healthy(settings):
    prepare_sources(settings)
    report = source_health(settings)
    direct = next(row for row in report["sources"] if row["id"] == "bg_direct")
    assert direct["tone"] == "neutral"
    assert "not available" in direct["label"]
    assert all(row["counts"]["observations"] == 0 for row in report["sources"])
    assert prometheus_summary(settings)["available"] is False


def test_expanded_pollutant_constraint_accepts_gases_after_v2_migration(settings):
    from conftest import raw_observation

    with connect(settings.database) as connection, connection:
        raw_observation(connection)
        legacy = OBSERVATION_DEFINITION.replace("IN (1,5,7,8,10,6001)", "IN (5,6001)")
        connection.executescript(
            f"CREATE TABLE legacy_observations {legacy};"
            "INSERT INTO legacy_observations SELECT * FROM observations;"
            "DROP TABLE observations;"
            "ALTER TABLE legacy_observations RENAME TO observations;"
            "PRAGMA user_version=2;"
        )
        original = connection.execute("SELECT * FROM observations").fetchone()
        gas = list(original)
        gas[1] = 8
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            connection.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", gas)
    initialize(settings.database)
    with connect(settings.database) as connection, connection:
        original = connection.execute("SELECT * FROM observations").fetchone()
        data = list(original)
        data[1] = 8
        connection.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", data)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


def test_monitoring_age_is_not_a_pollution_score(settings):
    prepare_sources(settings)
    report = source_health(settings, "CO")
    assert report["pollutant"] == "CO"
    assert any("API response alone" in text for text in report["definitions"])
    with pytest.raises(ValueError):
        prometheus_summary(settings, "untrusted-query")


def test_ready_openaq_cache_without_key_is_not_called_healthy_live_collection(settings, monkeypatch):
    monkeypatch.delenv("OPENAQ_API_KEY", raising=False)
    prepare_sources(settings)
    source_day(settings, "openaq", "Alpha", "OA1", "2025-01-01", 3)
    values = settings.options.model_dump()
    values["collection"]["enabled"] = True
    report = source_health(replace(settings, options=Options.model_validate(values)))
    state = next(row for row in report["sources"] if row["id"] == "openaq")
    assert state["label"] == "Saved history available; key needed for updates"
    assert state["tone"] == "waiting"


def test_native_prometheus_rule_respects_configured_provider_staleness(settings):
    environment = {**os.environ, "TEMP": str(settings.var / "tmp"), "TMP": str(settings.var / "tmp")}
    result = subprocess.run(
        [str(ROOT / "runtime" / "prometheus" / "promtool.exe"), "test", "rules",
         str(ROOT / "tests" / "prometheus_rules.test.yml")],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_stopped_expected_worker_is_not_reported_healthy_or_given_false_refresh_success(settings):
    prepare_sources(settings)
    source_day(settings, "eea", "Alpha", "A1", "2025-01-01", 3)
    values = settings.options.model_dump()
    values["collection"]["enabled"] = True
    configured = replace(settings, options=Options.model_validate(values))
    report = source_health(configured, worker_states={"eea": False})
    state = next(row for row in report["sources"] if row["id"] == "eea")
    assert state["tone"] == "bad"
    assert "Collector worker stopped" in state["label"]
    with pytest.raises(ValueError, match="Restart"):
        SourceWorkers(configured).refresh(configured, "eea")
