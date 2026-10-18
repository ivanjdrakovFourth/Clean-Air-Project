from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import raw_observation
from pydantic import ValidationError

from clean_air.comparison import ComparisonError, Search, compare
from clean_air.database import connect, rebuild_station_days
from clean_air.station_catalog import StationCatalog, apply_catalog


def add_station_metadata(settings, station, name, address="A measured station address"):
    with connect(settings.database) as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO station_metadata VALUES (?,?,?,?,?,?,?,?,?,?)",
            (station, "test", name, address, 42.7, 23.3, "urban", "background",
             "2026-10-18", "https://www.eea.government.bg/kav/"),
        )


def test_catalog_contains_verified_station_sites_not_measurement_zeros(settings):
    assert apply_catalog(
        settings.database, settings.root / "config" / "bulgarian-stations.json"
    ) == 32
    with connect(settings.database) as connection:
        row = connection.execute(
            "SELECT * FROM station_metadata WHERE station_id='BG0043A'"
        ).fetchone()
        assert row["name"] == "Враца - АИС жп гара"
        assert "Войводов" in row["address"]
        assert row["latitude"] == 43.207978
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_catalog_rejects_duplicate_station_identity(settings):
    import json

    values = json.loads((settings.root / "config" / "bulgarian-stations.json").read_text("utf-8"))
    values["stations"].append(values["stations"][0])
    with pytest.raises(ValidationError, match="duplicate"):
        StationCatalog.model_validate(values)


def test_named_station_view_preserves_addresses_and_separates_city_average(settings, seed):
    seed("Alpha", "A1", "2025-01-01", 1)
    seed("Alpha", "A2", "2025-01-01", 99)
    seed("Beta", "B1", "2025-01-01", 30)
    add_station_metadata(settings, "A1", "Alpha park monitor")
    add_station_metadata(settings, "A2", "Alpha traffic monitor")
    add_station_metadata(settings, "B1", "Beta monitor")
    station_result = compare(
        settings, Search(start="2025-01-01", end="2025-01-01", view="stations")
    )
    city_result = compare(
        settings, Search(start="2025-01-01", end="2025-01-01", view="cities")
    )
    assert station_result["winners"][0]["name"] == "Alpha park monitor"
    assert station_result["winners"][0]["location"]["address"] == "A measured station address"
    assert city_result["winners"][0]["city"] == "Beta"
    assert station_result["source"]["station_metadata_url"].endswith("/kav/")


def test_reference_day_definition_can_choose_a_different_location(settings, seed):
    for index, (alpha, beta) in enumerate(((0, 14), (20, 14), (20, 14)), start=1):
        day = f"2025-01-0{index}"
        seed("Alpha", "A1", day, alpha)
        seed("Beta", "B1", day, beta)
    average = compare(settings, Search(start="2025-01-01", end="2025-01-03"))
    reference = compare(
        settings, Search(start="2025-01-01", end="2025-01-03", objective="reference_days")
    )
    assert average["winners"][0]["city"] == "Alpha"
    assert reference["winners"][0]["city"] == "Beta"
    assert reference["title"] == "Most days within your daily reference"
    assert reference["winners"][0]["days_within_reference_percent"] == 100


def test_improvement_uses_earlier_history_and_is_not_labelled_cleanest(settings, seed):
    for index in range(30):
        day = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        seed("Alpha", "A1", day, 100)
        seed("Beta", "B1", day, 10)
    seed("Alpha", "A1", "2025-02-01", 40)
    seed("Beta", "B1", "2025-02-01", 5)
    average = compare(settings, Search(start="2025-02-01", end="2025-02-01"))
    result = compare(
        settings, Search(start="2025-02-01", end="2025-02-01", objective="improvement")
    )
    assert average["winners"][0]["city"] == "Beta"
    assert result["winners"][0]["city"] == "Alpha"
    assert result["winners"][0]["baseline_mean"] == 100
    assert result["winners"][0]["baseline_days"] == 30
    assert result["winners"][0]["improvement_percent"] == 60
    assert "Cleanest" not in result["title"]
    assert any("does not mean lowest" in item for item in result["warnings"])


