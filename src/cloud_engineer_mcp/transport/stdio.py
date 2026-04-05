"""stdio transport runner for the MCP gateway."""

from __future__ import annotations

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from cloud_engineer_mcp.config import CloudEngineerConfig
from cloud_engineer_mcp.observability.logging import get_logger

log = get_logger("transport.stdio")


async def run_stdio(server: Server, config: CloudEngineerConfig) -> None:
    """Run the MCP server over stdio."""
    log.info("transport.stdio.starting")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
