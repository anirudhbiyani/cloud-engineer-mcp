"""Main MCP gateway server tying all components together."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from mcp.server.lowlevel import Server
from mcp.types import TextContent, Tool

from cloud_engineer_mcp.backends.manager import BackendManager
from cloud_engineer_mcp.backends.registry import ToolRegistry
from cloud_engineer_mcp.config import BackendConfig, CloudEngineerConfig
from cloud_engineer_mcp.errors import (
    PolicyDeniedError,
    PolicyRateLimitedError,
    ToolNotFoundError,
)
from cloud_engineer_mcp.observability.logging import get_logger
from cloud_engineer_mcp.observability.metrics import get_metrics
from cloud_engineer_mcp.observability.tracing import get_tracer
from cloud_engineer_mcp.policy import PolicyDecision, PolicyEngine
from cloud_engineer_mcp.selector.backend import SelectorBackend, make_selector_backend
from cloud_engineer_mcp.selector.context import ContextExtractor
from cloud_engineer_mcp.selector.engine import EmbeddingEngine
from cloud_engineer_mcp.session.sessions import SessionManager

log = get_logger("server")
tracer = get_tracer("cloud_engineer_mcp.server")

SET_CONTEXT_TOOL = Tool(
    name="set_context",
    description=(
        "Update the conversation context for intelligent tool selection. "
        "Call this with a summary of what you need to do, and subsequent "
        "tools/list calls will return only the most relevant tools. "
        "You should call this whenever your task changes significantly. "
        "Optional `action` and `resource_type` give more precise selection."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "context": {
                "type": "string",
                "description": "Natural language description of the current task or intent",
            },
            "cloud_providers": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "aws",
                        "azure",
                        "gcp",
                        "kubernetes",
                        "cloudflare",
                        "digitalocean",
                    ],
                },
                "description": "Optional: limit tools to specific cloud providers",
            },
            "action": {
                "type": "string",
                "description": (
                    "Optional structured action hint, e.g. 'create', 'list', "
                    "'delete', 'describe', 'deploy'. Improves selection precision."
                ),
            },
            "resource_type": {
                "type": "string",
                "description": (
                    "Optional structured resource hint, e.g. 's3 bucket', "
                    "'lambda function', 'azure storage account', 'gke cluster'."
                ),
            },
        },
        "required": ["context"],
    },
)

_STDIO_SESSION_ID = "__stdio__"


class GatewayComponents:
    """Holds all the shared gateway components."""

    def __init__(self, config: CloudEngineerConfig, all_backends: dict[str, BackendConfig]) -> None:
        self.config = config
        self.backend_manager = BackendManager.from_config(all_backends)
        self.registry: ToolRegistry = self.backend_manager.registry
        self.engine = EmbeddingEngine(config.selector.model_name)
        self.tool_index: SelectorBackend = make_selector_backend(config.selector, self.engine)
        self.context_extractor = ContextExtractor(config.selector.context_max_tokens)
        self.session_manager = SessionManager(
            ttl_seconds=config.sessions.ttl_seconds,
            persist_path=(config.sessions.persist_path if config.sessions.persist else None),
            persist_interval_seconds=config.sessions.persist_interval_seconds,
        )
        self.metrics = get_metrics()
        self.policy = PolicyEngine(config.policy)
        self.start_time = time.time()
        self._mcp_server: Server | None = None
        self._server_session: Any = None

    @classmethod
    async def create(cls, config: CloudEngineerConfig) -> GatewayComponents:
        """Create components, running discovery to expand backends first."""
        from cloud_engineer_mcp.discovery import discover_all, expand_backends

        discovered = await discover_all(config.discovery)
        all_backends = expand_backends(discovered, config.discovery, config.backends)
        log.info(
            "gateway.backends_expanded",
            discovered=len(discovered),
            manual=len(config.backends),
            total=len(all_backends),
        )
        return cls(config, all_backends)

    async def startup(self) -> None:
        """Start the gateway -- launches backend startup in the background.

        Returns quickly so the MCP stdio transport can begin accepting
        requests immediately. Cursor won't timeout waiting for backends.
        """
        log.info("gateway.starting", name=self.config.server.name)
        self.session_manager.start_cleanup_loop()
        self._startup_task = asyncio.create_task(self._background_startup())

    async def _background_startup(self) -> None:
        """Start backends and build the embedding index in the background."""
        try:
            results = await self.backend_manager.start_all()
            ready_count = sum(1 for v in results.values() if v)
            log.info("gateway.backends_started", total=len(results), ready=ready_count)
        except Exception as exc:
            log.error("gateway.backend_startup_error", error=str(exc))

        # Skip model load when the BM25 backend is in use — no model needed.
        if self.config.selector.backend.lower() == "embedding":
            try:
                await self.engine.load()
            except Exception as exc:
                log.warning("gateway.embedding_load_failed", error=str(exc))

        tool_refs = self.registry.all_refs()
        if tool_refs:
            cached = False
            if self.config.selector.cache_embeddings:
                current_names = [r.namespaced_name for r in tool_refs]
                cached = self.tool_index.load_cache(
                    self.config.selector.embedding_cache_path, current_names
                )
            if not cached:
                self.tool_index.build(tool_refs)
                if self.config.selector.cache_embeddings:
                    self.tool_index.save_cache(self.config.selector.embedding_cache_path)

        log.info(
            "gateway.ready",
            tools_indexed=self.tool_index.size,
        )

        await self._notify_tools_changed()

        await self.backend_manager.start_health_monitors()

    async def _notify_tools_changed(self) -> None:
        """Send tools/list_changed notification so Cursor re-fetches the tool list."""
        if self._server_session is None:
            log.debug("gateway.no_session_for_notification")
            return
        try:
            await self._server_session.send_tool_list_changed()
            log.info("gateway.tools_list_changed_sent")
        except Exception as exc:
            log.debug("gateway.tools_list_changed_failed", error=str(exc))

    async def shutdown(self) -> None:
        log.info("gateway.shutting_down")
        if hasattr(self, "_startup_task") and not self._startup_task.done():
            self._startup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._startup_task
        self.session_manager.stop_cleanup_loop()
        await self.backend_manager.stop_all()
        log.info("gateway.stopped")


def create_server(
    config: CloudEngineerConfig,
    components: GatewayComponents,
) -> tuple[Server, GatewayComponents]:
    """Create the low-level MCP server with dynamic tool selection.

    Returns the Server and the GatewayComponents (needed for lifecycle management).
    """
    server = Server(config.server.name)
    components._mcp_server = server

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> list[Tool]:
        if components._server_session is None:
            with contextlib.suppress(LookupError):
                components._server_session = server.request_context.session

        session = components.session_manager.get_or_create(_STDIO_SESSION_ID)

        t0 = time.time()

        with tracer.start_as_current_span("tool_selection") as span:
            span.set_attribute("session.id", session.session_id)
            span.set_attribute("selector.top_k", config.selector.top_k)
            if session.context:
                query = components.context_extractor.extract_query(
                    user_message=session.context,
                    conversation_history=session.conversation_messages,
                    tool_call_history=session.tool_call_history,
                    action=session.action,
                    resource_type=session.resource_type,
                )
                scored = components.tool_index.search(
                    query,
                    top_k=config.selector.top_k,
                    cloud_providers=session.cloud_providers,
                    score_boosts=session.get_score_boosts(),
                )
                tool_names = [s.namespaced_name for s in scored]
                span.set_attribute("selector.returned", len(tool_names))
                span.set_attribute("selector.has_context", True)
                if session.cloud_providers:
                    span.set_attribute(
                        "selector.cloud_providers",
                        ",".join(session.cloud_providers),
                    )

                log.debug(
                    "tool_selection.results",
                    session_id=session.session_id,
                    query=query[:100],
                    tool_names=tool_names[:5],
                    scores=[round(s.score, 3) for s in scored[:5]],
                )
            else:
                all_refs = components.registry.all_refs()
                tool_names = [r.namespaced_name for r in all_refs[: config.selector.top_k]]
                span.set_attribute("selector.has_context", False)
                span.set_attribute("selector.returned", len(tool_names))

        duration_ms = (time.time() - t0) * 1000
        components.metrics.record_selection(duration_ms / 1000)

        session.decay_pins()

        tools = components.registry.get_tool_definitions(tool_names)
        tools.append(SET_CONTEXT_TOOL)

        return tools

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        session = components.session_manager.get_or_create(_STDIO_SESSION_ID)

        if name == "set_context":
            ctx = arguments.get("context", "")
            providers = arguments.get("cloud_providers")
            action = arguments.get("action")
            resource_type = arguments.get("resource_type")
            session.set_context(ctx, providers, action=action, resource_type=resource_type)
            log.info(
                "set_context.called",
                session_id=session.session_id,
                context=ctx[:100],
                cloud_providers=providers,
                action=action,
                resource_type=resource_type,
            )
            return [
                TextContent(
                    type="text",
                    text=f"Context updated. Next tools/list will return tools relevant to: {ctx}",
                )
            ]

        ref = components.registry.lookup(name)
        if ref is None:
            raise ToolNotFoundError(name)

        # Policy gate before we hit the backend.
        decision = components.policy.check(name, session_id=session.session_id)
        if decision.decision is PolicyDecision.DENY:
            log.warning(
                "policy.deny",
                session_id=session.session_id,
                namespaced_name=name,
                reason=decision.reason,
                matched_pattern=decision.matched_pattern,
            )
            raise PolicyDeniedError(name, decision.reason)
        if decision.decision is PolicyDecision.RATE_LIMITED:
            log.warning(
                "policy.rate_limited",
                session_id=session.session_id,
                namespaced_name=name,
                pattern=decision.matched_pattern,
                per_minute=decision.rate_limit_per_minute,
            )
            raise PolicyRateLimitedError(
                name,
                decision.matched_pattern or "",
                decision.rate_limit_per_minute or 0,
            )
        if decision.decision is PolicyDecision.ALLOW_DRY_RUN:
            log.info(
                "policy.dry_run",
                session_id=session.session_id,
                namespaced_name=name,
            )
            return [
                TextContent(
                    type="text",
                    text=(
                        f"[dry-run] Would call {name} with arguments: {arguments}. "
                        "No backend invocation made (policy.dry_run=true)."
                    ),
                )
            ]

        log.info(
            "tool_call.start",
            session_id=session.session_id,
            namespaced_name=name,
        )

        # Streaming: forward backend progress to the gateway client and emit
        # heartbeats so long calls don't look hung.
        progress_callback, heartbeat_task = _setup_streaming(
            config.streaming,
            server,
            name,
        )

        t0 = time.time()
        with tracer.start_as_current_span("tool_call") as span:
            span.set_attribute("tool.namespaced_name", name)
            span.set_attribute("tool.backend_id", ref.backend_id)
            span.set_attribute("tool.original_name", ref.original_name)
            span.set_attribute("session.id", session.session_id)
            try:
                result = await components.backend_manager.route_tool_call(
                    name,
                    arguments,
                    progress_callback=progress_callback,
                )
                duration_s = time.time() - t0

                components.metrics.record_tool_call(ref.backend_id, ref.original_name, duration_s)
                session.record_tool_call(name)

                backend_tool_names = components.registry.get_backend_tool_names(ref.backend_id)
                session.pin_backend_tools(backend_tool_names)

                log.info(
                    "tool_call.complete",
                    session_id=session.session_id,
                    namespaced_name=name,
                    duration_ms=round(duration_s * 1000, 1),
                )

                # The MCP SDK returns a union of content types; we only emit
                # TextContent from this gateway. Cast at the boundary.
                return result.content  # type: ignore[return-value]

            except Exception as exc:
                duration_s = time.time() - t0
                components.metrics.record_tool_error(ref.backend_id, ref.original_name)
                span.record_exception(exc)
                log.error(
                    "tool_call.error",
                    session_id=session.session_id,
                    namespaced_name=name,
                    error=str(exc),
                    duration_ms=round(duration_s * 1000, 1),
                )
                raise
            finally:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat_task

    return server, components


def _setup_streaming(
    streaming_cfg: object,
    server: Server,
    namespaced_name: str,
) -> tuple[Any | None, asyncio.Task[None] | None]:
    """Return (progress_callback, heartbeat_task) for the current call.

    Both are None when streaming is disabled or when the client didn't request
    progress (no progressToken in the request `_meta`). The progress_callback
    forwards backend progress upstream; the heartbeat task emits "still
    working" pulses for backends that don't send progress themselves.
    """
    enabled = getattr(streaming_cfg, "enabled", True)
    if not enabled:
        return None, None

    progress_token: str | int | None = None
    session: Any = None
    with contextlib.suppress(LookupError, AttributeError):
        rc = server.request_context
        session = rc.session
        meta = rc.meta
        if meta is not None:
            progress_token = getattr(meta, "progressToken", None)

    if progress_token is None or session is None:
        return None, None

    progress_callback: Any | None = None
    if getattr(streaming_cfg, "passthrough", True):

        async def _forward(progress: float, total: float | None, message: str | None) -> None:
            with contextlib.suppress(Exception):
                await session.send_progress_notification(
                    progress_token=progress_token,
                    progress=progress,
                    total=total,
                    message=message,
                )

        progress_callback = _forward

    heartbeat_task: asyncio.Task[None] | None = None
    interval = float(getattr(streaming_cfg, "heartbeat_interval_seconds", 0.0))
    if interval > 0:

        async def _heartbeat() -> None:
            start = time.time()
            try:
                while True:
                    await asyncio.sleep(interval)
                    elapsed = time.time() - start
                    with contextlib.suppress(Exception):
                        await session.send_progress_notification(
                            progress_token=progress_token,
                            progress=elapsed,
                            total=None,
                            message=f"{namespaced_name} still running ({elapsed:.1f}s)",
                        )
            except asyncio.CancelledError:
                return

        heartbeat_task = asyncio.create_task(_heartbeat())
    return progress_callback, heartbeat_task
