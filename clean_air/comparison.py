from __future__ import annotations

import hashlib
import heapq
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from pydantic import Field, model_validator

from .database import connect, get_metadata
from .settings import (
    EEA_DATA_URL,
    POLLUTANTS,
    Pollutant,
    Provider,
    Settings,
    SourceChoice,
    StrictModel,
    timestamp_now,
    utc_today,
)
from .station_catalog import NATIONAL_URL

TIME_RESULT_LIMIT = 50


class Search(StrictModel):
    start: date | None = None
    end: date | None = None
    pollutant: Pollutant | None = None
    source: SourceChoice | None = None
    sources: list[Provider] | None = Field(default=None, min_length=1, max_length=3)
    monitor_class: Literal["all", "reference"] = "all"
    allow_partial_history: bool = False
    profile: Literal["who_2021", "custom"] = "who_2021"
    threshold: float | None = Field(default=None, ge=0)
    view: Literal["cities", "stations"] | None = None
    objective: Literal["average", "reference_days", "improvement"] | None = None
    target: Literal["locations", "day", "hour"] | None = None
    location: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def coherent(self) -> Search:
        if (self.start is None) != (self.end is None):
            raise ValueError("Set both dates, or leave both blank for the latest reported hour.")
        if self.start is not None and self.end is not None:
            if self.start > self.end:
                raise ValueError("The start date must not be after the end date.")
            if self.end >= utc_today():
                raise ValueError("Choose completed historical UTC days, not today or a future date.")
        if self.profile == "custom" and self.threshold is None:
            raise ValueError("A custom daily threshold is required for the custom profile.")
        if self.profile != "custom" and self.threshold is not None:
            raise ValueError("Custom thresholds must use the custom profile.")
        if self.sources:
            if self.source not in (None, "combined"):
                raise ValueError("Choose combined mode when selecting multiple source stores.")
            if len(set(self.sources)) != len(self.sources):
                raise ValueError("Each source may only be selected once.")
            self.source = "combined"
        return self


class ComparisonError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422, details: object = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


@dataclass(frozen=True)
class Sample:
    station: str
    bucket: str
    mean: float
    kind: str
    coverage: float | None


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    city: str | None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    station_type: str | None = None
    area_type: str | None = None
    metadata_verified_on: str | None = None
    upstream_provider: str | None = None
    is_reference_monitor: bool | None = None
    canonical_station: str | None = None
    licenses: tuple[str, ...] = ()


def _status_single(settings: Settings) -> dict:
    with connect(settings.database) as connection:
        complete = bool(get_metadata(connection, "initial_import_complete", False))
        run = connection.execute("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1").fetchone()
        bounds = connection.execute(
            "SELECT (SELECT MIN(day) FROM station_days),(SELECT MAX(day) FROM station_days)"
        ).fetchone()
        latest = get_metadata(connection, "latest_observation", None)
        stations = [
            dict(row) for row in connection.execute(
                "SELECT m.station_id AS id,m.name,m.address,s.city,m.verified_on "
                "FROM station_metadata m JOIN stations s ON s.id=m.station_id ORDER BY m.name"
            )
        ]
        return {
            "ready": complete,
            "timezone": "UTC",
            "available_start": bounds[0],
            "available_end": min(bounds[1], (utc_today() - timedelta(days=1)).isoformat()) if bounds[1] else None,
            "last_success": get_metadata(connection, "last_success", None),
            "latest_observation": latest,
            "source_stale": bool(
                latest and timestamp_now() - int(latest) > settings.options.collection.stale_after_hours * 3600
            ),
            "collection_enabled": settings.options.collection.enabled,
            "collection": dict(run) if run else None,
            "unmapped_stations": connection.execute(
                "SELECT COUNT(*) FROM stations WHERE city IS NULL"
            ).fetchone()[0],
            "cities": [row[0] for row in connection.execute("SELECT name FROM cities ORDER BY name")],
            "official_stations": stations,
            "national_metadata_source": NATIONAL_URL,
            "direct_national_readings_available": False,
            "measurement_provenance": {
                "eea": "Bulgarian official measurements delivered by the EEA",
                "openaq": "Separately collected OpenAQ observations; upstream provider and license are retained per location.",
                "bg_direct": "Verified Bulgarian-direct archive observations only; no usable live feed is configured.",
            }[settings.provider],
            "data_source": settings.provider,
            "backfill_complete": (
                bool(get_metadata(connection, "openaq_backfill_complete", False))
                if settings.provider == "openaq" else complete
            ),
            "available_pollutants": [
                name for name, code in POLLUTANTS.items()
                if connection.execute(
                    "SELECT 1 FROM station_days WHERE pollutant=? LIMIT 1", (code,)
                ).fetchone()
            ],
        }


