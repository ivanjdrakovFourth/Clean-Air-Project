from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import pytest

from clean_air.database import connect
from clean_air.operations import instance_lock
from clean_air.shutdown_control import process_start_ticks, release_storage, shutdown_control


def test_shutdown_request_sets_server_exit_and_cleans_control_files(settings):
    target = SimpleNamespace(should_exit=False)
    state_path = settings.var / "run" / "server.json"
    with shutdown_control(settings.root, target):
        state = json.loads(state_path.read_text("utf-8"))
        assert state["pid"] == os.getpid()
        assert state["workspace"] == str(settings.root.resolve())
        assert int(state["process_start_ticks"]) > 0
        request = settings.var / "run" / f"stop-{state['instance_id']}.request"
        request.write_text("stop", encoding="utf-8")
        deadline = time.monotonic() + 2
        while not target.should_exit and time.monotonic() < deadline:
            time.sleep(0.02)
        assert target.should_exit
    assert not state_path.exists()
    assert not request.exists()


def test_stale_shutdown_request_does_not_stop_a_new_instance(settings):
    stale = settings.var / "run" / ("stop-" + "0" * 32 + ".request")
    stale.write_text("stop", encoding="utf-8")
    target = SimpleNamespace(should_exit=False)
    with shutdown_control(settings.root, target):
        time.sleep(0.3)
        assert not target.should_exit


def test_shutdown_metadata_is_removed_after_server_start_failure(settings):
    with pytest.raises(ValueError, match="startup failed"):
        with shutdown_control(settings.root, SimpleNamespace(should_exit=False)):
            raise ValueError("startup failed")
    assert not (settings.var / "run" / "server.json").exists()


def test_process_creation_time_is_available():
    assert process_start_ticks() > 0


def test_storage_release_checkpoints_wal_and_removes_stale_state(settings):
    state = settings.var / "run" / "server.json"
    state.write_text("stale shutdown state", encoding="utf-8")
    with connect(settings.database) as connection:
        connection.execute("INSERT INTO metadata VALUES ('stop-test', 'true')")
        connection.commit()
        release_storage(settings.root)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM metadata WHERE key='stop-test'").fetchone()[0] == "true"
    assert not state.exists()


def test_storage_release_refuses_an_active_workspace_lock(settings):
    with instance_lock(settings), pytest.raises(RuntimeError, match="still in use"):
        release_storage(settings.root)


@pytest.fixture
def cli_serve(monkeypatch, settings):
    from clean_air import __main__ as cli

    monkeypatch.setattr("sys.argv", ["clean_air", "serve", "--offline", "--no-browser"])
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda configured: None)
    monkeypatch.setattr(cli, "ensure_port_available", lambda port: None)
    monkeypatch.setattr(cli, "create_app", lambda configured: object())
    return cli


def test_console_interrupt_remains_a_normal_shutdown(cli_serve, monkeypatch, settings):
    class ConsoleInterruptedServer:
        should_exit = False
        started = True

        def __init__(self, config):
            pass

        def run(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_serve.uvicorn, "Server", ConsoleInterruptedServer)
    assert cli_serve.main() == 0
    assert not (settings.var / "run" / "server.json").exists()


def test_failed_server_start_is_not_reported_as_success(cli_serve, monkeypatch, settings):
    class FailedServer:
        should_exit = True
        started = False

        def __init__(self, config):
            pass

        def run(self):
            pass

    monkeypatch.setattr(cli_serve.uvicorn, "Server", FailedServer)
    assert cli_serve.main() == 1
    assert not (settings.var / "run" / "server.json").exists()
