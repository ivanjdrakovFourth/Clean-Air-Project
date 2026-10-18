from __future__ import annotations

import shutil
from datetime import UTC, datetime

import pytest

from clean_air.archive import archive_old, restore
from clean_air.comparison import ComparisonError, Search, compare, status_snapshot
from clean_air.database import connect, set_metadata
from clean_air.provider_store import for_provider, prepare_sources
from clean_air.settings import timestamp_now


def source_day(settings, provider, city, station, day, mean, *, canonical=None, reference=True, pollutant=6001):
    configured = for_provider(settings, provider)
    with connect(configured.database) as connection, connection:
        connection.execute("INSERT OR IGNORE INTO cities VALUES (?)", (city,))
        connection.execute(
            "INSERT INTO stations VALUES (?,?) ON CONFLICT(id) DO UPDATE SET city=excluded.city",
            (station, city),
        )
        connection.execute(
            "INSERT OR REPLACE INTO station_metadata VALUES (?,?,?,?,?,?,?,?,?,?)",
            (station, "source-id", f"{provider} {city}", f"{provider} station street", 42.7, 23.3,
             "urban", "government" if reference else "community", "2026-10-18", "https://example.invalid/source"),
        )
        connection.execute(
            "INSERT OR REPLACE INTO station_days VALUES (?,?,?,?,?,?,?)",
            (station, pollutant, day, mean, 1, 24, "OpenAQ reported hourly" if provider == "openaq" else "validated daily"),
        )
        set_metadata(connection, "initial_import_complete", True)
        set_metadata(connection, "last_success", timestamp_now())
        set_metadata(connection, "latest_observation", timestamp_now())
        if provider == "openaq":
            set_metadata(connection, "openaq_backfill_complete", True)
            set_metadata(connection, f"station_provider:{station}", {
                "provider": {"name": "EEA" if canonical else "Other monitor network"},
                "canonical_station": canonical, "is_monitor": reference, "licenses": [],
            })
    return configured


@pytest.fixture
def source_stores(settings):
    prepare_sources(settings)
    return settings


def test_provider_histories_and_winners_are_not_substituted(source_stores):
    settings = source_stores
    source_day(settings, "eea", "Alpha", "A1", "2025-01-01", 2)
    source_day(settings, "eea", "Beta", "B1", "2025-01-01", 50)
    source_day(settings, "openaq", "Alpha", "OA1", "2025-01-01", 200)
    source_day(settings, "openaq", "Beta", "OA2", "2025-01-01", 5)
    first = compare(settings, Search(start="2025-01-01", end="2025-01-01", source="eea"))
    second = compare(settings, Search(start="2025-01-01", end="2025-01-01", source="openaq"))
    assert first["winners"][0]["city"] == "Alpha"
    assert first["winners"][0]["mean"] == 2
    assert second["winners"][0]["city"] == "Beta"
    assert second["winners"][0]["mean"] == 5
    assert first["data_source"] == "eea"
    assert second["data_source"] == "openaq"
    assert status_snapshot(settings, "openaq")["measurement_provenance"].startswith("Separately collected OpenAQ")


def test_empty_source_does_not_read_eea_history(source_stores):
    source_day(source_stores, "eea", "Alpha", "A1", "2025-01-01", 1)
    for provider in ("openaq", "bg_direct"):
        with pytest.raises(ComparisonError) as error:
            compare(source_stores, Search(start="2025-01-01", end="2025-01-01", source=provider))
        assert error.value.status == 503


def test_combined_keeps_each_answer_and_unavailable_source_panel(source_stores):
    source_day(source_stores, "eea", "Alpha", "A1", "2025-01-01", 2)
    source_day(source_stores, "openaq", "Beta", "OA1", "2025-01-01", 3)
    result = compare(source_stores, Search(
        start="2025-01-01", end="2025-01-01", source="combined",
        sources=["eea", "openaq", "bg_direct"],
    ))
    assert result["is_combined"]
    assert result["panels"][0]["result"]["winners"][0]["city"] == "Alpha"
    assert result["panels"][1]["result"]["winners"][0]["city"] == "Beta"
    assert result["panels"][2]["error"]["code"] == "SOURCE_UNAVAILABLE"
    assert result["agreement"]["shared_top_result"] is False
    assert "winners" not in result


def test_unselected_ready_source_is_not_used_by_combined(source_stores):
    source_day(source_stores, "eea", "Alpha", "A1", "2025-01-01", 1)
    with pytest.raises(ComparisonError) as error:
        compare(source_stores, Search(start="2025-01-01", end="2025-01-01",
                                     source="combined", sources=["openaq", "bg_direct"]))
    assert set(error.value.details["sources"]) == {"openaq", "bg_direct"}


def test_partial_history_is_explicit_opt_in_not_automatic(source_stores):
    source_day(source_stores, "openaq", "Alpha", "OA1", "2025-01-01", 4)
    configured = for_provider(source_stores, "openaq")
    with connect(configured.database) as connection, connection:
        set_metadata(connection, "openaq_backfill_complete", False)
    with pytest.raises(ComparisonError) as error:
        compare(source_stores, Search(start="2025-01-01", end="2025-01-01", source="openaq"))
    assert error.value.code == "SOURCE_BACKFILL_IN_PROGRESS"
    result = compare(source_stores, Search(
        start="2025-01-01", end="2025-01-01", source="openaq", allow_partial_history=True,
    ))
    assert result["partial_history"] is True
    assert result["winners"][0]["mean"] == 4