def _locations(
    connection: sqlite3.Connection, view: str, monitor_class: str = "all"
) -> tuple[dict[str, Location], dict[str, str]]:
    provider = get_metadata(connection, "data_provider", "eea")
    provenance = {
        row["key"].removeprefix("station_provider:"): json.loads(row["value"])
        for row in connection.execute("SELECT * FROM metadata WHERE key LIKE 'station_provider:%'")
    }
    allowed = None
    if provider == "openaq" and monitor_class == "reference":
        allowed = {station for station, value in provenance.items() if value.get("is_monitor")}
    if view == "cities":
        locations = {
            row[0]: Location(row[0], row[0], row[0])
            for row in connection.execute("SELECT name FROM cities")
        }
        mapping = {
            row["id"]: row["city"]
            for row in connection.execute("SELECT id,city FROM stations WHERE city IS NOT NULL")
        }
        if allowed is not None:
            mapping = {station: city for station, city in mapping.items() if station in allowed}
    else:
        locations = {}
        for row in connection.execute(
            "SELECT m.*,s.city FROM station_metadata m JOIN stations s ON s.id=m.station_id"
        ):
            if allowed is not None and row["station_id"] not in allowed:
                continue
            details = provenance.get(row["station_id"], {})
            locations[row["station_id"]] = Location(
                row["station_id"], row["name"], row["city"], row["address"],
                row["latitude"], row["longitude"], row["station_type"], row["area_type"],
                row["verified_on"],
                (details.get("provider") or {}).get("name") if provider == "openaq" else "EEA/Bulgaria",
                details.get("is_monitor") if provider == "openaq" else True,
                details.get("canonical_station") if provider == "openaq" else row["station_id"],
                tuple(item["name"] for item in details.get("licenses", [])),
            )
        mapping = {station: station for station in locations}
    return locations, mapping


def _daily_samples(
    connection: sqlite3.Connection, code: int, start: str, end: str, coverage: float
) -> list[Sample]:
    return [
        Sample(row["station_id"], row["day"], row["mean"], row["kind"], row["coverage"])
        for row in connection.execute(
            "SELECT * FROM station_days WHERE pollutant=? AND day BETWEEN ? AND ? "
            "AND (coverage IS NULL OR coverage>=?)",
            (code, start, end, coverage),
        )
    ]


def _hour_query(
    code: int, start: int, end: int, stations: set[str] | None = None,
    starts: list[int] | None = None,
) -> tuple[str, list]:
    filters = ""
    arguments = [code, start, end]
    if stations is not None:
        filters += " AND p.station_id IN (SELECT value FROM json_each(?))"
        arguments.append(json.dumps(sorted(stations)))
    if starts is not None:
        filters += " AND o.started IN (SELECT value FROM json_each(?))"
        arguments.append(json.dumps(starts))
    return (
        """
        WITH ranked AS (
            SELECT o.started,o.value,o.priority,o.result_time,o.point_id,p.station_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY p.station_id,o.started
                       ORDER BY o.priority DESC,o.result_time DESC,o.point_id
                   ) AS choice
            FROM observations o JOIN sampling_points p ON p.id=o.point_id
            WHERE o.pollutant=? AND o.aggregation='hour'
              AND o.started>=? AND o.ended<=?
              AND o.validity>0 AND o.value>=0
        """ + filters + """
        ), hourly AS (SELECT * FROM ranked WHERE choice=1)
        """, arguments,
    )


def _hour_samples(
    connection: sqlite3.Connection, code: int, start: int, end: int,
    starts: list[int] | None = None,
) -> list[Sample]:
    query, arguments = _hour_query(code, start, end, starts=starts)
    rows = connection.execute(query + "SELECT * FROM hourly", arguments)
    provider = get_metadata(connection, "data_provider", "eea")
    return [
        Sample(
            row["station_id"], datetime.fromtimestamp(row["started"], UTC).isoformat(),
            row["value"], "OpenAQ reported hourly" if provider == "openaq"
            else "validated hourly" if row["priority"] == 2 else "provisional hourly", 1,
        )
        for row in rows
    ]


