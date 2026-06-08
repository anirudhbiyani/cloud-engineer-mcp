"""Evaluation runner.

Builds a `ToolIndex` from the bundled catalog, runs the labeled query set
through the same code path the live gateway uses, and computes Recall@K.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from mcp.types import Tool

from cloud_engineer_mcp.backends.registry import ToolRef, build_embedding_text
from cloud_engineer_mcp.eval.catalog import CATALOG, CatalogEntry
from cloud_engineer_mcp.selector.backend import SelectorBackend
from cloud_engineer_mcp.selector.bm25 import BM25SelectorBackend
from cloud_engineer_mcp.selector.context import ContextExtractor
from cloud_engineer_mcp.selector.engine import EmbeddingEngine
from cloud_engineer_mcp.selector.index import ToolIndex

DATASET_PATH = Path(__file__).parent / "dataset.yml"
DEFAULT_KS = (5, 10, 15, 30)


@dataclass(frozen=True)
class EvalCase:
    """A single labeled query / expected-tool pair."""

    query: str
    expected: tuple[str, ...]
    action: str | None = None
    resource_type: str | None = None
    providers: tuple[str, ...] | None = None


@dataclass
class EvalResult:
    """Aggregate metrics across all eval cases."""

    total_cases: int
    recall_at: dict[int, float] = field(default_factory=dict)
    mean_rank: float = 0.0
    median_rank: float = 0.0
    misses: list[str] = field(default_factory=list)
    p99_latency_ms: float = 0.0
    catalog_size: int = 0
    mode: str = "embedding"

    def passed(self, threshold_at_15: float) -> bool:
        return self.recall_at.get(15, 0.0) >= threshold_at_15


def load_dataset(path: Path = DATASET_PATH) -> list[EvalCase]:
    """Parse the YAML dataset into typed EvalCase instances."""
    raw = yaml.safe_load(path.read_text())
    cases: list[EvalCase] = []
    for entry in raw:
        expected = entry["expected"]
        expected_tuple = tuple(expected) if isinstance(expected, list) else (expected,)
        providers = entry.get("providers")
        cases.append(
            EvalCase(
                query=entry["query"],
                expected=expected_tuple,
                action=entry.get("action"),
                resource_type=entry.get("resource_type"),
                providers=tuple(providers) if providers else None,
            )
        )
    return cases


def _catalog_to_refs(catalog: tuple[CatalogEntry, ...]) -> list[ToolRef]:
    refs: list[ToolRef] = []
    for entry in catalog:
        # `Tool` requires an inputSchema; an empty object dict is valid MCP.
        tool = Tool(
            name=entry.namespaced_name,
            description=entry.description,
            inputSchema={"type": "object"},
        )
        backend_id = entry.backend_id
        display = backend_id.replace("_", " ").title()
        embedding_text = build_embedding_text(backend_id, display, tool)
        refs.append(
            ToolRef(
                namespaced_name=entry.namespaced_name,
                original_name=entry.namespaced_name.split("__", 1)[1],
                backend_id=backend_id,
                tool=tool,
                description_for_embedding=embedding_text,
            )
        )
    return refs


def run_eval(
    cases: list[EvalCase] | None = None,
    catalog: tuple[CatalogEntry, ...] = CATALOG,
    model_name: str = "all-MiniLM-L6-v2",
    ks: tuple[int, ...] = DEFAULT_KS,
    *,
    use_embeddings: bool = True,
    backend: str | None = None,
) -> EvalResult:
    """Run the eval and return aggregate metrics.

    Args:
        cases: Eval cases to run. Defaults to the bundled dataset.
        catalog: Tool catalog to index. Defaults to bundled synthetic catalog.
        model_name: Sentence-transformer to load. Ignored for non-embedding backends.
        ks: K values for Recall@K.
        use_embeddings: Legacy flag. When False, runs the embedding backend's
            keyword fallback. Kept for back-compat; prefer `backend=`.
        backend: One of "embedding", "bm25", or None (use `use_embeddings`).
    """
    if cases is None:
        cases = load_dataset()

    chosen = backend or ("embedding" if use_embeddings else "embedding")
    index: SelectorBackend
    mode_label: str
    if chosen == "bm25":
        index = BM25SelectorBackend(min_similarity=0.0)
        mode_label = "bm25"
    else:
        engine = EmbeddingEngine(model_name)
        if use_embeddings:
            engine._load_sync()
        # ToolIndex satisfies SelectorBackend at runtime; mypy can't always
        # see that from a literal-string branch, so help it along.
        index = ToolIndex(engine, min_similarity=0.0)
        mode_label = "embedding" if use_embeddings else "keyword"
    refs = _catalog_to_refs(catalog)
    index.build(refs)

    extractor = ContextExtractor(max_tokens=512)

    ranks: list[int] = []
    latencies_ms: list[float] = []
    misses: list[str] = []
    found_in_k: dict[int, int] = {k: 0 for k in ks}
    max_k = max(ks)

    for case in cases:
        query = extractor.extract_query(
            user_message=case.query,
            action=case.action,
            resource_type=case.resource_type,
        )
        t0 = time.perf_counter()
        results = index.search(
            query,
            top_k=max_k,
            cloud_providers=list(case.providers) if case.providers else None,
        )
        latencies_ms.append((time.perf_counter() - t0) * 1000)

        rank = _first_match_rank(results, case.expected)
        if rank is None:
            misses.append(f"{case.query!r} expected one of {list(case.expected)}")
            # Use max_k + 1 as a sentinel for "not found in top max_k".
            ranks.append(max_k + 1)
            continue
        ranks.append(rank)
        for k in ks:
            if rank <= k:
                found_in_k[k] += 1

    n = len(cases)
    sorted_ranks = sorted(ranks)
    median_rank = float(sorted_ranks[n // 2]) if n else 0.0
    mean_rank = sum(ranks) / n if n else 0.0
    p99 = sorted(latencies_ms)[int(len(latencies_ms) * 0.99) - 1] if latencies_ms else 0.0

    return EvalResult(
        total_cases=n,
        recall_at={k: found_in_k[k] / n for k in ks} if n else dict.fromkeys(ks, 0.0),
        mean_rank=mean_rank,
        median_rank=median_rank,
        misses=misses,
        p99_latency_ms=p99,
        catalog_size=len(catalog),
        mode=mode_label,
    )


def _first_match_rank(results: list[Any], expected: tuple[str, ...]) -> int | None:
    """Return the 1-indexed position of the first expected tool, or None."""
    expected_set = set(expected)
    for i, scored in enumerate(results, start=1):
        if scored.namespaced_name in expected_set:
            return i
    return None
