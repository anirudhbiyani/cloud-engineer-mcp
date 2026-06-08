"""ToolRegistry: namespaced catalog of all tools from all backends."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from mcp.types import Tool

from cloud_engineer_mcp.observability.logging import get_logger

log = get_logger("backends.registry")

NAMESPACE_SEP = "__"
NAMESPACED_PATTERN = re.compile(r"^[a-z][a-z0-9_]*__[a-z][a-z0-9_]*$")
MAX_TOOL_NAME_LEN = 40


@dataclass(frozen=True)
class ToolRef:
    namespaced_name: str
    original_name: str
    backend_id: str
    tool: Tool
    description_for_embedding: str


def build_embedding_text(backend_id: str, display_name: str, tool: Tool) -> str:
    """Build a rich text string for embedding that includes provider context."""
    # Order matters: longer/more specific prefixes first so e.g. "ado_" wins
    # over "az" for Azure DevOps backends.
    if backend_id.startswith("ado_"):
        provider = "Azure DevOps"
    elif backend_id.startswith("aws_kb"):
        provider = "AWS Documentation"
    elif backend_id.startswith("aws_rem"):
        provider = "AWS (managed)"
    elif backend_id.startswith("aws"):
        provider = "AWS"
    elif backend_id.startswith("az"):
        provider = "Azure"
    elif backend_id.startswith("gcp_rem"):
        provider = "Google Cloud (managed)"
    elif backend_id.startswith("gcp"):
        provider = "Google Cloud"
    elif backend_id.startswith("gh_remote"):
        provider = "GitHub"
    elif backend_id.startswith("mslearn"):
        provider = "Microsoft Learn"
    elif backend_id.startswith("k8s"):
        provider = "Kubernetes"
    elif backend_id.startswith("cloudflare"):
        provider = "Cloudflare"
    elif backend_id.startswith("digitalocean"):
        provider = "DigitalOcean"
    elif backend_id.startswith("playwright"):
        provider = "Playwright"
    else:
        provider = backend_id
    return f"[{provider} - {display_name}] {tool.name}: {tool.description or ''}"


def make_namespaced_name(backend_id: str, tool_name: str) -> str:
    name = f"{backend_id}{NAMESPACE_SEP}{tool_name}"
    if len(name) > MAX_TOOL_NAME_LEN:
        prefix_budget = MAX_TOOL_NAME_LEN - len(NAMESPACE_SEP) - len(tool_name)
        if prefix_budget < 4:
            name = name[:MAX_TOOL_NAME_LEN]
        else:
            name = f"{backend_id[:prefix_budget]}{NAMESPACE_SEP}{tool_name}"
    return name


def split_namespaced_name(namespaced_name: str) -> tuple[str, str]:
    """Split on the first __ to get (backend_id, original_name)."""
    parts = namespaced_name.split(NAMESPACE_SEP, 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid namespaced tool name: {namespaced_name}")
    return parts[0], parts[1]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolRef] = {}
        self._backend_tools: dict[str, list[str]] = defaultdict(list)

    def register_backend_tools(
        self,
        backend_id: str,
        display_name: str,
        tools: list[Tool],
    ) -> None:
        """Register all tools from a backend with namespace prefixing."""
        names: list[str] = []
        for tool in tools:
            ns_name = make_namespaced_name(backend_id, tool.name)
            namespaced_tool = Tool(
                name=ns_name,
                description=tool.description,
                inputSchema=tool.inputSchema,
            )
            embedding_text = build_embedding_text(backend_id, display_name, tool)
            ref = ToolRef(
                namespaced_name=ns_name,
                original_name=tool.name,
                backend_id=backend_id,
                tool=namespaced_tool,
                description_for_embedding=embedding_text,
            )
            self._tools[ns_name] = ref
            names.append(ns_name)

        self._backend_tools[backend_id] = names
        log.info(
            "registry.tools_registered",
            backend_id=backend_id,
            count=len(tools),
        )

    def unregister_backend(self, backend_id: str) -> None:
        """Remove all tools from a backend using the pre-built index."""
        names = self._backend_tools.pop(backend_id, [])
        for name in names:
            self._tools.pop(name, None)
        log.info(
            "registry.backend_unregistered",
            backend_id=backend_id,
            removed_count=len(names),
        )

    def lookup(self, namespaced_name: str) -> ToolRef | None:
        """Find a tool by its namespaced name."""
        return self._tools.get(namespaced_name)

    def all_refs(self) -> list[ToolRef]:
        """Return all registered ToolRefs."""
        return list(self._tools.values())

    def get_tool_definitions(self, namespaced_names: list[str]) -> list[Tool]:
        """Return MCP Tool definitions for the given namespaced names."""
        result = []
        for name in namespaced_names:
            ref = self._tools.get(name)
            if ref is not None:
                result.append(ref.tool)
        return result

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    def get_backend_tool_names(self, backend_id: str) -> list[str]:
        """Get all namespaced tool names for a specific backend. O(1) lookup."""
        return self._backend_tools.get(backend_id, [])
