"""Tests for the embedding engine and tool index."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from cloud_engineer_mcp.backends.registry import ToolRef
from cloud_engineer_mcp.selector.index import ToolIndex


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


class FakeEngine:
    """A fake embedding engine that produces deterministic vectors."""

    def __init__(self) -> None:
        self._model = True  # pretend loaded

    @property
    def is_loaded(self) -> bool:
        return True

    def encode(self, texts: list[str]) -> np.ndarray:
        np.random.seed(42)
        vecs = np.random.randn(len(texts), 8).astype(np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / norms

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


class TestToolIndex:
    def setup_method(self) -> None:
        self.engine = FakeEngine()
        self.index = ToolIndex(self.engine, min_similarity=0.0)
        self.refs = [
            _make_ref("create_bucket", "aws_s3", "Create an S3 bucket"),
            _make_ref("list_buckets", "aws_s3", "List all S3 buckets"),
            _make_ref("delete_bucket", "aws_s3", "Delete an S3 bucket"),
            _make_ref("list_vms", "azure", "List Azure virtual machines"),
            _make_ref("list_instances", "gcp", "List GCP compute instances"),
        ]

    def test_build_and_search(self) -> None:
        self.index.build(self.refs)
        assert self.index.size == 5
        results = self.index.search("create a bucket", top_k=3)
        assert len(results) <= 3
        assert all(r.score is not None for r in results)

    def test_search_returns_sorted_descending(self) -> None:
        self.index.build(self.refs)
        results = self.index.search("list resources", top_k=5)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_cloud_provider_filter(self) -> None:
        self.index.build(self.refs)
        results = self.index.search("list", top_k=10, cloud_providers=["aws"])
        for r in results:
            assert r.namespaced_name.startswith("aws_")

    def test_cloud_provider_filter_azure(self) -> None:
        refs = [
            _make_ref("list_buckets", "aws_s3", "List S3 buckets"),
            _make_ref("list_vms", "az_aaaaaaaa_bbbb", "List Azure VMs"),
            _make_ref("list_instances", "gcp", "List GCP instances"),
        ]
        self.index.build(refs)
        results = self.index.search("list", top_k=10, cloud_providers=["azure"])
        for r in results:
            assert r.namespaced_name.startswith("az_")

    def test_score_boosts(self) -> None:
        self.index.build(self.refs)
        boosted = {"aws_s3__create_bucket": 10.0}
        results = self.index.search("anything", top_k=5, score_boosts=boosted)
        assert results[0].namespaced_name == "aws_s3__create_bucket"

    def test_empty_refs(self) -> None:
        self.index.build([])
        assert self.index.size == 0
        results = self.index.search("test", top_k=5)
        assert results == []

    def test_min_similarity_filter(self) -> None:
        strict_index = ToolIndex(self.engine, min_similarity=0.99)
        strict_index.build(self.refs)
        results = strict_index.search("random query", top_k=5)
        for r in results:
            assert r.score >= 0.99


class TestKeywordFallback:
    def test_keyword_search(self) -> None:
        engine = FakeEngine()
        engine._model = None  # simulate model not loaded

        class BrokenEngine(FakeEngine):
            @property
            def is_loaded(self) -> bool:
                return False

        index = ToolIndex(BrokenEngine(), min_similarity=0.0)
        refs = [
            _make_ref("create_bucket", "aws_s3", "Create an S3 bucket"),
            _make_ref("list_vms", "azure", "List Azure virtual machines"),
        ]
        index._names = [r.namespaced_name for r in refs]
        index._backend_ids = [r.backend_id for r in refs]
        index._descriptions = [r.description_for_embedding for r in refs]

        results = index.search("S3 bucket", top_k=5)
        assert len(results) > 0
        assert any("s3" in r.namespaced_name for r in results)


class TestIndexCache:
    def test_save_and_load(self, tmp_path) -> None:
        engine = FakeEngine()
        index = ToolIndex(engine, min_similarity=0.0)
        refs = [
            _make_ref("create", "aws", "Create resource"),
            _make_ref("delete", "aws", "Delete resource"),
        ]
        index.build(refs)

        cache_path = str(tmp_path / "cache.npz")
        index.save_cache(cache_path)

        index2 = ToolIndex(engine, min_similarity=0.0)
        loaded = index2.load_cache(cache_path, ["aws__create", "aws__delete"])
        assert loaded is True
        assert index2.size == 2

    def test_cache_invalidated_on_name_change(self, tmp_path) -> None:
        engine = FakeEngine()
        index = ToolIndex(engine, min_similarity=0.0)
        refs = [_make_ref("create", "aws", "Create resource")]
        index.build(refs)

        cache_path = str(tmp_path / "cache.npz")
        index.save_cache(cache_path)

        index2 = ToolIndex(engine, min_similarity=0.0)
        loaded = index2.load_cache(cache_path, ["aws__different_name"])
        assert loaded is False

    def test_cache_invalidated_on_version_mismatch(self, tmp_path, monkeypatch) -> None:
        import numpy as np

        from cloud_engineer_mcp.selector import index as index_mod

        engine = FakeEngine()
        index = ToolIndex(engine, min_similarity=0.0)
        refs = [_make_ref("create", "aws", "Create resource")]
        index.build(refs)

        cache_path = str(tmp_path / "cache.npz")
        # Write a cache file with the wrong version stamp.
        np.savez_compressed(
            tmp_path / "cache.npz",
            version=np.array([index_mod.CACHE_VERSION - 1], dtype=np.int32),
            matrix=np.zeros((1, 8), dtype=np.float32),
            names=np.array(["aws__create"]),
            backend_ids=np.array(["aws"]),
        )

        index2 = ToolIndex(engine, min_similarity=0.0)
        loaded = index2.load_cache(cache_path, ["aws__create"])
        assert loaded is False
