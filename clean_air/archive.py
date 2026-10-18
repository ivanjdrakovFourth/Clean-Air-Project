from __future__ import annotations

import gzip
import hashlib
import json
import logging
import sqlite3
import time
from datetime import UTC, date, datetime
from datetime import time as day_time
from pathlib import Path

from .database import UPSERT_OBSERVATION, connect, get_metadata, rebuild_station_days, set_metadata
from .settings import Settings, months_before, timestamp_now, utc_today

LOGGER = logging.getLogger(__name__)
FORMAT = "clean-air-archive-v1"


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def inspect_archive(path: Path) -> tuple[dict, dict[str, int]]:
    counts = {"observations": 0, "station_days": 0}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        first = handle.readline()
        if not first:
            raise ValueError("The archive is empty.")
        header = json.loads(first)
        if header.get("format") != FORMAT:
            raise ValueError("Unsupported archive format")
        for line in handle:
            item = json.loads(line)
            if item["table"] not in counts or not isinstance(item["row"], list):
                raise ValueError("Invalid archive record")
            counts[item["table"]] += 1
    return header, counts


def archive_old(settings: Settings, today: date | None = None) -> dict[str, object] | None:
    cutoff = months_before(today or utc_today(), settings.options.retention.active_months)
    cutoff_epoch = int(datetime.combine(cutoff, day_time(), UTC).timestamp())
    with connect(settings.database) as connection:
        paused = get_metadata(connection, "archive_pause_until", 0)
        if int(paused) > timestamp_now():
            return None
        connection.execute("BEGIN IMMEDIATE")
        counts = {
            "observations": connection.execute(
                "SELECT COUNT(*) FROM observations WHERE started<?", (cutoff_epoch,)
            ).fetchone()[0],
            "station_days": connection.execute(
                "SELECT COUNT(*) FROM station_days WHERE day<?", (cutoff.isoformat(),)
            ).fetchone()[0],
        }
        if not any(counts.values()):
            connection.rollback()
            return None
        name = f"before-{cutoff.isoformat()}-{time.time_ns()}.jsonl.gz"
        settings.archive_directory.mkdir(parents=True, exist_ok=True)
        path = settings.archive_directory / name
        temporary = path.with_suffix(path.suffix + ".part")
        header = {
            "format": FORMAT,
            "cutoff": cutoff.isoformat(),
            "provider": settings.provider,
            "provider_metadata": [
                dict(row) for row in connection.execute(
                    "SELECT * FROM metadata WHERE key LIKE 'station_provider:%'"
                )
            ],
            "historical_window": dict(connection.execute(
                "SELECT MIN(day) AS start,MAX(day) AS end FROM station_days WHERE day<?",
                (cutoff.isoformat(),),
            ).fetchone()),
            "counts": counts,
            "cities": [dict(row) for row in connection.execute("SELECT * FROM cities")],
            "stations": [dict(row) for row in connection.execute("SELECT * FROM stations")],
            "station_metadata": [
                dict(row) for row in connection.execute("SELECT * FROM station_metadata")
            ],
            "sampling_points": [dict(row) for row in connection.execute("SELECT * FROM sampling_points")],
            "import_files": [dict(row) for row in connection.execute("SELECT * FROM import_files")],
        }
        try:
            with gzip.open(temporary, "xt", encoding="utf-8") as output:
                output.write(json.dumps(header, allow_nan=False) + "\n")
                for table, column, bound in (
                    ("observations", "started", cutoff_epoch),
                    ("station_days", "day", cutoff.isoformat()),
                ):
                    cursor = connection.execute(f"SELECT * FROM {table} WHERE {column}<?", (bound,))
                    for row in cursor:
                        output.write(json.dumps({"table": table, "row": list(row)}, allow_nan=False) + "\n")
            _, actual_counts = inspect_archive(temporary)
            if actual_counts != counts:
                raise ValueError(f"Archive verification failed: expected {counts}, received {actual_counts}")
            checksum = digest(temporary)
            temporary.replace(path)
            path.with_suffix(path.suffix + ".sha256").write_text(checksum + "\n", encoding="ascii")
            connection.execute("DELETE FROM observations WHERE started<?", (cutoff_epoch,))
            connection.execute("DELETE FROM station_days WHERE day<?", (cutoff.isoformat(),))
            connection.execute(
                "INSERT INTO archives VALUES (?, ?, ?, ?, ?, ?)",
                (name, checksum, timestamp_now(), counts["observations"], counts["station_days"], cutoff.isoformat()),
            )
            connection.commit()
        except (OSError, ValueError, sqlite3.Error):
            connection.rollback()
            LOGGER.exception("Archiving failed; active records were not removed")
            raise
        finally:
            if temporary.exists():
                temporary.unlink()
    with connect(settings.database) as connection:
        connection.execute("PRAGMA incremental_vacuum").fetchall()
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint[0]:
            LOGGER.warning("Archive committed; WAL compaction was deferred by an active reader.")
    LOGGER.info("Verified and archived records older than %s: %s", cutoff, counts)
    return {"name": name, "sha256": checksum, **counts}


