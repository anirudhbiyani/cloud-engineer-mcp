"""Reference Fly.io backend plugin for cloud-engineer-mcp.

This is the smallest possible useful plugin: it reads ``FLY_API_TOKEN`` from
the environment and, if present, registers a single Fly.io MCP backend.

To install locally for development:

    cd examples/plugin-flyio
    uv pip install -e .

Then ``uv run cloud-engineer-mcp plugins`` will list it.
"""

from __future__ import annotations

import os

from cloud_engineer_mcp.config import BackendConfig
from cloud_engineer_mcp.discovery import DiscoveredAccount

FLY_TOKEN_ENV = "FLY_API_TOKEN"


class FlyBackendProvider:
    """Single-backend Fly.io provider authenticated via env token."""

    name = "fly"

    async def discover(self) -> list[DiscoveredAccount]:
        token = os.environ.get(FLY_TOKEN_ENV, "").strip()
        if not token:
            return []
        return [
            DiscoveredAccount(
                provider=self.name,
                profile_id="default",
                display_name="Fly.io",
                env_vars={FLY_TOKEN_ENV: token},
                credentials_valid=True,
            )
        ]

    def expand(self, account: DiscoveredAccount) -> BackendConfig:
        # Replace with the real Fly.io MCP server package once available.
        return BackendConfig(
            display_name=account.display_name,
            command="npx",
            args=["-y", "@fly/mcp-server@latest"],
            env=account.env_vars,
            enabled=True,
            startup_timeout_seconds=60,
        )
