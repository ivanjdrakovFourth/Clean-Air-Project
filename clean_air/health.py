from __future__ import annotations

from datetime import timedelta

import httpx

from .database import connect, get_metadata
from .provider_search import _snapshot
from .provider_store import PROVIDERS, for_provider
from .settings import POLLUTANTS, Pollutant, Settings, months_before, timestamp_now, utc_today


def source_health(
    settings: Settings, pollutant: Pollutant = "PM10", *,
    worker_states: dict[str, bool] | None = None,
) -> dict:
    sources = []
    now = timestamp_now()
    beginning = months_before(utc_today(), settings.options.collection.history_months)
    expected_days = (utc_today() - beginning).days
    for provider in PROVIDERS:
        state = _snapshot(settings, provider)
        configured = for_provider(settings, provider)
        counts = {"observations": 0, "station_days": 0, "locations": 0}
        cities = []
        request_count = 0
        cooldown = None
        quality = {"records": 0, "excluded": 0, "future": 0}
        if configured.database.exists() and not state.get("error"):
            with connect(configured.database) as connection:
                counts = {
                    "observations": connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
                    "station_days": connection.execute("SELECT COUNT(*) FROM station_days").fetchone()[0],
                    "locations": connection.execute("SELECT COUNT(*) FROM stations").fetchone()[0],
                }
                quality = dict(connection.execute(
                    "SELECT COUNT(*) AS records,"
                    "COALESCE(SUM(CASE WHEN validity<=0 OR value IS NULL OR value<0 THEN 1 ELSE 0 END),0) AS excluded,"
                    "COALESCE(SUM(CASE WHEN ended>? THEN 1 ELSE 0 END),0) AS future "
                    "FROM observations WHERE pollutant=? AND started>=?",
                    (now, POLLUTANTS[pollutant], now - 86400),
                ).fetchone())
                latest_by_city = {
                    row["city"]: row["last_seen"] for row in connection.execute(
                        "SELECT s.city,MAX(o.ended) AS last_seen FROM observations o "
                        "JOIN sampling_points p ON p.id=o.point_id JOIN stations s ON s.id=p.station_id "
                        "WHERE o.pollutant=? AND o.validity>0 AND o.value>=0 AND o.ended<=? "
                        "AND s.city IS NOT NULL GROUP BY s.city",
                        (POLLUTANTS[pollutant], now),
                    )
                }
                best_days = {
                    row["city"]: row["days"] for row in connection.execute(
                        """
                        WITH station_coverage AS (
                            SELECT s.city,d.station_id,COUNT(*) AS days
                            FROM station_days d JOIN stations s ON s.id=d.station_id
                            WHERE d.pollutant=? AND d.day>=? AND d.day<?
                              AND (d.coverage IS NULL OR d.coverage>=?) AND s.city IS NOT NULL
                            GROUP BY s.city,d.station_id
                        )
                        SELECT city,MAX(days) AS days FROM station_coverage GROUP BY city
                        """,
                        (POLLUTANTS[pollutant], beginning.isoformat(), utc_today().isoformat(),
                         settings.options.comparison.minimum_hourly_coverage),
                    )
                }
                for city in state.get("cities", []):
                    latest = latest_by_city.get(city)
                    age = max(0, now - latest) / 3600 if latest else None
                    status = (
                        "No measurements for this pollutant" if latest is None
                        else "Recent readings" if age <= settings.options.comparison.current_max_age_hours
                        else "Historical / no recent reading"
                    )
                    cities.append({
                        "city": city, "latest": latest, "age_hours": age,
                        "best_station_days": best_days.get(city, 0), "expected_days": expected_days,
                        "coverage_percent": 100 * best_days.get(city, 0) / expected_days if expected_days else 0,
                        "status": status,
                    })
                stamps = get_metadata(connection, "openaq_request_times", [])
                active = [stamp for stamp in stamps if stamp > now - 3600]
                request_count = len(active)
                if len(active) >= 1900:
                    cooldown = min(active) + 3601
        run = state.get("collection")
        latest = state.get("latest_observation")
        age = max(0, now - latest) / 3600 if latest else None
        if state.get("error"):
            label, tone = "Storage error", "bad"
        elif provider == "bg_direct" and not state["ready"]:
            label, tone = "Direct feed not available", "neutral"
        elif provider == "openaq" and not state.get("credential_configured") and not state["ready"]:
            label, tone = "Needs local API key", "waiting"
        elif (
            provider == "openaq" and settings.options.collection.enabled
            and settings.options.openaq.enabled and not state.get("credential_configured")
        ):
            label, tone = "Saved history available; key needed for updates", "waiting"
        elif worker_states is not None and state.get("collection_enabled") and not worker_states.get(provider):
            label, tone = "Collector worker stopped - restart the application", "bad"
        elif latest and latest > now + 300:
            label, tone = "Unexpected future timestamp", "bad"
        elif quality["records"] and quality["excluded"] / quality["records"] > 0.25:
            label, tone = "Many source-excluded readings in the last day", "waiting"
        elif cooldown:
            label, tone = "Respecting hourly API allowance", "waiting"
        elif run and run["status"] in ("failed", "partial"):
            label, tone = "Collection needs attention", "bad"
        elif provider == "openaq" and not state.get("backfill_complete"):
            label, tone = "Collecting historical coverage", "waiting"
        elif not state["ready"]:
            label, tone = "Waiting for initial data", "waiting"
        elif age is not None and age > settings.options.collection.stale_after_hours:
            label, tone = "Readings are stale", "bad"
        elif not state.get("collection_enabled"):
            label, tone = "Offline - saved data available", "neutral"
        else:
            label, tone = "Healthy collection and recent data", "good"
        sources.append({
            "id": provider, "name": state["source_name"], "label": label, "tone": tone,
            "reason": state.get("reason"), "counts": counts, "last_success": state.get("last_success"),
            "latest": latest, "age_hours": age, "collection": run,
            "backfill_complete": state.get("backfill_complete", False),
            "available_start": state.get("available_start"), "available_end": state.get("available_end"),
            "available_pollutants": state.get("available_pollutants", []),
            "requests_last_hour": request_count, "cooldown_until": cooldown, "cities": cities,
            "quality_last_24h": quality,
            "bundled_seed": state.get("bundled_seed"),
            "worker_alive": worker_states.get(provider) if worker_states is not None else None,
        })
    return {
        "sources": sources, "pollutant": pollutant, "checked_at": now,
        "coverage_start": beginning.isoformat(),
        "coverage_end": (utc_today() - timedelta(days=1)).isoformat(),
        "definitions": [
            "A successful API response alone is not enough: freshness, validity and coverage matter.",
            "No measurement for a never-monitored city/pollutant is unavailable coverage, not an invented zero.",
            "Initial backfill and API cooldown are expected states, not evidence that cached data belongs to another source.",
            "City coverage shows the best station's qualifying complete days; it is not the city's pollution score.",
        ],
    }


