from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta

import httpx

from .archive import archive_old
from .credentials import openaq_key
from .database import UPSERT_OBSERVATION, connect, get_metadata, rebuild_station_days, set_metadata
from .provider_store import collection_start
from .settings import POLLUTANTS, Settings, timestamp_now, utc_today
from .shutdown_control import WorkspaceBusy, workspace_lock
from .source import SourceError, mass_unit_factor

LOGGER = logging.getLogger(__name__)
API = "https://api.openaq.org/v3"
PARAMETERS = {"pm25": "PM2.5", "pm10": "PM10", "no2": "NO2", "o3": "O3", "so2": "SO2", "co": "CO"}
WINDOW_SECONDS = 41 * 86400


class RequestBudget(SourceError):
    pass


class WindowTimeout(SourceError):
    pass


@dataclass(frozen=True)
class Sensor:
    id: int
    station: str
    pollutant: str
    first: int
    last: int
    license_windows: tuple[tuple[int, int | None], ...] = ()


def api_time(value: str) -> int:
    if not isinstance(value, str):
        raise SourceError("OpenAQ returned an invalid UTC timestamp.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SourceError("OpenAQ returned a timestamp without its UTC offset.")
    return int(parsed.timestamp())


def utc_text(value: int) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")


class OpenAQClient:
    def __init__(
        self, settings: Settings, stop: threading.Event,
        client: httpx.Client | None = None,
    ) -> None:
        key = openaq_key(settings.root)
        if not key:
            raise SourceError(
                "OpenAQ needs a local API credential. Run Configure-OpenAQ.cmd or set OPENAQ_API_KEY. "
                "EEA data is not substituted."
            )
        self.settings, self.stop = settings, stop
        self.client = client or httpx.Client(
            timeout=settings.options.collection.request_timeout_seconds,
            follow_redirects=False,
            headers={"X-API-Key": key, "User-Agent": "CleanAirFinder/3.0 (Bulgaria historical archive)"},
        )
        self.client.headers["X-API-Key"] = key
        self.calls = 0
        self.next_request = 0.0
        with connect(settings.database) as connection:
            self.recent_requests = get_metadata(connection, "openaq_request_times", [])

    def close(self) -> None:
        self.client.close()

    def request(self, path: str, params: dict | None = None) -> dict:
        if not path.startswith("/") or "://" in path or ".." in path:
            raise SourceError("Invalid OpenAQ resource path.")
        for attempt in range(self.settings.options.collection.request_attempts):
            if self.calls >= self.settings.options.openaq.requests_per_cycle:
                raise RequestBudget("OpenAQ request budget reached; committed history will resume next cycle.")
            delay = max(0.0, self.next_request - time.monotonic())
            now = time.time()
            self.recent_requests = [stamp for stamp in self.recent_requests if stamp > now - 3600]
            if len(self.recent_requests) >= 1900:
                delay = max(delay, self.recent_requests[0] + 3601 - now)
                LOGGER.info("OpenAQ hourly request allowance is cooling down; source checkpoints are preserved.")
            if self.stop.wait(delay):
                raise SourceError("OpenAQ collection stopped; committed checkpoints were retained.")
            self.next_request = time.monotonic() + 60 / self.settings.options.openaq.requests_per_minute
            self.calls += 1
            self.recent_requests.append(time.time())
            with connect(self.settings.database) as connection, connection:
                set_metadata(connection, "openaq_request_times", self.recent_requests)
            try:
                response = self.client.get(API + path, params=params)
            except httpx.TransportError as error:
                LOGGER.warning("OpenAQ transport failure on %s (attempt %s)", path, attempt + 1)
                if attempt + 1 == self.settings.options.collection.request_attempts:
                    raise SourceError(f"OpenAQ transport failed on {path}; credentials were not logged.") from error
                if self.stop.wait(2 ** attempt):
                    raise SourceError("OpenAQ collection stopped.") from error
                continue
            if response.status_code == 408:
                raise WindowTimeout(f"OpenAQ timed out on {path}; the historical window will be reduced.")
            if response.status_code == 429 or response.status_code >= 500:
                retry = 2 ** attempt
                reset = response.headers.get("retry-after") or response.headers.get("x-ratelimit-reset")
                if reset:
                    try:
                        raw = float(reset)
                        retry = max(1, min(3600, raw - time.time() if raw > 1_000_000_000 else raw))
                    except ValueError:
                        LOGGER.warning("OpenAQ returned an unrecognized retry interval; using bounded backoff.")
                LOGGER.warning("OpenAQ HTTP %s on %s; waiting %.1f seconds", response.status_code, path, retry)
                if self.stop.wait(retry):
                    raise SourceError("OpenAQ collection stopped during rate-limit wait.")
                continue
            if response.status_code != 200:
                raise SourceError(
                    f"OpenAQ HTTP {response.status_code} on {path}. "
                    "Check credential/permission and provider availability; response bodies are not logged."
                )
            if len(response.content) > 16 * 1024 * 1024:
                raise SourceError("OpenAQ returned an unexpectedly large page.")
            try:
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload["results"], list):
                    raise ValueError("results must be a list")
                return payload
            except (ValueError, KeyError, TypeError) as error:
                raise SourceError(f"OpenAQ returned an invalid page on {path}.") from error
        raise SourceError(f"OpenAQ retries exhausted on {path}; history remains unchanged.")


