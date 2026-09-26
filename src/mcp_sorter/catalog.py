import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, text

from mcp_sorter.models import ServerRecord
from mcp_sorter.storage import canonical, get_value

SCHEMA_VERSION = 1


class Catalog:
    def __init__(self, directory: Path, engine: Engine) -> None:
        self.directory = directory / "catalogs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.engine = engine

    def path(self, snapshot: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", snapshot):
            raise ValueError("Invalid catalog identifier")
        return self.directory / f"{snapshot}.sqlite"

    def publish(self, records: list[ServerRecord], as_of: str) -> str:
        if not records:
            raise ValueError("Refusing to publish an empty catalog")
        if len({record.id for record in records}) != len(records):
            raise ValueError("Duplicate server identifiers")
        normalized = [s.model_dump(mode="json") for s in sorted(records, key=lambda s: s.id)]
        payload = canonical({"schema": SCHEMA_VERSION, "as_of": as_of, "records": normalized})
        snapshot = hashlib.sha256(payload.encode()).hexdigest()
        target = self.path(snapshot)
        if target.exists():
            self.validate(snapshot)
            return snapshot
        temporary = self.directory / f".{uuid4().hex}.tmp"
        try:
            with closing(sqlite3.connect(temporary)) as db, db:
                db.executescript(
                    "CREATE TABLE manifest (payload TEXT NOT NULL);"
                    "CREATE TABLE servers (id TEXT PRIMARY KEY, payload TEXT NOT NULL);"
                    "CREATE VIRTUAL TABLE search USING fts5("
                    "id UNINDEXED, name, description, tags, tokenize='unicode61');"
                )
                db.execute("INSERT INTO manifest VALUES (?)", (payload,))
                for record in normalized:
                    db.execute(
                        "INSERT INTO servers VALUES (?,?)", (record["id"], canonical(record))
                    )
                    db.execute(
                        "INSERT INTO search VALUES (?,?,?,?)",
                        (
                            record["id"],
                            record["name"],
                            record["description"],
                            " ".join(record["tags"]),
                        ),
                    )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return snapshot

    @contextmanager
    def connect(self, snapshot: str) -> Iterator[sqlite3.Connection]:
        with closing(
            sqlite3.connect(self.path(snapshot).resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            yield db

    def validate(self, snapshot: str) -> None:
        with self.connect(snapshot) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Catalog integrity check failed")
            payload = db.execute("SELECT payload FROM manifest").fetchone()[0]
            if hashlib.sha256(payload.encode()).hexdigest() != snapshot:
                raise ValueError("Catalog manifest checksum mismatch")
            manifest = json.loads(payload)
            if manifest["schema"] != SCHEMA_VERSION:
                raise ValueError("Unsupported catalog schema")
            actual = [
                json.loads(row[0]) for row in db.execute("SELECT payload FROM servers ORDER BY id")
            ]
            if actual != manifest["records"]:
                raise ValueError("Catalog records do not match manifest")

    def activate(self, snapshot: str) -> None:
        self.validate(snapshot)
        with self.engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO state VALUES ('active_catalog', :id) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
                ),
                {"id": snapshot},
            )
            db.execute(
                text("INSERT INTO activations (snapshot, created_at) VALUES (:id,:at)"),
                {"id": snapshot, "at": datetime.now(UTC).isoformat()},
            )

    def active(self) -> str:
        snapshot = get_value(self.engine, "active_catalog")
        if snapshot is None:
            raise ValueError("No active catalog; run mcp-sorter demo first")
        return snapshot

    def records(self, snapshot: str | None = None) -> list[ServerRecord]:
        with self.connect(snapshot or self.active()) as db:
            return [
                ServerRecord.model_validate_json(row[0])
                for row in db.execute("SELECT payload FROM servers ORDER BY id")
            ]

    def snapshots(self) -> list[str]:
        return sorted(path.stem for path in self.directory.glob("*.sqlite"))

    def seed_demo(self) -> str:
        raw = json.loads(files("mcp_sorter.data").joinpath("catalog.json").read_text("utf-8"))
        snapshot = self.publish(
            [ServerRecord.model_validate(r) for r in raw["records"]], raw["as_of"]
        )
        if get_value(self.engine, "active_catalog") is None:
            self.activate(snapshot)
        return snapshot
