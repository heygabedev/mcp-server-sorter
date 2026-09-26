from mcp_sorter.catalog import Catalog
from mcp_sorter.settings import Settings
from mcp_sorter.storage import open_state
from mcp_sorter.versioning import Configurations


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = open_state(settings.data_dir)
        self.catalog = Catalog(settings.data_dir, self.engine)
        self.configurations = Configurations(settings.data_dir, self.engine)
        self.configurations.seed()
        if settings.mode == "demo":
            self.catalog.seed_demo()

    def close(self) -> None:
        self.engine.dispose()
