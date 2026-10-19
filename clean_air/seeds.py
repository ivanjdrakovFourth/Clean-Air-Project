from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .archive import inspect_archive
from .database import (
    OBSERVATION_CONFLICT,
    SCHEMA_VERSION,
    backup,
    connect,
    get_metadata,
    rebuild_station_days,
    set_metadata,
)
from .provider_store import for_provider
from .settings import Provider, Settings, months_before, timestamp_now, utc_today
from .shutdown_control import workspace_lock

LOGGER = logging.getLogger(__name__)
SEED_PROVIDERS: tuple[Provider, ...] = ("eea", "openaq", "bg_direct")
METADATA_KEYS = {
    "initial_import_complete", "openaq_backfill_complete", "history_requested_start",
    "last_success", "latest_observation", "openaq_catalog", "data_provider",
    "metadata_updated", "restored_source_windows",
    "bg_direct_backfill_complete", "bg_csv_cycle", "bg_csv_time_policy",
}


def shared_metadata(key: str) -> bool:
    return key in METADATA_KEYS or key.startswith(("station_provider:", "openaq_cursor:", "bg_csv_cursor:"))


def seed_providers(settings: Settings) -> tuple[Provider, ...]:
    return SEED_PROVIDERS if settings.options.bg_direct.enabled else ("eea", "openaq")


def file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verified_archive(folder: Path, record: dict, provider: Provider) -> Path:
    name = record["name"]
    if Path(name).name != name or not name.endswith(".jsonl.gz"):
        raise RuntimeError("A source archive has an invalid filename.")
    path = (folder / name).resolve()
    if not path.is_relative_to(folder.resolve()) or file_digest(path) != record["sha256"]:
        raise RuntimeError("A source archive checksum does not match its manifest.")
    if path.with_suffix(".gz.sha256").read_text("ascii").strip() != record["sha256"]:
        raise RuntimeError("A source archive checksum sidecar is missing or inconsistent.")
    header, counts = inspect_archive(path)
    if header.get("provider", "eea") != provider or header.get("counts") != counts or counts != {
        "observations": record["observations"], "station_days": record["station_days"],
    }:
        raise RuntimeError("A source archive identity or record count is invalid.")
    return path


