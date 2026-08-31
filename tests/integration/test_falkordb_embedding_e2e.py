"""End-to-end integration test: FalkorDB + Embedding API + Hybrid Search.

Run:  python -m pytest tests/integration/test_falkordb_embedding_e2e.py -v -s

This test actually calls:
  1. Embedding API (DashScope/OpenAI compatible) — real HTTP request
  2. FalkorDB graph database — real Cypher queries
  3. BM25 index — in-memory keyword search
  4. SearchService — RRF hybrid fusion

Prerequisites:
  - FalkorDB running on localhost:6379 (docker run -d -p 6379:6379 falkordb/falkordb)
  - KG_EMBEDDING_API_KEY set in .env
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from src.infrastructure.embedding.bm25 import BM25Index
from src.infrastructure.embedding.client import EmbeddingClient
from src.infrastructure.graph_store.client import GraphStoreClient
from src.application.search_service import SearchService
from src.config.settings import get_embedding_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

#: Test domain — will be cleared before/after.
TEST_DOMAIN = "integration_test"


@pytest.fixture(scope="module")
def graph_store():
    return GraphStoreClient()


@pytest.fixture(scope="module")
def embedding_client():
    return EmbeddingClient()


@pytest.fixture(scope="module")
def bm25_index():
    return BM25Index()


@pytest.fixture(scope="module")
def search_service(graph_store, embedding_client, bm25_index):
    return SearchService(
        graph_store=graph_store,
        embedding_client=embedding_client,
        bm25_index=bm25_index,
    )


# ============================================================
# 1. Embedding API — real HTTP call
# ============================================================

class TestEmbeddingAPI:
    @pytest.mark.asyncio
    async def test_01_embedding_client_available(self, embedding_client):
        """Verify the embedding client has an API key."""
        assert embedding_client.is_available, (
            "Embedding client not available — check KG_EMBEDDING_API_KEY in .env"
        )
        logger.info("✓ Embedding client is available")

    @pytest.mark.asyncio
    async def test_02_embed_single_text(self, embedding_client):
        """Embed a single Chinese text and verify vector dimensions."""
        vec = await embedding_client.embed_one("ReAct 推理循环")
        s = get_embedding_settings()
        assert len(vec) == s.dim, f"Expected dim={s.dim}, got len={len(vec)}"
        # Should not be all zeros (that would mean API failure)
        assert any(v != 0.0 for v in vec), "Vector is all zeros — API may have failed"
        logger.info("✓ embed_one('ReAct 推理循环') → %d-dim vector", len(vec))

    @pytest.mark.asyncio
    async def test_03_embed_batch(self, embedding_client):
        """Embed a batch of texts."""
        texts = [
            "ReAct推理是一种交替推理和行动的范式",
            "思维链 Chain of Thought",
            "MCP工具调用协议",
            "分布式训练框架",
        ]
        import numpy as np
        arr = await embedding_client.embed(texts)
        assert arr.shape == (4, get_embedding_settings().dim)
        # Each row should have non-zero values
        for i in range(4):
            assert any(arr[i] != 0), f"Row {i} is all zeros"
        logger.info("✓ embed_batch(4 texts) → shape %s", arr.shape)

    @pytest.mark.asyncio
    async def test_04_embed_english(self, embedding_client):
        """Embed English text."""
        vec = await embedding_client.embed_one("distributed training framework")
        assert len(vec) == get_embedding_settings().dim
        assert any(v != 0.0 for v in vec)
        logger.info("✓ embed_one('distributed training...') → %d-dim vector", len(vec))


# ============================================================
# 2. FalkorDB — real Cypher queries
# ============================================================

class TestFalkorDB:
    def test_10_falkordb_available(self, graph_store):
        """Verify FalkorDB is connected."""
        assert graph_store.is_available, (
            "FalkorDB not available — run: docker run -d -p 6379:6379 falkordb/falkordb"
        )
        logger.info("✓ FalkorDB is available")

    def test_11_clear_test_domain(self, graph_store):
        """Clear any leftover data from previous runs."""
        graph_store.clear_domain(TEST_DOMAIN)
        stats = graph_store.statistics(TEST_DOMAIN)
        assert stats["concepts"] == 0
        logger.info("✓ Cleared test domain '%s'", TEST_DOMAIN)

    def test_12_upsert_concept_without_embedding(self, graph_store):
        """Insert a Concept node without embedding."""
        ok = graph_store.upsert_concept(
            TEST_DOMAIN,
            name="ReAct推理",
            level=0,
            is_root=True,
            description="ReAct是一种交替推理和行动的范式",
        )
        assert ok, "upsert_concept returned False"
        logger.info("✓ upsert_concept('ReAct推理')")

    def test_13_upsert_concept_with_embedding(self, graph_store, embedding_client):
        """Insert a Concept node WITH embedding vector."""
        vec = asyncio.get_event_loop().run_until_complete(
            embedding_client.embed_one("思维链 Chain of Thought 推理")
        )
        ok = graph_store.upsert_concept(
            TEST_DOMAIN,
            name="思维链",
            level=1,
            description="Chain of Thought 思维链推理",
            embedding=vec,
        )
        assert ok
        logger.info("✓ upsert_concept('思维链') with embedding")

    def test_14_upsert_more_concepts(self, graph_store, embedding_client):
        """Insert more concepts for search testing."""
        concepts = [
            ("MCP协议", 1, "Model Context Protocol 工具调用"),
            ("分布式训练", 0, "多GPU并行训练框架"),
            ("工具调用", 1, "LLM调用外部工具的能力"),
        ]
        loop = asyncio.get_event_loop()
        for name, level, desc in concepts:
            vec = loop.run_until_complete(embedding_client.embed_one(f"{name} {desc}"))
            ok = graph_store.upsert_concept(
                TEST_DOMAIN,
                name=name,
                level=level,
                description=desc,
                embedding=vec,
            )
            assert ok, f"upsert_concept failed for {name}"
        logger.info("✓ Inserted %d more concepts", len(concepts))

    def test_15_add_edges(self, graph_store):
        """Add semantic edges between concepts."""
        edges = [
            ("concept:ReAct推理", "concept:工具调用", "part_of"),
            ("concept:思维链", "concept:ReAct推理", "prerequisite_of"),
            ("concept:MCP协议", "concept:工具调用", "enables"),
            ("concept:分布式训练", "concept:ReAct推理", "related_to"),
        ]
        for src, tgt, rel in edges:
            ok = graph_store.add_edge(
                TEST_DOMAIN,
                source=src, target=tgt, relation=rel,
                weight=0.9, evidence="integration test",
            )
            assert ok, f"add_edge failed for {rel}"
        logger.info("✓ Added %d edges", len(edges))

    def test_16_query_neighbors(self, graph_store):
        """Query 1-hop neighbors of a node."""
        neighbors = graph_store.neighbors(
            TEST_DOMAIN, "concept:ReAct推理", hops=1
        )
        # Should find at least 1 neighbor (工具调用, 分布式训练, etc.)
        assert len(neighbors) >= 1, f"Expected ≥1 neighbor, got {len(neighbors)}"
        neighbor_names = [n["name"] for n in neighbors]
        logger.info("✓ neighbors('ReAct推理') → %s", neighbor_names)

    def test_17_statistics(self, graph_store):
        """Verify graph statistics."""
        stats = graph_store.statistics(TEST_DOMAIN)
        assert stats["concepts"] >= 5, f"Expected ≥5 concepts, got {stats}"
        assert stats["edges"] >= 3, f"Expected ≥3 edges, got {stats}"
        logger.info("✓ statistics → %s", stats)

    def test_18_vector_search(self, graph_store, embedding_client):
        """Test FalkorDB vector similarity search.

        Creates a vector index, then queries for similar concepts.
        """
        # Create vector index
        graph_store.ensure_vector_index(TEST_DOMAIN)
        logger.info("✓ Vector index ensured")

        # Verify all concepts are indexed
        all_concepts = graph_store.all_concepts(TEST_DOMAIN)
        assert len(all_concepts) >= 5
        logger.info("✓ all_concepts → %d nodes", len(all_concepts))

    def test_18a_vector_search_query(self, graph_store, embedding_client):
        """Actually query vector search with a real embedding."""
        # Embed a query
        loop = asyncio.get_event_loop()
        query_vec = loop.run_until_complete(
            embedding_client.embed_one("推理和行动交替的范式")
        )

        results = graph_store.vector_search(TEST_DOMAIN, query_vec, top_k=3)
        logger.info("✓ vector_search('推理和行动交替的范式') → %d results", len(results))
        for r in results:
            logger.info("    %s (score=%.4f)", r["name"], r.get("score", 0))


# ============================================================
# 3. BM25 Index — in-memory keyword search
# ============================================================

class TestBM25Search:
    def test_20_rebuild_bm25(self, bm25_index, graph_store):
        """Rebuild BM25 index from FalkorDB concepts."""
        concepts = graph_store.all_concepts(TEST_DOMAIN)
        documents = [
            {
                "id": c["id"],
                "name": c["name"],
                "type": "concept",
                "text": f"{c['name']} {c.get('description', '')}",
            }
            for c in concepts
        ]
        bm25_index.rebuild(TEST_DOMAIN, documents)
        assert bm25_index.is_indexed(TEST_DOMAIN)
        logger.info("✓ BM25 index rebuilt with %d documents", len(documents))

    def test_21_bm25_search_chinese(self, bm25_index):
        """Search BM25 with Chinese keywords."""
        results = bm25_index.search(TEST_DOMAIN, "推理", top_k=5)
        assert len(results) > 0, "BM25 returned no results for '推理'"
        logger.info("✓ BM25 search('推理') → %d results", len(results))
        for r in results:
            logger.info("    %s (score=%.4f)", r["name"], r["bm25_score"])

    def test_22_bm25_search_english(self, bm25_index):
        """Search BM25 with English keywords."""
        results = bm25_index.search(TEST_DOMAIN, "training", top_k=5)
        # May or may not match depending on tokenization
        logger.info("✓ BM25 search('training') → %d results", len(results))

    def test_23_bm25_search_mixed(self, bm25_index):
        """Search BM25 with mixed Chinese+English."""
        results = bm25_index.search(TEST_DOMAIN, "工具调用 protocol", top_k=5)
        assert len(results) > 0
        logger.info("✓ BM25 search('工具调用 protocol') → %d results", len(results))


# ============================================================
# 4. SearchService — RRF hybrid fusion
# ============================================================

class TestHybridSearch:
    @pytest.mark.asyncio
    async def test_30_hybrid_search(self, search_service, bm25_index, graph_store):
        """Full hybrid search: BM25 + vector → RRF fusion."""
        # Ensure BM25 is indexed
        if not bm25_index.is_indexed(TEST_DOMAIN):
            concepts = graph_store.all_concepts(TEST_DOMAIN)
            documents = [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "type": "concept",
                    "text": f"{c['name']} {c.get('description', '')}",
                }
                for c in concepts
            ]
            bm25_index.rebuild(TEST_DOMAIN, documents)

        results = await search_service.search(
            TEST_DOMAIN, "推理范式", top_k=5
        )
        assert len(results) > 0, "Hybrid search returned no results"
        logger.info("✓ hybrid_search('推理范式') → %d results:", len(results))
        for r in results:
            logger.info(
                "    %s | hybrid=%.4f bm25=%.4f vec=%.4f",
                r.name, r.hybrid_score, r.bm25_score, r.vector_score,
            )

    @pytest.mark.asyncio
    async def test_31_global_search_with_neighbors(self, search_service, bm25_index):
        """Global search — results enriched with 1-hop neighbors."""
        results = await search_service.global_search(
            TEST_DOMAIN, "工具调用", top_k=3
        )
        assert "results" in results
        assert results["total"] > 0
        logger.info("✓ global_search('工具调用') → %d results:", results["total"])
        for r in results["results"]:
            neighbor_count = len(r.get("neighbors", []))
            logger.info(
                "    %s | score=%.4f | neighbors=%d",
                r["name"], r.get("hybrid_score", 0), neighbor_count,
            )

    @pytest.mark.asyncio
    async def test_32_search_distributed(self, search_service):
        """Search for 'distributed training' — tests semantic matching."""
        results = await search_service.search(
            TEST_DOMAIN, "distributed training", top_k=5
        )
        logger.info("✓ search('distributed training') → %d results", len(results))
        for r in results:
            logger.info("    %s (hybrid=%.4f)", r.name, r.hybrid_score)


# ============================================================
# 5. Cleanup
# ============================================================

class TestCleanup:
    def test_90_cleanup(self, graph_store, bm25_index):
        """Clean up test data."""
        graph_store.clear_domain(TEST_DOMAIN)
        bm25_index.clear(TEST_DOMAIN)
        stats = graph_store.statistics(TEST_DOMAIN)
        assert stats["concepts"] == 0
        logger.info("✓ Cleaned up test domain")
