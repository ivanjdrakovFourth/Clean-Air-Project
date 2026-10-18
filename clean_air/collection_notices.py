from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime

from .alerts import alert_connection, initialize_alerts
from .database import connect, get_metadata
from .provider_store import for_provider
from .settings import Provider, Settings, timestamp_now

LOGGER = logging.getLogger(__name__)
NAMES = {"eea": "EEA", "openaq": "OpenAQ", "bg_direct": "Bulgarian direct"}


class CollectionNotices:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = uuid.uuid4().hex
        self.started = timestamp_now()
        self.sources = []
        self.start_event = None
        initialize_alerts(settings)
        with alert_connection(settings) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS notification_preferences (key TEXT PRIMARY KEY,value TEXT NOT NULL)"
            )

    def desktop_enabled(self) -> bool:
        with alert_connection(self.settings) as connection:
            row = connection.execute(
                "SELECT value FROM notification_preferences WHERE key='collection_desktop'"
            ).fetchone()
        return bool(row and json.loads(row["value"]))

    def set_desktop(self, enabled: bool) -> None:
        if enabled and not self.settings.options.alerts.enabled:
            raise ValueError("Desktop delivery is disabled in this installation's alert settings.")
        with alert_connection(self.settings) as connection, connection:
            connection.execute(
                "INSERT INTO notification_preferences VALUES ('collection_desktop',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(enabled),),
            )

    def _record(
        self, source: str, phase: str, title: str, message: str, key: str, *,
        desktop: bool = False,
    ) -> int:
        requested = desktop and self.settings.options.alerts.enabled and self.desktop_enabled()
        with alert_connection(self.settings) as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO alert_events "
                "(source,created,title,message,details,event_key,desktop_status) VALUES (?,?,?,?,?,?,?)",
                (source, timestamp_now(), title, message,
                 json.dumps({"kind": "collection_sync", "session": self.session, "phase": phase}),
                 f"{self.session}:{source}:{key}", "pending" if requested else "not_requested"),
            )
            return connection.execute(
                "SELECT id FROM alert_events WHERE event_key=?", (f"{self.session}:{source}:{key}",)
            ).fetchone()[0]

    def begin(self, sources: list[dict]) -> None:
        self.sources = sources
        online = self.settings.options.collection.enabled
        self.start_event = self._record(
            "application", "starting",
            "Automatic source checks started" if online else "Offline saved-data mode",
            "Enabled sources are checked automatically. Missing dates are caught up in the background; "
            "clicking OK only acknowledges this notice and does not cancel collection."
            if online else "Explicit offline mode is enabled. Saved history remains searchable; source requests are disabled.",
            "startup",
        )
        for source in sources:
            if not source["enabled"]:
                self._record(
                    source["id"], "unavailable", f"{source['name']}: automatic update unavailable",
                    source["reason"], "unavailable",
                )

    def report(self, provider: Provider, outcome: dict | None) -> None:
        try:
            configured = for_provider(self.settings, provider)
            with connect(configured.database) as connection:
                run = connection.execute("SELECT id FROM collection_runs ORDER BY id DESC LIMIT 1").fetchone()
                last_complete = connection.execute(
                    "SELECT MAX(id) FROM collection_runs WHERE status='completed'"
                ).fetchone()[0]
                ready = bool(get_metadata(connection, "initial_import_complete", False))
                if provider in ("openaq", "bg_direct"):
                    key = "openaq_backfill_complete" if provider == "openaq" else "bg_direct_backfill_complete"
                    ready = ready and bool(get_metadata(connection, key, False))
                latest = get_metadata(connection, "latest_observation")
            name = NAMES[provider]
            if outcome is None:
                self._record(
                    provider, "failed", f"{name}: automatic update needs attention",
                    "The source could not be synchronized. Check Internet access, source availability "
                    "and any required credential in Monitoring. Saved measurements are retained; automatic retries continue.",
                    f"failed:{last_complete or 0}", desktop=True,
                )
            elif outcome.get("complete", True):
                if not ready or not latest:
                    phase, title = "unavailable", f"{name}: no valid source data available"
                    message = "The check finished without a usable measurement history. No data was invented or borrowed."
                elif latest > timestamp_now() + 300:
                    phase, title = "unavailable", f"{name}: unexpected future reading timestamp"
                    message = "The check returned an unexpected future timestamp. Inspect Monitoring; no up-to-date claim is made."
                else:
                    delayed = timestamp_now() - latest > self.settings.options.collection.stale_after_hours * 3600
                    phase = "delayed" if delayed else "completed"
                    title = f"{name}: check complete; provider readings delayed" if delayed else f"{name} is up to date"
                    reported = datetime.fromtimestamp(latest, UTC).strftime("%Y-%m-%d %H:%M UTC")
                    message = (
                        f"Available {name} records have been synchronized locally. Latest valid reading ends {reported}. "
                        "Unreported measurements are not invented. Background collection will keep checking for new data."
                    )
                self._record(
                    provider, phase, title, message,
                    f"completed:{run['id'] if run else 0}", desktop=True,
                )
        except (sqlite3.Error, OSError, ValueError):
            LOGGER.exception(
                "Could not persist the %s collection notification. Inspect Monitoring for the actual source outcome.",
                provider,
            )

    def status(self, after: int = 0) -> dict:
        with alert_connection(self.settings) as connection:
            events = [
                {**dict(row), "details": json.loads(row["details"])}
                for row in connection.execute(
                    "SELECT * FROM alert_events WHERE id>? AND unread=1 "
                    "AND json_extract(details,'$.kind')='collection_sync' "
                    "AND json_extract(details,'$.session')=? ORDER BY id LIMIT 100",
                    (after, self.session),
                )
            ]
        return {
            "session": self.session, "started": self.started,
            "collection_enabled": self.settings.options.collection.enabled,
            "startup_event": self.start_event, "sources": self.sources, "events": events,
            "desktop_enabled": self.desktop_enabled(),
            "desktop_available": self.settings.options.alerts.enabled,
        }
