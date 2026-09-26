from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "editable":
            return
        assets = Path(self.root) / "web" / "dist"
        if not (assets / "index.html").is_file():
            raise RuntimeError(
                "Build the web interface before packaging: npm run build --prefix web"
            )
        build_data["force_include"][str(assets)] = "mcp_sorter/static"
