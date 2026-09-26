import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal
from uuid import uuid4
from zipfile import ZipFile

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from mcp_sorter.artifacts import write_once
from mcp_sorter.recovery import backup, digest, service_lock, state_revision
from mcp_sorter.runtime import Runtime
from mcp_sorter.settings import Settings


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal[1]
    application_version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:rc\d+)?$")
    python: Literal["3.13"]
    readable_state_revisions: list[str] = Field(min_length=1, max_length=100)


class InstalledRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    filename: str = Field(pattern=r"^mcp_server_sorter-[0-9a-z.]+-py3-none-any\.whl$")
    manifest: ReleaseManifest


class ReleasePointer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: str = Field(pattern=r"^[a-f0-9]{64}$")
    previous: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    backup: str | None = None


def directory(settings: Settings, identifier: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("Invalid release digest")
    return settings.data_dir.resolve() / "releases" / identifier


def python_path(release_directory: Path) -> Path:
    return (
        release_directory
        / "environment"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )


def installed(settings: Settings, identifier: str) -> InstalledRelease:
    root = directory(settings, identifier)
    record = InstalledRelease.model_validate_json((root / "installed.json").read_text("utf-8"))
    if record.sha256 != identifier or digest(root / record.filename) != identifier:
        raise ValueError("Pinned wheel checksum mismatch")
    if not python_path(root).is_file():
        raise ValueError("The release environment is missing")
    return record


def stage(settings: Settings, wheel: Path, sha256: str, wheelhouse: Path) -> InstalledRelease:
    destination = directory(settings, sha256)
    if digest(wheel) != sha256:
        raise ValueError("Wheel does not match the operator's pinned SHA-256")
    with ZipFile(wheel) as archive:
        entry = archive.getinfo("mcp_sorter/release.json")
        if entry.file_size > 16_384:
            raise ValueError("Oversized release manifest")
        manifest = ReleaseManifest.model_validate_json(archive.read(entry))
        if "mcp_sorter/static/index.html" not in archive.namelist():
            raise ValueError("Release wheel does not contain the interface")
    record = InstalledRelease(sha256=sha256, filename=wheel.name, manifest=manifest)
    if record.filename != f"mcp_server_sorter-{manifest.application_version}-py3-none-any.whl":
        raise ValueError("Wheel filename and release manifest disagree")
    destination.mkdir(parents=True, exist_ok=True)
    with FileLock(destination.parent / ".stage.lock", timeout=0):
        if (destination / "installed.json").exists():
            return installed(settings, sha256)
        target = destination / wheel.name
        shutil.copyfile(wheel, target)
        if digest(target) != sha256:
            raise ValueError("Wheel changed while being staged")
        subprocess.run(
            [sys.executable, "-m", "venv", str(destination / "environment")],
            check=True,
            timeout=120,
        )
        executable = python_path(destination)
        subprocess.run(
            [
                str(executable),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-index",
                "--only-binary=:all:",
                "--find-links",
                str(wheelhouse.resolve()),
                str(target),
            ],
            check=True,
            timeout=300,
        )
        result = subprocess.run(
            [str(executable), "-m", "mcp_sorter.cli", "version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        if result.stdout.strip() != manifest.application_version:
            raise ValueError("Installed release failed its version check")
        write_once(destination / "installed.json", record.model_dump_json(indent=2))
    return record


def pointer(settings: Settings) -> ReleasePointer | None:
    path = settings.data_dir / "releases" / "active.json"
    return ReleasePointer.model_validate_json(path.read_text("utf-8")) if path.exists() else None


def activate_release(settings: Settings, identifier: str) -> ReleasePointer:
    record = installed(settings, identifier)
    with service_lock(settings.data_dir):
        old = pointer(settings)
        state = settings.data_dir / "state.sqlite"
        previous_backup = None
        if state.exists():
            if state_revision(state) not in record.manifest.readable_state_revisions:
                raise ValueError("Target release cannot read the current state schema")
            if old is not None and old.active == identifier:
                return old
            runtime = Runtime(settings)
            try:
                previous_backup = backup(runtime).name
            finally:
                runtime.close()
        result = ReleasePointer(
            active=identifier, previous=old.active if old else None, backup=previous_backup
        )
        path = settings.data_dir / "releases" / "active.json"
        temporary = path.with_name(f".{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as file:
                file.write(result.model_dump_json(indent=2))
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return result


def rollback_release(settings: Settings) -> ReleasePointer:
    current = pointer(settings)
    if current is None or current.previous is None:
        raise ValueError("No previously activated release is available")
    return activate_release(settings, current.previous)


def serve_release(settings: Settings, port: int) -> None:
    current = pointer(settings)
    if current is None:
        raise ValueError("Activate a staged release first")
    installed(settings, current.active)
    executable = python_path(directory(settings, current.active))
    subprocess.run(
        [str(executable), "-m", "mcp_sorter.cli", "serve", "--port", str(port)],
        env={**os.environ, "SORTER_DATA_DIR": str(settings.data_dir.resolve())},
        check=True,
    )
