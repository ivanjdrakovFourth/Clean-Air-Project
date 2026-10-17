from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clean_air.database import connect
from clean_air.web import create_app


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as value:
        yield value


def test_api_returns_required_shape_and_uses_only_local_data(client, seed, monkeypatch):
    from clean_air.source import EeaSource

    def forbidden(*args, **kwargs):
        raise AssertionError("A user search must not contact the EEA")

    monkeypatch.setattr(EeaSource, "request", forbidden)
    seed("Alpha", "A1", "2025-01-01", 10)
    response = client.get("/api/cleanest?start=2025-01-01&end=2025-01-01")
    assert response.status_code == 200
    payload = response.json()
    assert {
        "winners", "rankings", "excluded_cities", "reference", "methodology",
        "policy_id", "source", "warnings", "latest_observation",
    } <= payload.keys()
    assert payload["winners"][0]["city"] == "Alpha"
    assert payload["methodology"]["timezone"] == "UTC"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("query", [
    "start=invalid&end=2025-01-01",
    "start=2025-01-02&end=2025-01-01",
    "start=2025-01-01&end=2025-01-01&profile=custom",
    "start=2025-01-01&end=2025-01-01&pollutant=invalid",
    "start=2025-01-01&end=2025-01-01&profile=custom&threshold=nan",
])
def test_invalid_api_requests_have_explicit_error_shape(client, query):
    response = client.get("/api/cleanest?" + query)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert response.json()["error"]["message"]


def test_health_distinguishes_liveness_from_pending_history(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503
    response = client.get("/api/cleanest?start=2025-01-01&end=2025-01-01")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "HISTORY_NOT_READY"


def test_source_outage_does_not_break_cached_queries(client, settings, seed, monkeypatch):
    from clean_air.collector import Collector
    from clean_air.source import SourceError

    class UnavailableSource:
        def __init__(self, settings):
            pass

        def cities(self):
            raise SourceError("Upstream service unavailable")

        def close(self):
            pass

    seed("Alpha", "A1", "2025-01-01", 10)
    monkeypatch.setattr("clean_air.collector.EeaSource", UnavailableSource)
    with pytest.raises(SourceError, match="unavailable"):
        Collector(settings).sync_once(force=True)
    response = client.get("/api/cleanest?start=2025-01-01&end=2025-01-01")
    assert response.status_code == 200
    assert any("failed" in message for message in response.json()["warnings"])
    assert client.get("/health/ready").status_code == 200
    assert "clean_air_collection_healthy 0.0" in client.get("/metrics").text


def test_server_rendered_ui_and_docs_work_without_external_assets(client, seed):
    seed("Alpha", "A1", "2025-01-01", 10)
    response = client.get("/")
    assert response.status_code == 200
    assert "Alpha" in response.text
    assert "UTC" in response.text
    assert "LOWEST MEASURED AVERAGE" in response.text
    assert "https://cdn" not in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "works offline" in docs.text
    assert "/api/cleanest" in client.get("/openapi.json").json()["paths"]
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_browser_errors_are_escaped_and_explained(client, seed):
    seed("<script>alert(1)</script>", "A1", "2025-01-01", 10)
    response = client.get("/")
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    response = client.get("/results?start=wrong&end=2025-01-01")
    assert response.status_code == 422
    assert 'role="alert"' in response.text
    assert client.get("/?start=wrong&end=2025-01-01").status_code == 422


def test_untrusted_hostnames_are_rejected(client):
    assert client.get("/", headers={"Host": "untrusted.example"}).status_code == 400


def test_offline_banner_does_not_repeat_an_old_online_collection_message(client, seed, settings):
    seed("Alpha", "A1", "2025-01-01", 10)
    with connect(settings.database) as connection, connection:
        connection.execute(
            "INSERT INTO collection_runs(started,finished,status,message) "
            "VALUES(1,2,'completed','Updates continue in the background.')"
        )
    response = client.get("/")
    assert "Automatic updates are disabled." in response.text
    assert "Updates continue in the background." not in response.text
