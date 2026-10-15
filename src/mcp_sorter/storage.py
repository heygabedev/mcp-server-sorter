import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from filelock import FileLock
from sqlalchemy import (
    Column,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    inspect,
    text,
)

metadata = MetaData()
state = Table("state", metadata, Column("key", String, primary_key=True), Column("value", String))
activations = Table(
    "activations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("snapshot", String, nullable=False),
    Column("created_at", String, nullable=False),
)


def open_state(directory: Path, *, service_locked: bool = False) -> Engine:
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite:///" + str((directory / "state.sqlite").resolve()))

    @event.listens_for(engine, "connect")
    def configure(connection: Any, _: Any) -> None:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")

    try:
        with FileLock(directory / ".migration.lock", timeout=30), engine.begin() as db:
            db.exec_driver_sql("BEGIN IMMEDIATE")
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
            config.attributes["connection"] = db
            if inspect(db).has_table("state"):
                version: str = db.execute(
                    text("SELECT value FROM state WHERE key='schema_version'")
                ).scalar_one()
                if version != "1":
                    raise ValueError(f"Unsupported state schema: {version}")
                revision: str = db.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                if revision != ScriptDirectory.from_config(config).get_current_head():
                    from mcp_sorter.recovery import backup_directory, service_lock

                    with nullcontext() if service_locked else service_lock(directory):
                        backup_directory(directory)
                        command.upgrade(config, "head")
                    return engine
            command.upgrade(config, "head")
    except Exception:
        engine.dispose()
        raise
    return engine


def get_value(engine: Engine, key: str) -> str | None:
    with engine.connect() as db:
        value = db.execute(state.select().where(state.c.key == key)).first()
        return None if value is None else str(value.value)


def set_value(engine: Engine, key: str, value: str) -> None:
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO state VALUES (:key,:value) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            ),
            {"key": key, "value": value},
        )


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
