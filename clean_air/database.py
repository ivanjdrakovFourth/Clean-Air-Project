from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cities (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS stations (
    id TEXT PRIMARY KEY,
    city TEXT REFERENCES cities(name)
);
CREATE TABLE IF NOT EXISTS sampling_points (
    id TEXT PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(id)
);
CREATE TABLE IF NOT EXISTS import_files (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    dataset INTEGER NOT NULL,
    etag TEXT,
    last_success INTEGER,
    rows_imported INTEGER NOT NULL DEFAULT 0,
    window_start TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    point_id TEXT NOT NULL REFERENCES sampling_points(id),
    pollutant INTEGER NOT NULL CHECK (pollutant IN (5, 6001)),
    started INTEGER NOT NULL,
    ended INTEGER NOT NULL,
    value REAL,
    aggregation TEXT NOT NULL CHECK (aggregation IN ('hour', 'day')),
    validity INTEGER NOT NULL,
    verification INTEGER NOT NULL,
    result_time INTEGER NOT NULL,
    data_capture REAL,
    priority INTEGER NOT NULL,
    file_id INTEGER NOT NULL REFERENCES import_files(id),
    PRIMARY KEY (point_id, pollutant, started, aggregation)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS observations_station_time
    ON observations(point_id, started);
CREATE INDEX IF NOT EXISTS observations_retention ON observations(started);
CREATE TABLE IF NOT EXISTS station_days (
    station_id TEXT NOT NULL REFERENCES stations(id),
    pollutant INTEGER NOT NULL,
    day TEXT NOT NULL,
    mean REAL NOT NULL,
    coverage REAL,
    observation_count INTEGER NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (station_id, pollutant, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS station_days_query ON station_days(pollutant, day, station_id);
CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY,
    started INTEGER NOT NULL,
    finished INTEGER,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    total_files INTEGER NOT NULL DEFAULT 0,
    completed_files INTEGER NOT NULL DEFAULT 0,
    imported_rows INTEGER NOT NULL DEFAULT 0,
    rejected_rows INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS archives (
    name TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    created INTEGER NOT NULL,
    observations INTEGER NOT NULL,
    station_days INTEGER NOT NULL,
    cutoff TEXT NOT NULL
);
"""

UPSERT_OBSERVATION = """
INSERT INTO observations (
    point_id, pollutant, started, ended, value, aggregation, validity,
    verification, result_time, data_capture, priority, file_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(point_id, pollutant, started, aggregation) DO UPDATE SET
    ended=excluded.ended, value=excluded.value, validity=excluded.validity,
    verification=excluded.verification, result_time=excluded.result_time,
    data_capture=excluded.data_capture, priority=excluded.priority, file_id=excluded.file_id
WHERE excluded.priority > observations.priority
   OR (excluded.priority = observations.priority
       AND excluded.result_time >= observations.result_time)
"""


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=15000")
    try:
        yield connection
    finally:
        connection.close()


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            raise RuntimeError(
                f"Unsupported database schema {version}; expected {SCHEMA_VERSION}. "
                "Back up the database and use the matching application version."
            )
        if version == 0:
            connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.commit()


def set_metadata(connection: sqlite3.Connection, key: str, value: object) -> None:
    connection.execute(
        "INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def get_metadata(connection: sqlite3.Connection, key: str, default: object = None) -> object:
    row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def rebuild_station_days(connection: sqlite3.Connection, station_id: str) -> None:
    rows = connection.execute(
        """
        SELECT o.* FROM observations o
        JOIN sampling_points p ON p.id=o.point_id
        WHERE p.station_id=?
        ORDER BY o.priority DESC, o.result_time DESC, o.point_id
        """,
        (station_id,),
    )
    daily: dict[tuple[int, str], sqlite3.Row] = {}
    hourly: dict[tuple[int, str], dict[int, sqlite3.Row]] = {}
    for row in rows:
        if row["validity"] <= 0 or row["value"] is None or row["value"] < 0:
            continue
        start = datetime.fromtimestamp(row["started"], UTC)
        key = row["pollutant"], start.date().isoformat()
        duration = row["ended"] - row["started"]
        if row["aggregation"] == "day" and duration == 86400 and start.hour == 0:
            daily.setdefault(key, row)
        elif row["aggregation"] == "hour" and duration == 3600 and start.minute == 0:
            hourly.setdefault(key, {}).setdefault(row["started"], row)
    connection.execute("DELETE FROM station_days WHERE station_id=?", (station_id,))
    output = []
    for pollutant, day in daily.keys() | hourly.keys():
        key = pollutant, day
        if key in daily:
            row = daily[key]
            output.append(
                (
                    station_id, pollutant, day, row["value"], row["data_capture"], 1,
                    "validated daily" if row["priority"] == 2 else "provisional daily",
                )
            )
        else:
            values = hourly[key]
            mean = sum(row["value"] for row in values.values()) / len(values)
            quality = "validated" if all(row["priority"] == 2 for row in values.values()) else "provisional"
            output.append(
                (station_id, pollutant, day, mean, len(values) / 24, len(values), f"{quality} hourly")
            )
    connection.executemany("INSERT INTO station_days VALUES (?, ?, ?, ?, ?, ?, ?)", output)


def backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with connect(source) as connection, sqlite3.connect(destination) as target:
        connection.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
