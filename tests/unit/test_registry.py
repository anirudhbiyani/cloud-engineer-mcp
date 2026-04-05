"""Tests for ToolRegistry."""

from __future__ import annotations

import pytest
from mcp.types import Tool

from cloud_engineer_mcp.backends.registry import (
    ToolRegistry,
    build_embedding_text,
    make_namespaced_name,
    split_namespaced_name,
)


def _make_tool(name: str, description: str = "A test tool") -> Tool:
    return Tool(name=name, description=description, inputSchema={"type": "object"})


class TestNamespacing:
    def test_make_namespaced_name(self) -> None:
        assert make_namespaced_name("aws_s3", "list_buckets") == "aws_s3__list_buckets"

    def test_split_namespaced_name(self) -> None:
        bid, orig = split_namespaced_name("aws_s3__list_buckets")
        assert bid == "aws_s3"
        assert orig == "list_buckets"

    def test_split_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            split_namespaced_name("no_separator")

    def test_split_with_extra_underscores(self) -> None:
        bid, orig = split_namespaced_name("aws_ccapi__create_resource")
        assert bid == "aws_ccapi"
        assert orig == "create_resource"


class TestBuildEmbeddingText:
    def test_aws_provider(self) -> None:
        tool = _make_tool("list_buckets", "List S3 buckets")
        text = build_embedding_text("aws_s3", "AWS S3", tool)
        assert "[AWS - AWS S3]" in text
        assert "list_buckets" in text
        assert "List S3 buckets" in text

    def test_azure_provider(self) -> None:
        tool = _make_tool("list_vms", "List virtual machines")
        text = build_embedding_text("azure", "Azure Compute", tool)
        assert "[Azure - Azure Compute]" in text

    def test_azure_provider_az_prefix(self) -> None:
        tool = _make_tool("list_vms", "List virtual machines")
        text = build_embedding_text("az_aaaaaaaa_bbbb", "Azure (standard)", tool)
        assert "[Azure - Azure (standard)]" in text

    def test_gcp_provider(self) -> None:
        tool = _make_tool("list_instances", "List instances")
        text = build_embedding_text("gcp_adk", "Google Cloud", tool)
        assert "[Google Cloud - Google Cloud]" in text


class TestToolRegistry:
    def test_register_and_lookup(self) -> None:
        registry = ToolRegistry()
        tools = [_make_tool("create"), _make_tool("delete")]
        registry.register_backend_tools("aws_s3", "AWS S3", tools)

        ref = registry.lookup("aws_s3__create")
        assert ref is not None
        assert ref.original_name == "create"
        assert ref.backend_id == "aws_s3"
        assert ref.tool.name == "aws_s3__create"

    def test_lookup_missing_returns_none(self) -> None:
        registry = ToolRegistry()
        assert registry.lookup("nonexistent__tool") is None

    def test_unregister_backend(self) -> None:
        registry = ToolRegistry()
        registry.register_backend_tools("aws_s3", "AWS S3", [_make_tool("list")])
        registry.register_backend_tools("azure", "Azure", [_make_tool("list")])

        assert registry.tool_count == 2
        registry.unregister_backend("aws_s3")
        assert registry.tool_count == 1
        assert registry.lookup("aws_s3__list") is None
        assert registry.lookup("azure__list") is not None

    def test_all_refs(self) -> None:
        registry = ToolRegistry()
        registry.register_backend_tools("a", "A", [_make_tool("t1"), _make_tool("t2")])
        refs = registry.all_refs()
        assert len(refs) == 2

    def test_get_tool_definitions(self) -> None:
        registry = ToolRegistry()
        registry.register_backend_tools("b", "B", [_make_tool("x"), _make_tool("y")])
        tools = registry.get_tool_definitions(["b__x"])
        assert len(tools) == 1
        assert tools[0].name == "b__x"

    def test_duplicate_tool_names_different_backends(self) -> None:
        registry = ToolRegistry()
        registry.register_backend_tools("aws", "AWS", [_make_tool("list")])
        registry.register_backend_tools("gcp", "GCP", [_make_tool("list")])
        assert registry.tool_count == 2
        assert registry.lookup("aws__list") is not None
        assert registry.lookup("gcp__list") is not None

    def test_get_backend_tool_names(self) -> None:
        registry = ToolRegistry()
        registry.register_backend_tools("aws", "AWS", [_make_tool("a"), _make_tool("b")])
        registry.register_backend_tools("gcp", "GCP", [_make_tool("c")])
        names = registry.get_backend_tool_names("aws")
        assert len(names) == 2
        assert all(n.startswith("aws__") for n in names)
