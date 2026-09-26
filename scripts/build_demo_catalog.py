"""Rebuild the deliberately synthetic catalog fixture from its source rows."""

import json
from pathlib import Path

ROWS = [
    ("github", "GitHub", "development", "repositories pull requests code reviews", "oauth"),
    ("notion", "Notion", "knowledge", "wiki pages notes knowledge workspace", "oauth"),
    ("slack", "Slack", "communication", "channels messages conversation threads", "oauth"),
    ("figma", "Figma", "design", "design frames components layouts", "oauth"),
    ("linear", "Linear", "development", "engineering issues cycles roadmaps", "api_key"),
    (
        "postgres",
        "PostgreSQL",
        "database",
        "postgres relational SQL schemas transactions",
        "api_key",
    ),
    ("sqlite", "SQLite", "database", "sqlite embedded database local tables", "none"),
    ("filesystem", "Filesystem", "files", "files directories disk folders", "none"),
    ("browser", "Browser", "development", "browser screenshots page inspection automation", "none"),
    ("memory", "Memory", "knowledge", "memory entities relationships persistent graph", "none"),
    ("search", "Web Search", "research", "search websites discovery indexed results", "api_key"),
    ("fetch", "Page Fetch", "research", "fetch URLs retrieve article content", "none"),
    ("drive", "Google Drive", "files", "drive documents shared storage permissions", "oauth"),
    ("sheets", "Google Sheets", "data", "sheets spreadsheets cells workbook formulas", "oauth"),
    ("calendar", "Calendar", "productivity", "calendar events availability appointments", "oauth"),
    ("mail", "Mail", "communication", "mail email inbox correspondence", "oauth"),
    ("jira", "Jira", "development", "jira tickets sprints backlog planning", "oauth"),
    ("trello", "Trello", "productivity", "trello boards cards kanban", "api_key"),
    ("redis", "Redis", "database", "redis cache keys expiry values", "api_key"),
    (
        "elastic",
        "Elasticsearch",
        "database",
        "elasticsearch indexes aggregation fulltext analytics",
        "api_key",
    ),
    ("sentry", "Sentry", "operations", "sentry exceptions stacktraces error tracking", "api_key"),
    ("grafana", "Grafana", "operations", "grafana dashboards metrics observability", "api_key"),
    ("git", "Git", "development", "git commits branches diffs history", "none"),
    ("docker", "Docker", "operations", "docker containers images inspect workloads", "none"),
    ("kubernetes", "Kubernetes", "operations", "kubernetes pods clusters deployments", "api_key"),
    ("weather", "Weather", "data", "weather forecasts temperature precipitation", "api_key"),
    ("maps", "Maps", "data", "maps geocoding directions places", "api_key"),
    ("arxiv", "arXiv", "research", "arxiv papers abstracts academic publications", "none"),
    ("time", "Time", "productivity", "time timezone clocks conversions", "none"),
    (
        "sqlite-legacy",
        "SQLite Legacy",
        "database",
        "sqlite legacy tables deprecated database",
        "none",
    ),
]


def main() -> None:
    records = []
    for slug, name, category, capabilities, auth in ROWS:
        local = auth == "none"
        description = f"Simulated {name} integration for {capabilities}."
        records.append(
            {
                "id": f"demo/{slug}",
                "name": name,
                "version": "1.0.0-demo",
                "description": description,
                "category": category,
                "tags": capabilities.split(),
                "auth": auth,
                "transport": "stdio" if local else "streamable-http",
                "deployment": "local" if local else "remote",
                "license": "MIT" if local else None,
                "status": "deprecated" if slug.endswith("legacy") else "active",
                "updated_at": "2026-09-01T00:00:00Z",
                "simulated": True,
                "evidence": [
                    {
                        "id": f"fixture.{slug}.capabilities",
                        "kind": "fixture",
                        "url": f"https://example.invalid/demo/{slug}",
                        "statement": description,
                    }
                ],
            }
        )
    target = Path(__file__).resolve().parents[1] / "src/mcp_sorter/data/catalog.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "as_of": "2026-09-01T00:00:00Z",
                "notice": "Synthetic showcase metadata, not vendor assertions.",
                "records": records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