def test_no_improvement_result_is_invented_without_baseline(settings, seed):
    seed("Alpha", "A1", "2025-01-01", 5)
    with pytest.raises(ComparisonError, match="sufficient") as error:
        compare(
            settings, Search(start="2025-01-01", end="2025-01-01", objective="improvement")
        )
    assert error.value.details["excluded_cities"]


def test_cleanest_day_is_separate_from_cleanest_period_average(settings, seed):
    seed("Alpha", "A1", "2025-01-01", 1)
    seed("Alpha", "A1", "2025-01-02", 19)
    seed("Beta", "B1", "2025-01-01", 5)
    seed("Beta", "B1", "2025-01-02", 5)
    average = compare(settings, Search(start="2025-01-01", end="2025-01-02"))
    day = compare(settings, Search(start="2025-01-01", end="2025-01-02", target="day"))
    assert average["winners"][0]["city"] == "Beta"
    assert day["winners"][0]["city"] == "Alpha"
    assert day["winners"][0]["mean"] == 1
    assert datetime.fromtimestamp(day["winners"][0]["time_start"], UTC).date() == date(2025, 1, 1)
    assert day["title"] == "Cleanest measured day"


def test_hour_search_needs_real_hours_not_reported_daily_averages(settings, seed):
    seed("Alpha", "A1", "2025-01-01", 2)
    with pytest.raises(ComparisonError) as error:
        compare(settings, Search(start="2025-01-01", end="2025-01-01", target="hour"))
    assert error.value.code == "NO_HOURLY_HISTORY"


def test_hour_search_returns_actual_lowest_hour_and_no_daily_reference_label(settings, seed):
    seed("Alpha", "BG0001A", "2025-01-01", 7)
    with connect(settings.database) as connection, connection:
        for hour in range(24):
            raw_observation(
                connection, station="BG0001A", city="Alpha", aggregation="hour",
                hour=hour, value=1 if hour == 3 else 7,
            )
        rebuild_station_days(connection, "BG0001A")
    result = compare(settings, Search(start="2025-01-01", end="2025-01-01", target="hour"))
    assert result["winners"][0]["mean"] == 1
    assert datetime.fromtimestamp(result["winners"][0]["time_start"], UTC).hour == 3
    assert result["reference"]["applicable"] is False
    assert result["winners"][0]["days_within_reference_percent"] is None


def test_no_dates_uses_latest_common_hour_not_unrelated_reading_times(settings, seed, monkeypatch):
    now = int(datetime(2025, 1, 2, 12, 30, tzinfo=UTC).timestamp())
    monkeypatch.setattr("clean_air.comparison.timestamp_now", lambda: now)
    seed("Alpha", "BG0001A", "2025-01-02", 5)
    seed("Beta", "BG0002A", "2025-01-02", 10)
    with connect(settings.database) as connection, connection:
        raw_observation(
            connection, station="BG0001A", city="Alpha", day="2025-01-02",
            aggregation="hour", hour=10, value=5,
        )
        raw_observation(
            connection, station="BG0002A", point="SPO-BG0002A_06001_100", city="Beta",
            day="2025-01-02", aggregation="hour", hour=10, value=10,
        )
        raw_observation(
            connection, station="BG0001A", day="2025-01-02",
            aggregation="hour", hour=11, value=100,
        )
        raw_observation(
            connection, station="BG0002A", point="SPO-BG0002A_06001_100", city="Beta",
            day="2025-01-02", aggregation="hour", hour=12, value=0,
        )
    result = compare(settings, Search())
    assert result["period"]["mode"] == "current"
    assert datetime.fromtimestamp(result["period"]["time_start"], UTC).hour == 10
    assert result["winners"][0]["city"] == "Alpha"
    assert len(result["rankings"]) == 2
    assert all(row["time_start"] == result["period"]["time_start"] for row in result["rankings"])
    assert result["reference"]["applicable"] is False