def _best_hours(
    connection: sqlite3.Connection, code: int, start: int, end: int,
    qualifying: set[str], view: str, minimum_hours: int,
) -> tuple[list[sqlite3.Row], set[str], int]:
    query, arguments = _hour_query(code, start, end, stations=qualifying)
    identity = "s.city" if view == "cities" else "h.station_id"
    name = "s.city" if view == "cities" else "m.name"
    rows = connection.execute(
        query + f"""
        , qualifying_days AS (
            SELECT station_id,date(started,'unixepoch') AS day FROM hourly
            GROUP BY station_id,date(started,'unixepoch') HAVING COUNT(*)>=?
        ), location_hours AS (
            SELECT {identity} AS location_id,{name} AS name,h.started,
                   AVG(h.value) AS mean,GROUP_CONCAT(h.station_id,'|') AS stations
            FROM hourly h
            JOIN stations s ON s.id=h.station_id
            LEFT JOIN station_metadata m ON m.station_id=h.station_id
            WHERE (h.station_id,date(h.started,'unixepoch'))
                  IN (SELECT station_id,day FROM qualifying_days)
            GROUP BY {identity},h.started
        )
        SELECT *, (SELECT COUNT(*) FROM location_hours) AS total_results,
                  (SELECT json_group_array(DISTINCT location_id) FROM location_hours) AS identities
        FROM location_hours ORDER BY mean,name,started LIMIT ?
        """,
        (*arguments, minimum_hours, TIME_RESULT_LIMIT),
    ).fetchall()
    if not rows:
        return [], set(), 0
    return rows, set(json.loads(rows[0]["identities"])), rows[0]["total_results"]


def _group(samples: list[Sample], mapping: dict[str, str]) -> dict[str, dict[str, list[Sample]]]:
    groups: dict[str, dict[str, list[Sample]]] = defaultdict(lambda: defaultdict(list))
    for sample in samples:
        if sample.station in mapping:
            groups[mapping[sample.station]][sample.bucket].append(sample)
    return groups


def _mean(samples: list[Sample]) -> float:
    return sum(sample.mean for sample in samples) / len(samples)


def _summary(location: Location, buckets: dict[str, list[Sample]], days: int, threshold: float | None) -> dict:
    means = [_mean(samples) for samples in buckets.values()]
    all_samples = [sample for samples in buckets.values() for sample in samples]
    below = sum(value <= threshold for value in means) if threshold is not None else None
    return _result_row(
        location, sum(means) / len(means), len(buckets), days,
        {sample.station for sample in all_samples}, below,
        sum(sample.kind.startswith("provisional") for sample in all_samples),
        sum(sample.coverage is None for sample in all_samples),
    )


def _result_row(
    location: Location, mean: float, valid_days: int, requested_days: int,
    stations: set[str], within: int | None, provisional: int, unspecified_capture: int,
) -> dict:
    return {
        "location": asdict(location),
        "city": location.city,
        "name": location.name,
        "mean": mean,
        "valid_days": valid_days,
        "requested_days": requested_days,
        "coverage_percent": 100 * valid_days / requested_days,
        "station_count": len(stations),
        "stations": sorted(stations),
        "days_at_or_below_reference": within,
        "days_above_reference": valid_days - within if within is not None else None,
        "days_within_reference_percent": 100 * within / valid_days if within is not None else None,
        "provisional_station_days": provisional,
        "provider_hourly_coverage_unknown": unspecified_capture,
        "baseline_mean": None,
        "baseline_days": None,
        "improvement_percent": None,
        "time_start": None,
        "time_end": None,
    }


def _day_counts(
    connection: sqlite3.Connection, code: int, start: str, end: str, coverage: float
) -> dict[str, int]:
    return {
        row[0]: row[1] for row in connection.execute(
            "SELECT station_id,COUNT(*) FROM station_days "
            "WHERE pollutant=? AND day BETWEEN ? AND ? "
            "AND (coverage IS NULL OR coverage>=?) GROUP BY station_id",
            (code, start, end, coverage),
        )
    }


