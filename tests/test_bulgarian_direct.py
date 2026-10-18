from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from clean_air.bulgarian import EXPORT, BulgarianCollector, csv_end, parse_export
from clean_air.database import connect, get_metadata, rebuild_station_days, set_metadata
from clean_air.provider_store import for_provider, prepare_sources
from clean_air.seeds import build_seeds
from clean_air.settings import Options, months_before, timestamp_now, utc_today
from clean_air.source import SourceError

HEADER = "\u0414\u0430\u0442\u0430/\u0427\u0430\u0441,Particles [PM10] ug/m3,Carbon monoxide [CO] mg/m3,\n"


@pytest.fixture
def national(settings):
    prepare_sources(settings)
    values = settings.options.model_dump()
    values["bg_direct"] = {"enabled": True, "exports_per_cycle": 1}
    configured = replace(for_provider(settings, "bg_direct"), options=Options.model_validate(values))
    collector = BulgarianCollector(configured)
    station = collector._catalog()[0]
    return configured, collector, station


def test_national_hour_ending_labels_and_24_hour_notation_are_normalized_without_dst_guessing():
    assert csv_end("17.10.2026 01:00") == int(datetime(2026, 10, 17, tzinfo=UTC).timestamp())
    assert csv_end("17.10.2026 24:00") == int(datetime(2026, 10, 17, 23, tzinfo=UTC).timestamp())
    with pytest.raises(ValueError):
        csv_end("17.10.2026 24:30")


def test_real_csv_shape_mass_conversion_valid_zero_and_invalid_values(national):
    _, _, station = national
    end = csv_end("17.10.2026 01:00")
    rows, excluded = parse_export(
        HEADER + "17.10.2026 01:00,0,0.3,\n17.10.2026 02:00,-999,,\n",
        station, end - 3600, end + 3600,
    )
    assert excluded == 0
    assert [row[4] for row in rows] == [0, 300, -999, None]
    assert [row[6] for row in rows] == [1, 1, -1, -1]
    assert all(row[0].startswith("BG_DIRECT_") for row in rows)
    assert all(row[3] - row[2] == 3600 for row in rows)


def test_duplicate_clock_labels_quarantine_the_source_day_instead_of_overwriting(national):
    _, _, station = national
    lower = int(datetime(2026, 3, 28, tzinfo=UTC).timestamp())
    upper = int(datetime(2026, 3, 31, tzinfo=UTC).timestamp())
    rows, excluded = parse_export(
        HEADER + "29.03.2026 07:00,12.6,0.3,\n29.03.2026 07:00,13.2,0.3,\n30.03.2026 01:00,9,0.2,\n",
        station, lower, upper,
    )
    assert excluded == 4
    assert len(rows) == 2
    assert rows[0][3] == csv_end("30.03.2026 01:00")


@pytest.mark.parametrize("text", [
    "", "<html>Unavailable</html>", "Date,Value\n",
    HEADER + "invalid,1,0.2,\n", HEADER + "17.10.2026 01:00,1\n",
])
def test_invalid_or_placeholder_csv_is_not_a_successful_measurement_import(national, text):
    _, _, station = national
    with pytest.raises(SourceError):
        parse_export(text, station, 0, 2_000_000_000)


def test_national_export_uses_its_own_host_form_and_no_openaq_credential(national, monkeypatch):
    _, collector, station = national
    monkeypatch.setenv("OPENAQ_API_KEY", "test-only-key-must-not-be-forwarded")
    received = []

    def respond(request):
        received.append(request)
        return httpx.Response(200, text=HEADER + "17.10.2026 01:00,9.84,0.3,\n",
                              headers={"content-type": "text/csv;charset=UTF-8"})

    monkeypatch.setattr(collector.stop_event, "wait", lambda delay: False)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        text = collector._download(client, station, 1760659200, 1792195200)
    assert "9.84" in text
    request = received[0]
    assert str(request.url) == EXPORT
    assert request.method == "POST"
    assert "stationCode=" in request.content.decode()
    assert "x-api-key" not in request.headers


def test_national_partial_cycle_resumes_next_station_and_keeps_source_records_separate(national, monkeypatch):
    configured, collector, _ = national
    day = (utc_today() - timedelta(days=1)).strftime("%d.%m.%Y")
    requested = []

    def download(client, station, lower, upper):
        requested.append(station.station_id)
        return HEADER + f"{day} 01:00,9.84,0.3,\n{day} 02:00,8,0.2,\n"

    monkeypatch.setattr(collector, "_download", download)
    first = collector.sync_once()
    second = collector.sync_once()
    assert not first["complete"] and not second["complete"]
    assert requested[0] != requested[1]
    with connect(configured.database) as connection:
        assert connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 8
        assert len(get_metadata(connection, "bg_csv_cycle")["pending"]) == 30
        assert get_metadata(connection, "bg_direct_backfill_complete", False) is False
        assert get_metadata(connection, "data_provider") == "bg_direct"
    with connect(for_provider(configured, "eea").database) as connection:
        assert connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 0


def test_national_collector_rejects_another_sources_database(settings):
    with pytest.raises(ValueError, match="own"):
        BulgarianCollector(settings)


def test_national_city_can_use_its_explicit_address_without_borrowing_measurements(national):
    configured, _, _ = national
    with connect(configured.database) as connection:
        row = connection.execute(
            "SELECT s.city FROM stations s JOIN station_metadata m ON m.station_id=s.id WHERE m.national_code='77'"
        ).fetchone()
        assert row["city"] == "\u0414\u0435\u0432\u043d\u044f"


def test_complete_national_store_is_exported_as_its_own_seed(completed_sources):
    from conftest import raw_observation

    values = completed_sources.options.model_dump()
    values["bg_direct"]["enabled"] = True
    settings = replace(completed_sources, options=Options.model_validate(values))
    direct = for_provider(settings, "bg_direct")
    start = months_before(utc_today(), 12).isoformat()
    with connect(direct.database) as connection, connection:
        raw_observation(connection, day=start, value=17)
        rebuild_station_days(connection, "BG0001A")
        set_metadata(connection, "initial_import_complete", True)
        set_metadata(connection, "bg_direct_backfill_complete", True)
        set_metadata(connection, "bg_csv_cycle", {"pending": []})
        set_metadata(connection, "history_requested_start", start)
        connection.execute(
            "INSERT INTO collection_runs(started,finished,status,message) VALUES (?,?,'completed','Verified fixture')",
            (timestamp_now(), timestamp_now()),
        )
    manifest = build_seeds(settings)
    assert set(manifest["sources"]) == {"eea", "openaq", "bg_direct"}
    assert manifest["sources"]["bg_direct"]["observations"] == 1
    assert manifest["unavailable"] == {}


def test_enabled_but_incomplete_national_history_cannot_be_published(completed_sources):
    values = completed_sources.options.model_dump()
    values["bg_direct"]["enabled"] = True
    settings = replace(completed_sources, options=Options.model_validate(values))
    with pytest.raises(RuntimeError, match="bg_direct"):
        build_seeds(settings)
