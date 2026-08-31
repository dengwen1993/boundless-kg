"""SearchService — BM25 + vector hybrid retrieval.

Strategy
--------

1. BM25 keyword search → exact term matching (e.g. "MCP" → nodes containing "MCP")
2. Vector cosine similarity → semantic matching (e.g. "MCP" → "Tool Calling")
3. Reciprocal Rank Fusion → merge both rankings into a single sorted list

The fusion uses Reciprocal Rank Fusion (RRF):

    fused_score = w_bm25 / (k + rank_bm25) + w_vector / (k + rank_vector)

where k=60 (standard RRF constant), and weights are configurable.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.settings import get_embedding_settings
from src.infrastructure.embedding.bm25 import BM25Index
from src.infrastructure.embedding.client import EmbeddingClient
from src.infrastructure.graph_store.client import GraphStoreClient

logger = logging.getLogger(__name__)

#: RRF constant — standard value from the original paper.
RRF_K: int = 60


class SearchResult:
    """A single search result."""

    __slots__ = (
        "id", "name", "type", "domain",
        "bm25_score", "vector_score", "hybrid_score",
        "snippet", "neighbors",
    )

    def __init__(
        self, *,
        id: str, name: str, type: str, domain: str,
        bm25_score: float = 0.0, vector_score: float = 0.0,
        hybrid_score: float = 0.0,
        snippet: str = "", neighbors: list[dict] | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.type = type
        self.domain = domain
        self.bm25_score = bm25_score
        self.vector_score = vector_score
        self.hybrid_score = hybrid_score
        self.snippet = snippet
        self.neighbors = neighbors or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "domain": self.domain,
            "bm25_score": round(self.bm25_score, 4),
            "vector_score": round(self.vector_score, 4),
            "hybrid_score": round(self.hybrid_score, 4),
            "snippet": self.snippet,
            "neighbors": self.neighbors,
        }


class SearchService:
    """BM25 + vector hybrid search service."""

    def __init__(
        self,
        graph_store: GraphStoreClient,
        embedding_client: EmbeddingClient,
        bm25_index: BM25Index,
        *,
        bm25_weight: float | None = None,
        vector_weight: float | None = None,
    ) -> None:
        self._graph = graph_store
        self._embed = embedding_client
        self._bm25 = bm25_index
        s = get_embedding_settings()
        self._bm25_weight = bm25_weight if bm25_weight is not None else s.bm25_weight
        self._vector_weight = vector_weight if vector_weight is not None else s.vector_weight

    async def search(
        self, domain: str, query: str, *, top_k: int | None = None,
    ) -> list[SearchResult]:
        """Hybrid search: BM25 + vector → RRF fusion.

        Args:
            domain: Domain name
            query: Natural language query
            top_k: Number of results (default from config)

        Returns:
            List of SearchResult sorted by hybrid score descending.
        """
        if top_k is None:
            s = get_embedding_settings()
            top_k = s.default_top_k

        # 1. BM25 keyword search
        bm25_results = self._bm25.search(domain, query, top_k=top_k * 3)

        # 2. Vector semantic search
        vector_results: list[dict] = []
        if self._embed.is_available:
            try:
                query_vec = await self._embed.embed_one(query)
                vector_results = self._graph.vector_search(
                    domain, query_vec, top_k=top_k * 3
                )
            except Exception as e:
                logger.warning("Vector search failed: %s", e)

        # 3. Reciprocal Rank Fusion
        fused = self._fuse_ranks(bm25_results, vector_results, top_k)
        return fused

    async def global_search(
        self, domain: str, query: str, *, top_k: int | None = None,
    ) -> dict[str, Any]:
        """Agent global understanding search.

        Combines hybrid search with 1-hop graph neighbors for each result,
        giving the agent a complete picture of "what exists and how it's
        connected" for making large-scale plans.

        Returns:
            {
              "query": "...",
              "domain": "...",
              "results": [
                {
                  "id": "concept:ReAct推理",
                  "name": "ReAct推理",
                  "type": "concept",
                  "score": 0.85,
                  "snippet": "...",
                  "neighbors": [
                    {"name": "思维链", "relation": "PREREQUISITE_OF", "hops": 1}
                  ]
                }
              ],
              "total": N
            }
        """
        results = await self.search(domain, query, top_k=top_k)

        # Enrich with 1-hop neighbors
        enriched: list[dict[str, Any]] = []
        for r in results:
            neighbors = self._graph.neighbors(domain, r.id, hops=1)
            enriched.append({
                **r.to_dict(),
                "neighbors": neighbors,
            })

        return {
            "query": query,
            "domain": domain,
            "results": enriched,
            "total": len(enriched),
        }

    def _fuse_ranks(
        self,
        bm25_results: list[dict],
        vector_results: list[dict],
        top_k: int,
    ) -> list[SearchResult]:
        """Reciprocal Rank Fusion of BM25 and vector search results.

        RRF formula: score = w1 / (k + rank1) + w2 / (k + rank2)
        """
        # Build rank maps (1-indexed)
        bm25_rank: dict[str, int] = {}
        for i, r in enumerate(bm25_results):
            rid = r.get("id", "")
            if rid and rid not in bm25_rank:
                bm25_rank[rid] = i + 1

        vector_rank: dict[str, int] = {}
        for i, r in enumerate(vector_results):
            rid = r.get("id", r.get("name", ""))
            if rid and rid not in vector_rank:
                vector_rank[rid] = i + 1

        # Merge all unique result IDs
        all_ids = set(bm25_rank.keys()) | set(vector_rank.keys())

        # Build a lookup for metadata
        meta: dict[str, dict] = {}
        for r in bm25_results:
            rid = r.get("id", "")
            if rid:
                meta[rid] = r
        for r in vector_results:
            rid = r.get("id", r.get("name", ""))
            if rid and rid not in meta:
                meta[rid] = r

        results: list[SearchResult] = []
        for rid in all_ids:
            m = meta.get(rid, {})
            bm25_rank_val = bm25_rank.get(rid, 9999)
            vec_rank_val = vector_rank.get(rid, 9999)

            bm25_score = 1.0 / (RRF_K + bm25_rank_val)
            vec_score = 1.0 / (RRF_K + vec_rank_val)
            fused = (
                self._bm25_weight * bm25_score
                + self._vector_weight * vec_score
            )

            results.append(SearchResult(
                id=rid,
                name=m.get("name", rid),
                type=m.get("type", "concept"),
                domain=m.get("domain", ""),
                bm25_score=bm25_score,
                vector_score=vec_score,
                hybrid_score=fused,
                snippet=m.get("snippet", m.get("summary", ""))[:200],
            ))

        results.sort(key=lambda x: x.hybrid_score, reverse=True)
        return results[:top_k]


__all__ = ["SearchService", "SearchResult", "RRF_K"]
