from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from mcp_sorter.models import Filters, ServerRecord
from mcp_sorter.ranking import Ranking, RankRequest, compare, rank
from mcp_sorter.runtime import Runtime


def create_mcp(runtime: Runtime) -> FastMCP:
    server = FastMCP("MCP Server Sorter", log_level="WARNING")
    annotations = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )

    @server.tool(annotations=annotations)
    def search_servers(
        query: str = "", filters: Filters | None = None, snapshot: str | None = None
    ) -> Ranking:
        """Search local catalog records and return evidence-linked deterministic rankings."""
        return rank(
            runtime.catalog,
            RankRequest(query=query, filters=filters or Filters(), snapshot=snapshot),
        )

    @server.tool(annotations=annotations)
    def compare_servers(ids: list[str], snapshot: str | None = None) -> list[ServerRecord]:
        """Compare two to four distinct local server records in a pinned catalog."""
        return compare(runtime.catalog, ids, snapshot)

    return server
