"""BackendManager: orchestrates multiple BackendProcess instances."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from mcp.types import CallToolResult

from cloud_engineer_mcp.backends.process import BackendProcess
from cloud_engineer_mcp.backends.registry import ToolRegistry, split_namespaced_name
from cloud_engineer_mcp.config import BackendConfig
from cloud_engineer_mcp.errors import ToolNotFoundError
from cloud_engineer_mcp.observability.logging import get_logger

log = get_logger("backends.manager")

DEFAULT_EAGER_LIMIT = 10


class BackendManager:
    """Manages the lifecycle of all backend MCP server subprocesses."""

    def __init__(
        self,
        backends: dict[str, BackendProcess],
        configs: dict[str, BackendConfig],
        registry: ToolRegistry,
    ) -> None:
        self._backends = backends
        self._configs = configs
        self._registry = registry
        self._health_tasks: list[asyncio.Task[None]] = []

    @classmethod
    def from_config(cls, configs: dict[str, BackendConfig]) -> BackendManager:
        """Build a BackendManager from a dict of BackendConfig entries."""
        registry = ToolRegistry()
        backends: dict[str, BackendProcess] = {}
        enabled_configs: dict[str, BackendConfig] = {}
        for bid, cfg in configs.items():
            if cfg.enabled:
                backends[bid] = BackendProcess(backend_id=bid, config=cfg)
                enabled_configs[bid] = cfg
        log.info(
            "manager.created",
            total_configured=len(configs),
            enabled=len(backends),
        )
        return cls(backends, enabled_configs, registry)

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def backends(self) -> dict[str, BackendProcess]:
        return self._backends

    async def start_all(self) -> dict[str, bool]:
        """Start backends and register their tools.

        The first DEFAULT_EAGER_LIMIT backends are started eagerly;
        remaining backends are started lazily on first tool call.
        """
        results: dict[str, bool] = {}
        items = list(self._backends.items())
        eager = items[:DEFAULT_EAGER_LIMIT]
        lazy = items[DEFAULT_EAGER_LIMIT:]

        for bid, bp in eager:
            try:
                await bp.start()
                self._registry.register_backend_tools(
                    bid,
                    self._configs[bid].display_name,
                    bp.tools,
                )
                results[bid] = True
            except Exception as exc:
                log.error("manager.start_failed", backend_id=bid, error=str(exc))
                results[bid] = False

        if lazy:
            log.info(
                "manager.lazy_backends",
                count=len(lazy),
                backend_ids=[bid for bid, _ in lazy],
            )

        return results

    async def _ensure_started(self, backend_id: str) -> None:
        """Lazily start a backend if it hasn't been started yet."""
        bp = self._backends.get(backend_id)
        if bp is None:
            return
        from cloud_engineer_mcp.backends.process import BackendStatus

        if bp.status == BackendStatus.STOPPED:
            log.info("manager.lazy_start", backend_id=backend_id)
            await bp.start()
            self._registry.register_backend_tools(
                backend_id,
                self._configs[backend_id].display_name,
                bp.tools,
            )

    async def route_tool_call(
        self, namespaced_name: str, arguments: dict[str, Any]
    ) -> CallToolResult:
        """Route a namespaced tool call to the correct backend."""
        ref = self._registry.lookup(namespaced_name)
        if ref is None:
            raise ToolNotFoundError(namespaced_name)

        backend_id, original_name = split_namespaced_name(namespaced_name)
        await self._ensure_started(backend_id)

        bp = self._backends.get(backend_id)
        if bp is None:
            raise ToolNotFoundError(namespaced_name)

        return await bp.call_tool(original_name, arguments)

    async def stop_all(self) -> None:
        """Stop all backends and cancel health monitors."""
        for task in self._health_tasks:
            task.cancel()
        for task in self._health_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._health_tasks.clear()

        for bid, bp in self._backends.items():
            try:
                await bp.stop()
            except Exception as exc:
                log.error("manager.stop_error", backend_id=bid, error=str(exc))

    async def start_health_monitors(self) -> None:
        """Start a background health monitor for each running backend."""
        for bid, bp in self._backends.items():
            from cloud_engineer_mcp.backends.process import BackendStatus

            if bp.status == BackendStatus.READY:
                interval = self._configs[bid].health_check_interval_seconds
                task = asyncio.create_task(self._health_loop(bid, interval))
                self._health_tasks.append(task)

    async def _health_loop(self, backend_id: str, interval: int) -> None:
        """Periodically check backend health and restart on failure."""
        while True:
            await asyncio.sleep(interval)
            bp = self._backends.get(backend_id)
            if bp is None:
                return

            try:
                healthy = await bp.health_check()
                if not healthy:
                    log.warning("manager.health_check_failed", backend_id=backend_id)
                    if bp.config.restart_on_failure:
                        self._registry.unregister_backend(backend_id)
                        await bp.restart()
                        self._registry.register_backend_tools(
                            backend_id,
                            self._configs[backend_id].display_name,
                            bp.tools,
                        )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.error(
                    "manager.health_monitor_error",
                    backend_id=backend_id,
                    error=str(exc),
                )

    async def refresh_backend_tools(self, backend_id: str) -> None:
        """Refresh tools for a specific backend (e.g. on tools_changed notification)."""
        bp = self._backends.get(backend_id)
        if bp is None:
            return
        try:
            tools = await bp.refresh_tools()
            self._registry.unregister_backend(backend_id)
            self._registry.register_backend_tools(
                backend_id,
                self._configs[backend_id].display_name,
                tools,
            )
        except Exception as exc:
            log.error(
                "manager.refresh_failed",
                backend_id=backend_id,
                error=str(exc),
            )