def test_community_filter_changes_only_the_requested_source_scope(source_stores):
    source_day(source_stores, "openaq", "Alpha", "OA1", "2025-01-01", 1, reference=False)
    source_day(source_stores, "openaq", "Beta", "OA2", "2025-01-01", 5, reference=True)
    result = compare(source_stores, Search(start="2025-01-01", end="2025-01-01",
                                           source="openaq", monitor_class="reference"))
    assert result["winners"][0]["city"] == "Beta"


def test_matching_station_agreement_uses_exact_shared_days(source_stores):
    station = "BG0043A"
    for index in range(10):
        day = f"2025-01-{index + 1:02d}"
        if index < 9:
            source_day(source_stores, "eea", "Alpha", station, day, 10)
        if index > 0:
            source_day(source_stores, "openaq", "Alpha", station, day, 10.1, canonical=station)
    source_day(source_stores, "eea", "Boundary", "Z1", "2025-01-10", 99)
    source_day(source_stores, "openaq", "Boundary", "OAZ", "2025-01-01", 99)
    result = compare(source_stores, Search(
        start="2025-01-01", end="2025-01-10", source="combined",
        sources=["eea", "openaq"], view="stations",
    ))
    pair = result["agreement"]["matched_measurements"][0]
    assert pair["shared_days"] == 8
    assert pair["comparable"] is False
    assert pair["difference"] is None
    assert pair["shared_upstream"] is True


def test_matching_station_means_and_shared_upstream_notice(source_stores):
    station = "BG0043A"
    source_day(source_stores, "eea", "Alpha", station, "2025-01-01", 10)
    source_day(source_stores, "openaq", "Alpha", station, "2025-01-01", 10.1, canonical=station)
    result = compare(source_stores, Search(start="2025-01-01", end="2025-01-01",
                                          source="combined", sources=["eea", "openaq"], view="stations"))
    pair = result["agreement"]["matched_measurements"][0]
    assert pair["difference"] == pytest.approx(0.1)
    assert pair["within_display_tolerance"] is True
    assert "not independent" in result["agreement"]["independence_notice"]


def test_archive_identity_rejects_restore_into_another_source(source_stores):
    from conftest import raw_observation

    with connect(source_stores.database) as connection, connection:
        raw_observation(connection, day="2020-01-01")
        from clean_air.database import rebuild_station_days
        rebuild_station_days(connection, "BG0001A")
    archived = archive_old(source_stores)
    first = source_stores.archive_directory / archived["name"]
    other = for_provider(source_stores, "openaq")
    shutil.copyfile(first, other.archive_directory / first.name)
    shutil.copyfile(first.with_suffix(".gz.sha256"), (other.archive_directory / first.name).with_suffix(".gz.sha256"))
    with pytest.raises(ValueError, match="another measurement source"):
        restore(other, other.archive_directory / first.name)


def test_collection_classes_refuse_cross_source_store(source_stores):
    from clean_air.collector import Collector
    from clean_air.openaq import OpenAQCollector

    with pytest.raises(ValueError, match="cross-source"):
        Collector(for_provider(source_stores, "openaq"))
    with pytest.raises(ValueError, match="cross-source"):
        OpenAQCollector(source_stores)


def current_hour(settings, provider, hour, values):
    from conftest import raw_observation

    began = datetime.fromtimestamp(hour, UTC)
    for index, (city, value) in enumerate(zip(("Alpha", "Beta"), values, strict=True)):
        station = f"{provider}{index}"
        source_day(settings, provider, city, station, began.date().isoformat(), value)
        with connect(for_provider(settings, provider).database) as connection, connection:
            raw_observation(
                connection, city=city, station=station, point=f"{provider}-point-{index}",
                day=began.date().isoformat(), hour=began.hour, value=value, aggregation="hour",
            )


@pytest.mark.parametrize("aligned_initially", [True, False])
def test_combined_current_reuses_aligned_results_and_only_requeries_newer_source(
    source_stores, monkeypatch, aligned_initially,
):
    import clean_air.provider_search as dispatch

    latest = timestamp_now() // 3600 * 3600 - 3600
    common = latest if aligned_initially else latest - 3600
    current_hour(source_stores, "eea", latest, (1, 20))
    if not aligned_initially:
        current_hour(source_stores, "eea", common, (30, 5))
    current_hour(source_stores, "openaq", common, (2, 20))
    original = dispatch._single
    calls = []

    def tracked(*args, **kwargs):
        calls.append(args[2])
        return original(*args, **kwargs)

    monkeypatch.setattr(dispatch, "_single", tracked)
    result = compare(source_stores, Search(source="combined", sources=["eea", "openaq"]))
    assert result["agreement"]["same_time_window"] is True
    assert all(panel["result"]["period"]["time_start"] == common for panel in result["panels"])
    assert calls.count("openaq") == 1
    assert calls.count("eea") == (1 if aligned_initially else 2)
    assert result["panels"][0]["result"]["winners"][0]["city"] == ("Alpha" if aligned_initially else "Beta")


def test_combined_unaligned_hours_are_not_claimed_as_numeric_agreement(source_stores):
    latest = timestamp_now() // 3600 * 3600 - 3600
    current_hour(source_stores, "eea", latest, (1, 20))
    current_hour(source_stores, "openaq", latest - 3600, (2, 20))
    result = compare(source_stores, Search(source="combined", sources=["eea", "openaq"], view="stations"))
    assert result["agreement"]["same_time_window"] is False
    assert result["agreement"]["shared_top_result"] is None
    assert result["agreement"]["matched_measurements"] == []
