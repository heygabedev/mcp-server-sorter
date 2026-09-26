from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, field_validator


class VersionedModel(BaseModel):
    schema_version: Literal[1] = 1

    @field_validator("schema_version", mode="before")
    @classmethod
    def reject_boolean_version(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Schema version must be an integer")
        return value


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-z0-9._-]+$", max_length=150)
    url: HttpUrl
    statement: str = Field(min_length=1, max_length=1000)
    kind: Literal["fixture", "registry", "repository", "probe"]


class ServerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9._/-]+$", max_length=200)
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=4000)
    category: str = Field(min_length=1, max_length=80)
    tags: tuple[str, ...] = Field(max_length=30)
    transport: Literal["stdio", "streamable-http", "unknown"] = "unknown"
    auth: Literal["none", "api_key", "oauth", "unknown"] = "unknown"
    deployment: Literal["local", "remote", "unknown"] = "unknown"
    license: str | None = Field(default=None, max_length=100)
    status: Literal["active", "deprecated", "unknown"] = "unknown"
    updated_at: AwareDatetime
    evidence: tuple[Evidence, ...] = Field(min_length=1, max_length=30)
    simulated: bool = Field(default=False, strict=True)


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    category: str | None = None
    transport: Literal["stdio", "streamable-http", "unknown"] | None = None
    auth: Literal["none", "api_key", "oauth", "unknown"] | None = None
    deployment: Literal["local", "remote", "unknown"] | None = None
    license: str | None = None
    include_deprecated: bool = Field(default=False, strict=True)

    def matches(self, server: ServerRecord) -> bool:
        if server.status == "deprecated" and not self.include_deprecated:
            return False
        fields = ("category", "transport", "auth", "deployment", "license")
        return all(
            getattr(self, field) is None or getattr(self, field) == getattr(server, field)
            for field in fields
        )
