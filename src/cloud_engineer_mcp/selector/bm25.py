"""BM25 selector backend.

Pure-Python BM25 (Okapi variant) over tool descriptions. Self-contained: no
sentence-transformers, no torch, no numpy beyond what the project already
uses. Returns the same `ScoredTool` shape as the embedding backend so it's
a drop-in replacement.

Recall vs. embeddings on the bundled eval set:
- embedding: 100% Recall@15
- bm25:       ~95% Recall@15

Use the embedding backend by default. Pick BM25 when:
- You can't (or don't want to) download the embedding model.
- You need fully deterministic, dependency-light ranking (CI, dev).
- You want sub-millisecond ranking on tiny tool catalogs.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from cloud_engineer_mcp.backends.registry import ToolRef
from cloud_engineer_mcp.observability.logging import get_logger
from cloud_engineer_mcp.selector.index import ScoredTool

log = get_logger("selector.bm25")

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Standard BM25 hyperparameters.
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class BM25SelectorBackend:
    """BM25 ranker that satisfies the `SelectorBackend` protocol.

    Fields here are intentionally minimal — the heavy lifting happens in
    `build()` which precomputes per-document term frequencies and the
    inverted index.
    """

    min_similarity: float = 0.0
    k1: float = DEFAULT_K1
    b: float = DEFAULT_B

    def __post_init__(self) -> None:
        self._names: list[str] = []
        self._backend_ids: list[str] = []
        self._doc_lens: list[int] = []
        self._doc_freqs: list[Counter[str]] = []
        self._inverted_index: dict[str, list[int]] = defaultdict(list)
        self._idf: dict[str, float] = {}
        self._avg_doc_len: float = 0.0
        self._provider_indices: dict[str, set[int]] = {}
        self._name_to_idx: dict[str, int] = {}

    @property
    def is_loaded(self) -> bool:
        return bool(self._names)

    @property
    def size(self) -> int:
        return len(self._names)

    def build(self, refs: list[ToolRef]) -> None:
        """Tokenize, count, and precompute IDF."""
        self._names = [ref.namespaced_name for ref in refs]
        self._backend_ids = [ref.backend_id for ref in refs]
        self._name_to_idx = {name: i for i, name in enumerate(self._names)}
        self._doc_freqs = []
        self._doc_lens = []
        self._inverted_index = defaultdict(list)
        n_docs = len(refs)

        df: Counter[str] = Counter()
        for i, ref in enumerate(refs):
            tokens = _tokenize(ref.description_for_embedding)
            tf = Counter(tokens)
            self._doc_freqs.append(tf)
            self._doc_lens.append(len(tokens))
            for term in tf:
                self._inverted_index[term].append(i)
                df[term] += 1

        self._avg_doc_len = sum(self._doc_lens) / n_docs if n_docs else 0.0
        # Okapi BM25 IDF with the +1 smoothing to avoid negative IDFs for
        # very common terms.
        self._idf = {term: math.log(1.0 + (n_docs - n + 0.5) / (n + 0.5)) for term, n in df.items()}
        self._build_provider_indices()
        log.info("bm25.built", tool_count=n_docs, vocab_size=len(self._idf))

    def _build_provider_indices(self) -> None:
        groups: dict[str, set[int]] = defaultdict(set)
        for i, bid in enumerate(self._backend_ids):
            for prefix in (
                "ado_",
                "aws",
                "az",
                "gcp",
                "k8s",
                "cloudflare",
                "digitalocean",
                "playwright",
            ):
                if bid.startswith(prefix):
                    groups[prefix].add(i)
                    break
        self._provider_indices = dict(groups)
        if "az" in self._provider_indices:
            self._provider_indices["azure"] = self._provider_indices["az"]
        if "k8s" in self._provider_indices:
            self._provider_indices["kubernetes"] = self._provider_indices["k8s"]
        if "ado_" in self._provider_indices:
            self._provider_indices["azure_devops"] = self._provider_indices["ado_"]

    def search(
        self,
        query: str,
        top_k: int = 15,
        cloud_providers: list[str] | None = None,
        score_boosts: dict[str, float] | None = None,
    ) -> list[ScoredTool]:
        if not self._names:
            return []

        terms = _tokenize(query)
        if not terms:
            return []

        # Compute BM25 score for every doc that contains at least one term.
        scores: dict[int, float] = defaultdict(float)
        for term in terms:
            postings = self._inverted_index.get(term)
            if not postings:
                continue
            idf = self._idf.get(term, 0.0)
            for doc_id in postings:
                tf = self._doc_freqs[doc_id][term]
                dl = self._doc_lens[doc_id]
                norm = 1.0 - self.b + self.b * (dl / self._avg_doc_len)
                tf_component = (tf * (self.k1 + 1.0)) / (tf + self.k1 * norm)
                scores[doc_id] += idf * tf_component

        # Provider filter is a hard mask.
        if cloud_providers:
            allowed: set[int] = set()
            for cp in cloud_providers:
                allowed |= self._provider_indices.get(cp, set())
            scores = {i: s for i, s in scores.items() if i in allowed}

        # Apply score boosts (session pinning).
        if score_boosts:
            for name, boost in score_boosts.items():
                idx = self._name_to_idx.get(name)
                if idx is not None and idx in scores:
                    scores[idx] += boost

        # Filter by min_similarity and take top-K.
        candidates = [(i, s) for i, s in scores.items() if s >= self.min_similarity]
        if not candidates:
            return []
        candidates.sort(key=lambda kv: kv[1], reverse=True)
        return [
            ScoredTool(namespaced_name=self._names[i], score=float(s))
            for i, s in candidates[:top_k]
        ]

    def save_cache(self, path: str) -> None:
        """BM25 build is fast (no model load); the cache is a no-op by design."""
        return None

    def load_cache(self, path: str, current_names: list[str]) -> bool:
        """BM25 rebuilds on every startup. Returns False to force a build."""
        return False
