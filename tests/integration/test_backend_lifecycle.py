"""Integration tests for backend process lifecycle."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cloud_engineer_mcp.backends.process import BackendProcess, BackendStatus
from cloud_engineer_mcp.config import BackendConfig
from cloud_engineer_mcp.errors import BackendStartupError

MOCK_BACKEND_SCRIPT = str(Path(__file__).parent.parent / "fixtures" / "mock_backend.py")


def _mock_backend_config(**overrides) -> BackendConfig:
    defaults = {
        "display_name": "Mock",
        "command": sys.executable,
        "args": [MOCK_BACKEND_SCRIPT],
        "enabled": True,
        "startup_timeout_seconds": 30,
        "max_restarts": 2,
    }
    defaults.update(overrides)
    return BackendConfig(**defaults)


@pytest.mark.asyncio
@pytest.mark.timeout(60)
class TestBackendLifecycle:
    async def test_start_and_discover_tools(self) -> None:
        bp = BackendProcess(backend_id="mock", config=_mock_backend_config())
        try:
            await bp.start()
            assert bp.status == BackendStatus.READY
            assert len(bp.tools) == 5
            tool_names = [t.name for t in bp.tools]
            assert "mock_create" in tool_names
            assert "mock_list" in tool_names
        finally:
            await bp.stop()
            assert bp.status == BackendStatus.STOPPED

    async def test_call_tool(self) -> None:
        bp = BackendProcess(backend_id="mock", config=_mock_backend_config())
        try:
            await bp.start()
            result = await bp.call_tool("mock_list", {})
            assert result.content is not None
        finally:
            await bp.stop()

    async def test_stop_and_restart(self) -> None:
        bp = BackendProcess(backend_id="mock", config=_mock_backend_config())
        try:
            await bp.start()
            assert bp.status == BackendStatus.READY
            await bp.restart()
            assert bp.status == BackendStatus.READY
            assert bp.restart_count == 1
        finally:
            await bp.stop()

    async def test_health_check(self) -> None:
        bp = BackendProcess(backend_id="mock", config=_mock_backend_config())
        try:
            await bp.start()
            healthy = await bp.health_check()
            assert healthy is True
        finally:
            await bp.stop()

    async def test_health_check_when_stopped(self) -> None:
        bp = BackendProcess(backend_id="mock", config=_mock_backend_config())
        healthy = await bp.health_check()
        assert healthy is False

    async def test_startup_timeout(self) -> None:
        cfg = _mock_backend_config(
            command=sys.executable,
            args=["-c", "import time; time.sleep(60)"],
            startup_timeout_seconds=2,
        )
        bp = BackendProcess(backend_id="slow", config=cfg)
        with pytest.raises(BackendStartupError):
            await bp.start()
        assert bp.status == BackendStatus.FAILED

    async def test_max_restarts_exceeded(self) -> None:
        bp = BackendProcess(backend_id="mock", config=_mock_backend_config(max_restarts=1))
        try:
            await bp.start()
            bp.restart_count = 1
            await bp.restart()
            assert bp.status == BackendStatus.FAILED
        finally:
            await bp.stop()
