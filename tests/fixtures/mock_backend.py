"""Minimal MCP server for testing. Runs as a subprocess just like real backends."""

from __future__ import annotations

import os
import sys
import time

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mock-backend")


@mcp.tool()
def mock_create(name: str, config: dict) -> dict:
    """Create a mock cloud resource with the given name and configuration."""
    if os.environ.get("MOCK_SIMULATE_SLOW"):
        time.sleep(float(os.environ["MOCK_SIMULATE_SLOW"]))
    return {"id": f"mock-{name}", "status": "created", "config": config}


@mcp.tool()
def mock_list() -> list[dict]:
    """List all mock cloud resources in the current environment."""
    return [{"id": "mock-1", "name": "resource-1"}, {"id": "mock-2", "name": "resource-2"}]


@mcp.tool()
def mock_delete(resource_id: str) -> dict:
    """Delete a mock cloud resource by its identifier."""
    return {"id": resource_id, "status": "deleted"}


@mcp.tool()
def mock_describe(resource_id: str) -> dict:
    """Get detailed information about a specific mock cloud resource."""
    return {
        "id": resource_id,
        "status": "active",
        "region": "us-east-1",
        "tags": {"env": "test"},
    }


@mcp.tool()
def mock_update(resource_id: str, updates: dict) -> dict:
    """Update configuration of an existing mock cloud resource."""
    return {"id": resource_id, "status": "updated", "updates": updates}


if __name__ == "__main__":
    if os.environ.get("MOCK_SIMULATE_CRASH"):
        sys.exit(1)
    mcp.run(transport="stdio")
