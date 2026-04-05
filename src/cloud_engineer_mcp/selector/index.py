"""ToolIndex: vector index over all registered tools for similarity search."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from cloud_engineer_mcp.backends.registry import ToolRef
from cloud_engineer_mcp.observability.logging import get_logger
from cloud_engineer_mcp.selector.engine import EmbeddingEngine

log = get_logger("selector.index")


@dataclass
class ScoredTool:
    namespaced_name: str
    score: float


class ToolIndex:
    def __init__(self, engine: EmbeddingEngine, min_similarity: float = 0.15) -> None:
        self._engine = engine
        self._min_similarity = min_similarity
        self._names: list[str] = []
        self._backend_ids: list[str] = []
        self._matrix: NDArray[np.float32] | None = None
        self._descriptions: list[str] = []
        self._name_to_idx: dict[str, int] = {}
        self._provider_indices: dict[str, NDArray[np.intp]] = {}

    @property
    def size(self) -> int:
        return len(self._names)

    def build(self, tool_refs: list[ToolRef]) -> None:
        """Build the index from tool refs by encoding all descriptions."""
        if not tool_refs:
            self._names = []
            self._matrix = None
            self._name_to_idx = {}
            self._provider_indices = {}
            return

        self._names = [ref.namespaced_name for ref in tool_refs]
        self._backend_ids = [ref.backend_id for ref in tool_refs]
        self._descriptions = [ref.description_for_embedding for ref in tool_refs]

        self._name_to_idx = {name: i for i, name in enumerate(self._names)}
        self._build_provider_indices()

        if self._engine.is_loaded:
            self._matrix = self._engine.encode(self._descriptions)
            log.info("index.built", tool_count=len(self._names), dim=self._matrix.shape[1])
        else:
            self._matrix = None
            log.warning("index.no_model", tool_count=len(self._names))

    def _build_provider_indices(self) -> None:
        """Pre-compute provider prefix -> array of matching indices.

        Azure backend IDs use the "az_" prefix, but the user-facing
        cloud_providers enum uses "azure", so we store indices under both keys.
        """
        groups: dict[str, list[int]] = defaultdict(list)
        for i, bid in enumerate(self._backend_ids):
            for prefix in ("aws", "az", "gcp"):
                if bid.startswith(prefix):
                    groups[prefix].append(i)
                    break
        self._provider_indices = {
            prefix: np.array(indices, dtype=np.intp)
            for prefix, indices in groups.items()
        }
        if "az" in self._provider_indices:
            self._provider_indices["azure"] = self._provider_indices["az"]

    def search(
        self,
        query: str,
        top_k: int = 15,
        cloud_providers: list[str] | None = None,
        score_boosts: dict[str, float] | None = None,
    ) -> list[ScoredTool]:
        """Find the top-K most similar tools to the query."""
        if self._matrix is not None and self._engine.is_loaded:
            return self._vector_search(query, top_k, cloud_providers, score_boosts)
        return self._keyword_search(query, top_k, cloud_providers)

    def _vector_search(
        self,
        query: str,
        top_k: int,
        cloud_providers: list[str] | None,
        score_boosts: dict[str, float] | None,
    ) -> list[ScoredTool]:
        """Embedding-based similarity search with pre-computed indices."""
        assert self._matrix is not None
        query_vec = self._engine.encode_single(query)
        scores = self._matrix @ query_vec

        if cloud_providers:
            allowed = np.zeros(len(self._names), dtype=bool)
            for cp in cloud_providers:
                idx = self._provider_indices.get(cp)
                if idx is not None:
                    allowed[idx] = True
            scores[~allowed] = -1.0

        if score_boosts:
            for name, boost in score_boosts.items():
                idx = self._name_to_idx.get(name)
                if idx is not None:
                    scores[idx] += boost

        valid_mask = scores >= self._min_similarity
        valid_indices = np.where(valid_mask)[0]

        if len(valid_indices) == 0:
            return []

        if len(valid_indices) <= top_k:
            selected = valid_indices
        else:
            partition_indices = np.argpartition(-scores[valid_indices], top_k)[:top_k]
            selected = valid_indices[partition_indices]

        results = [
            ScoredTool(namespaced_name=self._names[i], score=float(scores[i]))
            for i in selected
        ]
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def _keyword_search(
        self,
        query: str,
        top_k: int,
        cloud_providers: list[str] | None,
    ) -> list[ScoredTool]:
        """Fallback keyword matching when embedding model is unavailable."""
        query_terms = set(re.findall(r"\w+", query.lower()))
        scored: list[ScoredTool] = []

        if cloud_providers:
            allowed_indices: set[int] = set()
            for cp in cloud_providers:
                idx = self._provider_indices.get(cp)
                if idx is not None:
                    allowed_indices.update(idx.tolist())
        else:
            allowed_indices = None

        for i, desc in enumerate(self._descriptions):
            if allowed_indices is not None and i not in allowed_indices:
                continue

            desc_lower = desc.lower()
            matches = sum(1 for term in query_terms if term in desc_lower)
            if matches > 0:
                score = matches / max(len(query_terms), 1)
                scored.append(ScoredTool(namespaced_name=self._names[i], score=score))

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]

    def save_cache(self, path: str) -> None:
        """Save embeddings to disk for faster restarts."""
        if self._matrix is None:
            return
        cache_path = Path(path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            cache_path,
            matrix=self._matrix,
            names=np.array(self._names),
            backend_ids=np.array(self._backend_ids),
        )
        log.info("index.cache_saved", path=path, tools=len(self._names))

    def load_cache(self, path: str, current_names: list[str]) -> bool:
        """Load cached embeddings. Returns True if cache was valid."""
        cache_path = Path(path)
        if not cache_path.exists():
            return False

        try:
            data = np.load(cache_path, allow_pickle=False)
            cached_names = list(data["names"])
            if cached_names != current_names:
                log.info("index.cache_invalidated", reason="tool names changed")
                return False
            self._matrix = data["matrix"]
            self._names = cached_names
            self._backend_ids = list(data["backend_ids"])
            self._name_to_idx = {name: i for i, name in enumerate(self._names)}
            self._build_provider_indices()
            log.info("index.cache_loaded", path=path, tools=len(self._names))
            return True
        except Exception as exc:
            log.warning("index.cache_load_failed", error=str(exc))
            return False
