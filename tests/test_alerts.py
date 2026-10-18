from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_sources import source_day as _source_day

from clean_air.alerts import AlertEngine, AlertRule, change_rule, rules_and_events, save_rule
from clean_air.database import connect, set_metadata
from clean_air.provider_store import prepare_sources
from clean_air.settings import timestamp_now, utc_today


def source_day(*args, **kwargs):
    return _source_day(*args, pollutant=5, **kwargs)


@pytest.fixture
def alert_setup(settings):
    prepare_sources(settings)
    yesterday = (utc_today() - timedelta(days=1)).isoformat()
    source_day(settings, "eea", "Alpha", "A1", yesterday, 10)
    source_day(settings, "eea", "Beta", "B1", yesterday, 20)
    return settings, yesterday, AlertEngine(settings)


def test_first_assessment_is_baseline_then_winner_change_persists_once(alert_setup):
    settings, day, engine = alert_setup
    save_rule(settings, AlertRule(name="Winner watch", window_days=1))
    engine.evaluate_once()
    assert not rules_and_events(settings)["events"]
    source_day(settings, "eea", "Beta", "B1", day, 5)
    engine.evaluate_once()
    events = rules_and_events(settings)["events"]
    assert len(events) == 1
    assert events[0]["title"] == "Cleanest city changed"
    assert "Alpha -> Beta" in events[0]["message"]
    assert events[0]["details"]["station_details"][0]["address"] == "eea station street"
    AlertEngine(settings).evaluate_once()
    assert len(rules_and_events(settings)["events"]) == 1


@pytest.mark.parametrize("condition,new_value", [("cleaner", 8), ("dirtier", 12)])
def test_cleaner_and_dirtier_rules_use_pollution_direction(alert_setup, condition, new_value):
    settings, day, engine = alert_setup
    save_rule(settings, AlertRule(name="Alpha change", condition=condition, city="Alpha",
                                 window_days=1, minimum_change=1))
    engine.evaluate_once()
    source_day(settings, "eea", "Alpha", "A1", day, new_value)
    engine.evaluate_once()
    events = rules_and_events(settings)["events"]
    assert len(events) == 1
    assert events[0]["source"] == "eea"
    assert "Alpha" in events[0]["message"]


def test_concentration_crossing_is_inclusive_below_and_strict_above(alert_setup):
    settings, day, engine = alert_setup
    save_rule(settings, AlertRule(name="Below 9", condition="below", city="Alpha",
                                 threshold=9, window_days=1))
    save_rule(settings, AlertRule(name="Above 9", condition="above", city="Alpha",
                                 threshold=9, window_days=1))
    engine.evaluate_once()
    source_day(settings, "eea", "Alpha", "A1", day, 9)
    engine.evaluate_once()
    assert len(rules_and_events(settings)["events"]) == 1
    source_day(settings, "eea", "Alpha", "A1", day, 10)
    engine.evaluate_once()
    assert len(rules_and_events(settings)["events"]) == 2


def test_collection_heartbeat_without_a_changed_search_does_not_alert(alert_setup):
    settings, _, engine = alert_setup
    save_rule(settings, AlertRule(name="Changes", condition="search_updated", window_days=1))
    engine.evaluate_once()
    with connect(settings.database) as connection, connection:
        set_metadata(connection, "last_success", timestamp_now() + 10)
    engine.evaluate_once()
    assert not rules_and_events(settings)["events"]


def test_alert_state_is_independent_for_each_source(alert_setup):
    settings, day, engine = alert_setup
    source_day(settings, "openaq", "Alpha", "OA1", day, 30)
    source_day(settings, "openaq", "Beta", "OA2", day, 3)
    identity = save_rule(settings, AlertRule(name="Sources", sources=["eea", "openaq"], window_days=1))
    engine.evaluate_once()
    states = rules_and_events(settings)["rules"][0]["state"]
    assert states["eea"]["winners"] == ["Alpha"]
    assert states["openaq"]["winners"] == ["Beta"]
    source_day(settings, "eea", "Alpha", "A1", day, 100)
    engine.evaluate_once()
    assert [item["source"] for item in rules_and_events(settings)["events"]] == ["eea"]
    change_rule(settings, identity, enabled=False)
    source_day(settings, "eea", "Alpha", "A1", day, 1)
    engine.evaluate_once()
    assert len(rules_and_events(settings)["events"]) == 1


