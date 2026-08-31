"""Unit tests for new settings — FalkorDB and Embedding configuration."""

import pytest

from src.config.settings import (
    FalkorDBSettings,
    EmbeddingSettings,
    get_falkordb_settings,
    get_embedding_settings,
    get_falkordb_enabled,
)


class TestFalkorDBSettings:
    def test_defaults(self):
        s = FalkorDBSettings()
        assert s.host == "localhost"
        assert s.port == 6379
        assert s.graph_prefix == "kg_"
        assert s.enabled is True
        assert s.password is None

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KG_FALKORDB_HOST", "falkordb.prod")
        monkeypatch.setenv("KG_FALKORDB_PORT", "16379")
        monkeypatch.setenv("KG_FALKORDB_ENABLED", "false")
        s = FalkorDBSettings()
        assert s.host == "falkordb.prod"
        assert s.port == 16379
        assert s.enabled is False


class TestEmbeddingSettings:
    def test_defaults(self):
        # Use _env_file=None to ignore .env file, testing pure code defaults
        s = EmbeddingSettings(_env_file=None)
        assert s.provider == "api"
        assert s.model == "text-embedding-v1"
        assert s.dim == 1024
        assert s.batch_size == 32
        assert s.bm25_weight == 0.4
        assert s.vector_weight == 0.6
        assert s.default_top_k == 10

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KG_EMBEDDING_MODEL", "custom-embed")
        monkeypatch.setenv("KG_EMBEDDING_DIM", "768")
        monkeypatch.setenv("KG_SEARCH_BM25_WEIGHT", "0.3")
        s = EmbeddingSettings(_env_file=None)
        assert s.model == "custom-embed"
        assert s.dim == 768
        assert s.bm25_weight == 0.3


class TestGlobalAccessors:
    def test_get_falkordb_settings(self):
        s = get_falkordb_settings()
        assert isinstance(s, FalkorDBSettings)

    def test_get_embedding_settings(self):
        s = get_embedding_settings()
        assert isinstance(s, EmbeddingSettings)

    def test_get_falkordb_enabled(self):
        # Should return a bool
        assert isinstance(get_falkordb_enabled(), bool)
