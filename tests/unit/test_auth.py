"""Tests for the CommandTokenAuth httpx flow."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from cloud_engineer_mcp.auth import CommandTokenAuth


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://example.test/mcp", headers={})


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=_request())


class TestConstruction:
    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty command"):
            CommandTokenAuth(command=[])


class TestSyncFlow:
    def test_attaches_minted_token(self) -> None:
        auth = CommandTokenAuth(["true"])
        with patch.object(auth, "_mint_sync", return_value="tok-1"):
            flow = auth.sync_auth_flow(_request())
            req_with_auth = next(flow)
            assert req_with_auth.headers["Authorization"] == "Bearer tok-1"
            # Send a 200 response; flow should end without retry.
            with pytest.raises(StopIteration):
                flow.send(_response(200))

    def test_refreshes_on_401(self) -> None:
        auth = CommandTokenAuth(["true"])
        with patch.object(auth, "_mint_sync", side_effect=["stale", "fresh"]):
            flow = auth.sync_auth_flow(_request())
            first = next(flow)
            assert first.headers["Authorization"] == "Bearer stale"
            retry = flow.send(_response(401))
            assert retry.headers["Authorization"] == "Bearer fresh"
            with pytest.raises(StopIteration):
                flow.send(_response(200))

    def test_command_failure_skips_header(self) -> None:
        auth = CommandTokenAuth(["false"])
        with patch.object(auth, "_mint_sync", return_value=None):
            flow = auth.sync_auth_flow(_request())
            req = next(flow)
            # No header attached when mint returns None.
            assert "Authorization" not in req.headers


@pytest.mark.asyncio
class TestAsyncFlow:
    async def test_attaches_minted_token(self) -> None:
        auth = CommandTokenAuth(["true"])

        async def _mint() -> str:
            return "async-tok"

        with patch.object(auth, "_mint_async", side_effect=_mint):
            flow = auth.async_auth_flow(_request())
            first = await flow.__anext__()
            assert first.headers["Authorization"] == "Bearer async-tok"
            with pytest.raises(StopAsyncIteration):
                await flow.asend(_response(200))

    async def test_refreshes_on_401_async(self) -> None:
        auth = CommandTokenAuth(["true"])
        tokens = iter(["stale-async", "fresh-async"])

        async def _mint() -> str:
            return next(tokens)

        with patch.object(auth, "_mint_async", side_effect=_mint):
            flow = auth.async_auth_flow(_request())
            first = await flow.__anext__()
            assert first.headers["Authorization"] == "Bearer stale-async"
            retry = await flow.asend(_response(401))
            assert retry.headers["Authorization"] == "Bearer fresh-async"
            with pytest.raises(StopAsyncIteration):
                await flow.asend(_response(200))


class TestTemplate:
    def test_custom_template(self) -> None:
        auth = CommandTokenAuth(
            ["true"],
            header_name="X-API-Key",
            template="{token}",
        )
        with patch.object(auth, "_mint_sync", return_value="raw-key"):
            flow = auth.sync_auth_flow(_request())
            req = next(flow)
            assert req.headers["X-API-Key"] == "raw-key"
            assert "Authorization" not in req.headers


class TestRealSubprocess:
    """Smoke-test the actual subprocess path so we know it's wired."""

    def test_echo_command_returns_stripped_output(self) -> None:
        auth = CommandTokenAuth(["echo", "-n", "shell-token"])
        token = auth._mint_sync()
        assert token == "shell-token"

    def test_nonexistent_command_returns_none(self) -> None:
        auth = CommandTokenAuth(["this-command-does-not-exist-zzzz"])
        assert auth._mint_sync() is None
