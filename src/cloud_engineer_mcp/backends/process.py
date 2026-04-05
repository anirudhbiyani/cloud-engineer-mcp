"""BackendProcess: wraps a single downstream MCP server subprocess."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, Tool

from cloud_engineer_mcp.config import BackendConfig
from cloud_engineer_mcp.errors import (
    BackendStartupError,
    BackendTimeoutError,
    BackendUnavailableError,
)
from cloud_engineer_mcp.observability.logging import get_logger

log = get_logger("backends.process")


def _resolve_command(command: str) -> str:
    """Resolve a command name to its full path using shutil.which.

    Cursor may launch the gateway without the user's full PATH, so commands
    like 'uvx' and 'npx' in /opt/homebrew/bin won't be found. This resolves
    them at process creation time using the current environment's PATH.
    """
    resolved = shutil.which(command)
    if resolved:
        return resolved
    return command


class BackendStatus(Enum):
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    STOPPED = "stopped"
    RESTARTING = "restarting"


@dataclass
class BackendProcess:
    backend_id: str
    config: BackendConfig
    status: BackendStatus = BackendStatus.STOPPED
    session: ClientSession | None = None
    tools: list[Tool] = field(default_factory=list)
    restart_count: int = 0
    _exit_stack: AsyncExitStack | None = field(default=None, repr=False)

    async def start(self) -> None:
        """Launch subprocess, initialize MCP session, discover tools."""
        old_status = self.status
        self.status = BackendStatus.STARTING
        log.info(
            "backend.starting",
            backend_id=self.backend_id,
            command=self.config.command,
            old_status=old_status.value,
            new_status=self.status.value,
        )

        merged_env = {**os.environ, **self.config.env}
        resolved_cmd = _resolve_command(self.config.command)
        if resolved_cmd != self.config.command:
            log.debug(
                "backend.command_resolved",
                backend_id=self.backend_id,
                original=self.config.command,
                resolved=resolved_cmd,
            )
        params = StdioServerParameters(
            command=resolved_cmd,
            args=self.config.args,
            env=merged_env,
        )

        try:
            stack = AsyncExitStack()
            self._exit_stack = stack
            await stack.__aenter__()

            read_stream, write_stream = await asyncio.wait_for(
                stack.enter_async_context(stdio_client(params)),
                timeout=self.config.startup_timeout_seconds,
            )

            session = await asyncio.wait_for(
                stack.enter_async_context(ClientSession(read_stream, write_stream)),
                timeout=self.config.startup_timeout_seconds,
            )

            await asyncio.wait_for(
                session.initialize(),
                timeout=self.config.startup_timeout_seconds,
            )

            tools_result = await asyncio.wait_for(
                session.list_tools(),
                timeout=self.config.startup_timeout_seconds,
            )

            self.session = session
            self.tools = list(tools_result.tools)
            self.status = BackendStatus.READY
            log.info(
                "backend.ready",
                backend_id=self.backend_id,
                tool_count=len(self.tools),
            )

        except TimeoutError as exc:
            self.status = BackendStatus.FAILED
            await self._cleanup_stack()
            raise BackendStartupError(
                self.backend_id,
                f"Startup timed out after {self.config.startup_timeout_seconds}s",
            ) from exc
        except Exception as exc:
            self.status = BackendStatus.FAILED
            await self._cleanup_stack()
            log.error(
                "backend.failed",
                backend_id=self.backend_id,
                error=str(exc),
            )
            raise BackendStartupError(self.backend_id, str(exc)) from exc

    async def stop(self) -> None:
        """Gracefully close session and exit the subprocess."""
        old_status = self.status
        log.info(
            "backend.stopping",
            backend_id=self.backend_id,
            old_status=old_status.value,
        )
        self.session = None
        self.tools = []
        await self._cleanup_stack()
        self.status = BackendStatus.STOPPED
        log.info(
            "backend.stopped",
            backend_id=self.backend_id,
        )

    async def _cleanup_stack(self) -> None:
        if self._exit_stack is not None:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(self._exit_stack.aclose(), timeout=5.0)
            self._exit_stack = None

    async def restart(self) -> None:
        """Restart the backend if within max_restarts."""
        if self.restart_count >= self.config.max_restarts:
            log.error(
                "backend.max_restarts_exceeded",
                backend_id=self.backend_id,
                restart_count=self.restart_count,
                max_restarts=self.config.max_restarts,
            )
            self.status = BackendStatus.FAILED
            return

        self.restart_count += 1
        self.status = BackendStatus.RESTARTING
        log.warning(
            "backend.restarting",
            backend_id=self.backend_id,
            restart_count=self.restart_count,
        )

        await self.stop()
        await self.start()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Forward a tool call to the backend session."""
        if self.session is None or self.status != BackendStatus.READY:
            raise BackendUnavailableError(
                self.backend_id,
                f"Backend is {self.status.value}, cannot call tool '{name}'",
            )
        try:
            result = await self.session.call_tool(name, arguments)
        except TimeoutError as exc:
            raise BackendTimeoutError(
                self.backend_id,
                f"Tool call '{name}' timed out",
            ) from exc
        return result

    async def refresh_tools(self) -> list[Tool]:
        """Re-discover tools from the backend."""
        if self.session is None or self.status != BackendStatus.READY:
            raise BackendUnavailableError(
                self.backend_id,
                "Cannot refresh tools: backend not ready",
            )
        tools_result = await self.session.list_tools()
        self.tools = list(tools_result.tools)
        log.info(
            "backend.tools_refreshed",
            backend_id=self.backend_id,
            tool_count=len(self.tools),
        )
        return self.tools

    async def health_check(self) -> bool:
        """Check backend health by listing tools within a timeout."""
        if self.session is None or self.status != BackendStatus.READY:
            return False
        try:
            await asyncio.wait_for(self.session.list_tools(), timeout=5.0)
            return True
        except Exception:
            return False
