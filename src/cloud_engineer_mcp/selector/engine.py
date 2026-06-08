"""EmbeddingEngine: loads a sentence-transformer model and encodes text to vectors."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cloud_engineer_mcp.observability.logging import get_logger

log = get_logger("selector.engine")

DEFAULT_CACHE_SIZE = 128


class EmbeddingEngine:
    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        query_cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        # SentenceTransformer is untyped; treat the model as Any inside this class.
        self._model: Any = None
        self._model_name = model_name
        self._dimension: int = 0
        self._query_cache: OrderedDict[str, NDArray[np.float32]] = OrderedDict()
        self._cache_size = query_cache_size

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def dimension(self) -> int:
        return self._dimension

    async def load(self) -> None:
        """Load the model in a thread executor to avoid blocking the event loop."""
        log.info("embedding.loading", model_name=self._model_name)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_sync)
        log.info("embedding.loaded", model_name=self._model_name, dimension=self._dimension)

    def _load_sync(self) -> None:
        """Synchronous model loading."""
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self._model_name)
        test_vec = self._model.encode(["test"], normalize_embeddings=True)
        self._dimension = test_vec.shape[1]

    def encode(self, texts: list[str]) -> NDArray[np.float32]:
        """Encode texts into normalized embedding vectors. Shape: (len(texts), dim)."""
        if self._model is None:
            raise RuntimeError("Model not loaded. Call load() first.")
        vectors = self._model.encode(texts, normalize_embeddings=True)
        arr: NDArray[np.float32] = np.array(vectors, dtype=np.float32)
        return arr

    def encode_single(self, text: str) -> NDArray[np.float32]:
        """Encode a single text with LRU caching. Returns shape (dim,)."""
        cached = self._query_cache.get(text)
        if cached is not None:
            self._query_cache.move_to_end(text)
            return cached

        vec: NDArray[np.float32] = self.encode([text])[0]
        self._query_cache[text] = vec
        if len(self._query_cache) > self._cache_size:
            self._query_cache.popitem(last=False)
        return vec

    async def encode_single_async(self, text: str) -> NDArray[np.float32]:
        """Encode a single text in a thread executor, with caching."""
        cached = self._query_cache.get(text)
        if cached is not None:
            self._query_cache.move_to_end(text)
            return cached

        loop = asyncio.get_event_loop()
        vec: NDArray[np.float32] = await loop.run_in_executor(None, lambda: self.encode([text])[0])
        self._query_cache[text] = vec
        if len(self._query_cache) > self._cache_size:
            self._query_cache.popitem(last=False)
        return vec
