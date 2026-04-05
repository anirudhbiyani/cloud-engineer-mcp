"""Streamable HTTP transport runner for the MCP gateway."""

from __future__ import annotations

import time
from collections import defaultdict

import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http import StreamableHTTPServerTransport
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from cloud_engineer_mcp.config import CloudEngineerConfig
from cloud_engineer_mcp.observability.logging import get_logger

log = get_logger("transport.http")


class TokenBucketRateLimiter:
    """Simple in-memory token bucket rate limiter per IP."""

    def __init__(self, rate: float, capacity: float) -> None:
        self._rate = rate  # tokens per second
        self._capacity = capacity
        self._tokens: dict[str, float] = defaultdict(lambda: capacity)
        self._last_refill: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        last = self._last_refill.get(key, now)
        elapsed = now - last
        self._last_refill[key] = now

        self._tokens[key] = min(
            self._capacity,
            self._tokens[key] + elapsed * self._rate,
        )

        if self._tokens[key] >= 1.0:
            self._tokens[key] -= 1.0
            return True
        return False


def create_http_app(
    server: Server,
    config: CloudEngineerConfig,
    health_handler: object = None,
    metrics_handler: object = None,
) -> Starlette:
    """Create the Starlette ASGI app with MCP transport and health endpoints."""

    transport = StreamableHTTPServerTransport(
        mcp_session_timeout_seconds=300,
    )

    rate_limiter: TokenBucketRateLimiter | None = None
    if config.rate_limit.enabled:
        rate_limiter = TokenBucketRateLimiter(
            rate=config.rate_limit.requests_per_minute / 60.0,
            capacity=float(config.rate_limit.requests_per_minute),
        )

    async def handle_mcp(request: Request):  # noqa: ANN201
        if rate_limiter:
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.allow(client_ip):
                return JSONResponse({"error": "Rate limit exceeded"}, status_code=429)
        return await transport.handle(request)

    routes: list[Route | Mount] = [
        Mount("/mcp", app=handle_mcp),
    ]

    if config.health.enabled and health_handler:
        routes.append(Route(config.health.endpoint, health_handler))

    if metrics_handler:
        routes.append(Route("/metrics", metrics_handler))

    app = Starlette(routes=routes)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.server.transports.http.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def startup() -> None:
        log.info("transport.http.starting", port=config.server.transports.http.port)
        await transport.connect(server)

    async def shutdown() -> None:
        log.info("transport.http.stopping")
        await transport.close()

    app.add_event_handler("startup", startup)
    app.add_event_handler("shutdown", shutdown)

    return app


async def run_http(server: Server, config: CloudEngineerConfig, **kwargs: object) -> None:
    """Run the MCP server over Streamable HTTP with uvicorn."""
    app = create_http_app(server, config, **kwargs)

    http_config = uvicorn.Config(
        app,
        host=config.server.transports.http.host,
        port=config.server.transports.http.port,
        log_level="warning",
    )
    http_server = uvicorn.Server(http_config)
    await http_server.serve()
