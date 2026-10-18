from __future__ import annotations

from conftest import raw_observation

from clean_air.archive import archive_old, restore
from clean_air.database import backup, connect, initialize, rebuild_station_days


def test_sqlite_is_persistent_indexed_and_wal_enabled(settings):
    initialize(settings.database)
    with connect(settings.database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        names = {row["name"] for row in connection.execute("PRAGMA index_list(station_days)")}
        assert "station_days_query" in names


def test_repeated_import_is_idempotent_and_validated_values_win(settings):
    with connect(settings.database) as connection, connection:
        raw_observation(connection, value=40, priority=1)
        raw_observation(connection, value=20, priority=2)
        raw_observation(connection, value=99, priority=1, result_time=100)
        raw_observation(connection, value=20, priority=2)
        rebuild_station_days(connection, "BG0001A")
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        assert connection.execute("SELECT mean FROM station_days").fetchone()[0] == 20


def test_invalid_revision_removes_previously_valid_daily_value(settings):
    with connect(settings.database) as connection, connection:
        raw_observation(connection, value=10, result_time=1)
        raw_observation(connection, value=10, validity=-1, result_time=2)
        rebuild_station_days(connection, "BG0001A")
        assert connection.execute("SELECT COUNT(*) FROM station_days").fetchone()[0] == 0


def test_multiple_sampling_points_do_not_duplicate_physical_station_weight(settings):
    with connect(settings.database) as connection, connection:
        raw_observation(connection, value=10, point="SPO-BG0001A_06001_100")
        raw_observation(connection, value=100, point="SPO-BG0001A_06001_500")
        rebuild_station_days(connection, "BG0001A")
        assert connection.execute("SELECT COUNT(*) FROM station_days").fetchone()[0] == 1


def test_hourly_rollup_uses_distinct_hours_and_excludes_negative_values(settings):
    with connect(settings.database) as connection, connection:
        for hour in range(18):
            raw_observation(connection, aggregation="hour", hour=hour, value=12)
        raw_observation(connection, aggregation="hour", hour=18, value=-2)
        rebuild_station_days(connection, "BG0001A")
        row = connection.execute("SELECT * FROM station_days").fetchone()
        assert row["mean"] == 12
        assert row["coverage"] == 0.75
        assert row["observation_count"] == 18


def test_reported_validated_daily_value_takes_precedence(settings):
    with connect(settings.database) as connection, connection:
        for hour in range(24):
            raw_observation(connection, aggregation="hour", priority=1, hour=hour, value=100)
        raw_observation(connection, aggregation="day", priority=2, value=10)
        rebuild_station_days(connection, "BG0001A")
        row = connection.execute("SELECT * FROM station_days").fetchone()
        assert row["mean"] == 10
        assert row["coverage"] is None


def test_verified_archive_round_trip_preserves_current_data(settings):
    from datetime import date

    with connect(settings.database) as connection, connection:
        raw_observation(connection, day="2020-01-01", value=11)
        raw_observation(connection, day="2025-01-01", value=22)
        rebuild_station_days(connection, "BG0001A")
    archived = archive_old(settings, today=date(2026, 1, 1))
    assert archived["observations"] == 1
    with connect(settings.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        assert connection.execute("SELECT mean FROM station_days").fetchone()[0] == 22
    counts = restore(settings, settings.var / "archives" / archived["name"])
    assert counts["observations"] == 1
    with connect(settings.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM station_days").fetchone()[0] == 2


def test_corrupt_archive_is_not_restored(settings):
    from datetime import date

    import pytest

    with connect(settings.database) as connection, connection:
        raw_observation(connection, day="2020-01-01")
        rebuild_station_days(connection, "BG0001A")
    archived = archive_old(settings, today=date(2026, 1, 1))
    path = settings.var / "archives" / archived["name"]
    path.write_bytes(path.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="checksum"):
        restore(settings, path)


def test_consistent_database_backup_passes_integrity_check(settings):
    destination = settings.var / "backups" / "test.sqlite3"
    backup(settings.database, destination)
    with connect(destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_archive_uses_exact_36_month_boundary(settings):
    from datetime import UTC, date, datetime

    with connect(settings.database) as connection, connection:
        raw_observation(connection, day="2022-12-31")
        raw_observation(connection, day="2023-01-01")
        rebuild_station_days(connection, "BG0001A")
    result = archive_old(settings, today=date(2026, 1, 1))
    assert result["observations"] == 1
    with connect(settings.database) as connection:
        remaining = connection.execute("SELECT started FROM observations").fetchone()[0]
    assert remaining == int(datetime(2023, 1, 1, tzinfo=UTC).timestamp())


def test_archive_verification_failure_never_prunes_active_data(settings, monkeypatch):
    from datetime import date

    import pytest

    def fail_verification(path):
        raise ValueError("Simulated archive verification failure")

    with connect(settings.database) as connection, connection:
        raw_observation(connection, day="2020-01-01")
        rebuild_station_days(connection, "BG0001A")
    monkeypatch.setattr("clean_air.archive.inspect_archive", fail_verification)
    with pytest.raises(ValueError, match="verification failure"):
        archive_old(settings, today=date(2026, 1, 1))
    with connect(settings.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM station_days").fetchone()[0] == 1
    assert list((settings.var / "archives").iterdir()) == []


def test_schema_one_migration_backs_up_and_preserves_measurements(settings):
    with connect(settings.database) as connection, connection:
        raw_observation(connection, value=17)
        connection.execute("DROP TABLE station_metadata")
        connection.execute("DROP INDEX observations_hour_search")
        connection.execute("PRAGMA user_version=1")
    initialize(settings.database)
    backups = list((settings.var / "backups").glob("before-schema-2-*.sqlite3"))
    assert len(backups) == 1
    with connect(settings.database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT value FROM observations").fetchone()[0] == 17
        assert connection.execute("SELECT COUNT(*) FROM station_metadata").fetchone()[0] == 0
    with connect(backups[0]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT value FROM observations").fetchone()[0] == 17
    initialize(settings.database)
    assert len(list((settings.var / "backups").glob("before-schema-2-*.sqlite3"))) == 1


def test_schema_migration_does_not_continue_if_backup_fails(settings, monkeypatch):
    import pytest

    def fail_backup(source, destination):
        raise OSError("Simulated full backup disk")

    with connect(settings.database) as connection, connection:
        connection.execute("DROP TABLE station_metadata")
        connection.execute("PRAGMA user_version=1")
    monkeypatch.setattr("clean_air.database.backup", fail_backup)
    with pytest.raises(OSError, match="full backup disk"):
        initialize(settings.database)
    with connect(settings.database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not connection.execute(
            "SELECT name FROM sqlite_schema WHERE name='station_metadata'"
        ).fetchone()


def test_archive_restores_station_identity_details(settings):
    from datetime import date

    with connect(settings.database) as connection, connection:
        raw_observation(connection, day="2020-01-01")
        rebuild_station_days(connection, "BG0001A")
        connection.execute(
            "INSERT INTO station_metadata VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("BG0001A", "test", "A station", "A street", 42.7, 23.3, "urban", "background",
             "2026-10-18", "https://www.eea.government.bg/kav/"),
        )
    archived = archive_old(settings, today=date(2026, 1, 1))
    with connect(settings.database) as connection, connection:
        connection.execute("DELETE FROM station_metadata")
    restore(settings, settings.var / "archives" / archived["name"])
    with connect(settings.database) as connection:
        row = connection.execute("SELECT name,address FROM station_metadata").fetchone()
        assert tuple(row) == ("A station", "A street")


def test_legacy_archive_without_station_metadata_remains_restorable(settings):
    import gzip
    import json
    from datetime import date

    from clean_air.archive import digest

    with connect(settings.database) as connection, connection:
        raw_observation(connection, day="2020-01-01", value=19)
        rebuild_station_days(connection, "BG0001A")
    archived = archive_old(settings, today=date(2026, 1, 1))
    path = settings.var / "archives" / archived["name"]
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        lines = handle.readlines()
    header = json.loads(lines[0])
    del header["station_metadata"]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(header) + "\n")
        handle.writelines(lines[1:])
    path.with_suffix(path.suffix + ".sha256").write_text(digest(path) + "\n", encoding="ascii")
    restore(settings, path)
    with connect(settings.database) as connection:
        assert connection.execute("SELECT value FROM observations").fetchone()[0] == 19


def test_schema_upgrade_refuses_to_modify_a_running_old_installation(settings):
    import pytest

    from clean_air.operations import instance_lock

    with connect(settings.database) as connection, connection:
        connection.execute("DROP TABLE station_metadata")
        connection.execute("PRAGMA user_version=1")
    with instance_lock(settings), pytest.raises(RuntimeError, match="still in use"):
        initialize(settings.database)
    with connect(settings.database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert not list((settings.var / "backups").glob("before-schema-2-*.sqlite3"))
