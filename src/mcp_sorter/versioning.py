import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field
from sqlalchemy import Engine, text

from mcp_sorter.artifacts import write_once
from mcp_sorter.catalog import Catalog
from mcp_sorter.models import VersionedModel
from mcp_sorter.storage import canonical


class Configuration(VersionedModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1
    name: str = Field(default="Balanced", min_length=1, max_length=80)
    version: str = Field(default="1.0.0", pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    algorithm: Literal["bm25-evidence-v1"] = "bm25-evidence-v1"
    name_weight: float = Field(default=5, gt=0, le=20, strict=True)
    description_weight: float = Field(default=1, gt=0, le=20, strict=True)
    tags_weight: float = Field(default=2, gt=0, le=20, strict=True)
    prompt_version: Literal["evidence-order-v1"] = "evidence-order-v1"
    model_profiles_version: Literal["profiles-v1"] = "profiles-v1"


class Configurations:
    def __init__(self, directory: Path, engine: Engine) -> None:
        self.directory = directory / "configurations"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.engine = engine

    def path(self, identifier: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ValueError("Invalid configuration identifier")
        return self.directory / f"{identifier}.json"

    def publish(self, configuration: Configuration) -> str:
        content = canonical(configuration.model_dump())
        identifier = hashlib.sha256(content.encode()).hexdigest()
        try:
            write_once(self.path(identifier), content)
        except FileExistsError:
            self.load(identifier)
        return identifier

    def load(self, identifier: str) -> Configuration:
        path = self.path(identifier)
        if not path.is_file():
            raise ValueError("Unknown configuration version")
        content = path.read_text("utf-8")
        if hashlib.sha256(content.encode()).hexdigest() != identifier:
            raise ValueError("Configuration checksum mismatch")
        return Configuration.model_validate_json(content)

    def seed(self) -> str:
        identifier = self.publish(Configuration())
        with self.engine.begin() as db:
            db.execute(
                text("INSERT OR IGNORE INTO state VALUES ('active_configuration',:id)"),
                {"id": identifier},
            )
        return identifier

    def versions(self) -> dict[str, Configuration]:
        return {path.stem: self.load(path.stem) for path in sorted(self.directory.glob("*.json"))}


def active_versions(engine: Engine) -> tuple[str, str]:
    with engine.connect() as db:
        values = {
            str(row[0]): str(row[1])
            for row in db.execute(
                text(
                    "SELECT key,value FROM state WHERE key IN "
                    "('active_catalog','active_configuration')"
                )
            )
        }
    if len(values) != 2:
        raise ValueError("No active catalog and configuration pair")
    return values["active_catalog"], values["active_configuration"]


def activate(
    catalog: Catalog, configurations: Configurations, snapshot: str, configuration: str
) -> None:
    catalog.validate(snapshot)
    configurations.load(configuration)
    with catalog.engine.begin() as db:
        db.exec_driver_sql("BEGIN IMMEDIATE")
        for key, value in (("active_catalog", snapshot), ("active_configuration", configuration)):
            db.execute(
                text(
                    "INSERT INTO state VALUES (:key,:value) ON CONFLICT(key) "
                    "DO UPDATE SET value=excluded.value"
                ),
                {"key": key, "value": value},
            )
        db.execute(
            text(
                "INSERT INTO activations(snapshot,configuration,created_at) "
                "VALUES (:snapshot,:configuration,:at)"
            ),
            {
                "snapshot": snapshot,
                "configuration": configuration,
                "at": datetime.now(UTC).isoformat(),
            },
        )
