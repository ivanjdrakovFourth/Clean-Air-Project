from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from clean_air.credentials import PREFIX, key_path, openaq_key, save_openaq_key
from clean_air.database import connect, get_metadata, set_metadata
from clean_air.openaq import (
    API,
    OpenAQClient,
    OpenAQCollector,
    RequestBudget,
    Sensor,
    WindowTimeout,
    observation,
)
from clean_air.provider_store import for_provider, prepare_sources
from clean_air.settings import Options
from clean_air.source import SourceError, mass_unit_factor

CANARY = "test-only-openaq-credential-not-a-real-api-key"


@pytest.fixture
def aq(settings, monkeypatch):
    prepare_sources(settings)
    monkeypatch.setenv("OPENAQ_API_KEY", CANARY)
    return for_provider(settings, "openaq")


def hour(**changes):
    return {
        "value": 14.8, "summary": {"avg": 14.83},
        "parameter": {"name": "pm10", "units": "\u00b5g/m\u00b3"},
        "flagInfo": {"hasFlags": False},
        "period": {
            "datetimeFrom": {"utc": "2025-01-01T00:00:00Z"},
            "datetimeTo": {"utc": "2025-01-01T01:00:00Z"},
        },
        "coverage": {"percentCoverage": 100},
        **changes,
    }


def sensor():
    return Sensor(1, "BG0043A", "PM10", 0, 2_000_000_000, ((0, None),))


def mock_client(aq, handler):
    client = OpenAQClient(aq, threading.Event(), httpx.Client(transport=httpx.MockTransport(handler)))
    client.stop.wait = lambda delay: False
    return client


def test_local_key_is_user_encrypted_and_environment_override_is_explicit(settings, monkeypatch):
    monkeypatch.delenv("OPENAQ_API_KEY", raising=False)
    save_openaq_key(settings.root, CANARY)
    assert key_path(settings.root).read_text("ascii").startswith(PREFIX)
    assert CANARY not in key_path(settings.root).read_text("ascii")
    assert openaq_key(settings.root) == CANARY
    monkeypatch.setenv("OPENAQ_API_KEY", "another-test-only-token")
    assert openaq_key(settings.root) == "another-test-only-token"


def test_plaintext_key_is_not_accepted_as_a_portable_credential(settings, monkeypatch):
    monkeypatch.delenv("OPENAQ_API_KEY", raising=False)
    path = key_path(settings.root)
    path.parent.mkdir(parents=True)
    path.write_text(CANARY, encoding="ascii")
    with pytest.raises(RuntimeError, match="Configure-OpenAQ"):
        openaq_key(settings.root)


def test_key_is_only_sent_in_header_to_the_fixed_api_host(aq):
    received = []

    def handler(request):
        received.append(request)
        return httpx.Response(200, json={"results": [], "meta": {}})

    client = mock_client(aq, handler)
    try:
        client.request("/locations", {"iso": "BG"})
        assert received[0].headers["x-api-key"] == CANARY
        assert CANARY not in str(received[0].url)
        assert received[0].url.host == "api.openaq.org"
        assert received[0].url.params["iso"] == "BG"
    finally:
        client.close()


def test_auth_error_body_cannot_leak_the_key_into_exception(aq):
    client = mock_client(aq, lambda request: httpx.Response(401, json={"echo": CANARY}))
    try:
        with pytest.raises(SourceError) as error:
            client.request("/locations")
        assert "401" in str(error.value)
        assert CANARY not in str(error.value)
    finally:
        client.close()


def test_429_waits_before_retry_and_408_requests_a_smaller_window(aq):
    replies = [httpx.Response(429, headers={"retry-after": "5"}), httpx.Response(200, json={"results": []})]
    client = mock_client(aq, lambda request: replies.pop(0))
    waits = []
    client.stop.wait = lambda delay: waits.append(delay) or False
    try:
        assert client.request("/locations")["results"] == []
        assert 5 in waits
    finally:
        client.close()
    client = mock_client(aq, lambda request: httpx.Response(408))
    try:
        with pytest.raises(WindowTimeout):
            client.request("/sensors/1/hours")
    finally:
        client.close()


def test_cycle_budget_stops_before_extra_requests(aq):
    options = aq.options.model_dump()
    options["openaq"]["requests_per_cycle"] = 1
    configured = replace(aq, options=Options.model_validate(options))
    client = mock_client(configured, lambda request: httpx.Response(200, json={"results": []}))
    try:
        client.request("/locations")
        with pytest.raises(RequestBudget):
            client.request("/locations")
        assert client.calls == 1
    finally:
        client.close()


