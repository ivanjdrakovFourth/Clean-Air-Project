from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .database import connect
from .settings import StrictModel

NATIONAL_URL = "https://www.eea.government.bg/kav/"


class Station(StrictModel):
    station_id: str = Field(pattern=r"^BG[A-Z0-9]+$")
    national_code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    latitude: float = Field(ge=41, le=45)
    longitude: float = Field(ge=22, le=29)
    area_type: str
    station_type: str


class StationCatalog(StrictModel):
    version: Literal[1]
    verified_on: date
    national_source: str
    identifier_source: str
    join: str
    measurement_notice: str
    stations: list[Station] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_stations(self) -> StationCatalog:
        ids = [station.station_id for station in self.stations]
        if len(set(ids)) != len(ids):
            raise ValueError("Station catalog contains duplicate physical station IDs.")
        if self.national_source != (
            "https://www.eea.government.bg/kav/ajax/getStationsForMapJson?hours=24"
        ):
            raise ValueError("Unexpected national station metadata source.")
        return self


def apply_catalog(database: Path, catalog_path: Path) -> int:
    catalog = StationCatalog.model_validate(json.loads(catalog_path.read_text("utf-8")))
    with connect(database) as connection, connection:
        for station in catalog.stations:
            connection.execute(
                "INSERT OR IGNORE INTO stations(id) VALUES (?)", (station.station_id,)
            )
            connection.execute(
                """
                INSERT INTO station_metadata
                    (station_id,national_code,name,address,latitude,longitude,
                     area_type,station_type,verified_on,source_url)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(station_id) DO UPDATE SET
                    national_code=excluded.national_code, name=excluded.name,
                    address=excluded.address, latitude=excluded.latitude,
                    longitude=excluded.longitude, area_type=excluded.area_type,
                    station_type=excluded.station_type, verified_on=excluded.verified_on,
                    source_url=excluded.source_url
                """,
                (
                    station.station_id, station.national_code, station.name, station.address,
                    station.latitude, station.longitude, station.area_type, station.station_type,
                    catalog.verified_on.isoformat(), NATIONAL_URL,
                ),
            )
    return len(catalog.stations)