def _period_rows(
    connection: sqlite3.Connection, code: int, start: str, end: str,
    coverage: float, qualifying: set[str], locations: dict[str, Location],
    view: str, threshold: float | None, minimum: int, days: int,
) -> list[dict]:
    identity = "city" if view == "cities" else "station_id"
    rows = connection.execute(
        f"""
        WITH eligible AS (
            SELECT d.*,s.city FROM station_days d JOIN stations s ON s.id=d.station_id
            WHERE d.pollutant=? AND d.day BETWEEN ? AND ?
              AND (d.coverage IS NULL OR d.coverage>=?)
              AND d.station_id IN (SELECT value FROM json_each(?))
        ), daily AS (
            SELECT {identity} AS location_id,day,AVG(mean) AS mean,
                   SUM(CASE WHEN kind LIKE 'provisional%' THEN 1 ELSE 0 END) AS provisional,
                   SUM(CASE WHEN coverage IS NULL THEN 1 ELSE 0 END) AS unspecified
            FROM eligible GROUP BY {identity},day
        ), station_lists AS (
            SELECT {identity} AS location_id,GROUP_CONCAT(DISTINCT station_id) AS stations
            FROM eligible GROUP BY {identity}
        )
        SELECT daily.location_id,AVG(daily.mean) AS mean,COUNT(*) AS valid_days,
               SUM(CASE WHEN daily.mean<=? THEN 1 ELSE 0 END) AS within_reference,
               SUM(provisional) AS provisional,SUM(unspecified) AS unspecified,station_lists.stations
        FROM daily JOIN station_lists ON station_lists.location_id=daily.location_id
        GROUP BY daily.location_id HAVING COUNT(*)>=?
        """,
        (code, start, end, coverage, json.dumps(sorted(qualifying)), threshold, minimum),
    )
    return [
        _result_row(
            locations[row["location_id"]], row["mean"], row["valid_days"], days,
            set(row["stations"].split(",")), row["within_reference"] if threshold is not None else None,
            row["provisional"], row["unspecified"],
        )
        for row in rows
    ]


def _current(
    settings: Settings, connection: sqlite3.Connection, search: Search,
    locations: dict[str, Location], mapping: dict[str, str], threshold: float | None,
    comparison_hour: int | None = None,
) -> tuple[list[dict], list[dict], dict]:
    now = timestamp_now()
    maximum_age = settings.options.comparison.current_max_age_hours * 3600
    samples = _hour_samples(connection, POLLUTANTS[search.pollutant], now - maximum_age - 3600, now)
    samples = [
        sample for sample in samples
        if int(datetime.fromisoformat(sample.bucket).timestamp()) + 3600 >= now - maximum_age
    ]
    grouped = _group(samples, mapping)
    by_hour: dict[str, dict[str, list[Sample]]] = defaultdict(dict)
    for location_id, buckets in grouped.items():
        for bucket, values in buckets.items():
            by_hour[bucket][location_id] = values
    minimum = 1 if search.location else 2
    hours = sorted(
        (hour for hour, entries in by_hour.items() if len(entries) >= minimum), reverse=True
    )
    if comparison_hour is not None:
        hours = [hour for hour in hours if int(datetime.fromisoformat(hour).timestamp()) == comparison_hour]
    if not hours:
        raise ComparisonError(
            "NO_FRESH_COMPARABLE_HOUR",
            f"No common recent hour has at least {minimum} eligible location(s) within "
            f"{settings.options.comparison.current_max_age_hours} hours. "
            "Start online collection, wait for reporting, or choose historical dates. "
            "Old readings are not presented as current.",
            details={"maximum_age_hours": settings.options.comparison.current_max_age_hours},
        )
    hour = hours[0]
    began = int(datetime.fromisoformat(hour).timestamp())
    rows = []
    for location_id, values in by_hour[hour].items():
        row = _summary(locations[location_id], {hour: values}, 1, threshold)
        row.update(time_start=began, time_end=began + 3600)
        rows.append(row)
    excluded = [
        {"name": location.name, "city": location.city, "reason": "No valid reading for the common comparison hour."}
        for key, location in locations.items() if key not in by_hour[hour]
    ]
    return rows, excluded, {
        "mode": "current", "time_start": began, "time_end": began + 3600,
        "label": "Latest comparable reported hour (UTC)", "requested_days": None,
        "minimum_valid_days": 1,
    }


