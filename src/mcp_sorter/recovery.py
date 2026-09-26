import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from mcp_sorter import __version__
from mcp_sorter.artifacts import write_once
from mcp_sorter.runtime import Runtime
from mcp_sorter.selections import Selection
from mcp_sorter.settings import Settings
from mcp_sorter.versioning import active_versions

CURRENT_REVISION = "0005"
READABLE_REVISIONS = {"0001", "0002", "0003", "0004", CURRENT_REVISION}
SAFE_FILE = re.compile(
    r"state\.sqlite|catalogs/[a-f0-9]{64}\.sqlite|"
    r"configurations/[a-f0-9]{64}\.json|reports/[a-f0-9]{32}\.json"
)


class BackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    created_at: str
    application_version: str = __version__
    state_revision: str
    files: dict[str, str]


def service_lock(directory: Path) -> FileLock:
    directory.mkdir(parents=True, exist_ok=True)
    return FileLock(directory / ".service.lock", timeout=0)


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def state_revision(path: Path) -> str:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("State database integrity check failed")
        version = db.execute("SELECT value FROM state WHERE key='schema_version'").fetchone()
        revision = str(db.execute("SELECT version_num FROM alembic_version").fetchone()[0])
        if version != ("1",) or revision not in READABLE_REVISIONS:
            raise ValueError("The backup requires an unsupported state schema")
        return revision


def backup(runtime: Runtime) -> Path:
    active_versions(runtime.engine)
    root = runtime.settings.data_dir.resolve()
    backups = root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    identifier = uuid4().hex
    destination = backups / identifier
    with TemporaryDirectory(prefix=".backup-", dir=backups) as temporary:
        staging = Path(temporary)
        # SQLite's backup API captures a consistent committed database while readers continue.
        with (
            closing(
                sqlite3.connect((root / "state.sqlite").as_uri() + "?mode=ro", uri=True)
            ) as source,
            closing(sqlite3.connect(staging / "state.sqlite")) as target,
        ):
            source.backup(target)
        for folder, pattern in (
            ("catalogs", "*.sqlite"),
            ("configurations", "*.json"),
            ("reports", "*.json"),
        ):
            (staging / folder).mkdir()
            for path in sorted((root / folder).glob(pattern)):
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("Refusing to follow an artifact link")
                shutil.copyfile(path, staging / folder / path.name)
        hashes = {
            path.relative_to(staging).as_posix(): digest(path)
            for path in staging.rglob("*")
            if path.is_file()
        }
        manifest = BackupManifest(
            id=identifier,
            created_at=datetime.now(UTC).isoformat(),
            state_revision=state_revision(staging / "state.sqlite"),
            files=hashes,
        )
        write_once(staging / "manifest.json", manifest.model_dump_json(indent=2))
        validate_backup(staging)
        staging.rename(destination)
    return destination


def validate_backup(directory: Path) -> BackupManifest:
    directory = directory.resolve()
    path = directory / "manifest.json"
    if not path.is_file() or path.stat().st_size > 10_000_000:
        raise ValueError("Missing or oversized backup manifest")
    manifest = BackupManifest.model_validate_json(path.read_text("utf-8"))
    if "state.sqlite" not in manifest.files or manifest.state_revision not in READABLE_REVISIONS:
        raise ValueError("The backup requires an unsupported state schema")
    for name, expected in manifest.files.items():
        if SAFE_FILE.fullmatch(name) is None or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("Unsafe artifact path or checksum in backup")
        artifact = directory / name
        if (
            not artifact.is_file()
            or artifact.is_symlink()
            or not artifact.resolve().is_relative_to(directory)
        ):
            raise ValueError("Missing or linked backup artifact")
        if digest(artifact) != expected:
            raise ValueError("Backup checksum mismatch")
    if state_revision(directory / "state.sqlite") != manifest.state_revision:
        raise ValueError("Backup schema does not match manifest")
    with closing(
        sqlite3.connect((directory / "state.sqlite").as_uri() + "?mode=ro", uri=True)
    ) as db:
        values = dict(db.execute("SELECT key,value FROM state"))
        for key, folder, extension in (
            ("active_catalog", "catalogs", "sqlite"),
            ("active_configuration", "configurations", "json"),
        ):
            identifier = values.get(key)
            if (
                identifier is not None
                and f"{folder}/{identifier}.{extension}" not in manifest.files
            ):
                raise ValueError("Backup is missing an active artifact")
        if manifest.state_revision in {"0004", "0005"}:
            for (raw,) in db.execute(
                "SELECT result FROM jobs WHERE status='completed' AND result IS NOT NULL"
            ):
                report_id = json.loads(raw).get("report_id")
                if report_id and f"reports/{report_id}.json" not in manifest.files:
                    raise ValueError("Backup is missing a completed evaluation report")
    return manifest


def restore(runtime: Runtime, source: Path, destination: Path) -> dict[str, str]:
    manifest = validate_backup(source)
    destination = destination.resolve()
    current = runtime.settings.data_dir.resolve()
    if destination.exists() or destination.is_relative_to(current):
        raise ValueError("Restore requires a new data directory outside the current one")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with service_lock(current):
        before = backup(runtime)
        with TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temporary:
            staging = Path(temporary)
            for name in manifest.files:
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name, target)
                if digest(target) != manifest.files[name]:
                    raise ValueError("Backup changed while restoration was in progress")
            # Open the copied database to apply forward-compatible migrations only.
            restored = Runtime(Settings(data_dir=staging, worker_enabled=False))
            try:
                with closing(
                    sqlite3.connect((before / "state.sqlite").as_uri() + "?mode=ro", uri=True)
                ) as db:
                    selections = [
                        Selection.model_validate_json(row[0])
                        for row in db.execute("SELECT payload FROM collections")
                    ]
                with restored.engine.begin() as db:
                    for selection in selections:
                        db.execute(
                            text(
                                "INSERT INTO collections(id,payload) VALUES (:id,:payload) "
                                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload"
                            ),
                            {"id": selection.id, "payload": selection.model_dump_json()},
                        )
                    db.execute(
                        text(
                            "UPDATE jobs SET status='queued',owner=NULL,lease_until=NULL "
                            "WHERE status='running'"
                        )
                    )
                snapshot, configuration = active_versions(restored.engine)
                restored.catalog.validate(snapshot)
                restored.configurations.load(configuration)
            finally:
                restored.close()
            write_once(
                staging / "restore.json",
                json.dumps({"backup": manifest.id, "pre_restore_backup": before.name}),
            )
            staging.rename(destination)
    return {"restored_directory": str(destination), "pre_restore_backup": str(before)}
