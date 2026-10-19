from __future__ import annotations

import json
import shutil
from dataclasses import replace

import pytest
from conftest import PRIVATE_MARKER, raw_observation

from clean_air.archive import archive_old
from clean_air.database import connect, rebuild_station_days, set_metadata
from clean_air.provider_store import for_provider, prepare_sources
from clean_air.seeds import build_seeds, file_digest, merge_seed
from clean_air.settings import Options, months_before, utc_today


@pytest.fixture
def published(completed_sources):
    build_seeds(completed_sources)
    return completed_sources


def recipient(published, *, copy_seeds=True):
    target = replace(published, root=published.root / "recipient")
    target.prepare_directories()
    shutil.copytree(published.root / "config", target.root / "config")
    if copy_seeds:
        shutil.copytree(published.root / "seed-data", target.root / "seed-data")
    return target


def refresh_hash(settings, provider):
    path = settings.root / "seed-data" / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    manifest["sources"][provider]["sha256"] = file_digest(path.parent / f"{provider}.sqlite3")
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_complete_seeds_are_sanitized_checksums_match_and_unavailable_source_is_honest(published):
    folder = published.root / "seed-data"
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    assert set(manifest["sources"]) == {"eea", "openaq"}
    assert "bg_direct" in manifest["unavailable"]
    assert not (folder / "bg_direct.sqlite3").exists()
    for entry in manifest["sources"].values():
        path = folder / entry["file"]
        assert file_digest(path) == entry["sha256"]
        assert PRIVATE_MARKER.encode() not in path.read_bytes()
        with connect(path) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == entry["observations"]
            assert connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 0
            assert not connection.execute("SELECT 1 FROM metadata WHERE key='openaq_request_times'").fetchone()


def test_exported_snapshot_is_not_needlessly_reimported_into_its_own_source(published):
    assert merge_seed(published) is False
    assert merge_seed(for_provider(published, "openaq")) is False


def test_first_start_loads_both_histories_without_credentials_and_is_idempotent(published, monkeypatch):
    monkeypatch.delenv("OPENAQ_API_KEY", raising=False)
    target = recipient(published)
    prepare_sources(target)
    for provider, mean in (("eea", 10), ("openaq", 20)):
        configured = for_provider(target, provider)
        with connect(configured.database) as connection:
            assert connection.execute("SELECT DISTINCT mean FROM station_days").fetchone()[0] == mean
            assert not connection.execute("PRAGMA foreign_key_check").fetchone()
            assert connection.execute("SELECT value FROM metadata WHERE key='bundled_seed_info'").fetchone()
            assert not connection.execute("SELECT value FROM metadata WHERE key='last_success'").fetchone()
        assert merge_seed(configured) is False
    with connect(for_provider(target, "bg_direct").database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_empty_store_uses_verified_snapshot_without_recomputing_all_observations(published, monkeypatch):
    target = recipient(published, copy_seeds=False)
    prepare_sources(target)
    shutil.copytree(published.root / "seed-data", target.root / "seed-data")
    monkeypatch.setattr(
        "clean_air.seeds.rebuild_station_days",
        lambda *args: pytest.fail("A genuinely empty store can reuse the verified snapshot's aggregates."),
    )
    configured = for_provider(target, "openaq")
    assert merge_seed(configured) is True
    with connect(configured.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
        assert connection.execute("SELECT DISTINCT mean FROM station_days").fetchone()[0] == 20
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("SELECT 1 FROM metadata WHERE key='last_success'").fetchone()
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_even_an_empty_observation_store_preserves_its_existing_local_state(published, monkeypatch):
    target = recipient(published, copy_seeds=False)
    prepare_sources(target)
    configured = for_provider(target, "openaq")
    with connect(configured.database) as connection, connection:
        set_metadata(connection, "local-note", "Preserve this installation state")
    shutil.copytree(published.root / "seed-data", target.root / "seed-data")
    assert merge_seed(configured) is True
    with connect(configured.database) as connection:
        assert json.loads(connection.execute("SELECT value FROM metadata WHERE key='local-note'").fetchone()[0]) == "Preserve this installation state"


def test_legacy_working_database_lfs_pointer_has_a_clear_download_instruction(published):
    target = recipient(published, copy_seeds=False)
    target.database.parent.mkdir(parents=True, exist_ok=True)
    target.database.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 123\n",
        encoding="ascii",
    )
    with pytest.raises(RuntimeError, match="git lfs pull"):
        prepare_sources(target)


@pytest.mark.parametrize("local_revision", [10, 11])
def test_update_preserves_local_equal_or_newer_revisions_and_maps_file_ids(published, local_revision):
    target = recipient(published, copy_seeds=False)
    prepare_sources(target)
    beginning = months_before(utc_today(), 12).isoformat()
    with connect(target.database) as connection, connection:
        connection.execute("INSERT INTO import_files(id,url,dataset) VALUES (1,'https://example.invalid/unrelated',2)")
        connection.execute("INSERT INTO import_files(id,url,dataset) VALUES (2,'https://example.invalid/fixture',2)")
        raw_observation(connection, day=beginning, value=99, result_time=local_revision)
        connection.execute("UPDATE observations SET file_id=2")
    shutil.copytree(published.root / "seed-data", target.root / "seed-data")
    prepare_sources(target)
    with connect(target.database) as connection:
        rows = connection.execute("SELECT value,file_id FROM observations ORDER BY started").fetchall()
        assert [(row["value"], row["file_id"]) for row in rows] == [(99, 2), (10, 2)]
        assert not connection.execute("PRAGMA foreign_key_check").fetchone()
    assert list((target.var / "backups").glob("before-seed-eea-*.sqlite3"))


def test_newer_seed_revision_replaces_older_local_value(published):
    target = recipient(published, copy_seeds=False)
    prepare_sources(target)
    with connect(target.database) as connection, connection:
        raw_observation(connection, day=months_before(utc_today(), 12).isoformat(), value=99, result_time=1)
    shutil.copytree(published.root / "seed-data", target.root / "seed-data")
    prepare_sources(target)
    with connect(target.database) as connection:
        assert {row[0] for row in connection.execute("SELECT value FROM observations")} == {10}


@pytest.mark.parametrize("damage", ["checksum", "provider", "schema", "count"])
def test_invalid_seed_fails_before_modifying_measurements(published, damage):
    target = recipient(published, copy_seeds=False)
    prepare_sources(target)
    shutil.copytree(published.root / "seed-data", target.root / "seed-data")
    path = target.root / "seed-data" / "eea.sqlite3"
    if damage == "checksum":
        path.write_bytes(path.read_bytes() + b"corruption")
    else:
        with connect(path) as connection, connection:
            if damage == "provider":
                set_metadata(connection, "data_provider", "openaq")
            elif damage == "schema":
                connection.execute("PRAGMA user_version=2")
            else:
                connection.execute("DELETE FROM observations")
        refresh_hash(target, "eea")
    with pytest.raises(RuntimeError):
        merge_seed(target)
    with connect(target.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_manifest_cannot_escape_seed_directory(published):
    target = recipient(published, copy_seeds=False)
    prepare_sources(target)
    shutil.copytree(published.root / "seed-data", target.root / "seed-data")
    path = target.root / "seed-data" / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    manifest["sources"]["eea"]["file"] = "..\\outside.sqlite3"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="snapshot"):
        merge_seed(target)


def test_unfinished_backfill_is_never_published_or_replaces_good_manifest(published):
    manifest = published.root / "seed-data" / "manifest.json"
    previous = manifest.read_bytes()
    with connect(for_provider(published, "openaq").database) as connection, connection:
        set_metadata(connection, "openaq_backfill_complete", False)
    with pytest.raises(RuntimeError, match="not finished"):
        build_seeds(published)
    assert manifest.read_bytes() == previous


def test_pending_sensor_cursor_prevents_false_complete_publication(published):
    with connect(for_provider(published, "openaq").database) as connection, connection:
        set_metadata(connection, "openaq_cursor:1", {"lower": 0, "next_to": 1})
    with pytest.raises(RuntimeError, match="not finished"):
        build_seeds(published)


def test_lfs_pointer_has_a_clear_download_instruction(published):
    target = recipient(published, copy_seeds=False)
    prepare_sources(target)
    shutil.copytree(published.root / "seed-data", target.root / "seed-data")
    (target.root / "seed-data" / "eea.sqlite3").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 123\n",
        encoding="ascii",
    )
    with pytest.raises(RuntimeError, match="git lfs pull"):
        merge_seed(target)


