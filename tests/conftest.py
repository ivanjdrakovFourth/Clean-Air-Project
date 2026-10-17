from __future__ import annotations

import shutil
from datetime import UTC, datetime

import pytest

from clean_air.database import connect, initialize, set_metadata
from clean_air.settings import Options, Settings, load_settings, timestamp_now


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