def _historical(
    settings: Settings, connection: sqlite3.Connection, search: Search,
    snapshot: dict, locations: dict[str, Location], mapping: dict[str, str], threshold: float | None,
) -> tuple[list[dict], list[dict], dict]:
    start, end = search.start.isoformat(), search.end.isoformat()
    if not snapshot["available_start"] or start < snapshot["available_start"]:
        raise ComparisonError(
            "PERIOD_NOT_STORED",
            "The requested period starts before the locally available history. "
            "Choose a stored period, or restore the relevant archive.",
            details={"available_start": snapshot["available_start"], "available_end": snapshot["available_end"]},
        )
    if not snapshot["available_end"] or end > snapshot["available_end"]:
        raise ComparisonError(
            "PERIOD_NOT_STORED",
            "The requested end date is not yet available locally. Source reporting can be delayed.",
            details={"available_start": snapshot["available_start"], "available_end": snapshot["available_end"]},
        )
    days = (search.end - search.start).days + 1
    minimum = math.ceil(days * settings.options.comparison.minimum_period_coverage - 1e-10)
    coverage = settings.options.comparison.minimum_hourly_coverage
    code = POLLUTANTS[search.pollutant]
    station_days = {
        station: count for station, count in _day_counts(connection, code, start, end, coverage).items()
        if station in mapping
    }
    qualifying = {station for station, count in station_days.items() if count >= minimum}
    baseline_end = (search.start - timedelta(days=1)).isoformat()
    if search.objective == "improvement":
        counts = _day_counts(
            connection, code, snapshot["available_start"], baseline_end, coverage
        )
        qualifying &= {
            station for station, count in counts.items()
            if count >= settings.options.comparison.baseline_minimum_days
        }
    grouped = {}
    if search.target == "day":
        selected = [
            sample for sample in _daily_samples(connection, code, start, end, coverage)
            if sample.station in qualifying
        ]
        grouped = _group(selected, mapping)
    hourly_rows = []
    hourly_ids: set[str] = set()
    hourly_total = 0
    if search.target == "hour":
        began = int(datetime.combine(search.start, time(), UTC).timestamp())
        ended = int(datetime.combine(search.end + timedelta(days=1), time(), UTC).timestamp())
        hourly_rows, hourly_ids, hourly_total = _best_hours(
            connection, POLLUTANTS[search.pollutant], began, ended, qualifying,
            search.view, math.ceil(24 * coverage),
        )
    rows = []
    time_results = sum(len(buckets) for buckets in grouped.values())
    if search.target == "hour" and hourly_rows:
        actual = {
            (sample.station, sample.bucket): sample
            for sample in _hour_samples(
                connection, POLLUTANTS[search.pollutant], began, ended,
                starts=sorted({row["started"] for row in hourly_rows}),
            )
        }
        for candidate in hourly_rows:
            bucket = datetime.fromtimestamp(candidate["started"], UTC).isoformat()
            samples = [actual[(station, bucket)] for station in candidate["stations"].split("|")]
            row = _summary(locations[candidate["location_id"]], {bucket: samples}, 1, threshold)
            row.update(time_start=candidate["started"], time_end=candidate["started"] + 3600)
            rows.append(row)
        time_results = hourly_total
    elif search.target == "day":
        candidates = (
            (_mean(values), location_id, bucket, values)
            for location_id, buckets in grouped.items() for bucket, values in buckets.items()
        )
        best = heapq.nsmallest(
            TIME_RESULT_LIMIT, candidates,
            key=lambda item: (item[0], locations[item[1]].name, item[2]),
        )
        for _, location_id, bucket, values in best:
            row = _summary(locations[location_id], {bucket: values}, 1, threshold)
            began = int(datetime.combine(date.fromisoformat(bucket), time(), UTC).timestamp())
            row.update(
                time_start=began, time_end=began + 86400
            )
            rows.append(row)
    elif search.target == "locations":
        rows = _period_rows(
            connection, code, start, end, coverage, qualifying, locations,
            search.view, threshold, minimum, days,
        )
        if search.objective == "improvement":
            baseline_days = (search.start - date.fromisoformat(snapshot["available_start"])).days
            baseline = {
                row["location"]["id"]: row
                for row in _period_rows(
                    connection, code, snapshot["available_start"], baseline_end, coverage,
                    qualifying, locations, search.view, threshold,
                    settings.options.comparison.baseline_minimum_days, max(1, baseline_days),
                )
            }
            measured = []
            for row in rows:
                earlier = baseline.get(row["location"]["id"])
                if not earlier or earlier["mean"] <= 0:
                    continue
                baseline_mean = earlier["mean"]
                row.update(
                    baseline_mean=baseline_mean, baseline_days=earlier["valid_days"],
                    improvement_percent=100 * (baseline_mean - row["mean"]) / baseline_mean,
                )
                measured.append(row)
            rows = measured
    eligible_ids = (
        hourly_ids if search.target == "hour" else set(grouped) if search.target == "day"
        else {row["location"]["id"] for row in rows}
    )
    excluded = []
    for location_id, location in locations.items():
        if location_id in eligible_ids:
            continue
        best = max(
            (count for station, count in station_days.items() if mapping.get(station) == location_id),
            default=0,
        )
        reason = (
            "No station meets the required period and baseline coverage."
            if search.objective == "improvement"
            else "No qualifying hourly history for this period."
            if search.target == "hour"
            else "No station meets the required period coverage."
            if best else "No eligible measurements for this pollutant and period."
        )
        excluded.append({
            "name": location.name, "city": location.city, "reason": reason,
            "best_station_coverage_percent": 100 * best / days,
        })
    if not rows:
        code = "NO_HOURLY_HISTORY" if search.target == "hour" else "NO_ELIGIBLE_CITY" if search.view == "cities" else "NO_ELIGIBLE_LOCATION"
        subject = "No city" if search.view == "cities" else "No station"
        guidance = (
            "Hourly searches need actual hourly history; daily averages cannot reconstruct hours. "
            "Choose a period with qualifying hourly records."
            if search.target == "hour"
            else f"Improvement needs at least {settings.options.comparison.baseline_minimum_days} "
            "valid earlier days at the same contributing stations and a positive baseline. "
            "Choose a later period or lowest-pollution ranking."
            if search.objective == "improvement"
            else f"The period requires at least {minimum} qualifying day(s). "
            "Try another stored period or explicitly choose another pollutant."
        )
        raise ComparisonError(
            code,
            f"{subject} has sufficient valid measurements for this pollutant, period and definition. "
            + guidance,
            details={"excluded_cities": excluded, "minimum_valid_days": minimum},
        )
    return rows, excluded, {
        "mode": "historical", "label": f"{start} to {end}, inclusive UTC",
        "time_start": None, "time_end": None, "requested_days": days,
        "minimum_valid_days": minimum,
        "total_time_results": time_results if search.target != "locations" else None,
        "eligible_time_locations": len(eligible_ids) if search.target != "locations" else None,
        "baseline": {
            "definition": "All locally stored valid history before the chosen start; same qualifying stations.",
            "minimum_station_days": settings.options.comparison.baseline_minimum_days,
        } if search.objective == "improvement" else None,
    }


