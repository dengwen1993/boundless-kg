"""Unit tests for GraphStoreClient — graceful degradation when FalkorDB unavailable."""

import pytest

from src.infrastructure.graph_store.client import GraphStoreClient, SEMANTIC_RELATIONS


class TestGraphStoreClient:
    @pytest.fixture
    def client(self):
        return GraphStoreClient()

    def test_semantic_relations_contains_key_types(self):
        assert "part_of" in SEMANTIC_RELATIONS
        assert "prerequisite_of" in SEMANTIC_RELATIONS
        assert "references" in SEMANTIC_RELATIONS
        # Structural-derivable edges should NOT be here
        assert "has_note" not in SEMANTIC_RELATIONS
        assert "has_resource" not in SEMANTIC_RELATIONS

    def test_graph_name_safety(self, client):
        # Spaces and slashes should be replaced
        name = client._graph_name("AI 架构师/深入学习")
        assert " " not in name
        assert "/" not in name
        assert name.startswith("kg_")

    def test_upsert_concept_returns_false_when_unavailable(self, client):
        # When FalkorDB is not connected, operations should return False
        # rather than raising — graceful degradation.
        # If FalkorDB IS running, this test is skipped (can't simulate unavailability).
        if client.is_available:
            pytest.skip("FalkorDB is running — cannot test unavailable path")
        client._connection = None  # Force no connection
        client._settings.enabled = True
        result = client.upsert_concept(
            "test_domain",
            name="test_node",
            level=0,
        )
        assert result is False

    def test_neighbors_returns_empty_when_unavailable(self, client):
        client._connection = None
        result = client.neighbors("test_domain", "concept:test", hops=1)
        assert result == []

    def test_statistics_returns_zeros_when_unavailable(self, client):
        if client.is_available:
            pytest.skip("FalkorDB is running — cannot test unavailable path")
        client._connection = None
        stats = client.statistics("test_domain")
        assert stats == {"concepts": 0, "notes": 0, "resources": 0, "edges": 0}

    def test_add_edge_skips_non_semantic(self, client):
        # HAS_NOTE / HAS_RESOURCE should be silently skipped
        # (return True — no error, just no-op)
        result = client.add_edge(
            "test_domain",
            source="concept:A",
            target="note:B",
            relation="has_note",
        )
        assert result is True
