"""BackendProcess: wraps a single downstream MCP server subprocess."""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import re
import shutil
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, Tool

from cloud_engineer_mcp.config import BackendConfig
from cloud_engineer_mcp.errors import (
    BackendStartupError,
    BackendTimeoutError,
    BackendUnavailableError,
)
from cloud_engineer_mcp.observability.logging import get_logger
from cloud_engineer_mcp.observability.tracing import get_tracer

log = get_logger("backends.process")
tracer = get_tracer("cloud_engineer_mcp.backends")


_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _resolve_headers(headers: dict[str, str]) -> dict[str, str]:
    """Expand ${ENV_VAR} references in header values at start time.

    Lets users keep tokens in environment variables instead of config files::

        headers:
          Authorization: "Bearer ${GCP_ACCESS_TOKEN}"
    """
    resolved: dict[str, str] = {}
    for key, value in headers.items():
        resolved[key] = _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    return resolved


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


_RESTART_JITTER = 0.25  # ±25% jitter on backoff to desynchronize restart storms.


def _restart_backoff_seconds(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with full jitter, capped at `cap` seconds.

    `attempt` is 1-indexed (the nth restart). Returns 0 when base is 0 (the
    common test override) so unit tests don't wait.
    """
    if base <= 0:
        return 0.0
    delay: float = min(base * (2 ** (attempt - 1)), cap)
    jitter: float = delay * _RESTART_JITTER * (random.random() * 2 - 1)
    result: float = max(0.0, delay + jitter)
    return result


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
        """Launch the backend (subprocess or remote), initialize MCP session, discover tools."""
        with tracer.start_as_current_span("backend.start") as span:
            span.set_attribute("backend.id", self.backend_id)
            span.set_attribute("backend.transport", self.config.transport)
            if self.config.transport == "stdio":
                span.set_attribute("backend.command", self.config.command)
            else:
                span.set_attribute("backend.url", self.config.url)
            await self._start_impl(span)

    async def _start_impl(self, span: object) -> None:
        old_status = self.status
        self.status = BackendStatus.STARTING
        log.info(
            "backend.starting",
            backend_id=self.backend_id,
            transport=self.config.transport,
            target=self.config.url or self.config.command,
            old_status=old_status.value,
            new_status=self.status.value,
        )

        try:
            stack = AsyncExitStack()
            self._exit_stack = stack
            await stack.__aenter__()

            if self.config.transport == "http":
                auth = None
                if self.config.auth_refresh_command:
                    from cloud_engineer_mcp.auth import CommandTokenAuth

                    auth = CommandTokenAuth(
                        command=self.config.auth_refresh_command,
                        header_name=self.config.auth_header_name,
                        template=self.config.auth_header_template,
                    )
                streams_ctx = streamablehttp_client(
                    url=self.config.url,
                    headers=_resolve_headers(self.config.headers),
                    timeout=self.config.http_timeout_seconds,
                    auth=auth,
                )
                streams_result = await asyncio.wait_for(
                    stack.enter_async_context(streams_ctx),
                    timeout=self.config.startup_timeout_seconds,
                )
                # streamablehttp_client yields (read, write, get_session_id);
                # we only need the streams.
                read_stream, write_stream = streams_result[0], streams_result[1]
            else:
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
            span.set_attribute("backend.tool_count", len(self.tools))  # type: ignore[attr-defined]
            log.info(
                "backend.ready",
                backend_id=self.backend_id,
                tool_count=len(self.tools),
            )

        except TimeoutError as exc:
            self.status = BackendStatus.FAILED
            span.record_exception(exc)  # type: ignore[attr-defined]
            await self._cleanup_stack()
            raise BackendStartupError(
                self.backend_id,
                f"Startup timed out after {self.config.startup_timeout_seconds}s",
            ) from exc
        except Exception as exc:
            self.status = BackendStatus.FAILED
            span.record_exception(exc)  # type: ignore[attr-defined]
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
            # Best-effort cleanup: swallow Exception and CancelledError so a
            # parent task being cancelled or a backend already-dead error can't
            # prevent us from releasing the stack. KeyboardInterrupt and
            # SystemExit still propagate.
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(self._exit_stack.aclose(), timeout=5.0)
            self._exit_stack = None

    async def restart(self) -> None:
        """Restart the backend if within max_restarts, with backoff between attempts."""
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
        backoff = _restart_backoff_seconds(
            self.restart_count,
            self.config.restart_backoff_base_seconds,
            self.config.restart_backoff_max_seconds,
        )
        self.status = BackendStatus.RESTARTING
        log.warning(
            "backend.restarting",
            backend_id=self.backend_id,
            restart_count=self.restart_count,
            backoff_seconds=round(backoff, 2),
        )

        await self.stop()
        if backoff > 0:
            await asyncio.sleep(backoff)
        await self.start()

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_callback: Any | None = None,
    ) -> CallToolResult:
        """Forward a tool call to the backend session.

        When `progress_callback` is provided, the underlying MCP client will
        invoke it for every progress notification the backend emits — see
        `mcp.client.session.ClientSession.call_tool`.
        """
        if self.session is None or self.status != BackendStatus.READY:
            raise BackendUnavailableError(
                self.backend_id,
                f"Backend is {self.status.value}, cannot call tool '{name}'",
            )
        try:
            result = await self.session.call_tool(
                name,
                arguments,
                progress_callback=progress_callback,
            )
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
