from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from clean_air.collector import Collector
from clean_air.database import connect
from clean_air.source import EeaSource, SourceError, point_identity, read_observations


def measurement(**overrides):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return {
        "Samplingpoint": "BG/SPO-BG0001A_06001_100",
        "Pollutant": 6001, "Start": start, "End": start + timedelta(hours=1),
        "Value": Decimal("12.5"), "Unit": "ug.m-3", "AggType": "hour",
        "Validity": 1, "Verification": 1, "ResultTime": start + timedelta(days=1),
        "DataCapture": None, **overrides,
    }


def parquet(path, rows):
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_realistic_parquet_normalizes_units_dates_and_invalid_values(settings):
    path = settings.var / "cache" / "fixture.parquet"
    parquet(path, [
        measurement(),
        measurement(Start=datetime(2024, 1, 1, tzinfo=UTC)),
        measurement(Value=Decimal("-1"), Validity=-1),
    ])
    rows, rejected, points = read_observations(path, date(2025, 1, 1), date(2025, 1, 2))
    assert len(rows) == 2
    assert rows[0][4] == 12.5
    assert rejected == 1
    assert points == {("SPO-BG0001A_06001_100", "BG0001A")}


def test_wrong_units_are_not_silently_interpreted(settings):
    path = settings.var / "cache" / "wrong-unit.parquet"
    parquet(path, [measurement(Unit="ppm")])
    with pytest.raises(SourceError, match="Unsupported"):
        read_observations(path, date(2025, 1, 1), date(2025, 1, 2))


def test_malformed_parquet_is_an_explicit_source_error(settings):
    path = settings.var / "cache" / "bad.parquet"
    path.write_bytes(b"not a parquet file")
    with pytest.raises(SourceError, match="Cannot interpret"):
        read_observations(path, date(2025, 1, 1), date(2025, 1, 2))


def test_misaligned_averaging_windows_are_excluded(settings):
    path = settings.var / "cache" / "misaligned.parquet"
    parquet(path, [measurement(End=datetime(2025, 1, 1, 2, tzinfo=UTC))])
    rows, rejected, _ = read_observations(path, date(2025, 1, 1), date(2025, 1, 2))
    assert rows == []
    assert rejected == 1


def test_download_inventory_uses_public_api_without_broken_date_filters(settings):
    source = EeaSource(settings)
    source.client.close()
    received = []

    def handle(request):
        received.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=b"\xef\xbb\xbfParquetFileUrl\n"
            b"https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/BG/SPO-BG0001A_06001_100.parquet\n",
        )

    source.client = httpx.Client(transport=httpx.MockTransport(handle))
    try:
        assert len(source.files(1)) == 1
        assert "dateTimeStart" not in received[0]
        assert received[0]["countries"] == ["BG"]
        assert received[0]["dataset"] == 1
    finally:
        source.close()


def test_source_retries_transient_failure_and_surfaces_final_error(settings, monkeypatch):
    import clean_air.source as module

    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    source = EeaSource(settings)
    source.client.close()
    attempts = []

    def handle(request):
        attempts.append(request)
        return httpx.Response(503, text="Source temporarily unavailable")

    source.client = httpx.Client(transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(SourceError, match="request failed"):
            source.request("GET", "https://example.invalid/")
        assert len(attempts) == settings.options.collection.request_attempts
    finally:
        source.close()


def test_conditional_download_skips_unchanged_files(settings):
    source = EeaSource(settings)
    source.client.close()

    def handle(request):
        assert request.headers["if-none-match"] == '"revision-1"'
        return httpx.Response(304)

    source.client = httpx.Client(transport=httpx.MockTransport(handle))
    try:
        result = source.download(
            "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/BG/SPO-BG0001A_06001_100.parquet",
            settings.var / "cache" / "unchanged.parquet",
            '"revision-1"',
        )
        assert result is None
        assert not (settings.var / "cache" / "unchanged.parquet").exists()
    finally:
        source.close()


@pytest.mark.parametrize("url", [
    "https://example.com/airquality.parquet",
    "http://eeadmz1batchservice02.blob.core.windows.net/airquality-p/BG/SPO-BG0001A_06001_100.parquet",
    "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/DE/SPO-BG0001A_06001_100.parquet",
])
def test_unexpected_download_destinations_are_rejected(url):
    from clean_air.source import validate_download_url

    with pytest.raises(SourceError):
        validate_download_url(url)


def test_sampling_point_is_mapped_to_physical_station():
    assert point_identity("BG/SPO-BG0053R_06001_100") == ("SPO-BG0053R_06001_100", "BG0053R")


def test_missing_point_identifier_is_an_explicit_source_error():
    with pytest.raises(SourceError, match="identifier"):
        point_identity(None)


def test_interrupted_file_is_retryable_and_reimport_is_idempotent(settings):
    class FixtureSource:
        calls = 0

        def download(self, url, path, etag):
            self.calls += 1
            if etag == '"same"':
                return None
            parquet(path, [measurement()])
            return '"same"'

    source = FixtureSource()
    collector = Collector(settings)
    url = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/BG/SPO-BG0001A_06001_100.parquet"
    arguments = (source, 1, url, date(2025, 1, 1), date(2025, 1, 2))
    assert collector._file(*arguments, force=False)[0] == 1
    assert collector._file(*arguments, force=False)[0] == 0
    assert collector._file(*arguments, force=True)[0] == 1
    with connect(settings.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        assert connection.execute("SELECT last_success FROM import_files").fetchone()[0]
    assert list((settings.var / "cache").iterdir()) == []