def verify_complete_history(settings: Settings, path: Path | None = None) -> None:
    with connect(path or settings.database) as connection:
        if get_metadata(connection, "data_provider") != settings.provider:
            raise RuntimeError("The measurement snapshot belongs to another source.")
        complete = bool(get_metadata(connection, "initial_import_complete", False))
        if settings.provider == "bg_direct":
            cycle = get_metadata(connection, "bg_csv_cycle", {})
            complete = complete and bool(get_metadata(connection, "bg_direct_backfill_complete", False)) and not cycle.get("pending")
        months = (
            settings.options.openaq.history_months or settings.options.collection.history_months
            if settings.provider == "openaq" else settings.options.collection.history_months
        )
        required_start = months_before(utc_today(), months)
        required_epoch = int(datetime.combine(required_start, datetime.min.time(), UTC).timestamp())
        if settings.provider == "openaq":
            complete = complete and bool(get_metadata(connection, "openaq_backfill_complete", False))
            catalog = get_metadata(connection, "openaq_catalog", {})
            sensors = catalog.get("sensors", [])
            if not sensors:
                complete = False
            for sensor in sensors:
                cursor = get_metadata(connection, f"openaq_cursor:{sensor['id']}", None)
                floor = max(required_epoch, sensor["first"] // 3600 * 3600)
                if (
                    not cursor or cursor["next_to"] > cursor["lower"]
                    or cursor["requested_start"] > required_epoch
                    or cursor.get("history_floor", cursor["lower"]) > floor
                ):
                    complete = False
        run = connection.execute(
            "SELECT status FROM collection_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        bundled = get_metadata(connection, "bundled_seed_info", {})
        requested = get_metadata(connection, "history_requested_start", None)
        if not requested or requested > required_start.isoformat():
            complete = False
        if not complete or (run and run["status"] != "completed") or (
            not run and not bundled.get("history_complete")
        ):
            raise RuntimeError(
                f"{settings.provider} historical collection has not finished for the configured window. "
                "Ready-to-use publication was refused."
            )
        if not connection.execute("SELECT 1 FROM observations LIMIT 1").fetchone():
            raise RuntimeError(f"{settings.provider} has no measurement records.")


def sanitized_snapshot(settings: Settings, output: Path, *, require_complete: bool = True) -> dict:
    backup(settings.database, output)
    if require_complete:
        verify_complete_history(settings, output)
    with connect(output) as connection:
        connection.execute("PRAGMA secure_delete=ON")
        with connection:
            for row in connection.execute("SELECT key FROM metadata").fetchall():
                if not shared_metadata(row["key"]):
                    connection.execute("DELETE FROM metadata WHERE key=?", (row["key"],))
            connection.execute("DELETE FROM collection_runs")
            set_metadata(connection, "bundled_seed_info", {
                "created": timestamp_now(), "history_complete": require_complete,
                "requested_start": get_metadata(connection, "history_requested_start"),
                "source_last_success": get_metadata(connection, "last_success"),
                "notice": "Bundled measurements; this does not prove a live fetch on this computer.",
            })
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("VACUUM")
        bounds = connection.execute("SELECT MIN(day),MAX(day) FROM station_days").fetchone()
        return {
            "file": output.name, "schema": SCHEMA_VERSION,
            "observations": connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "start": bounds[0], "end": bounds[1],
            "requested_start": get_metadata(connection, "history_requested_start"),
            "history_complete": require_complete,
        }


def build_seeds(settings: Settings) -> dict:
    folder = settings.root / "seed-data"
    folder.mkdir(exist_ok=True)
    entries = {}
    with workspace_lock(settings.root), tempfile.TemporaryDirectory(
        prefix="source-seeds-", dir=settings.var / "tmp",
    ) as temporary_directory:
        temporary = Path(temporary_directory)
        for provider in seed_providers(settings):
            source = for_provider(settings, provider)
            output = temporary / f"{provider}.sqlite3"
            entries[provider] = sanitized_snapshot(source, output)
            entries[provider]["sha256"] = file_digest(output)
            with connect(output) as connection:
                archives = [dict(row) for row in connection.execute("SELECT * FROM archives ORDER BY name")]
            entries[provider]["archives"] = archives
            archive_folder = temporary / "archives" / provider
            for record in archives:
                path = verified_archive(source.archive_directory, record, provider)
                archive_folder.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, archive_folder / path.name)
                shutil.copyfile(path.with_suffix(".gz.sha256"), (archive_folder / path.name).with_suffix(".gz.sha256"))
        manifest = {
            "format": "clean-air-source-seeds-v1", "created": timestamp_now(), "sources": entries,
            "unavailable": {} if "bg_direct" in entries else {"bg_direct": "National CSV collection is disabled for this snapshot."},
            "credential_notice": "No API credential, personal alert settings or API-quota history is included.",
        }
        for provider, entry in entries.items():
            (temporary / entry["file"]).replace(folder / entry["file"])
            archive_folder = folder / "archives" / provider
            for record in entry["archives"]:
                archive_folder.mkdir(parents=True, exist_ok=True)
                path = temporary / "archives" / provider / record["name"]
                path.replace(archive_folder / path.name)
                path.with_suffix(".gz.sha256").replace((archive_folder / path.name).with_suffix(".gz.sha256"))
        manifest_path = folder / "manifest.json.part"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        manifest_path.replace(folder / "manifest.json")
        for provider, entry in entries.items():
            with connect(for_provider(settings, provider).database) as connection, connection:
                set_metadata(connection, f"bundled_seed:{provider}", entry["sha256"])
    return manifest


def merge_seed(settings: Settings) -> bool:
    if settings.provider not in SEED_PROVIDERS:
        return False
    folder = settings.root / "seed-data"
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        return False
    manifest = json.loads(manifest_path.read_text("utf-8"))
    if manifest.get("format") != "clean-air-source-seeds-v1":
        raise RuntimeError("Unsupported bundled-seed manifest.")
    entry = manifest.get("sources", {}).get(settings.provider)
    if entry is None:
        return False
    path = (folder / entry["file"]).resolve()
    if (
        not path.is_relative_to(folder.resolve()) or not entry.get("history_complete")
        or entry.get("schema") != SCHEMA_VERSION
    ):
        raise RuntimeError("The selected source seed is not a verified complete-history snapshot.")
    with connect(settings.database) as connection:
        if get_metadata(connection, f"bundled_seed:{settings.provider}", None) == entry["sha256"]:
            return False
    with workspace_lock(settings.root):
        if not path.is_file():
            raise RuntimeError("Bundled source data is missing. Run git lfs pull or extract the complete Release ZIP.")
        with path.open("rb") as handle:
            if handle.read(64).startswith(b"version https://git-lfs.github.com/spec/v1"):
                raise RuntimeError("Bundled source data is an LFS pointer. Run git lfs pull before starting.")
        if file_digest(path) != entry["sha256"]:
            raise RuntimeError(f"{settings.provider} bundled data checksum failed. No seed merge was attempted.")
        with connect(settings.database) as connection:
            connection.execute("ATTACH DATABASE ? AS seed", (str(path),))
            source_row = connection.execute("SELECT value FROM seed.metadata WHERE key='data_provider'").fetchone()
            if not source_row or json.loads(source_row[0]) != settings.provider:
                raise RuntimeError("The bundled measurement data belongs to another source.")
            if connection.execute("PRAGMA seed.user_version").fetchone()[0] != SCHEMA_VERSION:
                raise RuntimeError("The bundled measurement schema does not match this application.")
            if connection.execute("SELECT COUNT(*) FROM seed.observations").fetchone()[0] != entry["observations"]:
                raise RuntimeError("The bundled measurement count does not match its manifest.")
            metadata = {
                row["key"]: json.loads(row["value"])
                for row in connection.execute("SELECT * FROM seed.metadata")
            }
            bounds = connection.execute("SELECT MIN(day),MAX(day) FROM seed.station_days").fetchone()
            if (
                [bounds[0], bounds[1]] != [entry["start"], entry["end"]]
                or metadata.get("history_requested_start") != entry["requested_start"]
                or not metadata.get("initial_import_complete")
                or (settings.provider == "openaq" and not metadata.get("openaq_backfill_complete"))
                or (settings.provider == "bg_direct" and not metadata.get("bg_direct_backfill_complete"))
            ):
                raise RuntimeError("The bundled source history does not match its manifest.")
            conflict = connection.execute(
                "SELECT s.id FROM seed.sampling_points s JOIN main.sampling_points m USING(id) "
                "WHERE s.station_id!=m.station_id LIMIT 1"
            ).fetchone()
            if conflict:
                raise RuntimeError("A bundled sampling point belongs to a different local station.")
            archives = [dict(row) for row in connection.execute("SELECT * FROM seed.archives ORDER BY name")]
            if archives != entry.get("archives", []):
                raise RuntimeError("Bundled archive entries do not match the seed database.")
            if connection.execute("PRAGMA seed.foreign_key_check").fetchone():
                raise RuntimeError("Bundled source data failed relationship verification.")
            for record in archives:
                source_archive = verified_archive(folder / "archives" / settings.provider, record, settings.provider)
                output = settings.archive_directory / record["name"]
                local = connection.execute("SELECT sha256 FROM archives WHERE name=?", (record["name"],)).fetchone()
                if (output.exists() and file_digest(output) != record["sha256"]) or (
                    local and local["sha256"] != record["sha256"]
                ):
                    raise RuntimeError("A bundled archive conflicts with an existing local archive; it was not replaced.")
                if not output.exists():
                    temporary = output.with_suffix(".gz.seed.part")
                    shutil.copyfile(source_archive, temporary)
                    temporary.replace(output)
                output.with_suffix(".gz.sha256").write_text(record["sha256"] + "\n", encoding="ascii")
            existing = bool(connection.execute("SELECT 1 FROM observations LIMIT 1").fetchone())
            untouched = not existing and not any(
                connection.execute(f"SELECT 1 FROM main.{table} LIMIT 1").fetchone()
                for table in (
                    "cities", "stations", "station_metadata", "sampling_points", "import_files",
                    "station_days", "collection_runs", "archives",
                )
            ) and not connection.execute(
                "SELECT 1 FROM main.metadata WHERE key!='data_provider' LIMIT 1"
            ).fetchone()
            if existing:
                before = settings.var / "backups" / f"before-seed-{settings.provider}-{timestamp_now()}-{entry['sha256'][:12]}.sqlite3"
                backup(settings.database, before)
            if untouched:
                LOGGER.info("Initializing empty %s store from its verified bundled snapshot.", settings.provider)
                with connect(path) as source:
                    source.backup(connection)
            else:
                LOGGER.info("Merging verified %s seed; existing/newer source observations are preserved.", settings.provider)
            with connection:
                if untouched:
                    connection.execute(
                        "DELETE FROM metadata WHERE key IN "
                        "('last_success','openaq_backfill_complete','bg_direct_backfill_complete')"
                    )
                else:
                    connection.execute("INSERT OR IGNORE INTO cities SELECT * FROM seed.cities")
                    connection.execute("INSERT OR IGNORE INTO stations SELECT * FROM seed.stations")
                    connection.execute("INSERT OR IGNORE INTO station_metadata SELECT * FROM seed.station_metadata")
                    connection.execute("INSERT OR IGNORE INTO sampling_points SELECT * FROM seed.sampling_points")
                    connection.execute(
                        "INSERT OR IGNORE INTO import_files(url,dataset,etag,last_success,rows_imported,window_start) "
                        "SELECT url,dataset,etag,last_success,rows_imported,window_start FROM seed.import_files"
                    )
                    connection.execute("INSERT OR IGNORE INTO archives SELECT * FROM seed.archives")
                    connection.execute(
                        """
                        INSERT INTO observations (
                            point_id,pollutant,started,ended,value,aggregation,validity,verification,
                            result_time,data_capture,priority,file_id
                        )
                        SELECT o.point_id,o.pollutant,o.started,o.ended,o.value,o.aggregation,
                               o.validity,o.verification,o.result_time,o.data_capture,o.priority,t.id
                        FROM seed.observations o JOIN seed.import_files f ON f.id=o.file_id
                        JOIN main.import_files t ON t.url=f.url WHERE 1
                        AND NOT EXISTS (
                            SELECT 1 FROM main.observations local
                            WHERE local.point_id=o.point_id AND local.pollutant=o.pollutant
                              AND local.started=o.started AND local.aggregation=o.aggregation
                              AND (local.priority>o.priority
                                   OR (local.priority=o.priority AND local.result_time>=o.result_time))
                        )
                        """ + OBSERVATION_CONFLICT
                    )
                    stations = connection.execute("SELECT DISTINCT station_id FROM seed.sampling_points").fetchall()
                    for index, station in enumerate(stations, 1):
                        rebuild_station_days(connection, station[0])
                        if index % 10 == 0 or index == len(stations):
                            LOGGER.info("%s bundled history: prepared %s/%s stations.", settings.provider, index, len(stations))
                for row in connection.execute("SELECT * FROM seed.metadata").fetchall():
                    if shared_metadata(row["key"]) and row["key"] not in {
                        "initial_import_complete", "openaq_backfill_complete", "bg_direct_backfill_complete", "last_success",
                    }:
                        if get_metadata(connection, row["key"], None) is None:
                            set_metadata(connection, row["key"], json.loads(row["value"]))
                set_metadata(connection, "initial_import_complete", True)
                months = (
                    settings.options.openaq.history_months or settings.options.collection.history_months
                    if settings.provider == "openaq" else settings.options.collection.history_months
                )
                if (
                    settings.provider in ("openaq", "bg_direct") and not existing
                    and entry["requested_start"] <= months_before(utc_today(), months).isoformat()
                ):
                    key = "openaq_backfill_complete" if settings.provider == "openaq" else "bg_direct_backfill_complete"
                    set_metadata(connection, key, True)
                windows = get_metadata(connection, "restored_source_windows", [])
                window = {"start": entry["start"], "end": entry["end"]}
                if window not in windows:
                    set_metadata(connection, "restored_source_windows", [*windows, window])
                latest = connection.execute(
                    "SELECT MAX(ended) FROM observations WHERE validity>0 AND value>=0"
                ).fetchone()[0]
                set_metadata(connection, "latest_observation", latest)
                set_metadata(connection, f"bundled_seed:{settings.provider}", entry["sha256"])
                set_metadata(connection, "bundled_seed_info", {
                    "created": manifest["created"], "requested_start": entry.get("requested_start"),
                    "history_complete": entry["history_complete"],
                    "source_last_success": metadata.get("last_success"),
                    "notice": "This is a bundled data snapshot; it does not prove this computer has performed a live fetch.",
                })
                if connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise RuntimeError("Seed merge failed relationship verification.")
        return True
