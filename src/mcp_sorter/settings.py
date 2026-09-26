from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SORTER_", extra="forbid")

    mode: Literal["demo", "live"] = "demo"
    data_dir: Path = Path(".data")
    allowed_hosts: tuple[str, ...] = ()
    request_timeout: float = Field(default=10, gt=0, le=60)
    spending_limit_usd: float = Field(default=0, ge=0)
