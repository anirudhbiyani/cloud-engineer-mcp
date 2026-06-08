"""Tests for the optional OpenTelemetry tracing helper.

These verify the no-op path. The real OTel SDK path is exercised opportunistically
when the dev environment has it installed; the rest of the codebase uses
get_tracer() which is safe in both states.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from cloud_engineer_mcp.observability import tracing


class TestNoopTracer:
    def setup_method(self) -> None:
        # Reset module state so each test starts from a clean slate.
        tracing._initialized = False
        tracing._enabled = False

    def test_disabled_without_endpoint(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert tracing.configure_tracing() is False
            assert tracing.is_enabled() is False

    def test_disabled_when_env_disables(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
                "OTEL_SDK_DISABLED": "true",
            },
            clear=True,
        ):
            assert tracing.configure_tracing() is False

    def test_get_tracer_returns_noop_when_disabled(self) -> None:
        tr = tracing.get_tracer("test")
        with tr.start_as_current_span("op") as span:
            span.set_attribute("anything", 1)
            span.record_exception(RuntimeError("ignored"))
            span.set_status("ok")
        # No exception, no side effect; the no-op accepts the same API surface.

    def test_configure_is_idempotent(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            tracing.configure_tracing()
            assert tracing.configure_tracing() is False