def _station_match(location: dict, official: list[dict]) -> dict | None:
    if (location.get("provider") or {}).get("name") != "EEA" or not location.get("isMonitor"):
        return None
    coordinates = location["coordinates"]
    matches = []
    for row in official:
        distance = math.hypot(
            (row["latitude"] - coordinates["latitude"]) * 111320,
            (row["longitude"] - coordinates["longitude"]) * 111320
            * math.cos(math.radians(coordinates["latitude"])),
        )
        if distance <= 50:
            matches.append(row)
    return matches[0] if len(matches) == 1 else None


def observation(row: dict, sensor: Sensor) -> tuple | None:
    try:
        parameter = row["parameter"]
        if PARAMETERS.get(parameter["name"]) != sensor.pollutant:
            raise SourceError("OpenAQ changed a sensor's pollutant identity.")
        factor = mass_unit_factor(parameter["units"])
        started = api_time(row["period"]["datetimeFrom"]["utc"])
        ended = api_time(row["period"]["datetimeTo"]["utc"])
        if ended - started != 3600 or started % 3600:
            raise SourceError("OpenAQ hour is not an aligned one-hour UTC interval.")
        raw = row.get("summary") or {}
        raw_value = raw.get("avg") if raw.get("avg") is not None else row.get("value")
        value = float(raw_value) * factor if raw_value is not None else None
        if value is not None and not math.isfinite(value):
            value = None
        coverage = row.get("coverage") or {}
        fraction = float(coverage.get("percentCoverage", coverage.get("percentComplete", 0))) / 100
        if not math.isfinite(fraction) or not 0 <= fraction <= 1:
            raise SourceError("OpenAQ returned invalid hour coverage.")
        flags = row.get("flagInfo")
        valid = (
            value is not None and value >= 0 and fraction >= 0.75
            and isinstance(flags, dict) and flags.get("hasFlags") is False
        )
        return (
            f"OA_SENSOR_{sensor.id}", POLLUTANTS[sensor.pollutant],
            started, ended, value, "hour", 1 if valid else -1, 0,
            timestamp_now(), fraction,
        )
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise SourceError("OpenAQ returned a malformed hourly measurement.") from error


