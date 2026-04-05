"""End-to-end integration tests for the full gateway flow."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cloud_engineer_mcp.backends.manager import BackendManager
from cloud_engineer_mcp.config import BackendConfig
from cloud_engineer_mcp.selector.context import ContextExtractor
from cloud_engineer_mcp.selector.index import ToolIndex
from cloud_engineer_mcp.session.sessions import SessionManager

MOCK_BACKEND_SCRIPT = str(Path(__file__).parent.parent / "fixtures" / "mock_backend.py")


class FakeEngine:
    """Deterministic embedding engine for testing."""

    @property
    def is_loaded(self) -> bool:
        return False  # force keyword fallback


@pytest.mark.asyncio
@pytest.mark.timeout(60)
class TestEndToEnd:
    async def test_full_flow_keyword_fallback(self) -> None:
        """Test the full flow: start backend, select tools, call tool."""
        configs = {
            "mock": BackendConfig(
                display_name="Mock Backend",
                command=sys.executable,
                args=[MOCK_BACKEND_SCRIPT],
                enabled=True,
                startup_timeout_seconds=30,
            ),
        }
        mgr = BackendManager.from_config(configs)
        try:
            await mgr.start_all()
            assert mgr.registry.tool_count == 5

            engine = FakeEngine()
            index = ToolIndex(engine, min_similarity=0.0)
            refs = mgr.registry.all_refs()

            index._names = [r.namespaced_name for r in refs]
            index._backend_ids = [r.backend_id for r in refs]
            index._descriptions = [r.description_for_embedding for r in refs]

            session_mgr = SessionManager()
            session = session_mgr.get_or_create("test-session")
            session.set_context("create a cloud resource")

            extractor = ContextExtractor()
            query = extractor.extract_query(
                user_message=session.context,
                tool_call_history=session.tool_call_history,
            )

            results = index.search(query, top_k=5)
            assert len(results) > 0

            found_names = [r.namespaced_name for r in results]
            assert any("mock_create" in n for n in found_names)

            result = await mgr.route_tool_call(
                "mock__mock_create",
                {"name": "test-resource", "config": {"env": "prod"}},
            )
            assert result.content is not None

            session.record_tool_call("mock__mock_create")
            assert "mock__mock_create" in session.tool_call_history

        finally:
            await mgr.stop_all()

    async def test_session_isolation(self) -> None:
        """Different sessions have independent contexts."""
        mgr = SessionManager()
        s1 = mgr.get_or_create("session-1")
        s2 = mgr.get_or_create("session-2")

        s1.set_context("AWS S3 operations", ["aws"])
        s2.set_context("Azure VMs", ["azure"])

        assert s1.context != s2.context
        assert s1.cloud_providers != s2.cloud_providers
