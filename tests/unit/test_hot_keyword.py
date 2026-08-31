"""Hot-keyword layer — query builder + dedup."""

from __future__ import annotations

import pytest

from src.domain.hot_keyword import build_queries, dedup_strings, jaccard
from src.domain.hot_keyword.query_builder import dedup as dedup_queries
from src.domain.intent.models import IntentMeta


def test_dedup_strings_removes_near_duplicates() -> None:
    items = ["abc def", "abc def ghi", "completely different"]
    out = dedup_strings(items, threshold=0.5)
    # "abc def" and "abc def ghi" share tokens; second one dropped.
    assert "abc def" in out
    assert "completely different" in out
    assert len(out) == 2


def test_jaccard_identical_is_one() -> None:
    assert jaccard("hello world", "hello world") == 1.0


def test_jaccard_disjoint_is_zero() -> None:
    assert jaccard("alpha", "beta") == 0.0


def test_jaccard_both_empty_is_one() -> None:
    assert jaccard("", "") == 1.0


def test_dedup_casefold_and_whitespace() -> None:
    out = dedup_queries(["Hello", "  hello  ", "HELLO"])
    assert out == ["Hello"]


def test_build_queries_topic_only() -> None:
    meta = IntentMeta(topic="RAG")
    qs = build_queries(meta)
    assert "RAG" in qs
    assert "RAG 入门" in qs


def test_build_queries_empty_topic_returns_empty() -> None:
    meta = IntentMeta(topic="")
    assert build_queries(meta) == []


def test_build_queries_respects_max_queries() -> None:
    meta = IntentMeta(topic="RAG")
    qs = build_queries(meta, max_queries=10)
    assert len(qs) <= 10


def test_build_queries_no_internal_duplicates() -> None:
    meta = IntentMeta(topic="RAG")
    qs = build_queries(meta, max_queries=50)
    assert len(qs) == len(set(q.lower() for q in qs))