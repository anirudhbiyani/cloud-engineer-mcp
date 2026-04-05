"""Integration tests for tool routing through the BackendManager."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cloud_engineer_mcp.backends.manager import BackendManager
from cloud_engineer_mcp.config import BackendConfig
from cloud_engineer_mcp.errors import ToolNotFoundError

MOCK_BACKEND_SCRIPT = str(Path(__file__).parent.parent / "fixtures" / "mock_backend.py")


def _mock_config(backend_id: str = "mock") -> dict[str, BackendConfig]:
    return {
        backend_id: BackendConfig(
            display_name=f"Mock {backend_id}",
            command=sys.executable,
            args=[MOCK_BACKEND_SCRIPT],
            enabled=True,
            startup_timeout_seconds=30,
        )
    }


@pytest.mark.asyncio
@pytest.mark.timeout(60)
class TestToolRouting:
    async def test_start_and_route(self) -> None:
        mgr = BackendManager.from_config(_mock_config("mock"))
        try:
            results = await mgr.start_all()
            assert results["mock"] is True
            assert mgr.registry.tool_count == 5

            result = await mgr.route_tool_call("mock__mock_list", {})
            assert result.content is not None
        finally:
            await mgr.stop_all()

    async def test_route_nonexistent_tool(self) -> None:
        mgr = BackendManager.from_config(_mock_config("mock"))
        try:
            await mgr.start_all()
            with pytest.raises(ToolNotFoundError):
                await mgr.route_tool_call("mock__nonexistent", {})
        finally:
            await mgr.stop_all()

    async def test_multiple_backends(self) -> None:
        configs = {
            "mock_a": BackendConfig(
                display_name="Mock A",
                command=sys.executable,
                args=[MOCK_BACKEND_SCRIPT],
                enabled=True,
                startup_timeout_seconds=30,
            ),
            "mock_b": BackendConfig(
                display_name="Mock B",
                command=sys.executable,
                args=[MOCK_BACKEND_SCRIPT],
                enabled=True,
                startup_timeout_seconds=30,
            ),
        }
        mgr = BackendManager.from_config(configs)
        try:
            results = await mgr.start_all()
            assert results["mock_a"] is True
            assert results["mock_b"] is True
            assert mgr.registry.tool_count == 10

            result_a = await mgr.route_tool_call("mock_a__mock_list", {})
            result_b = await mgr.route_tool_call("mock_b__mock_list", {})
            assert result_a.content is not None
            assert result_b.content is not None
        finally:
            await mgr.stop_all()

    async def test_disabled_backend_not_started(self) -> None:
        configs = {
            "enabled": BackendConfig(
                display_name="Enabled",
                command=sys.executable,
                args=[MOCK_BACKEND_SCRIPT],
                enabled=True,
                startup_timeout_seconds=30,
            ),
            "disabled": BackendConfig(
                display_name="Disabled",
                command=sys.executable,
                args=[MOCK_BACKEND_SCRIPT],
                enabled=False,
                startup_timeout_seconds=30,
            ),
        }
        mgr = BackendManager.from_config(configs)
        try:
            results = await mgr.start_all()
            assert "enabled" in results
            assert "disabled" not in results
            assert mgr.registry.tool_count == 5
        finally:
            await mgr.stop_all()
