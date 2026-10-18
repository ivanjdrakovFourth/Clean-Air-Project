from __future__ import annotations

import csv
import io
import logging
import math
import re
import time
import unicodedata
from datetime import UTC, date, datetime
from datetime import time as day_time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from .settings import EEA_API, EEA_BLOB_HOST, POLLUTANT_NAMES, POLLUTANTS, Settings

LOGGER = logging.getLogger(__name__)
POINT_PATTERN = re.compile(r"^SPO-(BG[A-Z0-9]+)_[A-Z0-9_]+$")
REQUIRED_COLUMNS = {
    "Samplingpoint", "Pollutant", "Start", "End", "Value",
    "Unit", "AggType", "Validity", "Verification", "ResultTime", "DataCapture",
}


class SourceError(RuntimeError):
    pass


def mass_unit_factor(unit: str) -> float:
    if not isinstance(unit, str):
        raise SourceError("A concentration unit is required.")
    normal = unicodedata.normalize("NFKC", unit).lower().replace(" ", "").replace("\u03bc", "u")
    if normal in ("ug.m-3", "ug/m3"):
        return 1
    if normal in ("mg.m-3", "mg/m3"):
        return 1000
    raise SourceError("Unsupported concentration unit. Gas volume ratios require additional physical assumptions.")


def point_identity(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise SourceError(f"Expected a sampling-point identifier, received {value!r}")
    point = value.removeprefix("BG/")
    match = POINT_PATTERN.fullmatch(point)
    if not match:
        raise SourceError(f"Unexpected Bulgarian sampling-point identifier: {value!r}")
    return point, match.group(1)


def validate_download_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != EEA_BLOB_HOST
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.query
        or not re.fullmatch(r"/airquality-p(?:-e1a)?/BG/SPO-BG[A-Z0-9_]+\.parquet", parsed.path)
    ):
        raise SourceError(f"The EEA returned an unexpected download location: {url!r}")


class EeaSource:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=settings.options.collection.request_timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": "CleanAirFinder/1.0 (local educational data analysis)"},
        )

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        attempts = self.settings.options.collection.request_attempts
        for attempt in range(attempts):
            try:
                response = self.client.request(method, url, **kwargs)
                if response.status_code == 304:
                    return response
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                retryable = not isinstance(error, httpx.HTTPStatusError) or (
                    error.response.status_code in (408, 429) or error.response.status_code >= 500
                )
                if not retryable or attempt + 1 == attempts:
                    raise SourceError(f"EEA request failed ({method} {url}): {error}") from error
                LOGGER.warning("EEA request attempt %s/%s failed: %s", attempt + 1, attempts, error)
                time.sleep(min(2 ** attempt, 8))
        raise AssertionError("Request retry loop did not return or raise")

    def cities(self) -> list[str]:
        try:
            payload = self.request("POST", f"{EEA_API}/City", json=["BG"]).json()
            values = payload if isinstance(payload, list) else payload["value"]
            cities = sorted({row["cityName"] for row in values if row["countryCode"] == "BG"})
        except (ValueError, KeyError, TypeError) as error:
            raise SourceError(f"Invalid EEA city metadata: {error}") from error
        if not cities or not all(isinstance(city, str) and city.strip() for city in cities):
            raise SourceError("The EEA did not return a usable Bulgarian city list.")
        return cities

    def city_points(self, city: str, dataset: int) -> list[tuple[str, str]]:
        payload = {
            "countries": ["BG"], "cities": [city], "pollutants": [],
            "dataset": dataset, "source": "CleanAirFinder",
        }
        try:
            rows = self.request(
                "POST", f"{EEA_API}/City/GetCountryCitySpos", json=payload
            ).json()
            values = rows if isinstance(rows, list) else rows["value"]
            return [point_identity(row["localid"]) for row in values]
        except (ValueError, KeyError, TypeError) as error:
            raise SourceError(f"Invalid EEA sampling-point metadata for {city}: {error}") from error

    def files(self, dataset: int) -> list[str]:
        # EEA date filters omitted: they excluded overlapping current files in a verified probe.
        payload = {
            "countries": ["BG"], "cities": [],
            "pollutants": [
                f"http://dd.eionet.europa.eu/vocabulary/aq/pollutant/{code}"
                for code in POLLUTANTS.values()
            ],
            "dataset": dataset, "source": "CleanAirFinder",
        }
        response = self.request("POST", f"{EEA_API}/ParquetFile/urls", json=payload)
        try:
            reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
            if reader.fieldnames != ["ParquetFileUrl"]:
                raise SourceError(f"Unexpected EEA download-list columns: {reader.fieldnames}")
            urls = sorted({row["ParquetFileUrl"].strip() for row in reader if row["ParquetFileUrl"]})
        except (UnicodeError, csv.Error, KeyError, TypeError) as error:
            raise SourceError(f"Invalid EEA file list: {error}") from error
        for url in urls:
            validate_download_url(url)
        return urls

    def download(self, url: str, path: Path, etag: str | None) -> str | None:
        validate_download_url(url)
        headers = {"If-None-Match": etag} if etag else {}
        response = self.request("GET", url, headers=headers)
        if response.status_code == 304:
            return None
        if len(response.content) > 256 * 1024 * 1024:
            raise SourceError("The EEA file exceeds the 256 MiB safety limit.")
        path.write_bytes(response.content)
        return response.headers.get("etag", "")


