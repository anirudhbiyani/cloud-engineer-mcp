"""Tests for the metrics collector and /metrics endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cloud_engineer_mcp.observability.metrics import (
    MetricsCollector,
    metrics_endpoint,
    reset_metrics,
)


class TestMetricsCollector:
    def setup_method(self) -> None:
        self.m = MetricsCollector()

    def test_record_tool_call(self) -> None:
        self.m.record_tool_call("aws_prod", "list_buckets", 0.12)
        assert self.m.tool_calls_total["aws_prod.list_buckets"] == 1
        h = self.m.tool_call_duration["aws_prod"]
        assert h.count == 1
        assert 0.119 <= h.total <= 0.121

    def test_record_tool_error(self) -> None:
        self.m.record_tool_error("aws_prod", "list_buckets")
        assert self.m.tool_call_errors["aws_prod.list_buckets"] == 1

    def test_to_dict_shape(self) -> None:
        self.m.record_tool_call("aws_prod", "list_buckets", 0.05)
        self.m.record_selection(0.004)
        d = self.m.to_dict()
        assert "tool_calls_total" in d
        assert "tool_selection_duration" in d
        assert d["tool_selection_duration"]["count"] == 1

    def test_to_prometheus_contains_all_metrics(self) -> None:
        self.m.record_tool_call("aws_prod", "list_buckets", 0.05)
        self.m.record_tool_error("aws_prod", "list_buckets")
        self.m.record_selection(0.004)
        self.m.record_restart("aws_prod")
        text = self.m.to_prometheus()
        assert "cloud_engineer_mcp_uptime_seconds" in text
        calls_line = 'cloud_engineer_mcp_tool_calls_total{backend="aws_prod",tool="list_buckets"} 1'
        errs_line = (
            'cloud_engineer_mcp_tool_call_errors_total{backend="aws_prod",tool="list_buckets"} 1'
        )
        assert calls_line in text
        assert errs_line in text
        assert "cloud_engineer_mcp_tool_call_duration_seconds_count" in text
        assert "cloud_engineer_mcp_tool_selection_seconds_count 1" in text
        assert 'cloud_engineer_mcp_backend_restarts_total{backend="aws_prod"} 1' in text

    def test_to_prometheus_escapes_label_values(self) -> None:
        self.m.record_tool_call('weird"backend', "tool", 0.01)
        text = self.m.to_prometheus()
        assert r'backend="weird\"backend"' in text


@pytest.mark.asyncio
class TestMetricsEndpoint:
    def setup_method(self) -> None:
        reset_metrics()

    async def test_default_returns_json(self) -> None:
        request = MagicMock()
        request.headers = {"accept": "*/*"}
        response = await metrics_endpoint(request)
        assert response.media_type == "application/json"

    async def test_prometheus_when_text_plain_requested(self) -> None:
        request = MagicMock()
        request.headers = {"accept": "text/plain"}
        response = await metrics_endpoint(request)
        assert "text/plain" in response.media_type

    async def test_prometheus_when_openmetrics_requested(self) -> None:
        request = MagicMock()
        request.headers = {"accept": "application/openmetrics-text; version=1.0.0"}
        response = await metrics_endpoint(request)
        assert "text/plain" in response.media_type
