from datetime import UTC, datetime
from typing import Any

import httpx

from mcp_sorter.models import ServerRecord
from mcp_sorter.network import client
from mcp_sorter.settings import Settings


def bounded_json(http: httpx.Client, url: str, params: dict[str, str] | None = None) -> Any:
    with http.stream("GET", url, params=params) as response:
        response.raise_for_status()
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) > 2_000_000:
                raise ValueError("Upstream response exceeds two megabytes")
        import json

        return json.loads(chunks)


def fetch_registry(
    settings: Settings, transport: httpx.BaseTransport | None = None
) -> tuple[list[ServerRecord], list[str]]:
    records: dict[str, ServerRecord] = {}
    rejected: list[str] = []
    cursor = ""
    seen: set[str] = set()
    with client(settings, transport) as http:
        for _ in range(100):
            data = bounded_json(
                http,
                "https://registry.modelcontextprotocol.io/v0.1/servers",
                {"cursor": cursor, "limit": "100", "version": "latest"},
            )
            if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
                raise ValueError("Unexpected registry response")
            for index, entry in enumerate(data["servers"]):
                try:
                    raw = entry["server"]
                    official = entry.get("_meta", {}).get(
                        "io.modelcontextprotocol.registry/official", {}
                    )
                    record = ServerRecord.model_validate(
                        {
                            "id": raw["name"],
                            "name": raw.get("title") or raw["name"],
                            "version": raw["version"],
                            "description": raw["description"],
                            "category": "uncategorized",
                            "tags": [],
                            "status": official.get("status", "unknown"),
                            "updated_at": official.get("updatedAt", datetime.now(UTC).isoformat()),
                            "evidence": [
                                {
                                    "id": "registry.metadata",
                                    "kind": "registry",
                                    "url": "https://registry.modelcontextprotocol.io",
                                    "statement": raw["description"][:1000],
                                }
                            ],
                        }
                    )
                    records[record.id] = record
                except (KeyError, TypeError, ValueError):
                    rejected.append(f"Invalid entry at page {len(seen) + 1}, index {index}")
            next_cursor = data.get("metadata", {}).get("nextCursor")
            if not next_cursor:
                return list(records.values()), rejected
            if not isinstance(next_cursor, str) or next_cursor in seen:
                raise ValueError("Registry returned a repeated or invalid cursor")
            seen.add(next_cursor)
            cursor = next_cursor
    raise ValueError("Registry pagination limit exceeded")


def fetch_repository(
    owner: str, repository: str, settings: Settings, transport: httpx.BaseTransport | None = None
) -> dict[str, object]:
    import re

    if not all(re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", item) for item in (owner, repository)):
        raise ValueError("Invalid repository name")
    with client(settings, transport) as http:
        data = bounded_json(http, f"https://api.github.com/repos/{owner}/{repository}")
    if not isinstance(data, dict) or not isinstance(data.get("id"), int):
        raise ValueError("Unexpected repository response")
    return {key: data.get(key) for key in ("id", "html_url", "archived", "pushed_at", "license")}
