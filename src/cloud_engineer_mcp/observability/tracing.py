"""Optional OpenTelemetry tracing.

Tracing is **opt-in** and silently no-ops when the OpenTelemetry SDK isn't
installed or when no OTLP endpoint is configured. This keeps the base install
lean (no OTel dependency in the default wheel) while letting operators turn
on full distributed tracing with a single env var.

Configuration
-------------
Standard OTel env vars apply:

  OTEL_EXPORTER_OTLP_ENDPOINT     e.g. http://localhost:4317
  OTEL_EXPORTER_OTLP_HEADERS      e.g. api-key=...
  OTEL_SERVICE_NAME               defaults to "cloud-engineer-mcp"
  OTEL_RESOURCE_ATTRIBUTES        standard OTel resource attrs

Set `OTEL_SDK_DISABLED=true` to disable even when configured.

Usage
-----
Call `configure_tracing()` once during gateway startup. Then use
`get_tracer("component")` anywhere to create spans. When tracing isn't
configured, both calls are no-ops — the spans become a `nullcontext()` so
callers don't need to branch.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import Any

from cloud_engineer_mcp.observability.logging import get_logger

log = get_logger("observability.tracing")

_DEFAULT_SERVICE_NAME = "cloud-engineer-mcp"
_initialized = False
_enabled = False


def is_enabled() -> bool:
    return _enabled


def configure_tracing(service_name: str = _DEFAULT_SERVICE_NAME) -> bool:
    """Initialize OTel if the SDK is installed and an endpoint is set.

    Returns True if tracing was enabled. Safe to call multiple times; only the
    first call performs initialization. Silently no-ops without opentelemetry
    installed or without OTEL_EXPORTER_OTLP_ENDPOINT.
    """
    global _initialized, _enabled
    if _initialized:
        return _enabled
    _initialized = True

    if os.environ.get("OTEL_SDK_DISABLED", "").lower() in {"1", "true", "yes"}:
        log.debug("tracing.disabled_by_env")
        return False
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        log.debug("tracing.no_endpoint_configured")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        log.info("tracing.sdk_not_installed", error=str(exc))
        return False

    resource_name = os.environ.get("OTEL_SERVICE_NAME", service_name)
    resource = Resource.create({"service.name": resource_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    _enabled = True
    log.info(
        "tracing.enabled",
        endpoint=os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"],
        service=resource_name,
    )
    return True


def get_tracer(name: str) -> Any:
    """Return an OTel tracer if enabled, else a no-op stub."""
    if not _enabled:
        return _NoopTracer()
    from opentelemetry import trace

    return trace.get_tracer(name)


class _NoopTracer:
    """Stand-in tracer used when OTel is not active. Same call surface."""

    @contextlib.contextmanager
    def start_as_current_span(
        self,
        _name: str,
        **_kwargs: Any,
    ) -> Iterator[_NoopSpan]:
        yield _NoopSpan()


class _NoopSpan:
    def set_attribute(self, _key: str, _value: object) -> None:
        return None

    def set_status(self, *_args: object, **_kwargs: object) -> None:
        return None

    def record_exception(self, _exc: BaseException, **_kwargs: object) -> None:
        return None