def test_failed_and_stale_source_alerts_are_separate_from_air_rules(alert_setup):
    settings, _, engine = alert_setup
    save_rule(settings, AlertRule(name="Failure", condition="collection_failed"))
    save_rule(settings, AlertRule(name="Age", condition="source_stale", stale_hours=6))
    engine.evaluate_once()
    with connect(settings.database) as connection, connection:
        connection.execute("INSERT INTO collection_runs(started,status,message) VALUES (?,'failed','upstream failed')",
                           (timestamp_now(),))
        set_metadata(connection, "latest_observation", timestamp_now() - 8 * 3600)
    engine.evaluate_once()
    assert {event["title"] for event in rules_and_events(settings)["events"]} == {
        "Source collection failed", "Source freshness alert",
    }


def test_incomplete_refresh_does_not_trigger_partial_ranking_alert(alert_setup):
    settings, day, engine = alert_setup
    save_rule(settings, AlertRule(name="Stable cycle", window_days=1))
    engine.evaluate_once()
    source_day(settings, "eea", "Beta", "B1", day, 1)
    with connect(settings.database) as connection, connection:
        connection.execute("INSERT INTO collection_runs(started,status,message) VALUES (?,'running','loading')",
                           (timestamp_now(),))
    engine.evaluate_once()
    assert not rules_and_events(settings)["events"]
    assert "completed source refresh" in rules_and_events(settings)["rules"][0]["state"]["eea"]["error"]


def test_editing_rule_resets_baseline_and_delete_preserves_history(alert_setup):
    settings, _, engine = alert_setup
    identity = save_rule(settings, AlertRule(name="Initial", window_days=1, notify_initial=True))
    engine.evaluate_once()
    assert len(rules_and_events(settings)["events"]) == 1
    save_rule(settings, AlertRule(name="Changed", condition="cleaner", city="Alpha", window_days=1), identity)
    assert rules_and_events(settings)["rules"][0]["state"] == {}
    change_rule(settings, identity, delete=True)
    data = rules_and_events(settings)
    assert not data["rules"]
    assert data["events"][0]["rule_id"] is None


@pytest.mark.parametrize("failed", [False, True])
def test_desktop_request_outcomes_remain_in_history_and_are_not_resent(alert_setup, monkeypatch, failed):
    settings, _, engine = alert_setup
    save_rule(settings, AlertRule(name="Desktop outcome", window_days=1, notify_initial=True, desktop=True))
    engine.evaluate_once()
    calls = []

    class NotificationProcess:
        returncode = 0

        def communicate(self, payload, timeout):
            assert "Clean Air Finder" in payload
            engine.stop_event.set()

    def request():
        calls.append(True)
        if failed:
            engine.stop_event.set()
            raise OSError("Simulated desktop session failure")
        return NotificationProcess()

    monkeypatch.setattr("clean_air.alerts.desktop_process", request)
    engine._deliver()
    events = rules_and_events(settings)["events"]
    assert len(events) == 1
    assert events[0]["desktop_status"] == ("failed" if failed else "requested")
    assert calls == [True]
    engine.stop_event.clear()
    monkeypatch.setattr(engine.stop_event, "wait", lambda delay: engine.stop_event.set())
    engine._deliver()
    assert calls == [True]


@pytest.mark.parametrize("values", [
    {"name": " "}, {"name": "X", "condition": "cleaner"},
    {"name": "X", "condition": "below", "city": "Alpha"},
    {"name": "X", "sources": ["eea", "eea"]},
    {"name": "X", "objective": "improvement"},
    {"name": "X", "objective": "reference_days", "window_days": 7, "pollutant": "O3"},
])
def test_invalid_alert_configuration_is_explicitly_rejected(values):
    with pytest.raises(ValidationError):
        AlertRule(**values)
