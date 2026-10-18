from __future__ import annotations

import threading
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from test_sources import source_day

from clean_air.collection_notices import CollectionNotices
from clean_air.collector import Collector
from clean_air.database import connect, get_metadata, set_metadata
from clean_air.openaq import OpenAQClient, OpenAQCollector, Sensor
from clean_air.provider_store import collection_start, for_provider, prepare_sources
from clean_air.settings import timestamp_now
from clean_air.web import create_app


@pytest.fixture
def notices(settings):
    prepare_sources(settings)
    return settings, CollectionNotices(settings)


def test_startup_status_is_source_specific_and_never_claims_unavailable_feeds_are_current(notices):
    settings, service = notices
    service.begin([
        {"id": "eea", "name": "EEA", "enabled": True, "reason": ""},
        {"id": "openaq", "name": "OpenAQ", "enabled": False, "reason": "Needs a local key."},
    ])
    status = service.status()
    assert status["startup_event"]
    assert len(status["events"]) == 2
    assert status["events"][1]["details"]["phase"] == "unavailable"
    assert "up to date" not in status["events"][1]["title"]
    assert service.status(status["events"][-1]["id"])["events"] == []


def test_partial_backfill_does_not_emit_a_completion_and_each_source_gets_its_own_event(notices):
    settings, service = notices
    source_day(settings, "eea", "Alpha", "A1", "2025-01-01", 10)
    source_day(settings, "openaq", "Alpha", "OA1", "2025-01-01", 20)
    service.report("openaq", {"complete": False})
    assert service.status()["events"] == []
    service.report("eea", {"imported_rows": 0})
    service.report("openaq", {"complete": True})
    events = service.status()["events"]
    assert {event["source"] for event in events} == {"eea", "openaq"}
    assert all(event["details"]["phase"] == "completed" for event in events)
    service.report("eea", {"imported_rows": 0})
    assert len(service.status()["events"]) == 2


def test_unusable_stale_future_and_failed_source_states_do_not_get_a_fresh_success_title(notices):
    settings, service = notices
    service.report("eea", {"imported_rows": 0})
    assert service.status()["events"][-1]["details"]["phase"] == "unavailable"
    source_day(settings, "openaq", "Alpha", "OA1", "2025-01-01", 20)
    with connect(for_provider(settings, "openaq").database) as connection, connection:
        set_metadata(connection, "latest_observation", timestamp_now() - 72 * 3600)
    service.report("openaq", {"complete": True})
    event = service.status()["events"][-1]
    assert event["details"]["phase"] == "delayed"
    assert "up to date" not in event["title"]
    second = CollectionNotices(settings)
    with connect(settings.database) as connection, connection:
        set_metadata(connection, "latest_observation", timestamp_now() + 24 * 3600)
    second.report("eea", {"imported_rows": 0})
    assert "up to date" not in second.status()["events"][0]["title"]
    second.report("openaq", None)
    second.report("openaq", None)
    failures = [event for event in second.status()["events"] if event["details"]["phase"] == "failed"]
    assert len(failures) == 1


def test_optional_desktop_preference_is_local_and_marks_only_requested_delivery(notices):
    settings, service = notices
    source_day(settings, "eea", "Alpha", "A1", "2025-01-01", 10)
    assert service.desktop_enabled() is False
    service.set_desktop(True)
    service.report("eea", {"imported_rows": 0})
    assert service.status()["events"][0]["desktop_status"] == "pending"
    service.set_desktop(False)
    assert CollectionNotices(settings).desktop_enabled() is False


def test_common_startup_dialog_has_only_ok_and_never_controls_collection(settings):
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        page = client.get("/monitoring")
        assert 'id="startup-sync-dialog"' in page.text
        assert 'id="startup-sync-ok"' in page.text
        assert "/static/collection-notices.js" in page.text
        status = client.get("/api/collection-notices").json()
        assert status["collection_enabled"] is False
        assert all(not source["enabled"] for source in status["sources"])
        blocked = client.post("/api/settings/collection-desktop", json={"enabled": True},
                              headers={"Origin": "https://untrusted.example"})
        assert blocked.status_code == 403
        assert client.post("/api/settings/collection-desktop", json={"enabled": True}).status_code == 200
        event = status["events"][0]
        assert client.post(f"/api/alerts/events/{event['id']}/read").status_code == 200
        assert event["id"] not in {row["id"] for row in client.get("/api/collection-notices").json()["events"]}


def test_startup_checks_begin_without_waiting_for_any_browser_acknowledgement(settings, monkeypatch):
    prepare_sources(settings)
    monkeypatch.setenv("OPENAQ_API_KEY", "test-only-local-not-a-real-openaq-credential")
    checked = {"eea": threading.Event(), "openaq": threading.Event()}
    values = settings.options.model_dump()
    values["collection"]["enabled"] = True
    from dataclasses import replace

    from clean_air.settings import Options

    configured = replace(settings, options=Options.model_validate(values))

    def check(self, force=False):
        checked[self.settings.provider].set()
        self.stop_event.set()
        return {"complete": True, "imported_rows": 0}

    monkeypatch.setattr(Collector, "sync_once", check)
    monkeypatch.setattr(OpenAQCollector, "sync_once", check)
    with TestClient(create_app(configured), base_url="http://127.0.0.1") as client:
        assert checked["eea"].wait(5)
        assert checked["openaq"].wait(5)
        state = client.get("/api/collection-notices").json()
        assert state["startup_event"]
        assert state["collection_enabled"] is True


def test_history_floor_grows_instead_of_dropping_missing_dates_after_a_long_pause(settings, monkeypatch):
    prepare_sources(settings)
    monkeypatch.setattr("clean_air.provider_store.utc_today", lambda: date(2028, 10, 18))
    assert collection_start(settings, 12) == date(2027, 10, 18)
    with connect(settings.database) as connection, connection:
        set_metadata(connection, "history_requested_start", "2025-10-18")
    assert collection_start(settings, 12) == date(2025, 10, 18)
    assert collection_start(settings, 36) == date(2025, 10, 18)


def test_openaq_long_gap_requests_start_at_previous_checkpoint_not_only_the_latest_year(
    settings, monkeypatch,
):
    prepare_sources(settings)
    monkeypatch.setenv("OPENAQ_API_KEY", "test-only-local-not-a-real-openaq-credential")
    aq = for_provider(settings, "openaq")
    old_start = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    last_end = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp())
    with connect(aq.database) as connection, connection:
        set_metadata(connection, "history_requested_start", "2024-01-01")
        set_metadata(connection, "openaq_backfill_complete", True)
        set_metadata(connection, "openaq_cursor:1", {
            "requested_start": old_start, "lower": old_start, "next_to": old_start,
            "target_end": last_end, "history_floor": old_start,
        })
    collector = OpenAQCollector(aq)
    series = Sensor(1, "OLD", "PM10", old_start, timestamp_now(), ((0, None),))
    monkeypatch.setattr(collector, "_catalog", lambda client, force: [series])
    monkeypatch.setattr(OpenAQClient, "request", lambda *args, **kwargs: pytest.fail("No network in this test"))
    windows = []

    def window(client, sensor, lower, upper, cursor, key):
        windows.append((lower, upper))
        with connect(aq.database) as connection, connection:
            set_metadata(connection, key, cursor)
        return 0

    monkeypatch.setattr(collector, "_window", window)
    assert collector.sync_once()["complete"] is True
    assert min(lower for lower, upper in windows) == last_end - 7 * 86400
    with connect(aq.database) as connection:
        assert get_metadata(connection, "history_requested_start") == "2024-01-01"
