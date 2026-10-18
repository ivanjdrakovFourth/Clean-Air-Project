from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Literal

from pydantic import Field, model_validator

from .notifications import desktop_process
from .settings import Pollutant, Provider, Settings, StrictModel, timestamp_now, utc_today

LOGGER = logging.getLogger(__name__)
CITY_RULES = {"cleaner", "dirtier", "below", "above", "city_missing"}
HEALTH_RULES = {"source_stale", "collection_failed", "source_unavailable", "city_missing"}


class AlertRule(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    sources: list[Provider] = Field(default_factory=lambda: ["eea"], min_length=1, max_length=3)
    pollutant: Pollutant = "PM10"
    condition: Literal[
        "winner_changed", "cleaner", "dirtier", "below", "above",
        "search_updated", "source_stale", "collection_failed", "source_unavailable", "city_missing",
    ] = "winner_changed"
    city: str | None = Field(default=None, max_length=160)
    objective: Literal["average", "reference_days", "improvement"] = "average"
    window_days: int = Field(default=0, ge=0, le=365)
    monitor_class: Literal["all", "reference"] = "all"
    profile: Literal["who_2021", "custom"] = "who_2021"
    reference: float | None = Field(default=None, ge=0)
    threshold: float | None = Field(default=None, ge=0)
    minimum_change: float = Field(default=0, ge=0)
    stale_hours: int = Field(default=48, ge=1, le=720)
    desktop: bool = False
    enabled: bool = True
    notify_initial: bool = False

    @model_validator(mode="after")
    def coherent(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Give the alert a meaningful name.")
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("Select each source only once.")
        if self.condition in CITY_RULES and not self.city:
            raise ValueError("Choose a city for a city-specific alert.")
        if self.condition not in CITY_RULES and self.city:
            raise ValueError("This rule monitors source/search changes, not a selected city.")
        if self.condition in ("below", "above") and self.threshold is None:
            raise ValueError("A concentration threshold is required for a crossing alert.")
        if self.window_days == 0 and self.objective != "average":
            raise ValueError("Reference-day and improvement definitions need a rolling historical window.")
        if self.profile == "custom" and self.reference is None:
            raise ValueError("The custom daily-reference value is missing.")
        if self.pollutant == "O3" and self.objective == "reference_days" and self.profile == "who_2021":
            raise ValueError("Ozone has no WHO 24-hour reference; choose a custom daily reference.")
        return self


@contextmanager
def alert_connection(settings: Settings) -> Iterator[sqlite3.Connection]:
    path = settings.var / "data" / "alerts.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
    finally:
        connection.close()


def initialize_alerts(settings: Settings) -> None:
    with alert_connection(settings) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INTEGER PRIMARY KEY,
            definition TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT '{}',
            created INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS alert_events (
            id INTEGER PRIMARY KEY,
            rule_id INTEGER REFERENCES alert_rules(id) ON DELETE SET NULL,
            source TEXT NOT NULL,
            created INTEGER NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            details TEXT NOT NULL,
            event_key TEXT NOT NULL UNIQUE,
            desktop_status TEXT NOT NULL,
            unread INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS alert_events_recent ON alert_events(created DESC);
        """)
        connection.execute(
            "UPDATE alert_events SET desktop_status='uncertain' WHERE desktop_status='sending'"
        )
        connection.commit()


def rules_and_events(settings: Settings, after: int = 0) -> dict:
    with alert_connection(settings) as connection:
        rules = [
            {
                "id": row["id"], "definition": json.loads(row["definition"]),
                "enabled": bool(row["enabled"]), "state": json.loads(row["state"]),
                "created": row["created"],
            }
            for row in connection.execute("SELECT * FROM alert_rules ORDER BY id DESC")
        ]
        events = [
            {**dict(row), "details": json.loads(row["details"])}
            for row in connection.execute(
                "SELECT * FROM alert_events WHERE id>? ORDER BY id DESC LIMIT 100", (after,)
            )
        ]
    return {"rules": rules, "events": events}


def save_rule(settings: Settings, definition: AlertRule, identity: int | None = None) -> int:
    with alert_connection(settings) as connection, connection:
        if identity is None:
            if connection.execute("SELECT COUNT(*) FROM alert_rules").fetchone()[0] >= 50:
                raise ValueError("The local installation supports up to 50 alert rules.")
            result = connection.execute(
                "INSERT INTO alert_rules(definition,enabled,created) VALUES (?,?,?)",
                (definition.model_dump_json(), int(definition.enabled), timestamp_now()),
            )
            return result.lastrowid
        changed = connection.execute(
            "UPDATE alert_rules SET definition=?,enabled=?,state='{}' WHERE id=?",
            (definition.model_dump_json(), int(definition.enabled), identity),
        ).rowcount
        if not changed:
            raise ValueError("This alert rule no longer exists.")
        return identity


def change_rule(settings: Settings, identity: int, *, enabled: bool | None = None, delete=False) -> None:
    with alert_connection(settings) as connection, connection:
        if delete:
            changed = connection.execute("DELETE FROM alert_rules WHERE id=?", (identity,)).rowcount
        else:
            changed = connection.execute(
                "UPDATE alert_rules SET enabled=?,state='{}' WHERE id=?", (int(enabled), identity)
            ).rowcount
        if not changed:
            raise ValueError("This alert rule no longer exists.")


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class AlertEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.delivery_thread: threading.Thread | None = None
        self.notification_process = None
        initialize_alerts(settings)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="air-alerts", daemon=True)
        self.delivery_thread = threading.Thread(target=self._deliver, name="desktop-alerts", daemon=True)
        self.thread.start()
        self.delivery_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.notification_process and self.notification_process.poll() is None:
            self.notification_process.terminate()
        for thread in (self.thread, self.delivery_thread):
            if thread:
                thread.join(timeout=3)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.evaluate_once()
            except (sqlite3.Error, OSError, ValueError, RuntimeError):
                LOGGER.exception("Alert evaluation failed; stored rules/events are retained")
            self.stop_event.wait(15)

    def evaluate_once(self) -> None:
        from .comparison import ComparisonError, Search, compare
        from .provider_search import _snapshot

        with alert_connection(self.settings) as connection:
            rows = connection.execute("SELECT * FROM alert_rules WHERE enabled=1").fetchall()
        for row in rows:
            rule = AlertRule.model_validate_json(row["definition"])
            states = json.loads(row["state"])
            for provider in rule.sources:
                previous = states.get(provider, {})
                snapshot = _snapshot(self.settings, provider)
                try:
                    if rule.condition in HEALTH_RULES:
                        latest = snapshot.get("latest_observation")
                        active = (
                            not snapshot["ready"] if rule.condition == "source_unavailable"
                            else False if rule.condition == "city_missing"
                            else
                            latest is not None and timestamp_now() - latest > rule.stale_hours * 3600
                            if rule.condition == "source_stale"
                            else bool(snapshot.get("collection") and snapshot["collection"]["status"] == "failed")
                        )
                        if rule.condition == "city_missing":
                            try:
                                compare(self.settings, Search(source=provider, pollutant=rule.pollutant,
                                                              view="cities", location=rule.city,
                                                              monitor_class=rule.monitor_class))
                            except ComparisonError:
                                active = True
                        current = {"active": active, "fingerprint": _fingerprint({"active": active})}
                        message = (
                            f"{provider}: source readings are older than {rule.stale_hours} hour(s)."
                            if rule.condition == "source_stale" else f"{provider}: the latest collection failed."
                        )
                        triggered = active and (not previous.get("active")) and bool(previous or rule.notify_initial)
                        if rule.condition == "source_unavailable":
                            message = f"{provider}: no searchable source history is available. {snapshot.get('reason') or ''}"
                        elif rule.condition == "city_missing":
                            message = f"{provider} / {rule.city} / {rule.pollutant}: no eligible recent city reading is available."
                        title = (
                            "City data unavailable" if rule.condition == "city_missing"
                            else "Source unavailable" if rule.condition == "source_unavailable"
                            else "Source freshness alert" if rule.condition == "source_stale"
                            else "Source collection failed"
                        )
                        details = {"rule_name": rule.name, "source_status": snapshot}
                    else:
                        run = snapshot.get("collection")
                        if run and run["status"] != "completed":
                            raise ComparisonError(
                                "ALERT_WAITING_FOR_REFRESH",
                                "Waiting for a completed source refresh; partial collection changes do not trigger air alerts.",
                            )
                        values = {
                            "source": provider, "pollutant": rule.pollutant, "view": "cities",
                            "objective": rule.objective, "profile": rule.profile,
                            "threshold": rule.reference if rule.profile == "custom" else None,
                            "monitor_class": rule.monitor_class,
                        }
                        if rule.window_days:
                            end = utc_today() - timedelta(days=1)
                            values.update(start=end - timedelta(days=rule.window_days - 1), end=end)
                        if rule.condition in CITY_RULES:
                            values["location"] = rule.city
                        result = compare(self.settings, Search(**values))
                        winners = [item["name"] for item in result["winners"]]
                        selected = result["rankings"][0] if rule.condition in CITY_RULES else None
                        material = {
                            "period": result["period"], "start": result["start"], "end": result["end"],
                            "rows": [
                                {"city": item["city"], "mean": item["mean"],
                                 "stations": item["stations"], "coverage": item["coverage_percent"]}
                                for item in result["rankings"]
                            ],
                        }
                        current = {
                            "fingerprint": _fingerprint(material), "winners": winners,
                            "mean": selected["mean"] if selected else None,
                            "measurement_time": result["period"].get("time_end"),
                        }
                        triggered, title, message = self._condition(rule, provider, previous, current)
                        details = {
                            "rule_name": rule.name, "criteria": rule.model_dump(),
                            "previous": previous, "current": current,
                            "period": result["period"], "pollutant": rule.pollutant,
                            "stations": selected["stations"] if selected else result["winners"][0]["stations"],
                            "station_details": selected["station_details"] if selected else result["winners"][0]["station_details"],
                            "notice": "Measured results can change due to coverage and source revisions as well as air conditions.",
                        }
                    current["last_checked"] = timestamp_now()
                    current["error"] = None
                    if triggered:
                        revision = snapshot.get("last_success")
                        event_key = _fingerprint({
                            "rule": row["id"], "source": provider, "revision": revision,
                            "before": previous.get("fingerprint"), "after": current["fingerprint"],
                        })
                        with alert_connection(self.settings) as connection, connection:
                            connection.execute(
                                "INSERT OR IGNORE INTO alert_events "
                                "(rule_id,source,created,title,message,details,event_key,desktop_status) "
                                "VALUES (?,?,?,?,?,?,?,?)",
                                (row["id"], provider, timestamp_now(), title, message,
                                 json.dumps(details, ensure_ascii=True), event_key,
                                 "pending" if rule.desktop else "not_requested"),
                            )
                    states[provider] = current
                except ComparisonError as error:
                    states[provider] = {**previous, "last_checked": timestamp_now(), "error": error.message}
                    if previous.get("error") != error.message:
                        LOGGER.warning("Alert %s / %s is waiting: %s", row["id"], provider, error.code)
            with alert_connection(self.settings) as connection, connection:
                connection.execute("UPDATE alert_rules SET state=? WHERE id=?", (json.dumps(states), row["id"]))

    @staticmethod
    def _condition(rule: AlertRule, provider: str, previous: dict, current: dict) -> tuple:
        if not previous.get("fingerprint"):
            return rule.notify_initial, "Air-quality baseline recorded", f"{provider}: first valid result for {rule.name}."
        if current["fingerprint"] == previous.get("fingerprint"):
            return False, "", ""
        prefix = f"{provider} / {rule.pollutant}"
        if rule.condition == "winner_changed":
            changed = set(current["winners"]) != set(previous.get("winners", []))
            return changed, "Cleanest city changed", (
                f"{prefix}: {', '.join(previous.get('winners', []))} -> {', '.join(current['winners'])}."
            )
        if rule.condition == "search_updated":
            return True, "Air-quality search updated", f"{prefix}: the completed source result for {rule.name} changed."
        old, new = previous.get("mean"), current.get("mean")
        if old is None or new is None:
            return False, "", ""
        change = new - old
        triggered = (
            change < -rule.minimum_change if rule.condition == "cleaner"
            else change > rule.minimum_change if rule.condition == "dirtier"
            else old > rule.threshold >= new if rule.condition == "below"
            else old <= rule.threshold < new
        )
        title = "City air became cleaner" if change < 0 else "City pollution increased"
        return triggered, title, f"{prefix} / {rule.city}: measured concentration {old:.3f} -> {new:.3f} ug/m3."

    def _deliver(self) -> None:
        while not self.stop_event.is_set():
            with alert_connection(self.settings) as connection:
                event = connection.execute(
                    "SELECT * FROM alert_events WHERE desktop_status='pending' ORDER BY id LIMIT 1"
                ).fetchone()
            if not event:
                self.stop_event.wait(2)
                continue
            status = "failed"
            try:
                with alert_connection(self.settings) as connection, connection:
                    connection.execute(
                        "UPDATE alert_events SET desktop_status='sending' WHERE id=?", (event["id"],)
                    )
                self.notification_process = desktop_process()
                payload = json.dumps({
                    "title": event["title"][:63],
                    "message": (event["message"] + " Open Alerts in Clean Air Finder for details.")[:255],
                }, ensure_ascii=False)
                self.notification_process.communicate(payload, timeout=15)
                if self.notification_process.returncode:
                    raise RuntimeError("Windows notification process failed; inspect Windows policy/session settings.")
                status = "requested"
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                LOGGER.exception("Desktop notification failed for event %s; the in-app event is retained", event["id"])
                if self.notification_process and self.notification_process.poll() is None:
                    self.notification_process.terminate()
            finally:
                self.notification_process = None
            with alert_connection(self.settings) as connection, connection:
                connection.execute(
                    "UPDATE alert_events SET desktop_status=? WHERE id=?", (status, event["id"])
                )
