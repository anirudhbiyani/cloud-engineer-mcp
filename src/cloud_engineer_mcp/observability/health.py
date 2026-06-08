"""Health check endpoint for cloud_engineer_mcp."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from cloud_engineer_mcp.backends.manager import BackendManager
    from cloud_engineer_mcp.config import CloudEngineerConfig
    from cloud_engineer_mcp.selector.backend import SelectorBackend
    from cloud_engineer_mcp.session.sessions import SessionManager


class HealthCheck:
    def __init__(
        self,
        config: CloudEngineerConfig,
        backend_manager: BackendManager,
        tool_index: SelectorBackend,
        session_manager: SessionManager,
        start_time: float | None = None,
    ) -> None:
        self._config = config
        self._backend_manager = backend_manager
        self._tool_index = tool_index
        self._session_manager = session_manager
        self._start_time = start_time or time.time()

    async def __call__(self, request: Request) -> JSONResponse:
        now = time.time()
        uptime = now - self._start_time

        backends_status: dict[str, dict[str, object]] = {}
        any_healthy = False

        if self._config.health.include_backends:
            for bid, bp in self._backend_manager.backends.items():
                entry: dict[str, object] = {"status": bp.status.value, "tool_count": len(bp.tools)}
                if bp.status.value == "ready":
                    any_healthy = True
                elif bp.status.value == "failed":
                    entry["error"] = "backend failed"
                backends_status[bid] = entry
        else:
            any_healthy = any(
                bp.status.value == "ready" for bp in self._backend_manager.backends.values()
            )

        body = {
            "status": "healthy" if any_healthy else "degraded",
            "version": self._config.server.version,
            "uptime_seconds": round(uptime, 1),
            "backends": backends_status,
            "selector": {
                "model_loaded": self._tool_index.is_loaded,
                "total_tools_indexed": self._tool_index.size,
                "model_name": self._config.selector.model_name,
            },
            "sessions": {
                "active_count": self._session_manager.active_count,
            },
        }

        status_code = 200 if any_healthy else 503
        return JSONResponse(body, status_code=status_code)
