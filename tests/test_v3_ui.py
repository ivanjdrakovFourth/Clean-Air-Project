from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clean_air.provider_store import prepare_sources
from clean_air.web import create_app


@pytest.fixture
def ui_client(settings):
    prepare_sources(settings)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        yield client


@pytest.mark.parametrize("page", ["/", "/monitoring", "/alerts", "/data", "/docs"])
def test_all_normal_menus_have_local_theme_and_navigation(ui_client, page):
    response = ui_client.get(page)
    assert response.status_code == 200
    assert 'id="theme-toggle"' in response.text
    assert "/static/theme.js" in response.text
    assert 'href="/monitoring"' in response.text
    assert 'href="/alerts"' in response.text
    assert 'href="/data"' in response.text
    assert "https://cdn" not in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]


def test_data_explorer_and_unavailable_monitoring_do_not_invent_records(ui_client):
    response = ui_client.get("/api/data?source=bg_direct&table=observations&page_size=25")
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "bg_direct"
    assert data["total"] == 0
    monitoring = ui_client.get("/api/monitoring").json()
    assert monitoring["prometheus"]["available"] is False
    assert len(monitoring["health"]["sources"]) == 3
    direct = next(source for source in monitoring["health"]["sources"] if source["id"] == "bg_direct")
    assert direct["tone"] != "good"
    assert direct["counts"]["observations"] == 0


def test_foreign_origin_cannot_mutate_alerts_or_source_settings(ui_client):
    headers = {"Origin": "https://untrusted.example"}
    response = ui_client.post("/api/alerts", json={"name": "Should not exist"}, headers=headers)
    assert response.status_code == 403
    assert ui_client.get("/api/alerts").json()["rules"] == []
    response = ui_client.post("/api/settings/openaq-key",
                              json={"api_key": "test-only-not-a-real-openaq-key"}, headers=headers)
    assert response.status_code == 403


def test_key_setup_does_not_echo_secret_store_it_in_sqlite_or_start_offline_network(ui_client, settings):
    secret = "test-only-not-a-real-openaq-credential"
    response = ui_client.post("/api/settings/openaq-key", json={"api_key": secret})
    assert response.status_code == 200
    assert secret not in response.text
    content = (settings.var / "secrets" / "openaq.key").read_text("ascii")
    assert secret not in content
    assert "encrypted" in response.json()["message"]
    state = ui_client.get("/api/status?source=openaq").json()
    assert secret not in str(state)
    assert not any(state["source_workers"].values())


def test_alert_rule_create_edit_pause_delete_and_history_api(ui_client):
    created = ui_client.post("/api/alerts", json={"name": "Sofia rule", "condition": "cleaner", "city": "Sofia"})
    assert created.status_code == 201
    identity = created.json()["id"]
    assert ui_client.put(f"/api/alerts/{identity}", json={"name": "Changed", "window_days": 1}).status_code == 200
    assert ui_client.post(f"/api/alerts/{identity}/enabled", json={"enabled": False}).status_code == 200
    assert ui_client.get("/api/alerts").json()["rules"][0]["enabled"] is False
    assert ui_client.delete(f"/api/alerts/{identity}").status_code == 200
    assert ui_client.get("/api/alerts").json()["rules"] == []


def test_mutating_operations_are_queued_and_never_accept_traversal(ui_client):
    response = ui_client.post("/api/jobs", json={"action": "backup", "source": "eea"})
    assert response.status_code == 202
    assert response.json()["id"] > 0
    invalid = ui_client.post("/api/jobs", json={"action": "restore", "source": "openaq", "artifact": "..\\somewhere.jsonl.gz"})
    assert invalid.status_code == 422
    assert ui_client.get("/api/jobs/99999/download").status_code == 404


def test_ozone_daily_mean_does_not_get_a_false_who_24h_label(ui_client, seed):
    seed("Alpha", "A1", "2025-01-01", 60, pollutant=7)
    response = ui_client.get("/api/cleanest?start=2025-01-01&end=2025-01-01&pollutant=O3")
    assert response.status_code == 200
    assert response.json()["reference"]["applicable"] is False
    assert response.json()["winners"][0]["days_at_or_below_reference"] is None
    assert ui_client.get("/?start=2025-01-01&end=2025-01-01&pollutant=O3").status_code == 200
    bad = ui_client.get("/api/cleanest?start=2025-01-01&end=2025-01-01&pollutant=O3&objective=reference_days")
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "DAILY_REFERENCE_UNAVAILABLE"


def test_api_accepts_repeated_selected_sources_without_adding_unselected_stores(ui_client, settings):
    from test_sources import source_day

    source_day(settings, "eea", "Alpha", "A1", "2025-01-01", 1, pollutant=5)
    source_day(settings, "openaq", "Beta", "OA1", "2025-01-01", 2, pollutant=5)
    response = ui_client.get("/api/cleanest", params=[
        ("source", "combined"), ("sources", "openaq"), ("sources", "eea"),
        ("start", "2025-01-01"), ("end", "2025-01-01"), ("pollutant", "PM10"),
    ])
    assert response.status_code == 200
    assert {panel["id"] for panel in response.json()["panels"]} == {"eea", "openaq"}


def test_api_docs_describe_v3_sources_writes_and_completed_date_examples(ui_client, seed):
    from datetime import timedelta

    from clean_air.settings import utc_today

    seed("Alpha", "A1", (utc_today() - timedelta(days=1)).isoformat(), 3)
    seed("Alpha", "A1", utc_today().isoformat(), 3)
    response = ui_client.get("/docs")
    assert "POST /api/jobs" in response.text
    assert "monitor_class" in response.text
    assert "allow_partial_history" in response.text
    assert f"end={(utc_today() - timedelta(days=1)).isoformat()}" in response.text
