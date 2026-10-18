from __future__ import annotations

import csv
import io
import math
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import Field, model_validator

from .provider_store import PROVIDER_NAMES, for_provider
from .settings import POLLUTANT_NAMES, POLLUTANTS, Pollutant, Provider, Settings, StrictModel

TABLES = {
    "station_days": "Daily station averages",
    "observations": "Source measurements (including excluded records)",
    "stations": "Physical monitoring stations",
    "station_metadata": "Names, addresses and coordinates",
    "cities": "Reported city groupings",
    "sampling_points": "Sensors and sampling points",
    "import_files": "Source files and import checkpoints",
    "collection_runs": "Collection attempts and progress",
    "archives": "Verified cold-storage archives",
    "metadata": "Internal source collection state",
}
Table = Literal[
    "station_days", "observations", "stations", "station_metadata", "cities",
    "sampling_points", "import_files", "collection_runs", "archives", "metadata",
]


class DataQuery(StrictModel):
    source: Provider = "eea"
    table: Table = "station_days"
    start: date | None = None
    end: date | None = None
    city: str | None = Field(default=None, max_length=160)
    station: str | None = Field(default=None, max_length=160)
    pollutant: Pollutant | None = None
    page: int = Field(default=1, ge=1, le=1000000)
    page_size: int = Field(default=25, ge=25, le=100)

    @model_validator(mode="after")
    def ordered(self):
        if self.page_size not in (25, 50, 100):
            raise ValueError("Choose 25, 50 or 100 rows per page.")
        if self.start and self.end and self.start > self.end:
            raise ValueError("The start date must not be after the end date.")
        temporal = self.table in ("observations", "station_days")
        if not temporal and (self.start or self.end or self.pollutant):
            raise ValueError("Date and pollutant filters apply to measurement/daily-average tables.")
        if self.table not in ("observations", "station_days", "stations", "station_metadata", "sampling_points") and (self.city or self.station):
            raise ValueError("This table does not have city/station filters.")
        return self


def _query(query: DataQuery) -> tuple[str, str, list, str]:
    conditions = []
    values = []
    if query.table == "observations":
        base = (
            "observations t JOIN sampling_points p ON p.id=t.point_id "
            "JOIN stations s ON s.id=p.station_id LEFT JOIN station_metadata m ON m.station_id=s.id"
        )
        columns = "s.city,m.name AS station_name,m.address,t.*"
        order = "t.started DESC,t.point_id"
    elif query.table == "station_days":
        base = "station_days t JOIN stations s ON s.id=t.station_id LEFT JOIN station_metadata m ON m.station_id=s.id"
        columns = "s.city,m.name AS station_name,m.address,t.*"
        order = "t.day DESC,t.station_id,t.pollutant"
    elif query.table == "stations":
        base = "stations s LEFT JOIN station_metadata m ON m.station_id=s.id"
        columns = "s.id,s.city,m.name,m.address,m.latitude,m.longitude,m.station_type,m.area_type"
        order = "s.city,s.id"
    elif query.table == "station_metadata":
        base = "station_metadata t JOIN stations s ON s.id=t.station_id"
        columns = "s.city,t.*"
        order = "t.name"
    elif query.table == "sampling_points":
        base = "sampling_points t JOIN stations s ON s.id=t.station_id"
        columns = "s.city,t.*"
        order = "t.station_id,t.id"
    else:
        base = query.table + " t"
        columns = "t.*"
        order = (
            "t.started DESC" if query.table == "collection_runs"
            else "t.created DESC" if query.table == "archives"
            else "t.name" if query.table == "cities"
            else "t.key" if query.table == "metadata" else "t.id"
        )
    if query.city:
        conditions.append("s.city=?")
        values.append(query.city)
    if query.station:
        conditions.append("s.id=?")
        values.append(query.station)
    if query.pollutant:
        conditions.append("t.pollutant=?")
        values.append(POLLUTANTS[query.pollutant])
    if query.table == "observations":
        if query.start:
            conditions.append("t.started>=?")
            values.append(int(datetime.combine(query.start, datetime.min.time(), UTC).timestamp()))
        if query.end:
            conditions.append("t.started<?")
            values.append(int(datetime.combine(query.end + timedelta(days=1), datetime.min.time(), UTC).timestamp()))
    elif query.table == "station_days":
        if query.start:
            conditions.append("t.day>=?")
            values.append(query.start.isoformat())
        if query.end:
            conditions.append("t.day<=?")
            values.append(query.end.isoformat())
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return f"SELECT {columns} FROM {base}{where}", f"SELECT COUNT(*) FROM {base}{where}", values, order