def prometheus_summary(settings: Settings, window: str = "1h") -> dict:
    spans = {"15m": 900, "1h": 3600, "24h": 86400, "7d": 604800}
    if window not in spans:
        raise ValueError("Choose a supported monitoring time window.")
    if not settings.options.monitoring.enabled:
        return {"available": False, "reason": "Prometheus is disabled; source-cache health is still shown.", "charts": {}, "alerts": []}
    base = f"http://127.0.0.1:{settings.options.monitoring.port}"
    now = timestamp_now()
    expressions = {
        "search_requests_per_second": 'sum(rate(clean_air_http_requests_total{route="/api/cleanest"}[5m]))',
        "search_p95_ms": '1000 * histogram_quantile(0.95, sum by (le) (rate(clean_air_http_request_duration_seconds_bucket{route="/api/cleanest"}[5m])))',
    }
    charts = {}
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            for name, expression in expressions.items():
                response = client.get(
                    base + "/api/v1/query_range",
                    params={"query": expression, "start": now - spans[window], "end": now,
                            "step": max(15, spans[window] // 120)},
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("status") != "success":
                    raise ValueError("Prometheus rejected the monitoring query.")
                charts[name] = payload["data"]["result"]
            response = client.get(base + "/api/v1/alerts")
            response.raise_for_status()
            alerts = response.json()["data"]["alerts"]
        return {"available": True, "charts": charts, "alerts": alerts, "window": window}
    except (httpx.HTTPError, ValueError, KeyError) as error:
        import logging

        logging.getLogger(__name__).warning("Prometheus history unavailable: %s", type(error).__name__)
        return {
            "available": False,
            "reason": "Prometheus is not responding or has no valid query response. Check its process/logs; missing graphs are not zero-valued healthy results.",
            "charts": {}, "alerts": [],
        }
