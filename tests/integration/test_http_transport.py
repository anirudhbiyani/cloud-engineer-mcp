"""Smoke tests for the Streamable HTTP transport.

These don't speak full MCP — they exercise the ASGI surface so the bug class
(SDK API mismatch, broken Starlette lifespan, missing auth check) cannot
silently regress.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from mcp.server.lowlevel import Server
from starlette.applications import Starlette

from cloud_engineer_mcp.config import CloudEngineerConfig
from cloud_engineer_mcp.transport.http import AUTH_TOKEN_ENV, create_http_app


def _server() -> Server:
    return Server("test")


@pytest.mark.asyncio
class TestCreateHttpApp:
    async def test_builds_starlette_app(self) -> None:
        os.environ.pop(AUTH_TOKEN_ENV, None)
        cfg = CloudEngineerConfig()
        app = create_http_app(_server(), cfg)
        assert isinstance(app, Starlette)

    async def test_refuses_non_loopback_without_auth(self) -> None:
        os.environ.pop(AUTH_TOKEN_ENV, None)
        cfg = CloudEngineerConfig()
        cfg.server.transports.http.host = "0.0.0.0"
        with pytest.raises(RuntimeError, match="no auth token"):
            create_http_app(_server(), cfg)

    async def test_allows_non_loopback_with_auth_token(self, monkeypatch) -> None:
        monkeypatch.setenv(AUTH_TOKEN_ENV, "test-token")
        cfg = CloudEngineerConfig()
        cfg.server.transports.http.host = "0.0.0.0"
        app = create_http_app(_server(), cfg)
        assert isinstance(app, Starlette)

    async def test_mcp_endpoint_rejects_unauthenticated(self, monkeypatch) -> None:
        monkeypatch.setenv(AUTH_TOKEN_ENV, "expected-token")
        cfg = CloudEngineerConfig()
        cfg.server.transports.http.host = "0.0.0.0"
        app = create_http_app(_server(), cfg)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "method": "ping", "id": 1},
            )
            assert response.status_code == 401
            assert "unauthorized" in response.text

    async def test_mcp_endpoint_rejects_wrong_token(self, monkeypatch) -> None:
        monkeypatch.setenv(AUTH_TOKEN_ENV, "expected-token")
        cfg = CloudEngineerConfig()
        cfg.server.transports.http.host = "0.0.0.0"
        app = create_http_app(_server(), cfg)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/mcp/",
                headers={"Authorization": "Bearer not-the-token"},
                json={"jsonrpc": "2.0", "method": "ping", "id": 1},
            )
            assert response.status_code == 401

    async def test_cors_disabled_by_default(self) -> None:
        # The default config must NOT expose a permissive CORS policy; a process
        # holding delegated cloud credentials should not be reachable cross-origin
        # unless an origin is explicitly listed. See SECURITY.md.
        cfg = CloudEngineerConfig()
        assert cfg.server.transports.http.cors_origins == []

    async def test_livez_always_returns_200(self, monkeypatch) -> None:
        monkeypatch.delenv(AUTH_TOKEN_ENV, raising=False)
        cfg = CloudEngineerConfig()

        async def health(_request):  # type: ignore[no-untyped-def]
            from starlette.responses import JSONResponse

            return JSONResponse({"status": "ok"})

        app = create_http_app(_server(), cfg, health_handler=health)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/livez")
            assert response.status_code == 200
            assert response.json() == {"status": "alive"}

    async def test_health_and_readyz_with_callable_instance_handler(self, monkeypatch) -> None:
        # Regression: the real handler (HealthCheck) is a callable *instance*,
        # not a function. Starlette's Route mistakes a bare instance for a
        # sub-ASGI app and invokes it with (scope, receive, send), so /health
        # and /readyz used to 500 in production even though a function-based
        # handler (as in test_livez) worked fine. Use an instance here.
        monkeypatch.delenv(AUTH_TOKEN_ENV, raising=False)
        cfg = CloudEngineerConfig()

        from starlette.responses import JSONResponse

        class _Health:
            async def __call__(self, _request):  # type: ignore[no-untyped-def]
                return JSONResponse({"status": "healthy"}, status_code=200)

        app = create_http_app(_server(), cfg, health_handler=_Health())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for path in (cfg.health.endpoint, "/readyz"):
                response = await client.get(path)
                assert response.status_code == 200, f"{path} -> {response.status_code}"
                assert response.json() == {"status": "healthy"}
