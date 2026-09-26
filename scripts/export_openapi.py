import json
import sys
from pathlib import Path

from mcp_sorter.api import create_app

target = Path(__file__).resolve().parents[1] / "web/openapi.json"
schema = create_app().openapi()
if "--check" in sys.argv:
    if json.loads(target.read_text("utf-8")) != schema:
        raise SystemExit("The checked-in OpenAPI schema differs from the application")
else:
    target.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")
