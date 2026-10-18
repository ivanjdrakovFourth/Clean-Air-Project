from __future__ import annotations

import json
import logging
import os
import sqlite3

from .credentials import key_path
from .database import connect, get_metadata
from .provider_store import BACKFILL_KEYS, PROVIDER_NAMES, PROVIDERS, for_provider
from .settings import POLLUTANTS, Settings, SourceChoice, timestamp_now

LOGGER = logging.getLogger(__name__)


def _snapshot(settings: Settings, provider: str) -> dict:
    from .comparison import _status_single

    configured = for_provider(settings, provider)
    bundled = None
    if configured.database.exists():
        try:
            snapshot = _status_single(configured)
            with connect(configured.database) as connection:
                bundled = get_metadata(connection, "bundled_seed_info")
        except (sqlite3.Error, json.JSONDecodeError):
            LOGGER.exception("Source-specific storage is unavailable: %s", provider)
            snapshot = {
                "ready": False, "available_start": None, "available_end": None,
                "cities": [], "official_stations": [], "collection": None,
                "error": "Source storage is unavailable; inspect application logs.",
            }
    else:
        snapshot = {
            "ready": False, "available_start": None, "available_end": None,
            "cities": [], "official_stations": [], "collection": None,
        }
    configured_key = bool(os.environ.get("OPENAQ_API_KEY") or key_path(settings.root).exists())
    reason = snapshot.get("error")
    if reason:
        pass
    elif provider == "bg_direct" and not settings.options.bg_direct.enabled:
        reason = "National CSV collection is disabled. Any saved direct-source history remains separate and searchable."
    elif provider == "openaq" and not configured_key:
        reason = "OpenAQ live updates need a locally configured API key. Any existing cache remains source-specific and searchable."
    elif not snapshot["ready"]:
        reason = "This source's first valid collection has not completed yet."
    snapshot.update(
        data_source=provider, source_name=PROVIDER_NAMES[provider],
        reason=reason, credential_configured=configured_key if provider == "openaq" else None,
        collection_enabled=(
            settings.options.collection.enabled
            and (provider != "openaq" or settings.options.openaq.enabled)
            and (provider != "openaq" or configured_key)
            and (provider != "bg_direct" or settings.options.bg_direct.enabled)
        ),
        timezone="UTC",
        bundled_seed=bundled,
    )
    return snapshot


def source_status(settings: Settings, source: SourceChoice | None = None) -> dict:
    source = source or settings.options.sources.default
    all_sources = {provider: _snapshot(settings, provider) for provider in PROVIDERS}
    selected = all_sources["eea"] if source == "combined" else all_sources[source]
    result = dict(selected)
    if source == "combined":
        locations = {}
        for state in all_sources.values():
            for location in state.get("official_stations", []):
                locations.setdefault(location["id"], location)
        result.update(
            source_name="Selected-source comparison",
            ready=any(value["ready"] for value in all_sources.values()),
            reason="Each selected source is queried separately. Missing history is shown explicitly, never substituted.",
            collection=None,
            data_source="combined",
            official_stations=list(locations.values()),
            cities=sorted({city for state in all_sources.values() for city in state.get("cities", [])}),
        )
    result.update(
        selected_source=source, provider_statuses=all_sources,
        application_ready=any(value["ready"] for value in all_sources.values()),
    )
    return result