def epoch(value: datetime) -> int:
    if not isinstance(value, datetime):
        raise SourceError(f"Expected an EEA timestamp, received {value!r}")
    return int(value.replace(tzinfo=UTC).timestamp() if value.tzinfo is None else value.timestamp())


def read_observations(
    path: Path, window_start: date, window_end: date
) -> tuple[list[tuple[object, ...]], int, set[tuple[str, str]]]:
    output: list[tuple[object, ...]] = []
    points: set[tuple[str, str]] = set()
    rejected = 0
    lower = int(datetime.combine(window_start, day_time(), UTC).timestamp())
    upper = int(datetime.combine(window_end, day_time(), UTC).timestamp())
    try:
        source = pq.ParquetFile(path)
        missing = REQUIRED_COLUMNS - set(source.schema_arrow.names)
        if missing:
            raise SourceError(f"EEA file is missing required columns: {sorted(missing)}")
        for batch in source.iter_batches(batch_size=8192, columns=sorted(REQUIRED_COLUMNS)):
            for row in batch.to_pylist():
                started, ended = epoch(row["Start"]), epoch(row["End"])
                if started < lower or started >= upper:
                    continue
                if row["Pollutant"] not in POLLUTANT_NAMES:
                    rejected += 1
                    continue
                aggregation = row["AggType"]
                if aggregation not in ("hour", "day"):
                    rejected += 1
                    continue
                factor = mass_unit_factor(row["Unit"])
                start_time = datetime.fromtimestamp(started, UTC)
                duration = ended - started
                if (
                    duration != (3600 if aggregation == "hour" else 86400)
                    or start_time.minute != 0 or start_time.second != 0
                    or (aggregation == "day" and start_time.hour != 0)
                ):
                    rejected += 1
                    continue
                point_id, station_id = point_identity(row["Samplingpoint"])
                points.add((point_id, station_id))
                value = float(row["Value"]) * factor if row["Value"] is not None else None
                if value is not None and not math.isfinite(value):
                    value = None
                capture = float(row["DataCapture"]) if row["DataCapture"] is not None else None
                if capture is not None:
                    if not math.isfinite(capture) or not 0 <= capture <= 100:
                        raise SourceError(f"Invalid EEA DataCapture percentage: {capture!r}")
                    capture /= 100
                validity = int(row["Validity"])
                if value is None or value < 0 or validity <= 0:
                    rejected += 1
                output.append(
                    (
                        point_id, int(row["Pollutant"]), started, ended, value, aggregation,
                        validity, int(row["Verification"]), epoch(row["ResultTime"]), capture,
                    )
                )
    except (pa.ArrowException, OSError, TypeError, ValueError, OverflowError) as error:
        raise SourceError(f"Cannot interpret EEA Parquet file {path.name}: {error}") from error
    return output, rejected, points