def test_stale_readings_are_not_passed_off_as_current(settings, seed):
    seed("Alpha", "BG0001A", "2025-01-01", 1)
    with connect(settings.database) as connection, connection:
        raw_observation(connection, aggregation="hour", value=0)
    with pytest.raises(ComparisonError) as error:
        compare(settings, Search())
    assert error.value.code == "NO_FRESH_COMPARABLE_HOUR"


def test_location_filter_is_explicit_and_checked(settings, seed):
    seed("Alpha", "A1", "2025-01-01", 10)
    seed("Beta", "B1", "2025-01-01", 1)
    result = compare(
        settings, Search(start="2025-01-01", end="2025-01-01", location="Alpha")
    )
    assert result["winners"][0]["city"] == "Alpha"
    with pytest.raises(ComparisonError) as error:
        compare(settings, Search(start="2025-01-01", end="2025-01-01", location="Unknown"))
    assert error.value.code == "UNKNOWN_LOCATION"


@pytest.mark.parametrize("options", [
    {"target": "day"}, {"target": "hour"}, {"objective": "reference_days"},
    {"objective": "improvement"},
])
def test_current_mode_does_not_silently_change_requested_definition(settings, options):
    with pytest.raises(ComparisonError) as error:
        compare(settings, Search(**options))
    assert error.value.code == "DATES_REQUIRED"


def test_partial_dates_are_rejected_not_interpreted_as_now():
    with pytest.raises(ValidationError, match="both dates"):
        Search(start="2025-01-01")


def test_time_search_does_not_apply_incompatible_period_objective(settings):
    with pytest.raises(ComparisonError) as error:
        compare(
            settings, Search(start="2025-01-01", end="2025-01-01",
                             target="hour", objective="reference_days")
        )
    assert error.value.code == "INCOMPATIBLE_DEFINITION"


def test_time_result_limit_discloses_full_scope_without_falsely_excluding_locations(settings, seed):
    for index in range(30):
        day = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        seed("Alpha", "A1", day, 1)
        seed("Beta", "B1", day, 2)
    result = compare(settings, Search(start="2025-01-01", end="2025-01-30", target="day"))
    assert len(result["rankings"]) == 50
    assert result["total_ranked_results"] == 60
    assert result["eligible_location_count"] == 2
    assert result["results_limited"] is True
    assert result["excluded_locations"] == []
    assert any("additional tied" in item for item in result["warnings"])


def test_true_timestamped_source_valid_zero_is_not_confused_with_a_placeholder(settings, seed, monkeypatch):
    now = int(datetime(2025, 1, 2, 12, 30, tzinfo=UTC).timestamp())
    monkeypatch.setattr("clean_air.comparison.timestamp_now", lambda: now)
    seed("Alpha", "BG0001A", "2025-01-02", 0)
    seed("Beta", "BG0002A", "2025-01-02", 10)
    with connect(settings.database) as connection, connection:
        for station, city, value in (("BG0001A", "Alpha", 0), ("BG0002A", "Beta", 10)):
            raw_observation(
                connection, station=station, point=f"SPO-{station}_06001_100", city=city,
                day="2025-01-02", aggregation="hour", hour=11, value=value,
            )
    result = compare(settings, Search())
    assert result["winners"][0]["city"] == "Alpha"
    assert result["winners"][0]["mean"] == 0
    assert any("reported as zero" in item for item in result["warnings"])