def resolved_search(settings: Settings, search: Search) -> Search:
    options = settings.options.comparison
    search = search.model_copy(update={
        "pollutant": search.pollutant or options.default_pollutant,
        "view": search.view or options.default_view,
        "objective": search.objective or options.default_objective,
        "target": search.target or options.default_target,
    })
    if search.start is None and (search.target != "locations" or search.objective != "average"):
        raise ComparisonError(
            "DATES_REQUIRED",
            "Leave dates blank only for latest-hour location comparisons with lowest concentration. "
            "Choose dates for daily-reference, improvement or cleanest-day/hour searches.",
        )
    if search.target != "locations" and search.objective != "average":
        raise ComparisonError(
            "INCOMPATIBLE_DEFINITION",
            "Cleanest-day/hour searches use lowest concentration. Other definitions rank locations over a period.",
        )
    threshold = search.threshold if search.profile == "custom" else settings.reference.daily_threshold(search.pollutant)
    if threshold is None and search.objective == "reference_days":
        raise ComparisonError(
            "DAILY_REFERENCE_UNAVAILABLE",
            "WHO has no 24-hour ozone concentration guideline. Select a custom daily reference "
            "or use the lowest-concentration definition; an eight-hour guideline is not a daily limit.",
        )
    return search


def _compare_single(
    settings: Settings, search: Search, comparison_hour: int | None = None, *,
    snapshot: dict | None = None,
) -> dict:
    options = settings.options.comparison
    search = resolved_search(settings, search)
    snapshot = _status_single(settings) if snapshot is None else snapshot
    if not snapshot["ready"]:
        raise ComparisonError(
            "HISTORY_NOT_READY",
            "The first real-data import is not complete. Check collection progress and retry.",
            503, snapshot,
        )
    threshold = search.threshold if search.profile == "custom" else settings.reference.daily_threshold(search.pollutant)
    with connect(settings.database) as connection:
        identity = get_metadata(connection, "data_provider", "eea")
        if identity != settings.provider:
            raise ComparisonError("SOURCE_IDENTITY_MISMATCH", "Source database identity does not match the selected source.", 503)
        locations, mapping = _locations(connection, search.view, search.monitor_class)
        if search.location:
            if search.location not in locations:
                raise ComparisonError(
                    "UNKNOWN_LOCATION", "Choose a station ID or city from the selected comparison view."
                )
            locations = {search.location: locations[search.location]}
            mapping = {station: identity for station, identity in mapping.items() if identity == search.location}
        if search.start is None:
            rows, excluded, period = _current(
                settings, connection, search, locations, mapping, threshold, comparison_hour
            )
        else:
            rows, excluded, period = _historical(
                settings, connection, search, snapshot, locations, mapping, threshold
            )
    def key(row: dict) -> tuple:
        if search.objective == "reference_days":
            return -row["days_within_reference_percent"], row["mean"]
        if search.objective == "improvement":
            return -row["improvement_percent"], row["mean"]
        return (row["mean"],)

    rows.sort(key=lambda row: (*key(row), row["name"], row["time_start"] or 0))
    with connect(settings.database) as connection:
        station_locations, _ = _locations(connection, "stations", search.monitor_class)
    for row in rows:
        row["station_details"] = [
            asdict(station_locations[station]) if station in station_locations
            else {
                "id": station, "name": station, "city": row["city"], "address": None,
                "latitude": None, "longitude": None, "upstream_provider": settings.provider,
                "is_reference_monitor": None,
            }
            for station in row["stations"]
        ]
    previous = None
    rank = 0
    for index, row in enumerate(rows):
        score = key(row)
        if previous is None or not all(
            math.isclose(a, b, rel_tol=0, abs_tol=1e-9) for a, b in zip(score, previous, strict=True)
        ):
            rank, previous = index + 1, score
        row["rank"] = rank
    hourly = period["mode"] == "current" or search.target == "hour"
    reference = {
        "id": search.profile,
        "version": settings.reference.version if search.profile == "who_2021" else "user-defined",
        "name": settings.reference.name if search.profile == "who_2021" else "Custom daily reference",
        "daily_threshold": threshold, "unit": "ug/m3",
        "averaging_period": "24-hour UTC mean",
        "applicable": not hourly and threshold is not None,
        "unavailable_reason": (
            "WHO has no 24-hour ozone guideline; the eight-hour reference is not applied to daily means."
            if threshold is None else "Daily references are not applied to hourly readings." if hourly else None
        ),
        "source": settings.reference.source if search.profile == "who_2021" else None,
    }
    if hourly or threshold is None:
        for row in rows:
            row.update(
                days_at_or_below_reference=None, days_above_reference=None,
                days_within_reference_percent=None,
            )
    title = (
        "Most days within your daily reference" if search.objective == "reference_days"
        else "Largest measured improvement" if search.objective == "improvement"
        else "Cleanest measured day" if search.target == "day"
        else "Cleanest measured hour" if search.target == "hour"
        else "Cleanest measured station" if search.view == "stations"
        else "Cleanest measured city"
    )
    methodology = {
        "version": "bulgarian-location-search-v2", "timezone": "UTC",
        "view": search.view, "target": search.target, "objective": search.objective,
        "monitor_class": search.monitor_class,
        "source": settings.provider,
        "ranking": (
            "Highest percentage of valid days at or below the daily reference; lower mean breaks ties."
            if search.objective == "reference_days"
            else "Greatest percentage reduction versus stored earlier history, using the same station cohort."
            if search.objective == "improvement"
            else "Lowest mean concentration in the selected comparable time window."
        ),
        "station_selection": "Verified national station locations." if search.view == "stations" else "EEA city-mapped stations.",
        "station_weighting": "Equal physical-station weight within a time bucket; equal valid-day weight over a period.",
        "minimum_hourly_coverage": options.minimum_hourly_coverage,
        "minimum_period_coverage": options.minimum_period_coverage,
        "minimum_valid_days": period["minimum_valid_days"],
        "baseline": period.get("baseline"),
    }
    policy_id = hashlib.sha256(
        json.dumps({"reference": reference, "methodology": methodology}, sort_keys=True).encode()
    ).hexdigest()[:16]
    warnings = [
        "Results describe eligible monitored locations, not every street or all of Bulgaria.",
        "A station address identifies a monitor; it is not a measurement for every nearby address.",
        "Different sources, pollutants, timestamps and definitions can legitimately produce different rankings.",
        "Missing measurements are not zero. Station placement and remaining gaps affect comparisons.",
        "WHO daily guidance uses an annual 99th-percentile convention; these results do not certify compliance or safety.",
        "The national map's unlabelled zero placeholders were not imported as measurements.",
    ]
    if search.objective == "improvement":
        warnings.append("Largest relative improvement does not mean lowest absolute pollution or healthy air.")
        if rows[0]["improvement_percent"] <= 0:
            warnings.append("No compared location improved versus its baseline; the first result has the smallest relative increase.")
    if search.location:
        warnings.append("Only the explicitly selected location is being searched; this is not a country-wide winner.")
    if hourly:
        warnings.append("These are reported hourly measurements, not instantaneous readings; daily WHO thresholds are not applied.")
    if rows[0]["mean"] == 0:
        warnings.append(
            "The first concentration is reported as zero by the measurement source with a valid timestamp. "
            "This is not an unlabelled national-map placeholder and does not prove zero health risk."
        )
    if period.get("total_time_results", 0) and period["total_time_results"] > TIME_RESULT_LIMIT:
        warnings.append(
            "Time search returns the best 50 measured combinations; additional tied results can "
            "fall outside this limit. Narrow the dates or select a location to inspect them."
        )
    if snapshot["source_stale"]:
        warnings.append("The latest stored measurement is stale; historical results use stored data.")
    if snapshot["collection"] and snapshot["collection"]["status"] == "failed":
        warnings.append("The latest collection attempt failed. Previously imported history is still available.")
    if search.view == "cities" and snapshot["unmapped_stations"]:
        warnings.append("Stations without an official EEA city mapping are excluded from city averages.")
    limited = search.target != "locations"
    return {
        "start": search.start.isoformat() if search.start else None,
        "end": search.end.isoformat() if search.end else None,
        "pollutant": search.pollutant, "unit": "ug/m3", "view": search.view,
        "objective": search.objective, "target": search.target, "title": title, "period": period,
        "winners": [row for row in rows if row["rank"] == 1],
        "rankings": rows[:TIME_RESULT_LIMIT] if limited else rows,
        "total_ranked_results": period.get("total_time_results") or len(rows),
        "results_limited": limited and (period.get("total_time_results") or len(rows)) > TIME_RESULT_LIMIT,
        "eligible_location_count": (
            period.get("eligible_time_locations") or len({row["location"]["id"] for row in rows})
        ),
        "excluded_cities": excluded, "excluded_locations": excluded,
        "reference": reference, "methodology": methodology, "policy_id": policy_id,
        "last_successful_collection": snapshot["last_success"],
        "latest_observation": snapshot["latest_observation"],
        "source": {
            "name": "Bulgarian official measurements via the European Environment Agency",
            "url": EEA_DATA_URL, "station_metadata_url": NATIONAL_URL,
            "copyright": "European Commission", "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "changes": "Bulgarian measurements normalized locally; national station locations joined by verified identity.",
        },
        "warnings": warnings,
    }


def compare(settings: Settings, search: Search) -> dict:
    from .provider_search import compare_sources

    return compare_sources(settings, search)


def status_snapshot(settings: Settings, source: SourceChoice | None = None) -> dict:
    from .provider_search import source_status

    return source_status(settings, source)
