"""Build the redistributable Windows runtime without changing the host Python."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
VENDOR = ROOT / "vendor"


def download(url: str, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "CleanAirFinder/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as target:
        shutil.copyfileobj(response, target)
    with destination.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != expected_sha256:
        destination.unlink()
        raise RuntimeError(f"Checksum mismatch for {url}: received {actual}")


def extract(archive: Path, destination: Path, strip_root: bool = False) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            parts = Path(member.filename).parts
            if strip_root:
                parts = parts[1:]
            if not parts:
                continue
            target = destination.joinpath(*parts).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError(f"Unsafe archive entry: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def remove_python_launchers(packages: Path) -> None:
    # pip's generated Windows launchers embed the build machine's Python path.
    for executable in (packages / "bin").glob("*.exe"):
        if zipfile.is_zipfile(executable):
            with zipfile.ZipFile(executable) as bundle:
                generated = "__main__.py" in bundle.namelist()
            if generated:
                executable.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-dependencies", action="store_true",
        help="Resolve new package versions instead of using requirements.lock",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("The bundled handover runtime targets 64-bit Windows.")
    versions = json.loads((ROOT / "tools" / "runtime-versions.json").read_text("utf-8"))
    staging = ROOT / "var" / "build" / "runtime-staging"
    previous = ROOT / "runtime.previous"
    if staging.exists() or previous.exists():
        raise RuntimeError(
            "A previous runtime build directory exists. Inspect var\\build\\runtime-staging "
            "and runtime.previous before retrying; the active runtime was not changed."
        )
    staging.mkdir(parents=True)
    temporary = ROOT / "var" / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = str(temporary)
    os.environ["TEMP"] = str(temporary)
    VENDOR.mkdir(exist_ok=True)
    for name, component in versions.items():
        destination = staging / name
        archive = VENDOR / f"{name}-{component['version']}.zip"
        print(f"Downloading and verifying {name} {component['version']}...", flush=True)
        download(component["url"], archive, component["sha256"])
        extract(archive, destination, strip_root=name == "prometheus")
        archive.unlink()

    (staging / "python" / "python313._pth").write_text(
        "python313.zip\n.\n..\\packages\n..\\..\nimport site\n", encoding="ascii"
    )
    packages = staging / "packages"
    report = VENDOR / "dependency-report.json"
    lockfile = ROOT / "requirements.lock"
    requirements = (
        lockfile if lockfile.exists() and not args.refresh_dependencies
        else ROOT / "requirements-dev.txt"
    )
    hash_arguments = ["--require-hashes"] if requirements == lockfile else []
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--target", str(packages),
            "--platform", "win_amd64", "--python-version", "3.13",
            "--implementation", "cp", "--abi", "cp313", "--only-binary=:all:",
            "--no-compile", "--no-cache-dir", "--no-warn-script-location",
            "--report", str(report), *hash_arguments,
            "-r", str(requirements),
        ],
        check=True,
        cwd=ROOT,
    )
    remove_python_launchers(packages)
    resolved = json.loads(report.read_text("utf-8"))
    lock = []
    third_party = []
    for package in resolved["install"]:
        metadata = package["metadata"]
        digest = package["download_info"]["archive_info"]["hashes"]["sha256"]
        lock.append(f"{metadata['name']}=={metadata['version']} --hash=sha256:{digest}")
        third_party.append(
            {
                "name": metadata["name"],
                "version": metadata["version"],
                "url": package["download_info"]["url"],
                "sha256": digest,
                "license": metadata.get("license_expression", metadata.get("license", "See package metadata")),
            }
        )
    lockfile.write_text(
        "# Windows x64, CPython 3.13; generated by tools/build_portable.py\n"
        + "\n".join(sorted(lock, key=str.casefold)) + "\n",
        encoding="utf-8",
    )
    (staging / "manifest.json").write_text(
        json.dumps({"components": versions, "packages": third_party}, indent=2) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            str(staging / "python" / "python.exe"), "-c",
            "import sqlite3, fastapi, httpx, pyarrow, tzdata; "
            "print('Portable runtime ready. SQLite', sqlite3.sqlite_version)",
        ],
        check=True,
        cwd=ROOT,
    )
    if RUNTIME.exists():
        RUNTIME.rename(previous)
    try:
        staging.rename(RUNTIME)
    except OSError:
        if previous.exists() and not RUNTIME.exists():
            previous.rename(RUNTIME)
        raise
    if previous.exists():
        shutil.rmtree(previous)
    subprocess.run(
        [str(RUNTIME / "python" / "python.exe"), "-c",
         "import clean_air, pyarrow; print('Portable application imports verified.')"],
        check=True, cwd=ROOT,
    )


if __name__ == "__main__":
    main()
