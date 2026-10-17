from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta
from typing import Literal

from pydantic import Field, model_validator

from .database import connect, get_metadata
from .settings import EEA_DATA_URL, POLLUTANTS, Settings, StrictModel, timestamp_now, utc_today


class Search(StrictModel):
    start: date
    end: date
    pollutant: Literal["PM2.5", "PM10"] = "PM2.5"
    profile: Literal["who_2021", "custom"] = "who_2021"
    threshold: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def coherent(self) -> Search:
        if self.start > self.end:
            raise ValueError("The start date must not be after the end date.")
        if self.end >= utc_today():
            raise ValueError("Choose completed historical UTC days, not today or a future date.")
        if self.profile == "custom" and self.threshold is None:
            raise ValueError("A custom daily threshold is required for the custom profile.")
        if self.profile != "custom" and self.threshold is not None:
            raise ValueError("Custom thresholds must use the custom profile.")
        return self


class ComparisonError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422, details: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def status_snapshot(settings: Settings) -> dict[str, object]:
    with connect(settings.database) as connection:
        complete = bool(get_metadata(connection, "initial_import_complete", False))
        run = connection.execute("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1").fetchone()
        bounds = connection.execute("SELECT MIN(day), MAX(day) FROM station_days").fetchone()
        latest = get_metadata(connection, "latest_observation", None)
        last_success = get_metadata(connection, "last_success", None)
        unmapped = connection.execute(
            "SELECT COUNT(*) FROM stations WHERE city IS NULL"
        ).fetchone()[0]
        yesterday = (utc_today() - timedelta(days=1)).isoformat()
        end = min(bounds[1], yesterday) if bounds[1] else None
        result = {
            "ready": complete,
            "timezone": "UTC",
            "available_start": bounds[0],
            "available_end": end,
            "last_success": last_success,
            "latest_observation": latest,
            "source_stale": bool(
                latest and timestamp_now() - int(latest) > settings.options.collection.stale_after_hours * 3600
            ),
            "collection_enabled": settings.options.collection.enabled,
            "collection": dict(run) if run else None,
            "unmapped_stations": unmapped,
            "cities": [row[0] for row in connection.execute("SELECT name FROM cities ORDER BY name")],
        }
    return result


