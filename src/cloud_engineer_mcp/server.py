"""Main MCP gateway server tying all components together."""

from __future__ import annotations

import asyncio
import contextlib
import time

from mcp.server.lowlevel import Server
from mcp.types import TextContent, Tool

from cloud_engineer_mcp.backends.manager import BackendManager
from cloud_engineer_mcp.backends.registry import ToolRegistry
from cloud_engineer_mcp.config import CloudEngineerConfig
from cloud_engineer_mcp.errors import ToolNotFoundError
from cloud_engineer_mcp.observability.logging import get_logger
from cloud_engineer_mcp.observability.metrics import get_metrics
from cloud_engineer_mcp.selector.context import ContextExtractor
from cloud_engineer_mcp.selector.engine import EmbeddingEngine
from cloud_engineer_mcp.selector.index import ToolIndex
from cloud_engineer_mcp.session.sessions import SessionManager

log = get_logger("server")

SET_CONTEXT_TOOL = Tool(
    name="set_context",
    description=(
        "Update the conversation context for intelligent tool selection. "
        "Call this with a summary of what you need to do, and subsequent "
        "tools/list calls will return only the most relevant tools. "
        "You should call this whenever your task changes significantly."
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
                "items": {"type": "string", "enum": ["aws", "azure", "gcp"]},
                "description": "Optional: limit tools to specific cloud providers",
            },
        },
        "required": ["context"],
    },
)

_STDIO_SESSION_ID = "__stdio__"


class GatewayComponents:
    """Holds all the shared gateway components."""

    def __init__(self, config: CloudEngineerConfig, all_backends: dict) -> None:
        self.config = config
        self.backend_manager = BackendManager.from_config(all_backends)
        self.registry: ToolRegistry = self.backend_manager.registry
        self.engine = EmbeddingEngine(config.selector.model_name)
        self.tool_index = ToolIndex(self.engine, config.selector.min_similarity)
        self.context_extractor = ContextExtractor(config.selector.context_max_tokens)
        self.session_manager = SessionManager()
        self.metrics = get_metrics()
        self.start_time = time.time()
        self._mcp_server: Server | None = None
        self._server_session: object | None = None

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

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        if components._server_session is None:
            with contextlib.suppress(LookupError):
                components._server_session = server.request_context.session

        session = components.session_manager.get_or_create(_STDIO_SESSION_ID)

        t0 = time.time()

        if session.context:
            query = components.context_extractor.extract_query(
                user_message=session.context,
                conversation_history=session.conversation_messages,
                tool_call_history=session.tool_call_history,
            )
            scored = components.tool_index.search(
                query,
                top_k=config.selector.top_k,
                cloud_providers=session.cloud_providers,
                score_boosts=session.get_score_boosts(),
            )
            tool_names = [s.namespaced_name for s in scored]

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

        duration_ms = (time.time() - t0) * 1000
        components.metrics.record_selection(duration_ms / 1000)

        session.decay_pins()

        tools = components.registry.get_tool_definitions(tool_names)
        tools.append(SET_CONTEXT_TOOL)

        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        session = components.session_manager.get_or_create(_STDIO_SESSION_ID)

        if name == "set_context":
            ctx = arguments.get("context", "")
            providers = arguments.get("cloud_providers")
            session.set_context(ctx, providers)
            log.info(
                "set_context.called",
                session_id=session.session_id,
                context=ctx[:100],
                cloud_providers=providers,
            )
            return [TextContent(
                type="text",
                text=f"Context updated. Next tools/list will return tools relevant to: {ctx}",
            )]

        ref = components.registry.lookup(name)
        if ref is None:
            raise ToolNotFoundError(name)

        log.info(
            "tool_call.start",
            session_id=session.session_id,
            namespaced_name=name,
        )

        t0 = time.time()
        try:
            result = await components.backend_manager.route_tool_call(name, arguments)
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

            return result.content

        except Exception as exc:
            duration_s = time.time() - t0
            components.metrics.record_tool_error(ref.backend_id, ref.original_name)
            log.error(
                "tool_call.error",
                session_id=session.session_id,
                namespaced_name=name,
                error=str(exc),
                duration_ms=round(duration_s * 1000, 1),
            )
            raise

    return server, components