def _single(
    settings: Settings, search, provider: str, hour: int | None = None, *,
    snapshot: dict | None = None,
) -> dict:
    from .comparison import ComparisonError, _compare_single

    snapshot = _snapshot(settings, provider) if snapshot is None else snapshot
    if provider == "bg_direct" and not snapshot["ready"] and not settings.options.bg_direct.enabled:
        raise ComparisonError("SOURCE_UNAVAILABLE", snapshot["reason"], 503, snapshot)
    if not snapshot["ready"]:
        raise ComparisonError(
            "HISTORY_NOT_READY" if provider == "eea" else "SOURCE_HISTORY_NOT_READY",
            snapshot["reason"] or "The selected source has no searchable measurements yet.",
            503, snapshot,
        )
    if provider in BACKFILL_KEYS and search.start is not None and not snapshot.get("backfill_complete"):
        with connect(for_provider(settings, provider).database) as connection:
            windows = get_metadata(connection, "restored_source_windows", [])
        restored = any(
            item["start"] <= search.start.isoformat()
            and item["end"] >= search.end.isoformat() for item in windows
        )
        if not restored and not search.allow_partial_history:
            raise ComparisonError(
                "SOURCE_BACKFILL_IN_PROGRESS",
                f"{PROVIDER_NAMES[provider]}'s own historical backfill is still running. Historical comparisons wait "
                "for the configured source window to finish unless you explicitly allow partial "
                "cached history. EEA history is never substituted.",
                503, snapshot,
            )
    result = _compare_single(for_provider(settings, provider), search, hour, snapshot=snapshot)
    result["data_source"] = provider
    result["is_combined"] = False
    result["source"]["id"] = provider
    result["source_name"] = PROVIDER_NAMES[provider]
    if provider == "openaq":
        result["source"].update(
            name="OpenAQ - per-location upstream providers",
            url="https://openaq.org/",
            station_metadata_url="https://docs.openaq.org/resources/locations",
            copyright="Original data owners/providers via OpenAQ",
            license="Per-location licenses",
            license_url="https://docs.openaq.org/resources/licenses",
            changes="Collected separately from OpenAQ, normalized to UTC hours and ug/m3, then aggregated locally.",
        )
        result["warnings"].extend([
            "OpenAQ includes source-labelled reference monitors and potentially low-cost community sensors.",
            "OpenAQ locations whose upstream provider is EEA are not independent confirmation of EEA observations.",
        ])
    elif provider == "bg_direct":
        result["source"].update(
            name="Bulgarian Executive Environment Agency - direct hourly CSV",
            url="https://www.eea.government.bg/kav/",
            station_metadata_url="https://www.eea.government.bg/kav/",
            license="Attributed factual national measurements; website assets are not redistributed",
            license_url="https://www.eea.government.bg/",
            changes="Downloaded directly from national CSV, mass units normalized, fixed UTC+01 hour-ending labels converted to UTC; duplicate-labelled source days excluded.",
        )
        result["warnings"].append(
            "National CSV values are preliminary and source-rounded. They can share the same official monitoring network as EEA; this is a distinct delivery route, not independent instruments."
        )
    if provider in BACKFILL_KEYS and not snapshot.get("backfill_complete"):
        result["warnings"].append("This source's history backfill is incomplete; cached eligible results can change as more station records arrive.")
        result["partial_history"] = True
    if snapshot.get("collection") and snapshot["collection"]["status"] == "running":
        result["warnings"].append("This source is refreshing; some locations can still have their last committed data.")
    return result


def _winner_keys(result: dict) -> set[tuple]:
    keys = set()
    for row in result["winners"]:
        location = row["location"]
        identity = (
            location.get("canonical_station") if result["view"] == "stations"
            else location["id"]
        )
        if identity:
            keys.add((identity, row["time_start"]))
    return keys


def _matched_measurements(settings: Settings, search, results: dict[str, dict]) -> list[dict]:
    if "eea" not in results or "openaq" not in results:
        return []
    first, second = results["eea"], results["openaq"]
    if first["view"] != "stations" or first["objective"] != "average":
        return []
    indexed = {}
    for provider, result in (("eea", first), ("openaq", second)):
        indexed[provider] = {
            (row["location"].get("canonical_station"), row["time_start"]): row
            for row in result["rankings"] if row["location"].get("canonical_station")
        }
    matched = indexed["eea"].keys() & indexed["openaq"].keys()
    pairs = []
    for key in sorted(matched, key=str):
        left, right = indexed["eea"][key], indexed["openaq"][key]
        days = None
        a, b = left["mean"], right["mean"]
        comparable = True
        if search.start is not None and first["target"] == "locations":
            series = {}
            for provider, row in (("eea", left), ("openaq", right)):
                configured = for_provider(settings, provider)
                with connect(configured.database) as connection:
                    series[provider] = {
                        record["day"]: record["mean"] for record in connection.execute(
                            "SELECT day,mean FROM station_days WHERE station_id=? AND pollutant=? "
                            "AND day BETWEEN ? AND ? AND (coverage IS NULL OR coverage>=?)",
                            (row["location"]["id"], POLLUTANTS[first["pollutant"]],
                             search.start.isoformat(), search.end.isoformat(),
                             settings.options.comparison.minimum_hourly_coverage),
                        )
                    }
            common = series["eea"].keys() & series["openaq"].keys()
            days = len(common)
            minimum = first["methodology"]["minimum_valid_days"]
            comparable = days >= minimum
            if comparable:
                a = sum(series["eea"][day] for day in common) / days
                b = sum(series["openaq"][day] for day in common) / days
        difference = b - a if comparable else None
        tolerance = max(0.5, 0.05 * max(abs(a), abs(b)))
        pairs.append({
            "station_id": key[0], "name": left["name"], "time_start": key[1],
            "eea_mean": a, "openaq_mean": b, "shared_days": days,
            "comparable": comparable, "difference": difference,
            "within_display_tolerance": abs(difference) <= tolerance if comparable else None,
            "tolerance": tolerance,
            "shared_upstream": right["location"].get("upstream_provider") == "EEA",
        })
    return pairs


