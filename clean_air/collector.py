from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from datetime import timedelta

from .archive import archive_old
from .database import (
    UPSERT_OBSERVATION,
    connect,
    get_metadata,
    rebuild_station_days,
    set_metadata,
)
from .metrics import Metrics
from .settings import Settings, months_before, timestamp_now, utc_today
from .source import EeaSource, SourceError, read_observations

LOGGER = logging.getLogger(__name__)


class Collector:
    def __init__(self, settings: Settings, metrics: Metrics | None = None) -> None:
        self.settings = settings
        self.metrics = metrics
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="eea-collector", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                LOGGER.warning(
                    "Collection is finishing an in-flight request. Committed file checkpoints "
                    "will be retained if the process exits."
                )

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.sync_once()
            except (SourceError, sqlite3.Error, OSError, ValueError):
                LOGGER.exception("Collection failed; stored history remains available")
            self.stop_event.wait(self.settings.options.collection.refresh_seconds)

    def _progress(self, run_id: int, message: str, **values: int) -> None:
        allowed = {"total_files", "completed_files", "imported_rows", "rejected_rows"}
        if not values.keys() <= allowed:
            raise ValueError("Invalid collection progress field")
        with connect(self.settings.database) as connection, connection:
            assignments = ", ".join(["message=?"] + [f"{name}=?" for name in values])
            connection.execute(
                f"UPDATE collection_runs SET {assignments} WHERE id=?",
                (message, *values.values(), run_id),
            )

    def sync_once(self, force: bool = False) -> dict[str, int]:
        source = EeaSource(self.settings)
        now = timestamp_now()
        with connect(self.settings.database) as connection, connection:
            connection.execute(
                "UPDATE collection_runs SET status='interrupted', finished=?, "
                "message='Interrupted; resuming from committed file checkpoints.' WHERE status='running'",
                (now,),
            )
            cursor = connection.execute(
                "INSERT INTO collection_runs(started,status,message) VALUES (?, 'running', ?)",
                (now, "Loading official Bulgarian city and station metadata."),
            )
            run_id = cursor.lastrowid
            if run_id is None:
                raise RuntimeError("SQLite did not create a collection run")
        try:
            self._metadata(source, force)
            inventories = [(dataset, source.files(dataset)) for dataset in (2, 1)]
            if not inventories[0][1] or not inventories[1][1]:
                raise SourceError(
                    "Both validated history and current provisional particulate-matter files "
                    "are required. The EEA returned an empty inventory."
                )
            files = [(dataset, url) for dataset, urls in inventories for url in urls]
            self._progress(run_id, f"Importing {len(files)} measurement files.", total_files=len(files))
            counts = {"completed_files": 0, "imported_rows": 0, "rejected_rows": 0}
            failures: list[str] = []
            start = months_before(utc_today(), self.settings.options.collection.history_months)
            end = utc_today() + timedelta(days=1)
            for dataset, url in files:
                if self.stop_event.is_set():
                    raise SourceError("Collection stopped; completed file checkpoints were retained.")
                try:
                    imported, rejected = self._file(source, dataset, url, start, end, force)
                    counts["imported_rows"] += imported
                    counts["rejected_rows"] += rejected
                except (SourceError, OSError, sqlite3.Error) as error:
                    failures.append(f"{url.rsplit('/', 1)[-1]}: {error}")
                    LOGGER.exception("Measurement file import failed: %s", url)
                counts["completed_files"] += 1
                self._progress(
                    run_id,
                    f"Processed {counts['completed_files']} of {len(files)} files; "
                    f"{len(failures)} failed.",
                    **counts,
                )
            if failures:
                raise SourceError(
                    f"{len(failures)} source file(s) failed; imports will retry. First failure: {failures[0]}"
                )
            archive_old(self.settings)
            with connect(self.settings.database) as connection, connection:
                bounds = connection.execute(
                    "SELECT MIN(day), MAX(day) FROM station_days"
                ).fetchone()
                if not bounds[0] or bounds[0] > start.isoformat():
                    raise SourceError(
                        "The imported source does not cover the required twelve-month start date. "
                        "No complete-history claim will be made."
                    )
                latest = connection.execute(
                    "SELECT MAX(ended) FROM observations WHERE validity>0 AND value>=0"
                ).fetchone()[0]
                set_metadata(connection, "latest_observation", latest)
                set_metadata(connection, "initial_import_complete", True)
                set_metadata(connection, "last_success", timestamp_now())
                set_metadata(connection, "history_requested_start", start.isoformat())
                connection.execute(
                    "UPDATE collection_runs SET status='completed', finished=?, message=? WHERE id=?",
                    (timestamp_now(), "History is ready. Updates continue in the background.", run_id),
                )
            LOGGER.info("Collection completed: %s", counts)
            return counts
        except (SourceError, sqlite3.Error, OSError, ValueError) as error:
            with connect(self.settings.database) as connection, connection:
                connection.execute(
                    "UPDATE collection_runs SET status='failed', finished=?, message=? WHERE id=?",
                    (timestamp_now(), str(error), run_id),
                )
            if self.metrics:
                self.metrics.failures.inc()
            raise
        finally:
            source.close()

    def _metadata(self, source: EeaSource, force: bool) -> None:
        with connect(self.settings.database) as connection:
            updated = get_metadata(connection, "metadata_updated", 0)
            if not force and timestamp_now() - int(updated) < 86400:
                return
        cities = source.cities()
        mappings: dict[str, str] = {}
        points: dict[str, str] = {}
        for city in cities:
            if self.stop_event.is_set():
                raise SourceError("Collection stopped while loading metadata.")
            for dataset in (2, 1):
                for point_id, station_id in source.city_points(city, dataset):
                    if station_id in mappings and mappings[station_id] != city:
                        raise SourceError(
                            f"EEA station {station_id} belongs to conflicting cities: "
                            f"{mappings[station_id]} and {city}."
                        )
                    mappings[station_id] = city
                    points[point_id] = station_id
        if not points:
            raise SourceError("The EEA returned no city-to-station mappings.")
        with connect(self.settings.database) as connection, connection:
            connection.executemany(
                "INSERT OR IGNORE INTO cities VALUES (?)", [(city,) for city in cities]
            )
            connection.executemany(
                "INSERT INTO stations VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET city=excluded.city",
                mappings.items(),
            )
            connection.executemany(
                "INSERT INTO sampling_points VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET station_id=excluded.station_id",
                points.items(),
            )
            set_metadata(connection, "metadata_updated", timestamp_now())
        LOGGER.info("Loaded %s cities, %s stations and %s sampling points", len(cities), len(mappings), len(points))

    def _file(self, source: EeaSource, dataset: int, url: str, start, end, force: bool) -> tuple[int, int]:
        with connect(self.settings.database) as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO import_files(url,dataset) VALUES (?,?)", (url, dataset)
            )
            previous = connection.execute("SELECT * FROM import_files WHERE url=?", (url,)).fetchone()
        etag = None
        if (
            not force and previous["last_success"] and previous["window_start"]
            and previous["window_start"] <= start.isoformat()
        ):
            etag = previous["etag"]
        path = self.settings.var / "cache" / (hashlib.sha256(url.encode()).hexdigest() + ".parquet")
        try:
            new_etag = source.download(url, path, etag)
            if new_etag is None:
                return 0, 0
            observations, rejected, points = read_observations(path, start, end)
            with connect(self.settings.database) as connection, connection:
                for point_id, station_id in points:
                    connection.execute("INSERT OR IGNORE INTO stations(id) VALUES (?)", (station_id,))
                    connection.execute(
                        "INSERT OR IGNORE INTO sampling_points VALUES (?,?)", (point_id, station_id)
                    )
                connection.executemany(
                    UPSERT_OBSERVATION,
                    [(*row, dataset, previous["id"]) for row in observations],
                )
                for station_id in {station_id for _, station_id in points}:
                    rebuild_station_days(connection, station_id)
                latest = max(
                    (row[3] for row in observations if row[4] is not None and row[4] >= 0 and row[6] > 0),
                    default=0,
                )
                if latest > int(get_metadata(connection, "latest_observation", 0)):
                    set_metadata(connection, "latest_observation", latest)
                connection.execute(
                    "UPDATE import_files SET etag=?, last_success=?, rows_imported=?, window_start=? WHERE id=?",
                    (new_etag, timestamp_now(), len(observations), start.isoformat(), previous["id"]),
                )
            if rejected:
                LOGGER.warning(
                    "File %s contains %s invalid, negative, missing or unsupported observations; "
                    "they do not contribute to rankings.",
                    url.rsplit("/", 1)[-1], rejected,
                )
            return len(observations), rejected
        finally:
            if path.exists():
                path.unlink()
