"""MCP server discovery and reproducible evaluation."""

import json
from importlib.resources import files

__version__: str = json.loads(files(__package__).joinpath("release.json").read_text("utf-8"))[
    "application_version"
]
