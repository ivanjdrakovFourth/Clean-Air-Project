from __future__ import annotations

import calendar
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
POLLUTANTS = {"PM2.5": 6001, "PM10": 5}
POLLUTANT_NAMES = {value: key for key, value in POLLUTANTS.items()}
EEA_API = "https://eeadmz1-downloads-api-appservice.azurewebsites.net"
EEA_BLOB_HOST = "eeadmz1batchservice02.blob.core.windows.net"
EEA_DATA_URL = "https://eeadata.github.io/AQ.Portal/pages/download-data.html"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AppOptions(StrictModel):
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1024, le=65535)
    timezone: Literal["UTC"] = "UTC"


class CollectionOptions(StrictModel):
    enabled: bool = True
    history_months: int = Field(default=12, ge=12, le=36)
    refresh_seconds: int = Field(default=3600, ge=60)
    request_timeout_seconds: int = Field(default=90, ge=5, le=300)
    request_attempts: int = Field(default=3, ge=1, le=5)
    stale_after_hours: int = Field(default=48, ge=1)


class ComparisonOptions(StrictModel):
    minimum_hourly_coverage: float = Field(default=0.75, ge=0.5, le=1)
    minimum_period_coverage: float = Field(default=0.90, ge=0.5, le=1)
    default_pollutant: Literal["PM2.5", "PM10"] = "PM2.5"
    default_view: Literal["cities", "stations"] = "cities"
    default_objective: Literal["average", "reference_days", "improvement"] = "average"
    default_target: Literal["locations", "day", "hour"] = "locations"
    current_max_age_hours: int = Field(default=6, ge=1, le=24)
    baseline_minimum_days: int = Field(default=30, ge=7, le=365)


class MonitoringOptions(StrictModel):
    enabled: bool = True
    port: int = Field(default=9090, ge=1024, le=65535)
    retention_days: int = Field(default=7, ge=1, le=90)


class RetentionOptions(StrictModel):
    active_months: Literal[36] = 36


class Options(StrictModel):
    app: AppOptions = Field(default_factory=AppOptions)
    collection: CollectionOptions = Field(default_factory=CollectionOptions)
    comparison: ComparisonOptions = Field(default_factory=ComparisonOptions)
    monitoring: MonitoringOptions = Field(default_factory=MonitoringOptions)
    retention: RetentionOptions = Field(default_factory=RetentionOptions)

    @model_validator(mode="after")
    def different_ports(self) -> Options:
        if self.app.port == self.monitoring.port:
            raise ValueError("The application and Prometheus must use different ports.")
        return self


class Reference(StrictModel):
    name: str
    version: str
    source: str
    averaging_period: Literal["24-hour"]
    unit: Literal["ug/m3"]
    pm25_daily: float = Field(gt=0)
    pm10_daily: float = Field(gt=0)
    pm25_annual: float = Field(gt=0)
    pm10_annual: float = Field(gt=0)

    def daily_threshold(self, pollutant: str) -> float:
        return self.pm25_daily if pollutant == "PM2.5" else self.pm10_daily


@dataclass(frozen=True)
class Settings:
    root: Path
    options: Options
    reference: Reference

    @property
    def var(self) -> Path:
        return self.root / "var"

    @property
    def database(self) -> Path:
        return self.var / "data" / "clean_air.sqlite3"

    def prepare_directories(self) -> None:
        for name in ("data", "logs", "cache", "archives", "backups", "run", "tmp", "reports"):
            (self.var / name).mkdir(parents=True, exist_ok=True)


def load_settings(root: Path = ROOT) -> Settings:
    root = root.resolve()
    with (root / "config" / "settings.toml").open("rb") as handle:
        options = Options.model_validate(tomllib.load(handle))
    with (root / "config" / "profiles.toml").open("rb") as handle:
        reference = Reference.model_validate(tomllib.load(handle)["who_2021"])
    return Settings(root, options, reference)


def months_before(day: date, months: int) -> date:
    absolute = day.year * 12 + day.month - 1 - months
    year, month = divmod(absolute, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def utc_today() -> date:
    return datetime.now(UTC).date()


def timestamp_now() -> int:
    return int(datetime.now(UTC).timestamp())