def test_fresh_single_location_filter_is_not_claimed_as_countrywide_comparison(settings, seed, monkeypatch):
    now = int(datetime(2025, 1, 2, 12, 30, tzinfo=UTC).timestamp())
    monkeypatch.setattr("clean_air.comparison.timestamp_now", lambda: now)
    seed("Alpha", "BG0001A", "2025-01-02", 5)
    with connect(settings.database) as connection, connection:
        raw_observation(
            connection, city="Alpha", day="2025-01-02", aggregation="hour", hour=11, value=5
        )
    result = compare(settings, Search(location="Alpha"))
    assert result["eligible_location_count"] == 1
    assert any("not a country-wide winner" in item for item in result["warnings"])


def test_declining_locations_are_explicitly_described_as_no_improvement(settings, seed):
    for index in range(30):
        seed("Alpha", "A1", (date(2025, 1, 1) + timedelta(days=index)).isoformat(), 5)
    seed("Alpha", "A1", "2025-02-01", 10)
    result = compare(
        settings, Search(start="2025-02-01", end="2025-02-01", objective="improvement")
    )
    assert result["winners"][0]["improvement_percent"] == -100
    assert any("No compared location improved" in item for item in result["warnings"])


def test_current_requests_and_default_page_do_not_silently_become_historical(settings, seed):
    from fastapi.testclient import TestClient

    from clean_air.web import create_app

    seed("Alpha", "A1", "2025-01-01", 1)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.get("/")
        assert response.status_code == 422
        assert 'name="start" type="date" value=""' in response.text
        assert "No common recent hour" in response.text
        assert "Search settings" in response.text
        assert "LESS POLLUTION = CLEANER AIR" not in response.text


def test_city_period_mean_weights_days_equally_when_reporting_station_counts_vary(settings, seed):
    for index in range(10):
        day = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        seed("Alpha", "A1", day, 10)
        seed("Beta", "B1", day, 19.2)
        if index < 9:
            seed("Alpha", "A2", day, 30)
    result = compare(settings, Search(start="2025-01-01", end="2025-01-10"))
    assert result["winners"][0]["city"] == "Alpha"
    assert result["winners"][0]["mean"] == 19
    assert result["winners"][0]["station_count"] == 2


def test_improvement_compares_the_same_physical_station_cohort_in_both_periods(settings, seed):
    for index in range(30):
        day = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        seed("Alpha", "A1", day, 100)
        if index < 29:
            seed("Alpha", "A2", day, 5)
    seed("Alpha", "A1", "2025-02-01", 50)
    seed("Alpha", "A2", "2025-02-01", 1)
    result = compare(
        settings, Search(start="2025-02-01", end="2025-02-01", objective="improvement")
    )
    assert result["winners"][0]["stations"] == ["A1"]
    assert result["winners"][0]["mean"] == 50
    assert result["winners"][0]["baseline_mean"] == 100
    assert result["winners"][0]["improvement_percent"] == 50


def test_latest_hour_deduplicates_sampling_methods_before_ranking(settings, seed, monkeypatch):
    now = int(datetime(2025, 1, 2, 12, 30, tzinfo=UTC).timestamp())
    monkeypatch.setattr("clean_air.comparison.timestamp_now", lambda: now)
    seed("Alpha", "BG0001A", "2025-01-02", 10)
    seed("Beta", "BG0002A", "2025-01-02", 5)
    with connect(settings.database) as connection, connection:
        for station, point, city, value, priority in (
            ("BG0001A", "SPO-BG0001A_06001_100", "Alpha", 10, 2),
            ("BG0001A", "SPO-BG0001A_06001_500", "Alpha", 0, 1),
            ("BG0002A", "SPO-BG0002A_06001_100", "Beta", 5, 2),
        ):
            raw_observation(
                connection, station=station, point=point, city=city, value=value,
                priority=priority, day="2025-01-02", aggregation="hour", hour=11,
            )
    result = compare(settings, Search())
    assert result["winners"][0]["city"] == "Beta"
    assert next(row for row in result["rankings"] if row["city"] == "Alpha")["mean"] == 10
