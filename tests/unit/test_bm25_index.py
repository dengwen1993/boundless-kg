"""Unit tests for BM25 index."""

import pytest

from src.infrastructure.embedding.bm25 import BM25Index, _tokenize


class TestTokenize:
    def test_english(self):
        tokens = _tokenize("ReAct reasoning loop")
        assert "react" in tokens
        assert "reasoning" in tokens
        assert "loop" in tokens

    def test_chinese(self):
        tokens = _tokenize("思维链推理")
        assert "思" in tokens
        assert "维" in tokens
        assert "链" in tokens

    def test_mixed(self):
        tokens = _tokenize("MCP工具调用")
        assert "mcp" in tokens
        assert "工" in tokens

    def test_empty(self):
        assert _tokenize("") == []

    def test_special_chars(self):
        tokens = _tokenize("hello! @world? #test")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens


class TestBM25Index:
    @pytest.fixture
    def index(self):
        return BM25Index()

    @pytest.fixture
    def docs(self):
        return [
            {"id": "concept:ReAct", "name": "ReAct推理", "type": "concept", "text": "ReAct 推理循环 Reasoning Acting"},
            {"id": "concept:CoT", "name": "思维链", "type": "concept", "text": "Chain of Thought 思维链推理"},
            {"id": "concept:MCP", "name": "MCP协议", "type": "concept", "text": "Model Context Protocol 工具调用"},
        ]

    def test_rebuild_and_search(self, index, docs):
        index.rebuild("test_domain", docs)
        results = index.search("test_domain", "ReAct推理", top_k=3)
        assert len(results) > 0
        assert results[0]["id"] == "concept:ReAct"

    def test_search_chinese(self, index, docs):
        index.rebuild("test_domain", docs)
        results = index.search("test_domain", "思维链", top_k=3)
        assert len(results) > 0
        assert results[0]["id"] == "concept:CoT"

    def test_search_empty_query(self, index, docs):
        index.rebuild("test_domain", docs)
        results = index.search("test_domain", "", top_k=3)
        assert results == []

    def test_search_no_match(self, index, docs):
        index.rebuild("test_domain", docs)
        results = index.search("test_domain", "量子计算", top_k=3)
        # BM25 may return 0 results for no match
        assert len(results) == 0

    def test_clear(self, index, docs):
        index.rebuild("test_domain", docs)
        assert index.is_indexed("test_domain")
        index.clear("test_domain")
        assert not index.is_indexed("test_domain")

    def test_clear_all(self, index, docs):
        index.rebuild("d1", docs)
        index.rebuild("d2", docs)
        index.clear_all()
        assert not index.is_indexed("d1")
        assert not index.is_indexed("d2")

    def test_empty_corpus(self, index):
        index.rebuild("empty", [])
        assert not index.is_indexed("empty")
        results = index.search("empty", "test")
        assert results == []