def test_hourly_allowance_cooldown_can_be_interrupted_without_request(aq):
    import time

    with connect(aq.database) as connection, connection:
        set_metadata(connection, "openaq_request_times", [time.time() - 20] * 1900)
    called = []
    client = mock_client(aq, lambda request: called.append(request))
    waits = []
    client.stop.wait = lambda delay: waits.append(delay) or True
    try:
        with pytest.raises(SourceError, match="stopped"):
            client.request("/locations")
        assert waits[0] > 3500
        assert not called
    finally:
        client.close()


def test_inactive_sensor_has_a_persisted_complete_empty_window_not_fake_measurements(aq, monkeypatch):
    collector = OpenAQCollector(aq)
    with connect(aq.database) as connection, connection:
        connection.execute(
            "INSERT INTO collection_runs(started,status,message) VALUES (1,'running','Interrupted fixture')"
        )
    monkeypatch.setattr(collector, "_catalog", lambda client, force: [
        Sensor(999, "Inactive", "PM10", 0, 86400, ((0, None),)),
    ])
    monkeypatch.setattr(
        OpenAQClient, "request",
        lambda *args, **kwargs: pytest.fail("A sensor that ended before the requested range needs no hourly request."),
    )
    result = collector.sync_once()
    assert result["complete"] is True
    assert collector.sync_once()["complete"] is True
    with connect(aq.database) as connection:
        cursor = get_metadata(connection, "openaq_cursor:999")
        assert cursor["next_to"] <= cursor["lower"]
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert get_metadata(connection, "initial_import_complete", False) is False
        assert connection.execute("SELECT status FROM collection_runs WHERE id=1").fetchone()[0] == "interrupted"


def test_hour_uses_unrounded_mean_and_exact_utc_interval():
    row = observation(hour(), sensor())
    assert row[4] == 14.83
    assert row[3] - row[2] == 3600
    assert row[6] == 1
    assert row[1] == 5


@pytest.mark.parametrize("changes", [
    {"flagInfo": {"hasFlags": True}},
    {"flagInfo": None},
    {"coverage": {"percentCoverage": 74}},
    {"value": -999, "summary": {"avg": -999}},
    {"value": None, "summary": None},
])
def test_flagged_sparse_negative_missing_values_are_not_rankable(changes):
    assert observation(hour(**changes), sensor())[6] == -1


def test_true_valid_zero_is_kept_but_volume_units_are_not_assumed():
    assert observation(hour(value=0, summary={"avg": 0}), sensor())[4] == 0
    with pytest.raises(SourceError, match="volume ratios"):
        observation(hour(parameter={"name": "pm10", "units": "ppm"}), sensor())


def test_co_mass_conversion_is_linear_not_a_ppm_conversion():
    carbon = Sensor(2, "OA2", "CO", 0, 2_000_000_000, ((0, None),))
    row = observation(hour(
        value=1.2, summary={"avg": 1.25}, parameter={"name": "co", "units": "mg/m3"}
    ), carbon)
    assert row[1] == 10
    assert row[4] == 1250
    assert mass_unit_factor("mg.m-3") == 1000


def test_daily_or_wrong_pollutant_record_cannot_be_imported_as_an_hour():
    with pytest.raises(SourceError, match="identity"):
        observation(hour(parameter={"name": "no2", "units": "ug/m3"}), sensor())
    with pytest.raises(SourceError, match="one-hour"):
        observation(hour(period={
            "datetimeFrom": {"utc": "2025-01-01T00:00:00Z"},
            "datetimeTo": {"utc": "2025-01-02T00:00:00Z"},
        }), sensor())


def test_sensor_window_commit_is_idempotent_utc_and_license_scoped(aq):
    with connect(aq.database) as connection, connection:
        connection.execute("INSERT INTO stations(id) VALUES ('BG0043A')")
        connection.execute("INSERT INTO sampling_points VALUES ('OA_SENSOR_1','BG0043A')")
        connection.execute("INSERT INTO import_files(id,url,dataset) VALUES (1,?,1)", (API + "/sensors/1/hours",))
        set_metadata(connection, "data_provider", "openaq")
    client = mock_client(aq, lambda request: httpx.Response(200, json={"results": [hour()]}))
    collector = OpenAQCollector(aq)
    start = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())
    try:
        collector._window(client, sensor(), start, start + 3600, {"next_to": start}, "openaq_cursor:1")
        collector._window(client, sensor(), start, start + 3600, {"next_to": start}, "openaq_cursor:1")
    finally:
        client.close()
    with connect(aq.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        assert connection.execute("SELECT value FROM observations").fetchone()[0] == 14.83
        assert connection.execute("SELECT kind FROM station_days").fetchone()[0] == "OpenAQ reported hourly"
        assert json.loads(connection.execute("SELECT value FROM metadata WHERE key='openaq_cursor:1'").fetchone()[0])["next_to"] == start
