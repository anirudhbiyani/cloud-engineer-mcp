"""Tests for the streaming progress wiring.

We don't exercise the full MCP client/server round-trip here — that's covered
implicitly by the integration tests. Instead we verify the helper function
that decides whether to install a progress callback and a heartbeat task,
which is the load-bearing piece.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cloud_engineer_mcp.config import StreamingConfig
from cloud_engineer_mcp.server import _setup_streaming


def _server_with_context(progress_token: str | None) -> MagicMock:
    """Build a Server mock whose request_context exposes the given token."""
    session = MagicMock()
    session.send_progress_notification = AsyncMock()
    meta = SimpleNamespace(progressToken=progress_token)
    ctx = SimpleNamespace(session=session, meta=meta)
    server = MagicMock()
    type(server).request_context = property(lambda _self: ctx)
    return server


class TestStreamingDisabled:
    def test_disabled_returns_none(self) -> None:
        cfg = StreamingConfig(enabled=False)
        server = _server_with_context("tok-1")
        cb, hb = _setup_streaming(cfg, server, "aws__create")
        assert cb is None
        assert hb is None


class TestNoProgressToken:
    def test_no_token_returns_none(self) -> None:
        cfg = StreamingConfig(enabled=True, heartbeat_interval_seconds=1.0)
        server = _server_with_context(progress_token=None)
        cb, hb = _setup_streaming(cfg, server, "aws__create")
        assert cb is None
        assert hb is None


@pytest.mark.asyncio
class TestForwarding:
    async def test_callback_forwards_to_session(self) -> None:
        cfg = StreamingConfig(
            enabled=True,
            passthrough=True,
            heartbeat_interval_seconds=0.0,
        )
        server = _server_with_context("tok-1")
        cb, hb = _setup_streaming(cfg, server, "aws__create")
        assert cb is not None
        assert hb is None  # heartbeat off

        await cb(0.5, 1.0, "halfway")
        session = server.request_context.session
        session.send_progress_notification.assert_awaited_once_with(
            progress_token="tok-1",
            progress=0.5,
            total=1.0,
            message="halfway",
        )

    async def test_passthrough_disabled_no_callback(self) -> None:
        cfg = StreamingConfig(
            enabled=True,
            passthrough=False,
            heartbeat_interval_seconds=0.0,
        )
        server = _server_with_context("tok-1")
        cb, hb = _setup_streaming(cfg, server, "aws__create")
        assert cb is None
        assert hb is None


@pytest.mark.asyncio
class TestHeartbeat:
    async def test_heartbeat_emits_periodic_progress(self) -> None:
        cfg = StreamingConfig(
            enabled=True,
            passthrough=False,
            heartbeat_interval_seconds=0.05,
        )
        server = _server_with_context("tok-1")
        cb, hb = _setup_streaming(cfg, server, "aws__create")
        assert hb is not None
        try:
            # Allow at least one heartbeat to fire.
            await asyncio.sleep(0.12)
        finally:
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb

        session = server.request_context.session
        assert session.send_progress_notification.await_count >= 1
        kwargs = session.send_progress_notification.await_args_list[0].kwargs
        assert kwargs["progress_token"] == "tok-1"
        assert kwargs["total"] is None
        assert "still running" in kwargs["message"]