def browse(settings: Settings, query: DataQuery) -> dict:
    configured = for_provider(settings, query.source)
    select, count, parameters, order = _query(query)
    with closing(sqlite3.connect(configured.database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        total = connection.execute(count, parameters).fetchone()[0]
        cursor = connection.execute(
            select + f" ORDER BY {order} LIMIT ? OFFSET ?",
            (*parameters, query.page_size, (query.page - 1) * query.page_size),
        )
        rows = []
        for record in cursor:
            row = dict(record)
            if query.table == "metadata" and any(
                word in str(row.get("key", "")).lower() for word in ("api_key", "password", "secret", "credential")
            ):
                row["value"] = "[credential-like metadata redacted]"
            for key, value in row.items():
                if isinstance(value, str) and len(value) > 2000:
                    row[key] = value[:2000] + " ... [preview shortened; CSV contains the full non-secret field]"
            rows.append(row)
        columns = [column[0] for column in cursor.description]
    return {
        "source": query.source, "source_name": PROVIDER_NAMES[query.source],
        "table": query.table, "table_name": TABLES[query.table],
        "columns": columns,
        "column_labels": {
            column: {
                "started": "Interval / operation start (UTC)", "ended": "Interval end (UTC)",
                "result_time": "Source revision time (UTC)", "last_success": "Last import (UTC)",
                "mean": "Daily mean (ug/m3)", "value": "Concentration (ug/m3)" if query.table == "observations" else "Saved value",
                "point_id": "Sensor / sampling point", "station_id": "Physical station ID",
                "data_capture": "Reported capture", "coverage": "Valid-hour coverage",
                "validity": "Source validity flag", "verification": "Source verification flag",
                "file_id": "Import provenance ID", "kind": "How the daily value was derived",
                "created": "Created (UTC)", "finished": "Finished (UTC)",
            }.get(column, column.replace("_", " "))
            for column in columns
        },
        "rows": rows, "total": total,
        "page": query.page, "page_size": query.page_size,
        "pages": max(1, math.ceil(total / query.page_size)),
        "notice": "Read-only source-specific records. Concentrations are normalized to ug/m3. Raw excluded records are not used in rankings.",
    }


def csv_rows(settings: Settings, query: DataQuery):
    configured = for_provider(settings, query.source)
    select, _, parameters, order = _query(query)
    with closing(sqlite3.connect(configured.database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(select + f" ORDER BY {order}", parameters)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(column[0] for column in cursor.description)
        yield "\ufeff" + buffer.getvalue()
        for record in cursor:
            row = dict(record)
            if query.table == "metadata" and any(
                word in str(row.get("key", "")).lower() for word in ("api_key", "password", "secret", "credential")
            ):
                row["value"] = "[credential-like metadata redacted]"
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow(
                "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
                else value for value in row.values()
            )
            yield buffer.getvalue()


def display_cell(value, column: str) -> str:
    if value is None:
        return "Not supplied"
    if column == "pollutant":
        return POLLUTANT_NAMES.get(value, str(value))
    if column in ("started", "ended", "result_time", "created", "finished", "last_success"):
        return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M UTC")
    if column in ("coverage", "data_capture"):
        return f"{value * 100:.1f}%"
    return str(value)
