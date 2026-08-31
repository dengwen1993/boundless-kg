"""Unit tests for SearchService — RRF fusion logic."""

import pytest
import numpy as np

from src.application.search_service import SearchService, SearchResult, RRF_K
from src.infrastructure.embedding.bm25 import BM25Index
from src.infrastructure.embedding.client import EmbeddingClient
from src.infrastructure.graph_store.client import GraphStoreClient


class MockEmbeddingClient:
    """Mock embedding client that returns deterministic vectors."""

    @property
    def is_available(self) -> bool:
        return True

    async def embed(self, texts):
        return np.array([[0.1, 0.2, 0.3] for _ in texts], dtype=np.float32)

    async def embed_one(self, text):
        return [0.1, 0.2, 0.3]


class TestRRFFusion:
    @pytest.fixture
    def service(self):
        return SearchService(
            graph_store=GraphStoreClient(),
            embedding_client=MockEmbeddingClient(),
            bm25_index=BM25Index(),
            bm25_weight=0.4,
            vector_weight=0.6,
        )

    def test_fuse_ranks_merges_results(self, service):
        bm25_results = [
            {"id": "a", "name": "A", "type": "concept"},
            {"id": "b", "name": "B", "type": "concept"},
            {"id": "c", "name": "C", "type": "concept"},
        ]
        vector_results = [
            {"id": "b", "name": "B", "type": "concept"},
            {"id": "d", "name": "D", "type": "concept"},
            {"id": "a", "name": "A", "type": "concept"},
        ]

        results = service._fuse_ranks(bm25_results, vector_results, top_k=10)
        ids = [r.id for r in results]

        # All unique IDs should be present
        assert "a" in ids
        assert "b" in ids
        assert "c" in ids
        assert "d" in ids

        # "b" appears in both rankings (rank 1 in BM25, rank 0 in vector)
        # so it should have a higher fused score than "c" (only in BM25)
        b_score = next(r.hybrid_score for r in results if r.id == "b")
        c_score = next(r.hybrid_score for r in results if r.id == "c")
        assert b_score > c_score

    def test_fuse_ranks_top_k_limit(self, service):
        bm25_results = [{"id": f"id_{i}", "name": f"N{i}", "type": "concept"} for i in range(20)]
        vector_results = [{"id": f"id_{i}", "name": f"N{i}", "type": "concept"} for i in range(20)]

        results = service._fuse_ranks(bm25_results, vector_results, top_k=5)
        assert len(results) == 5

    def test_fuse_ranks_empty_inputs(self, service):
        results = service._fuse_ranks([], [], top_k=10)
        assert results == []

    def test_fuse_ranks_bm25_only(self, service):
        bm25_results = [
            {"id": "a", "name": "A", "type": "concept"},
            {"id": "b", "name": "B", "type": "concept"},
        ]
        results = service._fuse_ranks(bm25_results, [], top_k=10)
        assert len(results) == 2
        # With only BM25, rank 1 should score higher than rank 2
        assert results[0].hybrid_score > results[1].hybrid_score
        assert results[0].id == "a"

    def test_fuse_ranks_vector_only(self, service):
        vector_results = [
            {"id": "a", "name": "A", "type": "concept"},
            {"id": "b", "name": "B", "type": "concept"},
        ]
        results = service._fuse_ranks([], vector_results, top_k=10)
        assert len(results) == 2
        assert results[0].hybrid_score > results[1].hybrid_score
        assert results[0].id == "a"


class TestSearchResultToDict:
    def test_to_dict_has_all_fields(self):
        r = SearchResult(
            id="test:1",
            name="Test",
            type="concept",
            domain="d",
            bm25_score=0.5,
            vector_score=0.3,
            hybrid_score=0.8,
            snippet="hello",
        )
        d = r.to_dict()
        assert d["id"] == "test:1"
        assert d["name"] == "Test"
        assert d["type"] == "concept"
        assert d["domain"] == "d"
        assert d["bm25_score"] == 0.5
        assert d["vector_score"] == 0.3
        assert d["hybrid_score"] == 0.8
        assert d["snippet"] == "hello"
        assert d["neighbors"] == []
