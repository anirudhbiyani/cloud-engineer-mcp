"""In-memory metrics collector for cloud_engineer_mcp.

Exposes a Prometheus text-format renderer alongside the JSON view. The
endpoint negotiates on the request `Accept` header:

- `Accept: text/plain` (or `application/openmetrics-text`) → Prometheus.
- Anything else → JSON (default for `curl /metrics`, browsers).
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


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

    def to_dict(self) -> dict[str, float | int]:
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
    tool_call_duration: dict[str, Histogram] = field(default_factory=lambda: defaultdict(Histogram))
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

    def to_dict(self) -> dict[str, object]:
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

    def to_prometheus(self) -> str:
        """Render counters and histograms in Prometheus text format.

        Cardinality note: tool_calls keys include backend_id+tool_name. Bound
        by the registered tool catalog (~800), so cardinality is finite.
        """
        ns = "cloud_engineer_mcp"
        lines: list[str] = []

        lines.append(f"# HELP {ns}_uptime_seconds Process uptime.")
        lines.append(f"# TYPE {ns}_uptime_seconds gauge")
        lines.append(f"{ns}_uptime_seconds {round(time.time() - self._start_time, 3)}")

        lines.append(f"# HELP {ns}_tool_calls_total Tool calls per backend+tool.")
        lines.append(f"# TYPE {ns}_tool_calls_total counter")
        for key, count in self.tool_calls_total.items():
            backend, _, tool = key.partition(".")
            labels = f'backend="{_esc(backend)}",tool="{_esc(tool)}"'
            lines.append(f"{ns}_tool_calls_total{{{labels}}} {count}")

        lines.append(f"# HELP {ns}_tool_call_errors_total Tool-call errors.")
        lines.append(f"# TYPE {ns}_tool_call_errors_total counter")
        for key, count in self.tool_call_errors.items():
            backend, _, tool = key.partition(".")
            labels = f'backend="{_esc(backend)}",tool="{_esc(tool)}"'
            lines.append(f"{ns}_tool_call_errors_total{{{labels}}} {count}")

        lines.append(
            f"# HELP {ns}_tool_call_duration_seconds Aggregate tool-call duration per backend."
        )
        lines.append(f"# TYPE {ns}_tool_call_duration_seconds summary")
        for backend, hist in self.tool_call_duration.items():
            labels = f'backend="{_esc(backend)}"'
            lines.append(f"{ns}_tool_call_duration_seconds_count{{{labels}}} {hist.count}")
            lines.append(f"{ns}_tool_call_duration_seconds_sum{{{labels}}} {hist.total}")

        sel_count = self.tool_selection_duration.count
        sel_sum = self.tool_selection_duration.total
        lines.append(f"# HELP {ns}_tool_selection_seconds Tool selection latency.")
        lines.append(f"# TYPE {ns}_tool_selection_seconds summary")
        lines.append(f"{ns}_tool_selection_seconds_count {sel_count}")
        lines.append(f"{ns}_tool_selection_seconds_sum {sel_sum}")

        lines.append(f"# HELP {ns}_backend_restarts_total Backend restarts.")
        lines.append(f"# TYPE {ns}_backend_restarts_total counter")
        for backend, n in self.backend_restarts.items():
            labels = f'backend="{_esc(backend)}"'
            lines.append(f"{ns}_backend_restarts_total{{{labels}}} {n}")

        return "\n".join(lines) + "\n"


def _esc(value: str) -> str:
    """Escape a Prometheus label value per the text-format spec."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


_global_metrics = MetricsCollector()


def get_metrics() -> MetricsCollector:
    return _global_metrics


def reset_metrics() -> None:
    global _global_metrics
    _global_metrics = MetricsCollector()


def _wants_prometheus(accept: str) -> bool:
    if not accept:
        return False
    accept_lower = accept.lower()
    return "application/openmetrics-text" in accept_lower or accept_lower.startswith("text/plain")


async def metrics_endpoint(request: Request) -> Response:
    """Serve metrics in Prometheus text format or JSON per `Accept` header."""
    metrics = get_metrics()
    accept = request.headers.get("accept", "")
    if _wants_prometheus(accept):
        return PlainTextResponse(metrics.to_prometheus(), media_type=PROM_CONTENT_TYPE)
    return JSONResponse(metrics.to_dict())
