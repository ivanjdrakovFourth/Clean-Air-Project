from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest

from clean_air.database import connect, initialize, rebuild_station_days, set_metadata
from clean_air.provider_store import for_provider, prepare_sources
from clean_air.settings import (
    Options,
    Settings,
    load_settings,
    months_before,
    timestamp_now,
    utc_today,
)

PRIVATE_MARKER = "test-only-private-operational-metadata-not-a-credential"


@pytest.fixture
def settings(tmp_path):
    original = load_settings()
    options = Options.model_validate(
        {
            "collection": {"enabled": False},
            "monitoring": {"enabled": False},
        }
    )
    configured = Settings(tmp_path, options, original.reference)
    configured.prepare_directories()
    shutil.copytree(original.root / "config", tmp_path / "config")
    shutil.copytree(original.root / "clean_air" / "templates", tmp_path / "clean_air" / "templates")
    shutil.copytree(original.root / "clean_air" / "static", tmp_path / "clean_air" / "static")
    initialize(configured.database)
    return configured


@pytest.fixture
def seed(settings):
    def insert(city, station, day, value, pollutant=6001, coverage=None, kind="validated daily"):
        with connect(settings.database) as connection, connection:
            connection.execute("INSERT OR IGNORE INTO cities VALUES (?)", (city,))
            connection.execute("INSERT OR IGNORE INTO stations VALUES (?,?)", (station, city))
            connection.execute(
                "INSERT OR REPLACE INTO station_days VALUES (?,?,?,?,?,?,?)",
                (station, pollutant, day, value, coverage, 1 if coverage is None else 24, kind),
            )
            set_metadata(connection, "initial_import_complete", True)
            set_metadata(connection, "last_success", timestamp_now())
            set_metadata(connection, "latest_observation", timestamp_now())
    return insert


def raw_observation(
    connection, *, point="SPO-BG0001A_06001_100", station="BG0001A",
    city="Test City", day="2025-01-01", value=10, aggregation="day", priority=2, hour=0,
    validity=1, result_time=1,
):
    from clean_air.database import UPSERT_OBSERVATION

    connection.execute("INSERT OR IGNORE INTO cities VALUES (?)", (city,))
    connection.execute("INSERT OR IGNORE INTO stations VALUES (?,?)", (station, city))
    connection.execute("INSERT OR IGNORE INTO sampling_points VALUES (?,?)", (point, station))
    connection.execute(
        "INSERT OR IGNORE INTO import_files(id,url,dataset) VALUES (1,'https://example.invalid/fixture',2)"
    )
    began = datetime.fromisoformat(day).replace(tzinfo=UTC, hour=hour)
    epoch = int(began.timestamp())
    connection.execute(
        UPSERT_OBSERVATION,
        (
            point, 6001, epoch, epoch + (86400 if aggregation == "day" else 3600),
            value, aggregation, validity, 1, result_time, None, priority, 1,
        ),
    )


@pytest.fixture
def completed_sources(settings):
    prepare_sources(settings)
    beginning = months_before(utc_today(), 12).isoformat()
    for provider, value in (("eea", 10), ("openaq", 20)):
        configured = for_provider(settings, provider)
        with connect(configured.database) as connection, connection:
            for day in (beginning, (utc_today() - timedelta(days=1)).isoformat()):
                raw_observation(connection, day=day, value=value, result_time=10)
            rebuild_station_days(connection, "BG0001A")
            set_metadata(connection, "initial_import_complete", True)
            set_metadata(connection, "history_requested_start", beginning)
            set_metadata(connection, "last_success", timestamp_now())
            set_metadata(connection, "openaq_request_times", [12345])
            set_metadata(connection, "private_operational_note", PRIVATE_MARKER)
            connection.execute(
                "INSERT INTO collection_runs(started,finished,status,message) VALUES (?,?,'completed',?)",
                (timestamp_now(), timestamp_now(), PRIVATE_MARKER),
            )
            if provider == "openaq":
                set_metadata(connection, "openaq_backfill_complete", True)
                set_metadata(connection, "openaq_catalog", {
                    "signature": "test", "updated": timestamp_now(),
                    "sensors": [{"id": 1, "station": "BG0001A", "pollutant": "PM2.5",
                                 "first": 0, "last": timestamp_now(), "license_windows": [[0, None]]}],
                })
                set_metadata(connection, "openaq_cursor:1", {
                    "requested_start": 0, "lower": 0, "next_to": 0, "target_end": 1,
                })
                set_metadata(connection, "station_provider:BG0001A", {
                    "provider": {"name": "Test provider"}, "licenses": [{"name": "CC BY"}],
                })
    return settings
