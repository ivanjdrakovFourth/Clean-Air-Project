from __future__ import annotations

import csv
import io
import logging
import math
import re
import sqlite3
import ssl
import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from .archive import archive_old
from .database import UPSERT_OBSERVATION, connect, get_metadata, rebuild_station_days, set_metadata
from .provider_store import collection_start, for_provider
from .settings import POLLUTANTS, Settings, timestamp_now
from .shutdown_control import WorkspaceBusy, workspace_lock
from .source import SourceError, mass_unit_factor
from .station_catalog import Station, StationCatalog, apply_catalog

LOGGER = logging.getLogger(__name__)
BASE = "https://www.eea.government.bg/kav"
EXPORT = BASE + "/reports/air/exportcsv/"
COMPONENTS = "25,39,4,7,1,6"
CSV_ZONE = timezone(timedelta(hours=1))
TIME_POLICY = (
    "National CSV hour-ending labels use fixed UTC+01:00, including 24:00. "
    "Alignment verified against official hourly observations. Source days with duplicate labels are excluded."
)


def csv_end(value: str) -> int:
    if value.endswith(" 24:00"):
        parsed = datetime.strptime(value[:10], "%d.%m.%Y") + timedelta(days=1)
    else:
        parsed = datetime.strptime(value, "%d.%m.%Y %H:%M")
    return int(parsed.replace(tzinfo=CSV_ZONE).timestamp())


def parse_export(text: str, station: Station, lower: int, upper: int) -> tuple[list[tuple], int]:
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    header = next(reader, None)
    if not header or header[0].strip() != "\u0414\u0430\u0442\u0430/\u0427\u0430\u0441":
        raise SourceError("The national portal did not return its hourly CSV format.")
    columns = []
    for index, label in enumerate(header[1:], 1):
        found = re.search(r"\[([A-Za-z0-9.]+)\]\s+(.+)$", label.strip())
        if found and found[1] in POLLUTANTS:
            columns.append((index, POLLUTANTS[found[1]], mass_unit_factor(found[2])))
    if not columns:
        raise SourceError("The national CSV has no supported mass-concentration columns.")
    records = []
    labels = Counter()
    for row in reader:
        if not row or not any(value.strip() for value in row):
            continue
        row[0] = row[0].strip()
        if len(row) != len(header):
            raise SourceError("The national CSV has an inconsistent number of fields.")
        try:
            ended = csv_end(row[0].strip())
        except ValueError as error:
            raise SourceError("The national CSV contains an invalid hourly timestamp.") from error
        labels[row[0].strip()] += 1
        records.append((row, ended))
    # The portal has emitted missing and duplicated labels on a DST transition day.
    ambiguous_days = {label[:10] for label, count in labels.items() if count > 1}
    rows = []
    excluded = 0
    observed = timestamp_now()
    for row, ended in records:
        if row[0][:10] in ambiguous_days:
            excluded += len(columns)
            continue
        if ended - 3600 < lower or ended > upper:
            continue
        for index, pollutant, factor in columns:
            raw = row[index].strip()
            try:
                value = float(raw) * factor if raw else None
            except ValueError as error:
                raise SourceError("The national CSV contains an unrecognized concentration value.") from error
            valid = value is not None and math.isfinite(value) and value >= 0
            if value is not None and not math.isfinite(value):
                value = None
            rows.append((
                f"BG_DIRECT_{station.national_code}_{pollutant}", pollutant,
                ended - 3600, ended, value, "hour", 1 if valid else -1, 0, observed, None,
            ))
    if not records:
        raise SourceError("The national portal returned no hourly records; no completed-window claim was made.")
    return rows, excluded