def restore(settings: Settings, path: Path) -> dict[str, int]:
    path = path.resolve()
    if not path.is_relative_to(settings.archive_directory.resolve()):
        raise ValueError("Copy the archive and its checksum into this workspace's var\\archives first.")
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    expected = checksum_path.read_text("ascii").strip()
    if len(expected) != 64 or digest(path) != expected:
        raise ValueError("Archive checksum verification failed; nothing was restored.")
    header, counts = inspect_archive(path)
    if header.get("provider", "eea") != settings.provider:
        raise ValueError("This archive belongs to another measurement source; select its source explicitly.")
    if counts != header["counts"]:
        raise ValueError("Archive row counts do not match the manifest; nothing was restored.")
    with connect(settings.database) as connection, connection:
        restored_days = []
        for row in header.get("provider_metadata", []):
            if not row["key"].startswith("station_provider:"):
                raise ValueError("Unexpected provider metadata in archive.")
            connection.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (row["key"], row["value"]))
        connection.executemany(
            "INSERT OR IGNORE INTO cities VALUES (?)", [(row["name"],) for row in header["cities"]]
        )
        connection.executemany(
            "INSERT OR IGNORE INTO stations VALUES (?, ?)",
            [(row["id"], row["city"]) for row in header["stations"]],
        )
        connection.executemany(
            "INSERT OR IGNORE INTO station_metadata VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    row["station_id"], row["national_code"], row["name"], row["address"],
                    row["latitude"], row["longitude"], row["area_type"], row["station_type"],
                    row["verified_on"], row["source_url"],
                )
                for row in header.get("station_metadata", [])
            ],
        )
        connection.executemany(
            "INSERT OR IGNORE INTO sampling_points VALUES (?, ?)",
            [(row["id"], row["station_id"]) for row in header["sampling_points"]],
        )
        file_ids = {}
        for row in header["import_files"]:
            connection.execute(
                "INSERT OR IGNORE INTO import_files(url,dataset) VALUES (?,?)",
                (row["url"], row["dataset"]),
            )
            file_ids[row["id"]] = connection.execute(
                "SELECT id FROM import_files WHERE url=?", (row["url"],)
            ).fetchone()[0]
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            next(handle)
            for line in handle:
                item = json.loads(line)
                if item["table"] == "station_days":
                    restored_days.append(item["row"][2])
                if item["table"] == "observations":
                    row = item["row"]
                    if len(row) != 12 or row[-1] not in file_ids:
                        raise ValueError("Invalid archived observation")
                    row[-1] = file_ids[row[-1]]
                    connection.execute(UPSERT_OBSERVATION, row)
        for row in header["stations"]:
            rebuild_station_days(connection, row["id"])
        if restored_days and connection.execute("SELECT 1 FROM station_days LIMIT 1").fetchone():
            windows = get_metadata(connection, "restored_source_windows", [])
            windows.append({"start": min(restored_days), "end": max(restored_days)})
            set_metadata(connection, "restored_source_windows", windows)
            set_metadata(connection, "initial_import_complete", True)
        set_metadata(connection, "archive_pause_until", timestamp_now() + 86400)
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Restored records failed referential-integrity verification.")
    LOGGER.info("Restored verified archive %s: %s", path.name, counts)
    return counts
