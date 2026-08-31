"""Embedding API client (OpenAI/DeepSeek compatible).

Batch-embeds text via HTTP POST to ``/v1/embeddings``.
Returns float vectors for semantic similarity search.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import numpy as np

from src.config.settings import get_embedding_settings, require_secret

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """API embedding client — OpenAI/DeepSeek compatible.

    Usage::

        client = EmbeddingClient()
        vec = await client.embed_one("ReAct 推理")
        # vec = [0.01, -0.02, 0.03, ...]
    """

    def __init__(self) -> None:
        s = get_embedding_settings()
        self._base_url = s.base_url.rstrip("/")
        self._model = s.model
        self._dim = s.dim
        self._batch_size = s.batch_size
        # API key: try embedding-specific key, fall back to deepseek key
        if s.api_key is not None:
            self._api_key = s.api_key.get_secret_value()
        else:
            # Fall back to DEEPSEEK_API_KEY for convenience
            try:
                from src.config.settings import get_deepseek_api_key
                self._api_key = get_deepseek_api_key()
            except EnvironmentError:
                logger.warning(
                    "No embedding API key configured (KG_EMBEDDING_API_KEY "
                    "or DEEPSEEK_API_KEY); embedding disabled"
                )
                self._api_key = ""

    @property
    def is_available(self) -> bool:
        """Whether the embedding client has an API key configured."""
        return bool(self._api_key)

    async def embed(self, texts: list[str]) -> np.ndarray:
        """Batch embed texts → (N, dim) float array.

        Raises ``RuntimeError`` if API key is missing.
        """
        if not self._api_key:
            raise RuntimeError("Embedding API key not configured")
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)

        # Build the endpoint URL.
        # If base_url already ends with /v1, don't double-append it.
        if self._base_url.endswith("/v1"):
            endpoint = f"{self._base_url}/embeddings"
        else:
            endpoint = f"{self._base_url}/v1/embeddings"

        all_vectors: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i: i + self._batch_size]
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json={"input": batch, "model": self._model},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    batch_vecs = [d["embedding"] for d in data["data"]]
                    all_vectors.extend(batch_vecs)
            except Exception as e:
                logger.warning(
                    "Embedding API batch %d/%d failed: %s",
                    i // self._batch_size + 1,
                    (len(texts) + self._batch_size - 1) // self._batch_size,
                    e,
                )
                # Fill with zeros on failure — degraded but functional
                all_vectors.extend(
                    [[0.0] * self._dim] * len(batch)
                )

        return np.array(all_vectors, dtype=np.float32)

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single text → list[float]."""
        arr = await self.embed([text])
        if arr.shape[0] == 0:
            return [0.0] * self._dim
        return arr[0].tolist()


__all__ = ["EmbeddingClient"]
