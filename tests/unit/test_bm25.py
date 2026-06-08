"""Tests for the BM25 selector backend."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cloud_engineer_mcp.backends.registry import ToolRef
from cloud_engineer_mcp.selector.backend import SelectorBackend, make_selector_backend
from cloud_engineer_mcp.selector.bm25 import BM25SelectorBackend
from cloud_engineer_mcp.selector.engine import EmbeddingEngine


def _make_ref(name: str, backend_id: str, desc: str) -> ToolRef:
    tool = MagicMock()
    tool.name = name
    tool.description = desc
    return ToolRef(
        namespaced_name=f"{backend_id}__{name}",
        original_name=name,
        backend_id=backend_id,
        tool=tool,
        description_for_embedding=f"[Test] {name}: {desc}",
    )


class TestBM25Backend:
    def setup_method(self) -> None:
        self.index = BM25SelectorBackend(min_similarity=0.0)
        self.refs = [
            _make_ref("create_bucket", "aws_s3", "Create an S3 bucket with versioning"),
            _make_ref("list_buckets", "aws_s3", "List all S3 buckets in the account"),
            _make_ref("delete_bucket", "aws_s3", "Delete an S3 bucket and its objects"),
            _make_ref("list_vms", "az_compute", "List Azure virtual machines"),
            _make_ref("list_instances", "gcp", "List GCP compute instances"),
        ]
        self.index.build(self.refs)

    def test_is_loaded_after_build(self) -> None:
        assert self.index.is_loaded
        assert self.index.size == 5

    def test_search_finds_exact_match(self) -> None:
        results = self.index.search("create S3 bucket", top_k=3)
        assert len(results) > 0
        assert results[0].namespaced_name == "aws_s3__create_bucket"

    def test_search_returns_sorted(self) -> None:
        results = self.index.search("list buckets", top_k=5)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_empty_query(self) -> None:
        assert self.index.search("", top_k=5) == []
        assert self.index.search("!!!", top_k=5) == []

    def test_cloud_provider_filter(self) -> None:
        results = self.index.search("list", top_k=10, cloud_providers=["aws"])
        for r in results:
            assert r.namespaced_name.startswith("aws_")

    def test_cloud_provider_filter_azure_alias(self) -> None:
        results = self.index.search("list", top_k=10, cloud_providers=["azure"])
        for r in results:
            assert r.namespaced_name.startswith("az_")

    def test_score_boost(self) -> None:
        boosts = {"aws_s3__delete_bucket": 100.0}
        # "create" alone wouldn't pick delete; the boost should hoist it.
        results = self.index.search("create bucket", top_k=3, score_boosts=boosts)
        assert results[0].namespaced_name == "aws_s3__delete_bucket"

    def test_unknown_query_terms(self) -> None:
        assert self.index.search("xyzzy plugh", top_k=5) == []

    def test_min_similarity_filters(self) -> None:
        strict = BM25SelectorBackend(min_similarity=1000.0)
        strict.build(self.refs)
        assert strict.search("bucket", top_k=5) == []

    def test_cache_is_noop(self, tmp_path) -> None:
        cache_path = str(tmp_path / "cache.npz")
        # save is a no-op; load returns False.
        self.index.save_cache(cache_path)
        fresh = BM25SelectorBackend()
        assert fresh.load_cache(cache_path, []) is False


class TestSelectorBackendFactory:
    def test_make_embedding(self) -> None:
        from cloud_engineer_mcp.config import SelectorConfig

        cfg = SelectorConfig(backend="embedding")
        engine = EmbeddingEngine(cfg.model_name)
        sb = make_selector_backend(cfg, engine)
        assert isinstance(sb, SelectorBackend)

    def test_make_bm25(self) -> None:
        from cloud_engineer_mcp.config import SelectorConfig

        cfg = SelectorConfig(backend="bm25")
        engine = EmbeddingEngine(cfg.model_name)  # ignored by bm25
        sb = make_selector_backend(cfg, engine)
        assert isinstance(sb, BM25SelectorBackend)

    def test_make_unknown_raises(self) -> None:
        from cloud_engineer_mcp.config import SelectorConfig

        cfg = SelectorConfig(backend="random_forest")
        engine = EmbeddingEngine(cfg.model_name)
        with pytest.raises(ValueError, match="Unknown selector backend"):
            make_selector_backend(cfg, engine)
