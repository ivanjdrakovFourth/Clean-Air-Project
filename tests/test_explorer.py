from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import pytest
from conftest import raw_observation
from pydantic import ValidationError
from test_sources import source_day

from clean_air.data_explorer import DataQuery, browse, csv_rows
from clean_air.database import connect
from clean_air.provider_store import for_provider, prepare_sources


@pytest.fixture
def stores(settings):
    prepare_sources(settings)
    source_day(settings, "eea", "Alpha", "A1", "2025-01-01", 2)
    source_day(settings, "openaq", "Alpha", "OA1", "2025-01-01", 50)
    return settings


def test_explorer_reads_only_selected_source_with_human_column_labels(stores):
    first = browse(stores, DataQuery(source="eea", pollutant="PM2.5", city="Alpha"))
    second = browse(stores, DataQuery(source="openaq", pollutant="PM2.5", city="Alpha"))
    assert first["rows"][0]["mean"] == 2
    assert second["rows"][0]["mean"] == 50
    assert first["column_labels"]["mean"] == "Daily mean (ug/m3)"
    assert browse(stores, DataQuery(source="bg_direct"))["total"] == 0


def test_pagination_is_bounded_and_complete(stores):
    for index in range(30):
        source_day(stores, "eea", "Alpha", "A1", f"2025-01-{index + 1:02d}", index)
    first = browse(stores, DataQuery(city="Alpha", page=1))
    second = browse(stores, DataQuery(city="Alpha", page=2))
    assert first["total"] == 30
    assert len(first["rows"]) == 25
    assert len(second["rows"]) == 5
    assert {row["day"] for row in first["rows"]}.isdisjoint(row["day"] for row in second["rows"])


def test_table_city_station_and_date_filters_are_bound_not_sql(stores):
    selected = browse(stores, DataQuery(city="Alpha", station="A1",
                                       start="2025-01-01", end="2025-01-01", pollutant="PM2.5"))
    assert selected["total"] == 1
    assert browse(stores, DataQuery(city="' OR 1=1 --"))["total"] == 0
    with pytest.raises(ValidationError):
        DataQuery(table="sqlite_schema; DROP TABLE observations")


def test_raw_end_date_includes_the_full_selected_day(stores):
    with connect(stores.database) as connection, connection:
        raw_observation(connection, city="Alpha", aggregation="hour", day="2025-01-01", hour=23)
        raw_observation(connection, city="Alpha", aggregation="hour", day="2025-01-02", hour=0)
    result = browse(stores, DataQuery(table="observations", start="2025-01-01", end="2025-01-01"))
    assert result["total"] == 1
    assert datetime.fromtimestamp(result["rows"][0]["started"], UTC).hour == 23


def test_csv_exports_all_filtered_rows_not_only_current_page(stores):
    for index in range(30):
        source_day(stores, "openaq", "Alpha", "OA1", f"2025-01-{index + 1:02d}", index)
    text = "".join(csv_rows(stores, DataQuery(source="openaq", city="Alpha")))
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    assert len(rows) == 30
    assert {row["station_id"] for row in rows} == {"OA1"}


def test_csv_neutralizes_spreadsheet_formula_strings_and_redacts_credential_metadata(stores):
    with connect(stores.database) as connection, connection:
        connection.execute("UPDATE station_metadata SET name='=1+1' WHERE station_id='A1'")
        connection.execute("INSERT INTO metadata VALUES ('api_key','do-not-export-this')")
    text = "".join(csv_rows(stores, DataQuery(table="station_metadata", station="A1")))
    assert "'=1+1" in text
    secret = "".join(csv_rows(stores, DataQuery(table="metadata")))
    assert "do-not-export-this" not in secret
    assert "redacted" in secret


def test_browsing_is_read_only_and_rejects_inapplicable_filters(stores):
    path = for_provider(stores, "openaq").database
    with connect(path) as connection:
        before = connection.execute("SELECT COUNT(*) FROM station_days").fetchone()[0]
    browse(stores, DataQuery(source="openaq"))
    with connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM station_days").fetchone()[0] == before
    with pytest.raises(ValidationError):
        DataQuery(table="cities", pollutant="PM10")