class BulgarianCollector:
    def __init__(self, settings: Settings, report: Callable[[dict | None], None] | None = None):
        if settings.provider != "bg_direct":
            raise ValueError("The national collector requires its own Bulgarian-direct store.")
        self.settings, self.report = settings, report
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="bulgarian-direct-collector", daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_event.is_set():
            delay = 60
            try:
                result = self.sync_once()
                if self.report:
                    self.report(result)
                if result["complete"]:
                    delay = self.settings.options.collection.refresh_seconds
            except WorkspaceBusy:
                LOGGER.info("National CSV collection is waiting for its source maintenance lock.")
            except (SourceError, sqlite3.Error, OSError, ValueError):
                LOGGER.exception("National CSV collection failed; its committed source history is retained.")
                if self.report and not self.stop_event.is_set():
                    self.report(None)
            deadline = time.monotonic() + delay
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                if self.wake_event.wait(min(0.5, max(0, deadline - time.monotonic()))):
                    break
            self.wake_event.clear()

    def _catalog(self) -> list[Station]:
        catalog = StationCatalog.model_validate_json(
            (self.settings.root / "config" / "bulgarian-stations.json").read_text("utf-8")
        )
        apply_catalog(self.settings.database, self.settings.root / "config" / "bulgarian-stations.json")
        with connect(for_provider(self.settings, "eea").database) as source:
            cities = {row["id"]: row["city"] for row in source.execute("SELECT * FROM stations")}
        with connect(self.settings.database) as connection, connection:
            for station in catalog.stations:
                city = cities.get(station.station_id)
                address_city = station.address.split(",", 1)[0].strip()
                if not city and "," in station.address and station.name.startswith(address_city):
                    city = address_city
                if city:
                    connection.execute("INSERT OR IGNORE INTO cities VALUES (?)", (city,))
                    connection.execute("UPDATE stations SET city=? WHERE id=?", (city, station.station_id))
                set_metadata(connection, f"station_provider:{station.station_id}", {
                    "provider": {"name": "Bulgarian Executive Environment Agency"},
                    "is_monitor": True, "canonical_station": station.station_id,
                    "source_url": BASE + f"/reports/air/qReport/{station.national_code}/01",
                    "time_policy": TIME_POLICY, "license_notice": "Attributed factual measurements from the agency's public CSV export; website assets are not copied.",
                })
                for pollutant in POLLUTANTS.values():
                    connection.execute("INSERT OR IGNORE INTO sampling_points VALUES (?,?)",
                                       (f"BG_DIRECT_{station.national_code}_{pollutant}", station.station_id))
        return catalog.stations

    def _download(self, client: httpx.Client, station: Station, lower: int, upper: int) -> str:
        options = self.settings.options
        for attempt in range(options.collection.request_attempts):
            with connect(self.settings.database) as connection:
                previous = get_metadata(connection, "bg_csv_last_export", 0)
            wait = max(0, previous + options.bg_direct.export_interval_seconds - time.time())
            if self.stop_event.wait(wait):
                raise SourceError("National collection stopped; committed station checkpoints are retained.")
            with connect(self.settings.database) as connection, connection:
                set_metadata(connection, "bg_csv_last_export", time.time())
            first = datetime.fromtimestamp(lower, UTC).strftime("%d.%m.%Y 00:00")
            last = (datetime.fromtimestamp(upper, UTC) + timedelta(days=1)).strftime("%d.%m.%Y 00:00")
            try:
                response = client.post(EXPORT, data={
                    "stationCode": station.national_code, "betreiberCode": "01", "type": "1",
                    "startDate": first, "endDate": last, "stationName": station.name,
                    "comps": COMPONENTS, "show1": first, "show2": last,
                })
            except httpx.TransportError as error:
                LOGGER.warning("National CSV transport failed for station %s, attempt %s.", station.national_code, attempt + 1)
                if attempt + 1 == options.collection.request_attempts:
                    raise SourceError("National CSV transport failed; cached data is retained.") from error
                continue
            if response.status_code in (408, 429) or response.status_code >= 500:
                LOGGER.warning("National CSV HTTP %s for station %s; retry remains rate limited.", response.status_code, station.national_code)
                retry = response.headers.get("retry-after")
                if retry:
                    try:
                        try:
                            seconds = float(retry)
                        except ValueError:
                            seconds = parsedate_to_datetime(retry).timestamp() - time.time()
                        if math.isfinite(seconds) and seconds > options.bg_direct.export_interval_seconds:
                            with connect(self.settings.database) as connection, connection:
                                set_metadata(connection, "bg_csv_last_export", time.time() + seconds - options.bg_direct.export_interval_seconds)
                    except (ValueError, TypeError, OverflowError):
                        LOGGER.warning("National export supplied an unreadable Retry-After; retaining the full export cooldown.")
                continue
            if response.status_code != 200 or "text/csv" not in response.headers.get("content-type", "").lower():
                raise SourceError(f"National export returned HTTP {response.status_code} or a non-CSV page; no placeholder data was imported.")
            if len(response.content) > 16 * 1024 * 1024:
                raise SourceError("The national CSV exceeded the safe export size.")
            return response.content.decode("utf-8-sig")
        raise SourceError("National CSV retries exhausted; existing measurements were not replaced.")

    def sync_once(self, force: bool = False) -> dict:
        with workspace_lock(self.settings.root, "bg_direct-collector.lock"):
            return self._sync_once(force)

    def _sync_once(self, force: bool) -> dict:
        stations = self._catalog()
        floor = int(datetime.combine(
            collection_start(self.settings, self.settings.options.collection.history_months),
            datetime.min.time(), UTC,
        ).timestamp())
        with connect(self.settings.database) as connection, connection:
            connection.execute("UPDATE collection_runs SET status='interrupted',finished=?,message=? WHERE status='running'",
                               (timestamp_now(), "Resuming national CSV station checkpoints."))
            cycle = get_metadata(connection, "bg_csv_cycle")
            if force or not cycle or not cycle["pending"] or cycle["floor"] > floor:
                cycle = {"floor": floor, "end": timestamp_now() // 3600 * 3600,
                         "pending": [station.station_id for station in stations]}
            known_complete = bool(get_metadata(connection, "bg_direct_backfill_complete", False))
            for station in stations:
                cursor = get_metadata(connection, f"bg_csv_cursor:{station.station_id}")
                if force or not cursor or cursor["floor"] > floor:
                    known_complete = False
            set_metadata(connection, "bg_direct_backfill_complete", known_complete)
            set_metadata(connection, "history_requested_start", datetime.fromtimestamp(floor, UTC).date().isoformat())
            set_metadata(connection, "bg_csv_cycle", cycle)
            run = connection.execute(
                "INSERT INTO collection_runs(started,status,message,total_files,completed_files) VALUES (?,'running',?,?,?)",
                (timestamp_now(), "Reading genuine national hourly CSV exports; requests are spaced by at least three minutes.",
                 len(stations), len(stations) - len(cycle["pending"])),
            ).lastrowid
        imported = 0
        exports = 0
        try:
            with httpx.Client(
                verify=ssl.create_default_context(), trust_env=False, follow_redirects=False,
                timeout=self.settings.options.collection.request_timeout_seconds,
                headers={"User-Agent": "CleanAirFinder/3.0 (Bulgarian public measurement archive)", "Connection": "close"},
            ) as client:
                for station in stations:
                    if station.station_id not in cycle["pending"]:
                        continue
                    if self.stop_event.is_set():
                        raise SourceError("National collection stopped; saved checkpoints will resume.")
                    with connect(self.settings.database) as connection:
                        cursor = get_metadata(connection, f"bg_csv_cursor:{station.station_id}")
                    lower = floor
                    if cursor and not force and cursor["floor"] <= floor:
                        lower = max(floor, cursor["end"] - self.settings.options.bg_direct.revision_days * 86400)
                    text = self._download(client, station, lower, cycle["end"])
                    rows, excluded = parse_export(text, station, lower, cycle["end"])
                    exports += 1
                    if excluded:
                        LOGGER.warning("National station %s: excluded %s values on duplicate-labelled source days.", station.national_code, excluded)
                    with connect(self.settings.database) as connection, connection:
                        url = EXPORT + f"#station={station.national_code}&operator=01"
                        connection.execute("INSERT OR IGNORE INTO import_files(url,dataset) VALUES (?,1)", (url,))
                        file_id = connection.execute("SELECT id FROM import_files WHERE url=?", (url,)).fetchone()[0]
                        connection.executemany(UPSERT_OBSERVATION, [(*row, 1, file_id) for row in rows])
                        rebuild_station_days(connection, station.station_id)
                        imported += len(rows)
                        cycle["pending"].remove(station.station_id)
                        set_metadata(connection, f"bg_csv_cursor:{station.station_id}", {"floor": floor, "end": cycle["end"]})
                        set_metadata(connection, "bg_csv_cycle", cycle)
                        set_metadata(connection, "bg_csv_time_policy", TIME_POLICY)
                        latest = connection.execute("SELECT MAX(ended) FROM observations WHERE validity>0 AND value>=0").fetchone()[0]
                        set_metadata(connection, "latest_observation", latest)
                        if latest:
                            set_metadata(connection, "initial_import_complete", True)
                        connection.execute("UPDATE import_files SET last_success=?,rows_imported=?,window_start=? WHERE id=?",
                                           (timestamp_now(), len(rows), datetime.fromtimestamp(lower, UTC).date().isoformat(), file_id))
                        connection.execute("UPDATE collection_runs SET completed_files=?,imported_rows=?,rejected_rows=rejected_rows+?,message=? WHERE id=?",
                                           (len(stations) - len(cycle["pending"]), imported, excluded,
                                            f"National CSV: {len(stations)-len(cycle['pending'])}/{len(stations)} stations checked. Cached source history is preserved.", run))
                    if exports >= self.settings.options.bg_direct.exports_per_cycle and cycle["pending"]:
                        break
            complete = not cycle["pending"]
            self._catalog()
            archive_old(self.settings)
            with connect(self.settings.database) as connection, connection:
                if complete:
                    set_metadata(connection, "bg_direct_backfill_complete", True)
                    set_metadata(connection, "last_success", timestamp_now())
                connection.execute("UPDATE collection_runs SET status=?,finished=?,message=? WHERE id=?",
                                   ("completed" if complete else "paused", timestamp_now(),
                                    "National CSV source history checked; automatic updates continue." if complete else "National CSV checkpoints saved; collection resumes automatically.", run))
            return {"complete": complete, "imported_rows": imported}
        except (SourceError, sqlite3.Error, OSError, ValueError) as error:
            with connect(self.settings.database) as connection, connection:
                connection.execute("UPDATE collection_runs SET status='failed',finished=?,message=? WHERE id=?",
                                   (timestamp_now(), str(error), run))
            raise
