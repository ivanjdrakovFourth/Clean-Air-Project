from __future__ import annotations

import argparse
import getpass
import json
import logging
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import uvicorn

from .archive import archive_old, restore
from .bulgarian import BulgarianCollector
from .collector import Collector
from .comparison import status_snapshot
from .credentials import save_openaq_key
from .database import backup, connect
from .logging_setup import configure_logging
from .openaq import OpenAQCollector
from .operations import (
    benchmark,
    ensure_port_available,
    instance_lock,
    make_handover,
    monitoring,
    open_browser_when_ready,
)
from .provider_store import PROVIDERS, for_provider, prepare_sources
from .seeds import build_seeds
from .settings import Options, load_settings, timestamp_now
from .shutdown_control import shutdown_control
from .web import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Workspace-contained Clean Air Finder")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local app and bundled monitoring")
    serve.add_argument("--offline", action="store_true", help="Use stored data without source requests")
    serve.add_argument("--no-monitoring", action="store_true", help="Explicit application-only diagnostics")
    serve.add_argument("--open-browser", action="store_true")
    serve.add_argument("--no-browser", action="store_true", help="Suppress browser opening by the launcher")
    serve.add_argument("--port", type=int)
    sync = commands.add_parser("sync", help="Import or refresh real EEA data")
    sync.add_argument("--force", action="store_true")
    sync.add_argument("--source", choices=PROVIDERS, default="eea")
    for command, help_text in (
        ("status", "Show source-specific history and collection state"),
        ("doctor", "Verify portable components and source database"),
        ("archive", "Verify and archive source records older than 36 months"),
        ("backup", "Make a consistent source-specific SQLite backup"),
    ):
        commands.add_parser(command, help=help_text).add_argument("--source", choices=PROVIDERS, default="eea")
    restore_command = commands.add_parser("restore", help="Restore a checksummed archive from var\\archives")
    restore_command.add_argument("name")
    restore_command.add_argument("--source", choices=PROVIDERS, default="eea")
    commands.add_parser("configure-openaq", help="Save an encrypted local key; it is not echoed or committed")
    commands.add_parser("seed", help="Build sanitized source snapshots; refuses unfinished historical collection")
    handover = commands.add_parser("handover", help="Create a verified ZIP with runtime and database snapshot")
    handover.add_argument(
        "--without-git", action="store_true",
        help="Omit local Git/LFS object storage from a smaller runnable handover",
    )
    commands.add_parser("test", help="Run lint and the offline automated tests")
    performance = commands.add_parser("benchmark", help="Measure real-data query performance")
    performance.add_argument("--iterations", type=int, default=100)
    performance.add_argument("--source", choices=PROVIDERS, default="eea")
    args = parser.parse_args()
    try:
        settings = load_settings()
        if args.command == "serve":
            values = settings.options.model_dump()
            if args.offline:
                values["collection"]["enabled"] = False
            if args.no_monitoring:
                values["monitoring"]["enabled"] = False
            if args.port is not None:
                values["app"]["port"] = args.port
            settings = replace(settings, options=Options.model_validate(values))
        configure_logging(settings)
        if args.command == "configure-openaq":
            save_openaq_key(settings.root, getpass.getpass("OpenAQ API key (hidden): "))
            print("Local key encrypted for this Windows user. It is excluded from Git and handover ZIPs.")
            return 0
        prepare_sources(settings)
        if hasattr(args, "source"):
            settings = for_provider(settings, args.source)
        if args.command == "serve":
            ensure_port_available(settings.options.app.port)
            with instance_lock(settings), monitoring(settings):
                server = uvicorn.Server(uvicorn.Config(
                    create_app(settings), host=settings.options.app.host,
                    port=settings.options.app.port, workers=1, access_log=False,
                ))
                with shutdown_control(settings.root, server):
                    if args.open_browser and not args.no_browser:
                        open_browser_when_ready(settings.options.app.port)
                    print(f"\nClean Air Finder: http://127.0.0.1:{settings.options.app.port}")
                    print(
                        "Keep this console open. Press Ctrl+C or run Stop-CleanAir.cmd to stop.\n",
                        flush=True,
                    )
                    try:
                        server.run()
                    except KeyboardInterrupt:
                        logging.getLogger(__name__).info("Server stopped from the console.")
                    if not server.started:
                        raise RuntimeError(
                            "The web service failed to start. Inspect var\\logs\\application.jsonl."
                        )
            with connect(settings.database) as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        elif args.command == "sync":
            with instance_lock(settings):
                factories = {"eea": Collector, "openaq": OpenAQCollector, "bg_direct": BulgarianCollector}
                collector = factories[settings.provider](settings)
                print(json.dumps(collector.sync_once(force=args.force), indent=2))
        elif args.command == "status":
            print(json.dumps(status_snapshot(settings, settings.provider), indent=2))
        elif args.command == "doctor":
            for part in (Path("python") / "python.exe", Path("prometheus") / "prometheus.exe", Path("manifest.json")):
                if not (settings.root / "runtime" / part).is_file():
                    raise RuntimeError(f"Missing portable runtime component: {part}")
            with connect(settings.database) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"Database integrity check failed: {integrity}")
            print(json.dumps(
                {
                    "python": sys.version, "sqlite": sqlite3.sqlite_version,
                    "workspace": str(settings.root), "database_integrity": integrity,
                    "history": status_snapshot(settings, settings.provider),
                }, indent=2,
            ))
        elif args.command == "archive":
            with instance_lock(settings):
                print(json.dumps(archive_old(settings) or {"message": "No records need archiving."}, indent=2))
        elif args.command == "restore":
            with instance_lock(settings):
                print(json.dumps(restore(settings, settings.archive_directory / args.name), indent=2))
        elif args.command == "backup":
            destination = settings.var / "backups" / f"{settings.provider}-{timestamp_now()}.sqlite3"
            backup(settings.database, destination)
            print(f"Verified backup: {destination}")
        elif args.command == "seed":
            print(json.dumps(build_seeds(settings), indent=2))
        elif args.command == "handover":
            with instance_lock(settings):
                print(make_handover(settings, include_git=not args.without_git))
        elif args.command == "benchmark":
            if not 1 <= args.iterations <= 10000:
                raise ValueError("--iterations must be between 1 and 10000")
            report = benchmark(settings, args.iterations)
            print(json.dumps(report, indent=2))
            return 0 if report["all_targets_met"] else 1
        elif args.command == "test":
            for arguments in (
                ["-m", "ruff", "check", "clean_air", "tools", "tests"],
                ["-m", "pytest", "--basetemp", str(settings.var / "test-tmp"),
                 "-o", f"cache_dir={settings.var / 'test-cache'}"],
            ):
                subprocess.run([sys.executable, *arguments], cwd=settings.root, check=True)
        return 0
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
        logging.getLogger(__name__).exception("Command failed: %s", error)
        print(f"\nERROR: {error}\n", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"\nValidation command failed with exit code {error.returncode}.", file=sys.stderr)
        return error.returncode


if __name__ == "__main__":
    raise SystemExit(main())
