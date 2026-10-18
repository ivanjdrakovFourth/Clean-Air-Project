from __future__ import annotations

import io
import os
import shutil
import subprocess
import zipfile

import pytest

from clean_air.operations import make_handover
from clean_air.settings import ROOT
from tools.build_portable import download, extract, remove_python_launchers


def test_build_verifies_runtime_download_checksum(settings, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout: io.BytesIO(b"incorrect binary")
    )
    target = settings.var / "cache" / "component.zip"
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        download("https://example.invalid/component.zip", target, "0" * 64)
    assert not target.exists()


def test_build_rejects_archive_entries_outside_destination(settings):
    path = settings.var / "cache" / "fixture.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("../outside.txt", "not allowed")
    with pytest.raises(RuntimeError, match="Unsafe archive"):
        extract(path, settings.var / "cache" / "extracted")
    assert not (settings.var / "cache" / "outside.txt").exists()


def test_host_specific_launchers_are_removed_but_native_helpers_remain(settings):
    packages = settings.root / "runtime" / "packages"
    folder = packages / "bin"
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "pytest.exe", "w") as bundle:
        bundle.writestr("__main__.py", "host-specific generated launcher")
    (folder / "ruff.exe").write_bytes(b"native fixture, not a Python launcher")
    remove_python_launchers(packages)
    assert not (folder / "pytest.exe").exists()
    assert (folder / "ruff.exe").exists()


def test_handover_contains_portable_runtime_and_consistent_database_snapshot(settings):
    for relative in (
        ("python", "python.exe"), ("prometheus", "prometheus.exe"), ("manifest.json",)
    ):
        path = (settings.root / "runtime").joinpath(*relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unit-test fixture", encoding="utf-8")
    (settings.root / "README.md").write_text("unit-test handover", encoding="utf-8")
    (settings.root / "LICENSE").write_text("unit-test notice", encoding="utf-8")
    (settings.root / "Stop-CleanAir.cmd").write_text("unit-test stop launcher", encoding="utf-8")
    (settings.root / "tools").mkdir()
    (settings.root / "tools" / "stop.ps1").write_text("unit-test stop helper", encoding="utf-8")
    result = make_handover(settings)
    with zipfile.ZipFile(result) as bundle:
        names = set(bundle.namelist())
        assert "CleanAirFinder/runtime/python/python.exe" in names
        assert "CleanAirFinder/var/data/clean_air.sqlite3" in names
        assert "CleanAirFinder/handover-manifest.json" in names
        assert "CleanAirFinder/LICENSE" in names
        assert "CleanAirFinder/Stop-CleanAir.cmd" in names
        assert "CleanAirFinder/tools/stop.ps1" in names
        assert not any("test-tmp" in name or name.endswith("-wal") for name in names)
        assert bundle.testzip() is None
    assert result.with_suffix(".zip.sha256").is_file()


def test_start_launcher_explains_unresolved_lfs_runtime(settings):
    runtime = settings.root / "runtime" / "python"
    runtime.mkdir(parents=True)
    (runtime / "python.exe").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 105696\n",
        encoding="ascii",
    )
    launcher = settings.root / "Start-CleanAir.cmd"
    shutil.copyfile(ROOT / "Start-CleanAir.cmd", launcher)
    result = subprocess.run(
        [os.environ["ComSpec"], "/d", "/c", str(launcher)],
        cwd=settings.root.parent, input="\n", capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "git lfs pull" in result.stdout
    assert "not been fully downloaded" in result.stdout


def test_small_portable_handover_omits_only_git_internals(settings):
    for parts in (("python", "python.exe"), ("prometheus", "prometheus.exe"), ("manifest.json",)):
        path = (settings.root / "runtime").joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    (settings.root / ".git").mkdir()
    (settings.root / ".git" / "config").write_text("private local metadata", encoding="utf-8")
    output = make_handover(settings, include_git=False)
    with zipfile.ZipFile(output) as bundle:
        assert not any(name.startswith("CleanAirFinder/.git/") for name in bundle.namelist())
        assert "CleanAirFinder/runtime/python/python.exe" in bundle.namelist()
        assert "CleanAirFinder/var/data/clean_air.sqlite3" in bundle.namelist()