def test_twelve_month_seed_does_not_claim_thirty_six_month_backfill_is_complete(published):
    target = recipient(published, copy_seeds=False)
    values = target.options.model_dump()
    values["collection"]["history_months"] = 36
    target = replace(target, options=Options.model_validate(values))
    shutil.copytree(published.root / "seed-data", target.root / "seed-data")
    prepare_sources(target)
    with connect(for_provider(target, "openaq").database) as connection:
        complete = connection.execute(
            "SELECT value FROM metadata WHERE key='openaq_backfill_complete'"
        ).fetchone()
        assert not complete or json.loads(complete["value"]) is False
        assert connection.execute("SELECT value FROM metadata WHERE key='restored_source_windows'").fetchone()


def test_seed_does_not_reassign_a_local_sampling_point(published):
    target = recipient(published, copy_seeds=False)
    prepare_sources(target)
    with connect(target.database) as connection, connection:
        connection.execute("INSERT INTO cities VALUES ('Elsewhere')")
        connection.execute("INSERT INTO stations VALUES ('OTHER','Elsewhere')")
        connection.execute("INSERT INTO sampling_points VALUES ('SPO-BG0001A_06001_100','OTHER')")
    shutil.copytree(published.root / "seed-data", target.root / "seed-data")
    with pytest.raises(RuntimeError, match="different local station"):
        merge_seed(target)


def test_source_seeds_include_verified_cold_archives_and_their_explorer_index(published):
    with connect(published.database) as connection, connection:
        raw_observation(connection, day="2020-01-01", value=3)
        rebuild_station_days(connection, "BG0001A")
    archived = archive_old(published)
    build_seeds(published)
    target = recipient(published)
    prepare_sources(target)
    assert (target.archive_directory / archived["name"]).is_file()
    assert not list(for_provider(target, "openaq").archive_directory.glob("*.jsonl.gz"))
    with connect(target.database) as connection:
        assert connection.execute("SELECT sha256 FROM archives").fetchone()[0] == archived["sha256"]


def test_seed_archive_cannot_overwrite_a_conflicting_local_file(published):
    with connect(published.database) as connection, connection:
        raw_observation(connection, day="2020-01-01", value=3)
        rebuild_station_days(connection, "BG0001A")
    archived = archive_old(published)
    build_seeds(published)
    target = recipient(published, copy_seeds=False)
    prepare_sources(target)
    local = target.archive_directory / archived["name"]
    local.write_bytes(b"Local conflicting archive must not be overwritten")
    shutil.copytree(published.root / "seed-data", target.root / "seed-data")
    with pytest.raises(RuntimeError, match="not replaced"):
        merge_seed(target)
    assert local.read_bytes() == b"Local conflicting archive must not be overwritten"
