import json
from pathlib import Path

from mcp_sorter.api import create_app

target = Path(__file__).resolve().parents[1] / "web/openapi.json"
target.write_text(
    json.dumps(create_app().openapi(), indent=2) + "\n", encoding="utf-8", newline="\n"
)
