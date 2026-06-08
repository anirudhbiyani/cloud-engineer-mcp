"""Streamable HTTP transport runner for the MCP gateway.

Wraps the current MCP SDK's StreamableHTTPSessionManager in a Starlette ASGI
app, mounts /mcp, /livez, /readyz, /metrics, and applies bearer-token
authentication plus per-IP rate limiting.
"""

from __future__ import annotations

import hmac
import os
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from cloud_engineer_mcp.config import CloudEngineerConfig
from cloud_engineer_mcp.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger("transport.http")

AUTH_TOKEN_ENV = "CLOUD_ENGINEER_MCP_AUTH_TOKEN"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class TokenBucketRateLimiter:
    """In-memory token bucket rate limiter, one bucket per key."""

    def __init__(self, rate_per_second: float, capacity: float) -> None:
        self._rate = rate_per_second
        self._capacity = capacity
        self._tokens: dict[str, float] = defaultdict(lambda: capacity)
        self._last_refill: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        last = self._last_refill.get(key, now)
        self._last_refill[key] = now
        self._tokens[key] = min(self._capacity, self._tokens[key] + (now - last) * self._rate)
        if self._tokens[key] >= 1.0:
            self._tokens[key] -= 1.0
            return True
        return False


def _auth_token_from_env() -> str | None:
    # os.environ.get is typed via os._Environ in stdlib stubs and mypy strict
    # narrows it to Any in some configurations; cast at the boundary.
    raw: str | None = os.environ.get(AUTH_TOKEN_ENV)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped if stripped else None


def _client_ip(scope: MutableMapping[str, Any]) -> str:
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        return str(client[0])
    return "unknown"


def _bearer_from_headers(scope: MutableMapping[str, Any]) -> str | None:
    for name, value in scope.get("headers") or []:
        if name == b"authorization":
            text: str = bytes(value).decode("latin-1", errors="replace")
            prefix = "bearer "
            if text.lower().startswith(prefix):
                token: str = text[len(prefix) :].strip()
                return token
    return None


async def _send_json(
    send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    status: int,
    body: bytes,
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def create_http_app(
    server: Server,
    config: CloudEngineerConfig,
    health_handler: Callable[[Request], Awaitable[Response]] | None = None,
    metrics_handler: Callable[[Request], Awaitable[Response]] | None = None,
    auth_token: str | None = None,
) -> Starlette:
    """Build the Starlette ASGI app that exposes the gateway over Streamable HTTP."""
    session_manager = StreamableHTTPSessionManager(app=server, stateless=False)

    if auth_token is None:
        auth_token = _auth_token_from_env()

    http_cfg = config.server.transports.http
    if http_cfg.host not in LOOPBACK_HOSTS and not auth_token:
        raise RuntimeError(
            f"HTTP transport is bound to {http_cfg.host!r} but no auth token is set. "
            f"Set {AUTH_TOKEN_ENV} or bind to 127.0.0.1. "
            "Refusing to start: see SECURITY.md."
        )

    rate_limiter: TokenBucketRateLimiter | None = None
    if config.rate_limit.enabled:
        rate_limiter = TokenBucketRateLimiter(
            rate_per_second=config.rate_limit.requests_per_minute / 60.0,
            capacity=float(config.rate_limit.requests_per_minute),
        )

    async def mcp_asgi(
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            await session_manager.handle_request(scope, receive, send)
            return

        if auth_token is not None:
            presented = _bearer_from_headers(scope)
            # Constant-time compare so a network attacker can't recover the
            # token byte-by-byte via response-timing analysis.
            if not hmac.compare_digest(presented or "", auth_token):
                await _send_json(send, 401, b'{"error":"unauthorized"}')
                return

        if rate_limiter is not None and not rate_limiter.allow(_client_ip(scope)):
            await _send_json(send, 429, b'{"error":"rate limit exceeded"}')
            return

        await session_manager.handle_request(scope, receive, send)

    routes: list[Route | Mount] = [Mount("/mcp", app=mcp_asgi)]

    if config.health.enabled and health_handler is not None:
        # Wrap the handler in a function. Starlette's Route treats only
        # functions/methods as request handlers; a callable *instance* (like
        # HealthCheck) is mistaken for a sub-ASGI app and invoked with
        # (scope, receive, send), raising a TypeError at request time.
        handler = health_handler

        async def _health_route(request: Request) -> Response:
            return await handler(request)

        # Backwards-compatible single health endpoint plus split live/ready probes.
        routes.append(Route(config.health.endpoint, _health_route))
        routes.append(Route("/livez", _live_probe))
        routes.append(Route("/readyz", _health_route))

    if metrics_handler is not None:
        routes.append(Route("/metrics", metrics_handler))

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        log.info(
            "transport.http.starting",
            host=http_cfg.host,
            port=http_cfg.port,
            auth_required=auth_token is not None,
        )
        async with session_manager.run():
            yield
        log.info("transport.http.stopped")

    app = Starlette(routes=routes, lifespan=lifespan)

    if http_cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(http_cfg.cors_origins),
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Mcp-Session-Id"],
        )

    return app


async def _live_probe(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "alive"})


async def run_http(
    server: Server,
    config: CloudEngineerConfig,
    health_handler: Callable[[Request], Awaitable[Response]] | None = None,
    metrics_handler: Callable[[Request], Awaitable[Response]] | None = None,
    auth_token: str | None = None,
) -> None:
    """Run the MCP server over Streamable HTTP with uvicorn."""
    app = create_http_app(
        server,
        config,
        health_handler=health_handler,
        metrics_handler=metrics_handler,
        auth_token=auth_token,
    )
    http_config = uvicorn.Config(
        app,
        host=config.server.transports.http.host,
        port=config.server.transports.http.port,
        log_level="warning",
    )
    http_server = uvicorn.Server(http_config)
    await http_server.serve()