def compare(settings: Settings, search: Search) -> dict[str, object]:
    snapshot = status_snapshot(settings)
    if not snapshot["ready"]:
        raise ComparisonError(
            "HISTORY_NOT_READY",
            "The first real-data import is not complete. Check collection progress and retry.",
            503, snapshot,
        )
    if search.start.isoformat() < str(snapshot["available_start"]):
        raise ComparisonError(
            "PERIOD_NOT_STORED",
            "The requested period starts before the locally available history. "
            "Choose a stored period, or restore the relevant archive.",
            details={"available_start": snapshot["available_start"], "available_end": snapshot["available_end"]},
        )
    if search.end.isoformat() > str(snapshot["available_end"]):
        raise ComparisonError(
            "PERIOD_NOT_STORED",
            "The requested end date is not yet available locally. Source reporting can be delayed.",
            details={"available_start": snapshot["available_start"], "available_end": snapshot["available_end"]},
        )
    day_count = (search.end - search.start).days + 1
    minimum_days = math.ceil(day_count * settings.options.comparison.minimum_period_coverage - 1e-10)
    threshold = (
        search.threshold if search.profile == "custom"
        else settings.reference.daily_threshold(search.pollutant)
    )
    parameters = (
        POLLUTANTS[search.pollutant], search.start.isoformat(), search.end.isoformat(),
        settings.options.comparison.minimum_hourly_coverage, minimum_days,
    )
    with connect(settings.database) as connection:
        days = connection.execute(
            """
            WITH valid_days AS (
                SELECT d.* FROM station_days d
                WHERE pollutant=? AND day BETWEEN ? AND ?
                  AND (coverage IS NULL OR coverage>=?)
            ), qualifying_stations AS (
                SELECT station_id FROM valid_days
                GROUP BY station_id HAVING COUNT(*)>=?
            )
            SELECT s.city, d.day, AVG(d.mean) AS mean,
                   GROUP_CONCAT(d.station_id, '|') AS stations,
                   SUM(CASE WHEN d.kind LIKE 'provisional%' THEN 1 ELSE 0 END) AS provisional,
                   SUM(CASE WHEN d.coverage IS NULL THEN 1 ELSE 0 END) AS provider_coverage_unknown
            FROM valid_days d
            JOIN qualifying_stations q ON q.station_id=d.station_id
            JOIN stations s ON s.id=d.station_id
            WHERE s.city IS NOT NULL
            GROUP BY s.city, d.day ORDER BY s.city, d.day
            """,
            parameters,
        ).fetchall()
        observed = connection.execute(
            """
            SELECT s.city, d.station_id, COUNT(*) AS days
            FROM station_days d JOIN stations s ON s.id=d.station_id
            WHERE d.pollutant=? AND d.day BETWEEN ? AND ?
              AND (d.coverage IS NULL OR d.coverage>=?) AND s.city IS NOT NULL
            GROUP BY s.city, d.station_id
            """,
            parameters[:4],
        ).fetchall()
    city_days: dict[str, list] = {}
    for row in days:
        city_days.setdefault(row["city"], []).append(row)
    rankings = []
    excluded = []
    for city in snapshot["cities"]:
        values = city_days.get(city, [])
        if len(values) < minimum_days:
            best = max((row["days"] for row in observed if row["city"] == city), default=0)
            excluded.append(
                {
                    "city": city,
                    "reason": "No station meets the required period coverage."
                    if best else "No eligible measurements for this pollutant and period.",
                    "best_station_coverage_percent": 100 * best / day_count,
                }
            )
            continue
        stations = {station for row in values for station in row["stations"].split("|")}
        below = sum(row["mean"] <= threshold for row in values)
        rankings.append(
            {
                "city": city,
                "mean": sum(row["mean"] for row in values) / len(values),
                "valid_days": len(values),
                "requested_days": day_count,
                "coverage_percent": 100 * len(values) / day_count,
                "station_count": len(stations),
                "stations": sorted(stations),
                "days_at_or_below_reference": below,
                "days_above_reference": len(values) - below,
                "days_within_reference_percent": 100 * below / len(values),
                "provisional_station_days": sum(row["provisional"] for row in values),
                "provider_hourly_coverage_unknown": sum(row["provider_coverage_unknown"] for row in values),
            }
        )
    if not rankings:
        raise ComparisonError(
            "NO_ELIGIBLE_CITY",
            "No city has sufficient valid measurements for this pollutant and period. "
            "Try a different period or explicitly select the other pollutant.",
            details={"excluded_cities": excluded, "minimum_valid_days": minimum_days},
        )
    rankings.sort(key=lambda row: (row["mean"], row["city"]))
    rank = 0
    previous = None
    for index, row in enumerate(rankings):
        if previous is None or not math.isclose(row["mean"], previous, rel_tol=0, abs_tol=1e-9):
            rank = index + 1
            previous = row["mean"]
        row["rank"] = rank
    reference = {
        "id": search.profile,
        "version": settings.reference.version if search.profile == "who_2021" else "user-defined",
        "name": settings.reference.name if search.profile == "who_2021" else "Custom daily reference",
        "daily_threshold": threshold,
        "unit": "ug/m3",
        "averaging_period": "24-hour UTC city mean",
        "source": settings.reference.source if search.profile == "who_2021" else None,
    }
    methodology = {
        "version": "station-day-mean-v1",
        "timezone": "UTC",
        "ranking": "Lowest mean of eligible city daily means; each valid day has equal weight.",
        "station_selection": "EEA city-mapped monitoring stations; not filtered by traffic/background type.",
        "station_weighting": "Equal weight for each available qualifying physical station within each day.",
        "minimum_hourly_coverage": settings.options.comparison.minimum_hourly_coverage,
        "minimum_period_coverage": settings.options.comparison.minimum_period_coverage,
        "minimum_valid_days": minimum_days,
    }
    policy_hash = hashlib.sha256(
        json.dumps({"reference": reference, "methodology": methodology}, sort_keys=True).encode()
    ).hexdigest()[:16]
    warnings = [
        "Cleanest means lowest measured pollution among eligible cities, not all Bulgarian cities.",
        "Daily-reference statistics are descriptive city aggregates, not WHO or legal compliance certification.",
        "WHO's daily guideline uses an annual 99th-percentile convention; this view does not evaluate that.",
        "Missing days are not replaced with zero. Gaps and station placement can affect comparisons.",
    ]
    if snapshot["source_stale"]:
        warnings.append("The latest stored measurement is stale; results use stored historical data.")
    run = snapshot["collection"]
    if run and run["status"] == "failed":
        warnings.append("The latest collection attempt failed. Previously imported history is still available.")
    if snapshot["unmapped_stations"]:
        warnings.append("Stations without an official EEA city mapping are excluded.")
    return {
        "start": search.start.isoformat(),
        "end": search.end.isoformat(),
        "pollutant": search.pollutant,
        "unit": "ug/m3",
        "winners": [row for row in rankings if row["rank"] == 1],
        "rankings": rankings,
        "excluded_cities": excluded,
        "reference": reference,
        "methodology": methodology,
        "policy_id": policy_hash,
        "last_successful_collection": snapshot["last_success"],
        "latest_observation": snapshot["latest_observation"],
        "source": {
            "name": "European Environment Agency",
            "url": EEA_DATA_URL,
            "copyright": "European Commission",
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "changes": "Filtered to Bulgarian PM measurements; normalized, aggregated and ranked locally.",
        },
        "warnings": warnings,
    }