def compare_sources(settings: Settings, search) -> dict:
    from .comparison import ComparisonError, resolved_search

    search = resolved_search(settings, search)

    source = search.source or settings.options.sources.default
    if source != "combined":
        return _single(settings, search, source)
    selected = search.sources or list(PROVIDERS)
    results = {}
    failures = {}
    snapshots = {}
    for provider in selected:
        try:
            snapshots[provider] = _snapshot(settings, provider)
            results[provider] = _single(settings, search, provider, snapshot=snapshots[provider])
        except ComparisonError as error:
            failures[provider] = {
                "code": error.code, "message": error.message, "details": error.details,
                "status": error.status,
            }
    if not results:
        raise ComparisonError(
            "NO_SELECTED_SOURCE_CAN_ANSWER",
            "None of the selected source stores can answer this request. No other source was substituted.",
            503 if all(value["status"] == 503 for value in failures.values()) else 422,
            {"sources": failures},
        )
    aligned = search.start is not None
    if search.start is None and len(results) >= 2:
        initial = min(result["period"]["time_start"] for result in results.values())
        earliest = timestamp_now() - settings.options.comparison.current_max_age_hours * 3600 - 3600
        for hour in range(initial, int(earliest), -3600):
            attempt = {}
            for provider in results:
                try:
                    attempt[provider] = (
                        results[provider] if results[provider]["period"]["time_start"] == hour
                        else _single(settings, search, provider, hour, snapshot=snapshots[provider])
                    )
                except ComparisonError as error:
                    if error.code != "NO_FRESH_COMPARABLE_HOUR":
                        raise
                    break
            if len(attempt) == len(results):
                results = attempt
                aligned = True
                break
    shared_winners = None
    if len(results) >= 2 and aligned:
        winner_sets = [_winner_keys(result) for result in results.values()]
        shared_winners = bool(set.intersection(*winner_sets))
    pairs = _matched_measurements(settings, search, results) if aligned else []
    panels = [
        {
            "id": provider, "name": PROVIDER_NAMES[provider], "result": results.get(provider),
            "error": failures.get(provider),
        }
        for provider in selected
    ]
    return {
        "is_combined": True, "data_source": "combined", "source_name": "Selected-source comparison",
        "pollutant": next(iter(results.values()))["pollutant"],
        "start": search.start.isoformat() if search.start else None,
        "end": search.end.isoformat() if search.end else None,
        "panels": panels,
        "agreement": {
            "available_sources": list(results), "unavailable_sources": list(failures),
            "same_time_window": aligned,
            "shared_top_result": shared_winners,
            "matched_measurements": pairs,
            "numeric_rule": "Display comparison only: max(0.5 ug/m3, 5% of the larger matched mean). Not a health or measurement-uncertainty threshold.",
            "scope": "Matched physical stations; historical means use exactly shared qualifying days. Time searches compare listed results only.",
            "independence_notice": "National CSV, EEA and OpenAQ's EEA-supplied locations can share the same official instruments; agreement is not independent validation.",
        },
    }
