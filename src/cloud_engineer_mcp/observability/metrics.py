"""In-memory metrics collector for cloud_engineer_mcp."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from starlette.requests import Request
from starlette.responses import JSONResponse


@dataclass
class Histogram:
    """Simple histogram with min/max/avg/count."""

    count: int = 0
    total: float = 0.0
    min_val: float = float("inf")
    max_val: float = float("-inf")

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.min_val = min(self.min_val, value)
        self.max_val = max(self.max_val, value)

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "avg": round(self.total / self.count, 4) if self.count > 0 else 0,
            "min": round(self.min_val, 4) if self.count > 0 else 0,
            "max": round(self.max_val, 4) if self.count > 0 else 0,
        }


@dataclass
class MetricsCollector:
    """In-memory metrics collector. Thread-safe for read-only reporting."""

    tool_calls_total: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tool_call_errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tool_call_duration: dict[str, Histogram] = field(
        default_factory=lambda: defaultdict(Histogram)
    )
    tool_selection_duration: Histogram = field(default_factory=Histogram)
    backend_restarts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _start_time: float = field(default_factory=time.time)

    def record_tool_call(self, backend_id: str, tool_name: str, duration_s: float) -> None:
        key = f"{backend_id}.{tool_name}"
        self.tool_calls_total[key] += 1
        self.tool_call_duration[backend_id].observe(duration_s)

    def record_tool_error(self, backend_id: str, tool_name: str) -> None:
        key = f"{backend_id}.{tool_name}"
        self.tool_call_errors[key] += 1

    def record_selection(self, duration_s: float) -> None:
        self.tool_selection_duration.observe(duration_s)

    def record_restart(self, backend_id: str) -> None:
        self.backend_restarts[backend_id] += 1

    def to_dict(self) -> dict:
        return {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "tool_calls_total": dict(self.tool_calls_total),
            "tool_call_errors": dict(self.tool_call_errors),
            "tool_call_duration_by_backend": {
                k: v.to_dict() for k, v in self.tool_call_duration.items()
            },
            "tool_selection_duration": self.tool_selection_duration.to_dict(),
            "backend_restarts": dict(self.backend_restarts),
        }


_global_metrics = MetricsCollector()


def get_metrics() -> MetricsCollector:
    return _global_metrics


def reset_metrics() -> None:
    global _global_metrics
    _global_metrics = MetricsCollector()


async def metrics_endpoint(request: Request) -> JSONResponse:
    return JSONResponse(get_metrics().to_dict())
