from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from .database import connect, get_metadata
from .settings import Settings, timestamp_now


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "clean_air_http_requests_total", "HTTP requests", ["route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "clean_air_http_request_duration_seconds", "HTTP request duration", ["route"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.3, 0.5, 1, 3),
            registry=self.registry,
        )
        self.healthy = Gauge(
            "clean_air_collection_healthy", "Whether the last collection attempt is healthy",
            registry=self.registry,
        )
        self.failures = Counter(
            "clean_air_collection_failures_total", "Failed collection attempts",
            registry=self.registry,
        )
        self.source_age = Gauge(
            "clean_air_source_age_seconds", "Age of the latest observed measurement",
            registry=self.registry,
        )
        self.stale_after = Gauge(
            "clean_air_stale_after_seconds", "Configured source staleness warning threshold",
            registry=self.registry,
        )
        self.rows = Gauge(
            "clean_air_observations", "Locally stored source observations", registry=self.registry
        )
        self.days = Gauge(
            "clean_air_station_days", "Locally stored station daily aggregates", registry=self.registry
        )
        self.progress = Gauge(
            "clean_air_collection_progress_ratio", "Completed files divided by planned files",
            registry=self.registry,
        )
        self.worker_alive = Gauge(
            "clean_air_worker_alive", "Whether the collection thread is alive", registry=self.registry
        )
        self.worker_expected = Gauge(
            "clean_air_worker_expected", "Whether automatic collection is enabled", registry=self.registry
        )
        self.source_healthy = Gauge(
            "clean_air_source_collection_healthy", "Latest source collection is not failed/partial",
            ["source"], registry=self.registry,
        )
        self.source_enabled = Gauge(
            "clean_air_source_enabled", "Source collection is configured and enabled",
            ["source"], registry=self.registry,
        )
        self.source_age_by_provider = Gauge(
            "clean_air_source_data_age_seconds", "Age of source-specific latest measurement",
            ["source"], registry=self.registry,
        )
        self.source_ready = Gauge(
            "clean_air_source_cache_ready", "Source has its own searchable cache",
            ["source"], registry=self.registry,
        )
        self.source_backfill = Gauge(
            "clean_air_source_backfill_complete", "Configured source historical backfill finished",
            ["source"], registry=self.registry,
        )
        self.source_workers = Gauge(
            "clean_air_source_worker_alive", "This source's collection thread is alive",
            ["source"], registry=self.registry,
        )

    def refresh(self, settings: Settings) -> None:
        with connect(settings.database) as connection:
            row = connection.execute(
                "SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            latest = get_metadata(connection, "latest_observation", 0)
            self.healthy.set(0 if row and row["status"] == "failed" else 1)
            self.progress.set(
                row["completed_files"] / row["total_files"] if row and row["total_files"] else 0
            )
            self.rows.set(connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
            self.days.set(connection.execute("SELECT COUNT(*) FROM station_days").fetchone()[0])
        self.source_age.set(max(0, timestamp_now() - int(latest)) if latest else 0)
        self.stale_after.set(settings.options.collection.stale_after_hours * 3600)
        from .provider_search import source_status

        for provider, state in source_status(settings)["provider_statuses"].items():
            run = state.get("collection")
            self.source_healthy.labels(provider).set(
                bool(run and run["status"] not in ("failed", "partial", "interrupted"))
            )
            self.source_enabled.labels(provider).set(state.get("collection_enabled", False))
            self.source_ready.labels(provider).set(state["ready"])
            self.source_backfill.labels(provider).set(state.get("backfill_complete", False))
            latest = state.get("latest_observation")
            self.source_age_by_provider.labels(provider).set(max(0, timestamp_now() - latest) if latest else 0)
