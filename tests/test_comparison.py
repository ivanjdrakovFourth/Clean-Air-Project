from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from clean_air.comparison import ComparisonError, Search, compare
from clean_air.settings import months_before, utc_today


def test_station_and_day_weighting_are_explicit(settings, seed):
    seed("Alpha", "A1", "2025-01-01", 10)
    seed("Alpha", "A2", "2025-01-01", 30)
    seed("Alpha", "A1", "2025-01-02", 20)
    seed("Alpha", "A2", "2025-01-02", 40)
    seed("Beta", "B1", "2025-01-01", 26)
    seed("Beta", "B1", "2025-01-02", 26)
    result = compare(settings, Search(start="2025-01-01", end="2025-01-02"))
    assert result["winners"][0]["city"] == "Alpha"
    assert result["winners"][0]["mean"] == 25
    assert result["winners"][0]["station_count"] == 2
    assert result["reference"]["daily_threshold"] == 15


def test_exact_threshold_and_custom_threshold_do_not_change_ranking(settings, seed):
    seed("Alpha", "A1", "2025-01-01", 15)
    seed("Beta", "B1", "2025-01-01", 15.0001)
    who = compare(settings, Search(start="2025-01-01", end="2025-01-01"))
    custom = compare(
        settings,
        Search(start="2025-01-01", end="2025-01-01", profile="custom", threshold=20),
    )
    assert who["winners"][0]["days_at_or_below_reference"] == 1
    assert who["rankings"][1]["days_above_reference"] == 1
    assert custom["winners"][0]["city"] == who["winners"][0]["city"]
    assert custom["rankings"][1]["days_at_or_below_reference"] == 1
    assert custom["policy_id"] != who["policy_id"]


def test_pollutant_selection_changes_winner_without_fallback(settings, seed):
    seed("Alpha", "A1", "2025-01-01", 5)
    seed("Beta", "B1", "2025-01-01", 10)
    seed("Alpha", "A1", "2025-01-01", 40, pollutant=5)
    seed("Beta", "B1", "2025-01-01", 20, pollutant=5)
    result = compare(settings, Search(start="2025-01-01", end="2025-01-01", pollutant="PM10"))
    assert result["winners"][0]["city"] == "Beta"
    assert result["reference"]["daily_threshold"] == 45


def test_period_coverage_boundary_and_no_zero_fill(settings, seed):
    for index in range(10):
        day = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        if index < 9:
            seed("Eligible", "A1", day, 20)
        if index < 8:
            seed("Excluded", "B1", day, 1)
    seed("Z history boundary", "Z1", "2025-01-10", 50, coverage=0.1)
    result = compare(settings, Search(start="2025-01-01", end="2025-01-10"))
    assert result["winners"][0]["city"] == "Eligible"
    assert result["winners"][0]["mean"] == 20
    assert result["winners"][0]["coverage_percent"] == 90
    assert result["excluded_cities"][0]["best_station_coverage_percent"] == 80
    assert result["winners"][0]["days_above_reference"] == 9


def test_hourly_coverage_boundary(settings, seed):
    seed("Enough", "A1", "2025-01-01", 10, coverage=0.75, kind="provisional hourly")
    seed("Too sparse", "B1", "2025-01-01", 1, coverage=17 / 24, kind="provisional hourly")
    result = compare(settings, Search(start="2025-01-01", end="2025-01-01"))
    assert result["winners"][0]["city"] == "Enough"
    assert len(result["excluded_cities"]) == 1
    assert result["winners"][0]["provisional_station_days"] == 1


def test_ties_are_returned_not_broken_alphabetically(settings, seed):
    seed("Alpha", "A1", "2025-01-01", 10)
    seed("Beta", "B1", "2025-01-01", 10)
    result = compare(settings, Search(start="2025-01-01", end="2025-01-01"))
    assert {city["city"] for city in result["winners"]} == {"Alpha", "Beta"}


def test_ranking_uses_unrounded_values(settings, seed):
    seed("Alpha", "A1", "2025-01-01", 10.004)
    seed("Beta", "B1", "2025-01-01", 10.003)
    result = compare(settings, Search(start="2025-01-01", end="2025-01-01"))
    assert [row["city"] for row in result["winners"]] == ["Beta"]


def test_no_eligible_city_is_an_explicit_error(settings, seed):
    seed("Alpha", "A1", "2025-01-01", 10, coverage=0.5)
    with pytest.raises(ComparisonError, match="No city") as error:
        compare(settings, Search(start="2025-01-01", end="2025-01-01"))
    assert error.value.code == "NO_ELIGIBLE_CITY"
    assert error.value.details["excluded_cities"]


def test_not_ready_is_not_a_fake_success(settings):
    with pytest.raises(ComparisonError) as error:
        compare(settings, Search(start="2025-01-01", end="2025-01-01"))
    assert error.value.status == 503


def test_outside_stored_history_is_explained(settings, seed):
    seed("Alpha", "A1", "2025-01-02", 10)
    with pytest.raises(ComparisonError) as error:
        compare(settings, Search(start="2025-01-01", end="2025-01-02"))
    assert error.value.code == "PERIOD_NOT_STORED"


@pytest.mark.parametrize("changes", [
    {"end": "2024-12-31"}, {"end": utc_today()},
    {"pollutant": "ozone"}, {"profile": "custom"},
    {"profile": "custom", "threshold": -1}, {"profile": "custom", "threshold": float("nan")},
    {"profile": "custom", "threshold": float("inf")}, {"threshold": 10},
])
def test_invalid_requests_are_rejected(changes):
    values = {"start": "2025-01-01", "end": "2025-01-01", **changes}
    with pytest.raises(ValidationError):
        Search.model_validate(values)


def test_zero_custom_threshold_is_valid():
    assert Search(
        start="2025-01-01", end="2025-01-01", profile="custom", threshold=0
    ).threshold == 0


def test_calendar_month_subtraction_handles_leap_years():
    assert months_before(date(2024, 2, 29), 12) == date(2023, 2, 28)
    assert months_before(date(2025, 3, 31), 1) == date(2025, 2, 28)
