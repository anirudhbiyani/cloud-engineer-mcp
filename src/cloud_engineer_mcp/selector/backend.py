"""Pluggable selector backend protocol.

Lets the gateway pick the tool-ranking strategy at startup. Today there are
two implementations:

- `EmbeddingSelectorBackend` (alias: `ToolIndex` in `selector.index`) — local
  sentence-transformer + dense matvec. The default. ~5ms per query, ~80MB
  model download on first run.
- `BM25SelectorBackend` (`selector.bm25`) — pure-Python BM25 over tool
  descriptions. No model download, ~0.1ms per query, slightly lower recall.
  Useful for CI environments, offline use, or quick experimentation.

The two backends are interchangeable from `server.py`'s perspective. Adding a
third (ONNX runtime, hosted-API embedder, hybrid BM25→embedding rerank) is a
single file plus one branch in `make_selector_backend`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cloud_engineer_mcp.backends.registry import ToolRef
    from cloud_engineer_mcp.config import SelectorConfig
    from cloud_engineer_mcp.selector.engine import EmbeddingEngine
    from cloud_engineer_mcp.selector.index import ScoredTool


@runtime_checkable
class SelectorBackend(Protocol):
    """The interface server.py uses to rank tools per turn."""

    @property
    def is_loaded(self) -> bool: ...

    @property
    def size(self) -> int: ...

    def build(self, refs: list[ToolRef]) -> None: ...

    def search(
        self,
        query: str,
        top_k: int = 15,
        cloud_providers: list[str] | None = None,
        score_boosts: dict[str, float] | None = None,
    ) -> list[ScoredTool]: ...

    def save_cache(self, path: str) -> None: ...

    def load_cache(self, path: str, current_names: list[str]) -> bool: ...


def make_selector_backend(
    config: SelectorConfig,
    engine: EmbeddingEngine,
) -> SelectorBackend:
    """Construct the configured selector backend.

    `engine` is always passed for back-compat and because the embedding backend
    expects it; the BM25 backend ignores it.
    """
    backend = config.backend.lower()
    if backend == "bm25":
        from cloud_engineer_mcp.selector.bm25 import BM25SelectorBackend

        return BM25SelectorBackend(min_similarity=config.min_similarity)
    if backend == "embedding":
        from cloud_engineer_mcp.selector.index import ToolIndex

        return ToolIndex(engine, min_similarity=config.min_similarity)
    raise ValueError(
        f"Unknown selector backend {config.backend!r}. Expected 'embedding' or 'bm25'."
    )
