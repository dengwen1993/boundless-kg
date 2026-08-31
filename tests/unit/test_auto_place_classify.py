"""Layered auto-place classification — archive_metadata + keyword_matcher + auto_classify.

Covers the BUG-2026-08-26-001 fix: the classifier must never silently
fall back to a placeholder node like ``未命名资料`` when the LLM
returns unparseable output. The pipeline is deterministic-first and
explicitly signals ``needs_review`` / ``llm_failed`` / ``no_graph``
statuses instead of inventing garbage nodes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.domain.resource.archive_metadata import extract_archive_metadata
from src.domain.resource.auto_classify import (
    FORBIDDEN_NEW_NODE_NAMES,
    AutoClassifyDecision,
    auto_classify_async,
)
from src.domain.resource.keyword_matcher import (
    DEFAULT_MIN_CONFIDENCE,
    match_nodes,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _graph(node_names: list[str]) -> dict[str, Any]:
    """Tiny graph builder for tests."""
    return {
        "domain": "test",
        "nodes": [{"name": n, "links": []} for n in node_names],
    }


def _nested_graph(tree: dict[str, list[str]]) -> dict[str, Any]:
    """Build a graph from a parent->children map."""
    nodes = [{"name": parent, "links": kids} for parent, kids in tree.items()]
    # Children also need entries so the matcher can find them.
    seen = {n["name"] for n in nodes}
    for kids in tree.values():
        for kid in kids:
            if kid not in seen:
                nodes.append({"name": kid, "links": []})
                seen.add(kid)
    return {"domain": "test", "nodes": nodes}


# Realistic minimal LLM client for tests.
class _MockLLM:
    def __init__(self, reply: Any = '{"node": "ignored"}', raise_exc: bool = False):
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = 0

    async def chat(self, system: str, user: str, **kwargs: Any) -> str:
        self.calls += 1
        if self.raise_exc:
            raise RuntimeError("simulated LLM down")
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


# ----------------------------------------------------------------------
# archive_metadata
# ----------------------------------------------------------------------


class TestArchiveMetadata:
    def test_markdown_h1_title(self) -> None:
        parsed = {
            "text": "# hermes-agent-self-evolution 深度调研\n\nBody...",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("hermes-agent-self-evolution-深度调研.md", parsed)
        assert m.title == "hermes-agent-self-evolution 深度调研"
        assert "hermes" in m.topic_keywords
        assert "agent" in m.topic_keywords

    def test_metadata_hint_title_wins(self) -> None:
        parsed = {
            "text": "# Some heading\n\nbody",
            "format": "pdf",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "pdf", "title": "  Real PDF Title  "},
        }
        m = extract_archive_metadata("paper.pdf", parsed)
        assert m.title == "Real PDF Title"

    def test_unused_untitled_metadata_falls_through(self) -> None:
        parsed = {
            "text": "# Real Heading\n\nbody",
            "format": "pdf",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "pdf", "title": "Untitled"},
        }
        m = extract_archive_metadata("paper.pdf", parsed)
        assert m.title == "Real Heading"

    def test_filename_stem_fallback(self) -> None:
        parsed = {
            "text": "Body without headings",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("deep-research-notes.md", parsed)
        # Either the filename stem or the first plausible line — must be non-empty.
        assert m.title

    def test_filename_stem_strips_extension(self) -> None:
        parsed = {"text": "", "format": "md", "size": 0, "chars": 0, "hints": {}}
        m = extract_archive_metadata("foo.bar.baz.md", parsed)
        assert m.filename_stem == "foo.bar.baz"

    def test_summary_after_heading(self) -> None:
        parsed = {
            "text": "# Title\n\nFirst body paragraph. Second sentence here.",
            "format": "md",
            "size": 100,
            "chars": 60,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("foo.md", parsed)
        assert "First body paragraph" in m.summary

    def test_topic_keywords_include_filename_tokens(self) -> None:
        parsed = {
            "text": "Some unrelated prose about something else.",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("hermes-agent-self-evolution.md", parsed)
        assert "hermes" in m.topic_keywords
        assert "agent" in m.topic_keywords
        assert "self" in m.topic_keywords or "evolution" in m.topic_keywords

    def test_topic_keywords_cjk_bigrams(self) -> None:
        parsed = {
            "text": "# RAG 检索增强生成调研\n\nRAG 是当前主流的 LLM 增强方案...",
            "format": "md",
            "size": 100,
            "chars": 80,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("rag-survey.md", parsed)
        # Bigrams from "检索增强生成" should appear.
        assert "检索" in m.topic_keywords
        assert "增强" in m.topic_keywords
        assert "rag" in m.topic_keywords

    def test_stopwords_filtered(self) -> None:
        parsed = {
            "text": "the and for are but not you all can had her was one our out day",
            "format": "md",
            "size": 100,
            "chars": 60,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("foo.md", parsed)
        for sw in ("the", "and", "for", "are", "but"):
            assert sw not in m.topic_keywords

    def test_extra_hints_whitelist(self) -> None:
        parsed = {
            "text": "x",
            "format": "pdf",
            "size": 100,
            "chars": 1,
            "hints": {"kind": "pdf", "pages": 10, "author": "me", "garbage_field": 1},
        }
        m = extract_archive_metadata("x.pdf", parsed)
        assert m.extra_hints.get("pages") == 10
        assert m.extra_hints.get("author") == "me"
        assert "garbage_field" not in m.extra_hints


# ----------------------------------------------------------------------
# keyword_matcher
# ----------------------------------------------------------------------


class TestKeywordMatcher:
    def test_empty_graph_no_match(self) -> None:
        m = extract_archive_metadata(
            "hermes.md", {"text": "body", "format": "md", "size": 1, "chars": 5, "hints": {}}
        )
        r = match_nodes(m, None)
        assert r.best is None
        assert not r.has_high_confidence

    def test_clear_keyword_match(self) -> None:
        parsed = {
            "text": "# hermes agent framework\n\ndeep dive into the hermes agent",
            "format": "md",
            "size": 100,
            "chars": 60,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("hermes-agent-deep.md", parsed)
        r = match_nodes(m, _graph(["AI 基础", "Agent 智能体", "RAG 检索"]))
        assert r.best is not None
        assert r.best.node == "Agent 智能体"
        assert r.best.confidence >= DEFAULT_MIN_CONFIDENCE
        assert r.has_high_confidence

    def test_exact_title_bonus(self) -> None:
        parsed = {
            "text": "Agent 智能体\n\nbody",
            "format": "md",
            "size": 100,
            "chars": 30,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("notes.md", parsed)
        r = match_nodes(m, _graph(["Agent 智能体", "RAG"]))
        assert r.best is not None
        assert r.best.node == "Agent 智能体"

    def test_no_high_confidence_when_ambiguous(self) -> None:
        parsed = {
            "text": "# Foo\n\nbody with no clear topic match anywhere",
            "format": "md",
            "size": 100,
            "chars": 60,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("notes.md", parsed)
        r = match_nodes(m, _graph(["Alpha", "Beta", "Gamma"]))
        # 'foo' from filename matches nothing -> no high confidence.
        assert r.best is not None
        assert not r.has_high_confidence

    def test_leaf_beats_ancestor_at_same_score(self) -> None:
        parsed = {
            "text": "# Agent 子节点\n\nagent body about agents",
            "format": "md",
            "size": 100,
            "chars": 30,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("agent-survey.md", parsed)
        # Title exactly matches "Agent 子节点" so it gets the +5 bonus AND
        # is a leaf. Both signals point at the same node.
        g = _nested_graph({"Agent": ["Agent 子节点"]})
        r = match_nodes(m, g)
        assert r.best is not None
        assert r.best.node == "Agent 子节点"
        assert r.best.is_leaf

    def test_candidate_list_sorted_desc(self) -> None:
        parsed = {
            "text": "# hermes agent\n\nhermes agent stuff",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("hermes-agent.md", parsed)
        r = match_nodes(m, _graph(["Agent", "Beta", "Hermes Agent 子节点", "Gamma"]))
        assert r.candidates
        scores = [c.confidence for c in r.candidates]
        assert scores == sorted(scores, reverse=True)

    def test_min_confidence_threshold(self) -> None:
        parsed = {
            "text": "topic content about agents",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        m = extract_archive_metadata("agent-survey.md", parsed)
        # High threshold -> nothing qualifies.
        r = match_nodes(m, _graph(["Agent", "Other"]), min_confidence=0.99)
        assert not r.has_high_confidence


# ----------------------------------------------------------------------
# auto_classify_async — the BUG-2026-08-26-001 fix
# ----------------------------------------------------------------------


class TestAutoClassifyStatus:
    async def test_high_confidence_skips_llm(self) -> None:
        """When deterministic match is high, LLM is not consulted."""
        llm = _MockLLM(reply='{"node": "ShouldNeverRun"}')
        parsed = {
            "text": "# Agent 智能体\n\nbody about agents",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="agent.md",
            parsed=parsed,
            graph=_graph(["Agent 智能体", "RAG"]),
            llm_client=llm,
        )
        assert d.status == "matched"
        assert d.node == "Agent 智能体"
        assert d.confidence > 0
        assert llm.calls == 0  # critical: LLM never called

    async def test_llm_called_when_uncertain(self) -> None:
        llm = _MockLLM(reply='{"node": "Beta", "rationale": "fits"}')
        parsed = {
            "text": "# unclear topic\n\nambiguous content",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="notes.md",
            parsed=parsed,
            graph=_graph(["Alpha", "Beta", "Gamma"]),
            llm_client=llm,
        )
        assert d.status == "matched"
        assert d.node == "Beta"
        assert llm.calls == 1

    async def test_llm_returns_unparseable_garbage_no_placeholder_node(self) -> None:
        """BUG-2026-08-26-001 reproduction: LLM returns junk -> no '未命名资料'."""
        llm = _MockLLM(reply="Some prose without any JSON, thinking aloud...")
        parsed = {
            "text": "# Topic\n\nbody",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="notes.md",
            parsed=parsed,
            graph=_graph(["Alpha", "Beta", "Gamma"]),
            llm_client=llm,
        )
        # The fix means: status='llm_failed', node='' (NOT '未命名资料').
        assert d.status == "llm_failed"
        assert d.node == ""
        assert d.new_node_name not in FORBIDDEN_NEW_NODE_NAMES
        assert d.new_node_name != "未命名资料"
        assert "未命名资料" not in (d.rationale or "")
        # We still surface deterministic candidates for the UI.
        assert d.candidates

    async def test_llm_returns_forbidden_placeholder_blocked(self) -> None:
        """Even if LLM tries to name a node '未命名资料', we refuse."""
        llm = _MockLLM(
            reply=json.dumps(
                {"node": "__new_node__", "new_node_name": "未命名资料", "rationale": "x"}
            )
        )
        parsed = {
            "text": "# Topic\n\nbody",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="notes.md",
            parsed=parsed,
            graph=_graph(["Alpha", "Beta"]),
            llm_client=llm,
        )
        assert d.status == "llm_failed"
        assert d.node == ""
        assert d.new_node_name not in FORBIDDEN_NEW_NODE_NAMES

    async def test_llm_call_exception(self) -> None:
        llm = _MockLLM(raise_exc=True)
        parsed = {
            "text": "body",
            "format": "md",
            "size": 100,
            "chars": 5,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="notes.md",
            parsed=parsed,
            graph=_graph(["Alpha"]),
            llm_client=llm,
        )
        assert d.status == "llm_failed"
        assert "llm_call_error" in d.error

    async def test_no_graph_returns_no_graph_status(self) -> None:
        llm = _MockLLM(reply='{"node": "x"}')
        parsed = {
            "text": "body",
            "format": "md",
            "size": 100,
            "chars": 5,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="notes.md",
            parsed=parsed,
            graph={"nodes": []},
            llm_client=llm,
        )
        assert d.status == "no_graph"
        # LLM should NOT be called when graph is empty — no useful work.
        assert llm.calls == 0

    async def test_llm_creates_new_node_passes_through(self) -> None:
        """LLM may suggest a new node with a *sensible* name."""
        llm = _MockLLM(
            reply=json.dumps(
                {"node": "__new_node__", "new_node_name": "Hermes 高级应用", "rationale": "新主题"}
            )
        )
        parsed = {
            "text": "completely off-topic content about hermes framework",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="hermes.md",
            parsed=parsed,
            graph=_graph(["Alpha", "Beta"]),
            llm_client=llm,
        )
        assert d.status == "matched"
        assert d.is_new is True
        assert d.new_node_name == "Hermes 高级应用"

    async def test_llm_agrees_with_heuristic_top1(self) -> None:
        """LLM picks heuristic's #1 candidate → matched, LLM's pick wins."""
        # Title "agent survey" — heuristic best is "Agent 智能体" at ~0.30
        # (between LOW and HIGH). LLM picks the same node.
        llm = _MockLLM(reply='{"node": "Agent 智能体", "rationale": "fits"}')
        parsed = {
            "text": "# agent survey\n\nvarious notes about agents",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="agent-survey.md",
            parsed=parsed,
            graph=_graph(["Agent 智能体", "RAG 检索", "其他主题"]),
            llm_client=llm,
        )
        assert d.status == "matched"
        assert d.node == "Agent 智能体"
        assert llm.calls == 1

    async def test_llm_picks_inside_heuristic_top3_accepted(self) -> None:
        """LLM picks a node in heuristic top-3 (but not top-1) → matched."""
        llm = _MockLLM(reply='{"node": "RAG 检索", "rationale": "fits"}')
        parsed = {
            "text": "# agent survey\n\nsome agent and rag stuff",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="agent-rag-survey.md",
            parsed=parsed,
            graph=_graph(["Agent 智能体", "RAG 检索", "其他主题"]),
            llm_client=llm,
        )
        assert d.status == "matched"
        assert d.node == "RAG 检索"

    async def test_llm_picks_outside_heuristic_top3_needs_review(self) -> None:
        """Heuristic has candidates but LLM picks something completely off → needs_review."""
        llm = _MockLLM(reply='{"node": "ZetaNode", "rationale": "fits"}')
        # Use a graph with many nodes so ZetaNode (which has zero overlap)
        # can't sneak into heuristic top-3.
        nodes = [
            "Agent 智能体", "Agent 子节点",
            "RAG 检索", "RAG 检索增强生成",
            "Prompt 工程", "Prompt 高级技巧",
            "LLM API 开发", "大模型微调",
            "ZetaNode",  # the LLM pick — should be flagged as off-topic
        ]
        parsed = {
            "text": "# agent survey\n\ndeep dive into the agent framework",
            "format": "md",
            "size": 100,
            "chars": 60,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="agent-survey.md",
            parsed=parsed,
            graph=_graph(nodes),
            llm_client=llm,
        )
        # Heuristic best is "Agent 智能体" / "Agent 子节点" at ~0.30 — between
        # LOW and HIGH. LLM picks "ZetaNode" which has zero keyword
        # overlap. The adjudicator should downgrade to needs_review.
        assert d.status == "needs_review"
        assert d.node == ""
        names = [c.node for c in d.candidates]
        assert "Agent 智能体" in names or "Agent 子节点" in names
        assert "ZetaNode" in names  # LLM's pick appended for review

    async def test_no_heuristic_candidate_trusts_llm(self) -> None:
        """Heuristic has zero candidates → trust LLM even if outside top-3."""
        llm = _MockLLM(reply='{"node": "Alpha", "rationale": "fits"}')
        parsed = {
            "text": "# completely off-topic\n\nno overlap with any node",
            "format": "md",
            "size": 100,
            "chars": 50,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="mystery.md",
            parsed=parsed,
            graph=_graph(["Alpha", "Beta", "Gamma"]),
            llm_client=llm,
        )
        assert d.status == "matched"
        assert d.node == "Alpha"

    async def test_high_confidence_still_skips_llm(self) -> None:
        """Strong heuristic match (>HIGH_CONFIDENCE) never consults LLM."""
        llm = _MockLLM(reply='{"node": "ShouldNotRun"}')
        parsed = {
            "text": "# Agent 智能体\n\ndeep dive into the agent framework",
            "format": "md",
            "size": 100,
            "chars": 60,
            "hints": {"kind": "text"},
        }
        d = await auto_classify_async(
            filename="agent.md",
            parsed=parsed,
            graph=_graph(["Agent 智能体", "RAG"]),
            llm_client=llm,
        )
        assert d.status == "matched"
        assert d.node == "Agent 智能体"
        assert llm.calls == 0


# ----------------------------------------------------------------------
# AutoClassifyDecision.is_new — never True for forbidden names
# ----------------------------------------------------------------------


class TestIsNewNeverForbidden:
    def test_is_new_false_for_forbidden_name(self) -> None:
        d = AutoClassifyDecision(
            status="matched",
            node="__new_node__",
            new_node_name="未命名资料",
        )
        assert d.is_new is False

    def test_is_new_true_for_sensible_name(self) -> None:
        d = AutoClassifyDecision(
            status="matched",
            node="__new_node__",
            new_node_name="Hermes 高级应用",
        )
        assert d.is_new is True


# ----------------------------------------------------------------------
# FORBIDDEN_NEW_NODE_NAMES is exposed and stable
# ----------------------------------------------------------------------


def test_forbidden_names_include_legacy_placeholder() -> None:
    assert "未命名资料" in FORBIDDEN_NEW_NODE_NAMES
    assert "未分类资料" in FORBIDDEN_NEW_NODE_NAMES
    assert "未知资料" in FORBIDDEN_NEW_NODE_NAMES