class OpenAQCollector:
    def __init__(self, settings: Settings, report: Callable[[dict | None], None] | None = None) -> None:
        if settings.provider != "openaq":
            raise ValueError("The OpenAQ collector requires the OpenAQ store; cross-source import was refused.")
        self.settings = settings
        self.report = report
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.wake_event = threading.Event()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="openaq-collector", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                outcome = self.sync_once()
                if self.report:
                    self.report(outcome)
                delay = 60 if not outcome["complete"] else self.settings.options.collection.refresh_seconds
            except WorkspaceBusy:
                LOGGER.info("OpenAQ collection is waiting for another source operation to finish.")
                delay = 60
            except (RuntimeError, sqlite3.Error, OSError, ValueError):
                LOGGER.exception("OpenAQ collection failed; its own stored history is retained")
                if self.report and not self.stop_event.is_set():
                    self.report(None)
                delay = 60
            deadline = time.monotonic() + delay
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                if self.wake_event.wait(min(0.5, max(0, deadline - time.monotonic()))):
                    break
            self.wake_event.clear()

    def _catalog(self, client: OpenAQClient, force: bool) -> list[Sensor]:
        signature = hashlib.sha256(
            json.dumps([self.settings.options.openaq.include_community, list(POLLUTANTS)]).encode()
        ).hexdigest()
        with connect(self.settings.database) as connection:
            old = get_metadata(connection, "openaq_catalog", None)
            if (
                not force and old and old["signature"] == signature
                and timestamp_now() - old["updated"] < 86400
            ):
                return [Sensor(**row) for row in old["sensors"]]
        locations = []
        page = 1
        while True:
            values = client.request("/locations", {"iso": "BG", "limit": 1000, "page": page})["results"]
            locations.extend(values)
            if len(values) < 1000:
                break
            page += 1
        official = json.loads(
            (self.settings.root / "config" / "bulgarian-stations.json").read_text("utf-8")
        )["stations"]
        with connect(replace(self.settings, provider="eea").database) as connection:
            city_names = {row["id"]: row["city"] for row in connection.execute("SELECT * FROM stations")}
        licenses: dict[int, dict] = {}
        sensors = []
        accepted = []
        rejected = []
        city_aliases: dict[str, set[str]] = {}
        for location in locations:
            if not isinstance(location, dict) or not isinstance(location.get("sensors"), list):
                raise SourceError("OpenAQ returned malformed location/sensor metadata.")
            if (location.get("country") or {}).get("code") != "BG":
                raise SourceError("OpenAQ country filtering returned a foreign location.")
            if location.get("isMobile") or not location.get("coordinates"):
                rejected.append({"id": location["id"], "reason": "Mobile monitor or missing fixed coordinates."})
                continue
            if not self.settings.options.openaq.include_community and not location.get("isMonitor"):
                rejected.append({"id": location["id"], "reason": "Community sensors excluded by configuration."})
                continue
            if not location.get("datetimeFirst") or not location.get("datetimeLast"):
                rejected.append({"id": location["id"], "reason": "No reported observation date bounds."})
                continue
            permitted = []
            for license_ref in location.get("licenses", []):
                identity = int(license_ref["id"])
                if identity not in licenses:
                    result = client.request(f"/licenses/{identity}")["results"]
                    if len(result) != 1:
                        raise SourceError("OpenAQ license identity was ambiguous.")
                    licenses[identity] = result[0]
                rights = licenses[identity]
                if rights.get("modificationAllowed") is True and rights.get("redistributionAllowed") is True:
                    permitted.append({**license_ref, "rights": rights})
            if not permitted:
                rejected.append({"id": location["id"], "reason": "Permitted modification/redistribution not established."})
                continue
            coordinates = location["coordinates"]
            lat, lon = float(coordinates["latitude"]), float(coordinates["longitude"])
            if not 41 <= lat <= 45 or not 22 <= lon <= 29:
                rejected.append({"id": location["id"], "reason": "Coordinates outside the Bulgarian bounding region."})
                continue
            match = _station_match(location, official)
            station = match["station_id"] if match else f"OA{int(location['id'])}"
            city = city_names.get(station) if match else None
            locality = location.get("locality")
            if city and locality:
                city_aliases.setdefault(locality, set()).add(city)
            matched_sensors = []
            first = api_time(location["datetimeFirst"]["utc"])
            last = api_time(location["datetimeLast"]["utc"])
            windows = []
            for license_ref in permitted:
                begin = license_ref.get("dateFrom")
                finish = license_ref.get("dateTo")
                lower = int(datetime.fromisoformat(begin).replace(tzinfo=UTC).timestamp()) if begin else 0
                upper = (
                    int((datetime.fromisoformat(finish).replace(tzinfo=UTC) + timedelta(days=1)).timestamp())
                    if finish else None
                )
                windows.append((lower, upper))
            for item in location["sensors"]:
                pollutant = PARAMETERS.get(item.get("parameter", {}).get("name"))
                if not pollutant:
                    continue
                try:
                    mass_unit_factor(item["parameter"]["units"])
                except SourceError:
                    rejected.append({"id": item["id"], "reason": "Unsupported gas/other unit; no assumed ppm conversion."})
                    continue
                sensor = Sensor(int(item["id"]), station, pollutant, first, last, tuple(windows))
                matched_sensors.append(sensor)
            if matched_sensors:
                sensors.extend(matched_sensors)
                accepted.append((location, station, city, match, permitted))
        with connect(self.settings.database) as connection, connection:
            for location, station, city, match, permitted in accepted:
                name = location.get("name")
                if not isinstance(name, str) or not name.strip():
                    name = f"OpenAQ location {location['id']} (name not supplied)"
                    LOGGER.warning(
                        "OpenAQ location %s has no supplied name; its ID is displayed explicitly.",
                        location["id"],
                    )
                locality = location.get("locality")
                aliases = city_aliases.get(locality, set())
                city = city or (next(iter(aliases)) if len(aliases) == 1 else locality)
                if city:
                    connection.execute("INSERT OR IGNORE INTO cities VALUES (?)", (city,))
                connection.execute(
                    "INSERT INTO stations VALUES (?,?) ON CONFLICT(id) DO UPDATE SET city=excluded.city",
                    (station, city),
                )
                coordinates = location["coordinates"]
                connection.execute(
                    "INSERT OR REPLACE INTO station_metadata VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        station, str(location["id"]), name,
                        match["address"] if match else "", coordinates["latitude"], coordinates["longitude"],
                        match["area_type"] if match else "OpenAQ fixed location",
                        "Government monitor" if location.get("isMonitor") else "Community sensor",
                        utc_today().isoformat(), f"https://explore.openaq.org/locations/{location['id']}",
                    ),
                )
                set_metadata(connection, f"station_provider:{station}", {
                    "location_id": location["id"], "provider": location.get("provider"),
                    "owner": location.get("owner"), "is_monitor": bool(location.get("isMonitor")),
                    "canonical_station": match["station_id"] if match else None,
                    "licenses": permitted, "locality": locality,
                })
            for sensor in sensors:
                connection.execute(
                    "INSERT OR IGNORE INTO sampling_points VALUES (?,?)",
                    (f"OA_SENSOR_{sensor.id}", sensor.station),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO import_files(url,dataset) VALUES (?,1)",
                    (f"{API}/sensors/{sensor.id}/hours",),
                )
            set_metadata(connection, "openaq_catalog", {
                "signature": signature, "updated": timestamp_now(),
                "sensors": [asdict(sensor) for sensor in sensors],
                "rejected": rejected,
            })
        LOGGER.info("OpenAQ catalog: %s supported sensor series; %s exclusions", len(sensors), len(rejected))
        return sensors

    def _window(
        self, client: OpenAQClient, sensor: Sensor, lower: int, upper: int,
        cursor: dict, cursor_key: str,
    ) -> int:
        page = 1
        imported = 0
        while True:
            payload = client.request(
                f"/sensors/{sensor.id}/hours",
                {"datetime_from": utc_text(lower), "datetime_to": utc_text(upper),
                 "limit": 1000, "page": page},
            )
            values = payload["results"]
            rows = []
            rejected = 0
            for value in values:
                try:
                    rows.append(observation(value, sensor))
                except SourceError:
                    rejected += 1
            if rejected:
                LOGGER.warning(
                    "OpenAQ sensor %s: excluded %s malformed/unsupported hourly records; no values were guessed.",
                    sensor.id, rejected,
                )
            rows = [
                row for row in rows if row and lower <= row[2] and row[3] <= upper
                and any(
                    row[2] >= licensed_start and (licensed_end is None or row[3] <= licensed_end)
                    for licensed_start, licensed_end in sensor.license_windows
                )
            ]
            with connect(self.settings.database) as connection, connection:
                file_id = connection.execute(
                    "SELECT id FROM import_files WHERE url=?", (f"{API}/sensors/{sensor.id}/hours",)
                ).fetchone()[0]
                connection.executemany(UPSERT_OBSERVATION, [(*row, 1, file_id) for row in rows])
                rebuild_station_days(connection, sensor.station)
                connection.execute(
                    "UPDATE import_files SET last_success=?,rows_imported=rows_imported+? WHERE id=?",
                    (timestamp_now(), len(rows), file_id),
                )
                imported += len(rows)
            if len(values) < 1000:
                break
            page += 1
        with connect(self.settings.database) as connection, connection:
            set_metadata(connection, cursor_key, cursor)
            latest = connection.execute(
                "SELECT MAX(ended) FROM observations WHERE validity>0 AND value>=0"
            ).fetchone()[0]
            set_metadata(connection, "latest_observation", latest)
            if latest:
                set_metadata(connection, "initial_import_complete", True)
        return imported

    def sync_once(self, force: bool = False) -> dict:
        with workspace_lock(self.settings.root, "openaq-collector.lock"):
            return self._sync_once(force)

    def _sync_once(self, force: bool = False) -> dict:
        client = None
        run = None
        try:
            with connect(self.settings.database) as connection, connection:
                set_metadata(connection, "data_provider", "openaq")
                previously_complete = bool(get_metadata(connection, "openaq_backfill_complete", False))
                connection.execute(
                    "UPDATE collection_runs SET status='interrupted',finished=?,message=? WHERE status='running'",
                    (timestamp_now(), "Interrupted; resuming from committed OpenAQ sensor checkpoints."),
                )
                run = connection.execute(
                    "INSERT INTO collection_runs(started,status,message) VALUES (?,'running',?)",
                    (timestamp_now(), "Loading OpenAQ locations, source permissions and sensor history."),
                ).lastrowid
            client = OpenAQClient(self.settings, self.stop_event)
            sensors = self._catalog(client, force)
            if not sensors:
                raise SourceError("OpenAQ has no permitted supported Bulgarian sensor series.")
            months = self.settings.options.openaq.history_months or self.settings.options.collection.history_months
            start = int(datetime.combine(collection_start(self.settings, months), datetime.min.time(), UTC).timestamp())
            end = timestamp_now() // 3600 * 3600
            work = []
            requires_backfill = force
            with connect(self.settings.database) as connection, connection:
                for sensor in sensors:
                    lower = max(start, sensor.first // 3600 * 3600)
                    key = f"openaq_cursor:{sensor.id}"
                    old = get_metadata(connection, key, None)
                    if old:
                        # Legacy cursors reused lower for incremental refresh windows.
                        old.setdefault("history_floor", max(old["requested_start"], sensor.first // 3600 * 3600))
                    if (
                        not old or force or old["requested_start"] > start
                        or old.get("history_floor", lower) > lower
                    ):
                        requires_backfill = True
                        state = {"requested_start": start, "lower": lower, "history_floor": lower,
                                 "next_to": min(end, sensor.last // 3600 * 3600 + 3600), "target_end": end}
                    else:
                        state = old
                        if (
                            state["next_to"] <= state["lower"]
                            and sensor.last // 3600 * 3600 + 3600 > lower
                        ):
                            refresh_from = max(lower, state["target_end"] - self.settings.options.openaq.revision_days * 86400)
                            state = {**state, "lower": refresh_from, "next_to": end, "target_end": end}
                    if state["next_to"] <= state["lower"]:
                        set_metadata(connection, key, state)
                    work.append([sensor, key, state])
            previously_complete = previously_complete and not requires_backfill
            with connect(self.settings.database) as connection, connection:
                set_metadata(connection, "openaq_backfill_complete", previously_complete)
                set_metadata(connection, "history_requested_start", datetime.fromtimestamp(start, UTC).date().isoformat())
            imported = 0
            complete = False
            failures: dict[int, str] = {}
            while not self.stop_event.is_set():
                pending = [
                    item for item in work
                    if item[2]["next_to"] > item[2]["lower"] and item[0].id not in failures
                ]
                if not pending:
                    complete = not failures
                    break
                for sensor, key, state in pending:
                    upper = state["next_to"]
                    window = state.get("window_seconds", WINDOW_SECONDS)
                    lower = max(state["lower"], upper - window)
                    try:
                        imported += self._window(
                            client, sensor, lower, upper, {**state, "next_to": lower}, key
                        )
                    except WindowTimeout:
                        if window <= 86400:
                            failures[sensor.id] = "Provider timed out even for a one-day window; history is retained for retry."
                            LOGGER.warning("OpenAQ sensor %s timed out; continuing other sensor histories.", sensor.id)
                            continue
                        state["window_seconds"] = max(86400, window // 7200 * 3600)
                        with connect(self.settings.database) as connection, connection:
                            set_metadata(connection, key, state)
                        LOGGER.warning(
                            "OpenAQ sensor %s: reducing the historical request window to %s hours after a timeout.",
                            sensor.id, state["window_seconds"] // 3600,
                        )
                        continue
                    state["next_to"] = lower
                    with connect(self.settings.database) as connection, connection:
                        finished = sum(item[2]["next_to"] <= item[2]["lower"] for item in work)
                        connection.execute(
                            "UPDATE collection_runs SET total_files=?,completed_files=?,imported_rows=?,message=? WHERE id=?",
                            (len(sensors), finished, imported,
                             f"OpenAQ: {finished}/{len(sensors)} sensor histories complete; "
                             f"{client.calls} API requests this cycle. UTC hourly backfill is resumable.", run),
                        )
                        set_metadata(connection, "openaq_backfill_complete", previously_complete and not force)
            if self.stop_event.is_set():
                raise SourceError("OpenAQ collection stopped; source-specific checkpoints are retained.")
            archive_old(self.settings)
            with connect(self.settings.database) as connection, connection:
                set_metadata(connection, "openaq_backfill_complete", complete or previously_complete)
                set_metadata(connection, "openaq_pending_failures", failures)
                if complete:
                    set_metadata(connection, "last_success", timestamp_now())
                set_metadata(connection, "history_requested_start", datetime.fromtimestamp(start, UTC).date().isoformat())
                connection.execute(
                    "UPDATE collection_runs SET status=?,finished=?,message=? WHERE id=?",
                    ("completed" if complete else "partial", timestamp_now(),
                     "OpenAQ source history is ready; incremental collection continues."
                     if complete else f"OpenAQ retained completed history; {len(failures)} timed-out series will retry.", run),
                )
            return {"complete": complete, "imported_rows": imported, "requests": client.calls}
        except RequestBudget:
            with connect(self.settings.database) as connection, connection:
                connection.execute(
                    "UPDATE collection_runs SET status='paused',finished=?,message=? WHERE id=?",
                    (timestamp_now(), "OpenAQ quota budget reached; source backfill resumes next cycle.", run),
                )
            return {"complete": False, "requests": client.calls}
        except (RuntimeError, sqlite3.Error, OSError, ValueError) as error:
            with connect(self.settings.database) as connection, connection:
                connection.execute(
                    "UPDATE collection_runs SET status='failed',finished=?,message=? WHERE id=?",
                    (timestamp_now(), str(error), run),
                )
            raise
        finally:
            if client:
                client.close()
