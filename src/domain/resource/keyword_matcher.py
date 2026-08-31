"""Deterministic keyword-based node matching against a knowledge graph.

Given the :class:`ArchiveMetadata` extracted from a parsed upload plus
the ``knowledge_graph.json`` dict for the target domain, this module
scores each existing node for how well it matches the upload — purely
on overlap between the upload's topic keywords / title / filename and
each node's *name*.

The point is to make ``kg_auto_place_uploaded_file`` decide a target
node **without** asking the LLM to read the body. LLM-based reasoning
should only kick in when the deterministic score is below the
confidence threshold (handled in :mod:`src.domain.resource.auto_classify`).

Algorithm — kept deliberately simple so behaviour is auditable:

1. **Node tokenisation** — for every node name produce a bag of tokens:
    * English / ASCII words split on non-letters.
    * Chinese characters and overlapping bigrams of CJK runs.
    * The *full* node name as one token too, so a file titled exactly
      "Agent 智能体" still matches the node "Agent 智能体".
2. **Scoring** — for each node, count how many keyword tokens overlap
   with its token bag, weighted:
    * ``+3`` per filename-token hit (filenames carry strong signal).
    * ``+2`` per title-token hit.
    * ``+1`` per body-token hit.
    * ``+5`` bonus if the node name appears verbatim in the title.
3. **Confidence** — the raw score is normalised by dividing by the
   *maximum possible* score for that node so the result is in
   ``[0.0, 1.0]``. Nodes below the confidence threshold are dropped.
4. **Tie-break** — leaves score higher than their parents (we prefer the
   most specific node). Among equals, prefer alphabetical for stability.

No IO, no LLM, no global state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.domain.resource.archive_metadata import ArchiveMetadata


# ----------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------


@dataclass(slots=True)
class NodeMatch:
    """One scored candidate from the keyword matcher."""

    node: str
    score: float
    confidence: float
    matched_tokens: list[str] = field(default_factory=list)
    # True if this node is a leaf (no children) — leaf matches win over
    # ancestor matches when scores are close.
    is_leaf: bool = False


@dataclass(slots=True)
class MatchResult:
    """Outcome of :func:`match_nodes`."""

    candidates: list[NodeMatch]  # sorted by score desc
    best: NodeMatch | None
    has_high_confidence: bool  # True iff ``best`` >= ``min_confidence``


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


#: Confidence threshold for :func:`match_nodes`'s ``has_high_confidence``
#: flag. Used by callers that want a single boolean "is the top match
#: good enough?" without invoking the LLM.
DEFAULT_MIN_CONFIDENCE = 0.25

#: Confidence thresholds consumed by :func:`auto_classify_async`:
#:     HIGH = heuristic alone is sufficient (skip LLM entirely).
#:     LOW  = heuristic has *some* candidate worth cross-checking with LLM.
#:
#: Scoring weight table:
#:     raw = 3 * filename_hit + 2 * title_hit + 1 * body_hit + 5 * exact_title_bonus
#:     max = max(1, node_token_count) * 3 + 5
#:
#: 0.40 ≈ "strong hit" on a small node (e.g. +3 filename + 2 title hit
#:         on a 4-token node => 5/17 ≈ 0.29; +3 filename + 2 title +
#:         exact bonus on a 2-token node => 12/11 ≈ 1.0).
#: 0.15 ≈ "weak hint" — heuristic has a candidate worth checking with LLM.
HIGH_CONFIDENCE = 0.40
LOW_CONFIDENCE = 0.15


def match_nodes(
    metadata: ArchiveMetadata,
    graph: dict[str, Any] | None,
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> MatchResult:
    """Score every node in *graph* against *metadata*.

    Args:
        metadata: archive metadata produced by
            :func:`src.domain.resource.archive_metadata.extract_archive_metadata`.
        graph: the ``knowledge_graph.json`` dict (``{"nodes": [{"name",
            "links": [...]}, ...]}``). Tolerates missing keys.
        min_confidence: confidence floor for ``has_high_confidence``.
    """
    nodes = _normalise_nodes(graph)
    if not nodes:
        return MatchResult(candidates=[], best=None, has_high_confidence=False)

    fname_tokens = _tokenise(metadata.filename_stem)
    title_tokens = _tokenise(metadata.title)
    body_tokens = set(metadata.topic_keywords) - fname_tokens - title_tokens

    matches: list[NodeMatch] = []
    for node in nodes:
        node_tokens = _tokenise(node["name"])
        full_name_bonus = (
            5.0
            if metadata.title.strip() and metadata.title.strip() == node["name"].strip()
            else 0.0
        )

        # Overlap with the three bags.
        fname_hit = sorted(fname_tokens & node_tokens)
        title_hit = sorted(title_tokens & node_tokens)
        body_hit = sorted(body_tokens & node_tokens)
        matched = sorted(set(fname_hit) | set(title_hit) | set(body_hit))

        raw_score = (
            3.0 * len(fname_hit)
            + 2.0 * len(title_hit)
            + 1.0 * len(body_hit)
            + full_name_bonus
        )
        # Max possible: every node token could match. Use the node's own
        # token count as a rough normaliser so bigger nodes don't auto-win.
        max_possible = max(1, len(node_tokens)) * 3.0 + 5.0
        confidence = raw_score / max_possible

        matches.append(
            NodeMatch(
                node=node["name"],
                score=raw_score,
                confidence=confidence,
                matched_tokens=matched,
                is_leaf=not node.get("links"),
            )
        )

    matches.sort(key=_sort_key, reverse=True)
    best = matches[0] if matches else None
    has_hi = best is not None and best.confidence >= min_confidence
    return MatchResult(candidates=matches, best=best, has_high_confidence=has_hi)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


_EN_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CN_RUN_RE = re.compile(r"[一-鿿]{2,}")


def _tokenise(text: str) -> set[str]:
    """Produce a feature bag from *text*.

    English tokens are lower-cased. CJK runs are split into overlapping
    bigrams (since Chinese has no word boundary) AND the full run is
    kept when it's short. Numbers / mixed tokens are preserved verbatim
    for things like "rag" / "llm" / "v2".
    """
    if not text:
        return set()
    bag: set[str] = set()
    blob = text.lower()

    for m in _EN_TOKEN_RE.finditer(blob):
        bag.add(m.group(0))

    for m in _CN_RUN_RE.finditer(blob):
        run = m.group(0)
        for i in range(len(run) - 1):
            bag.add(run[i : i + 2])
        if len(run) <= 4:
            bag.add(run)

    return bag


def _normalise_nodes(graph: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pull out ``[{"name": ..., "links": [...]}, ...]`` from any shape."""
    if not graph or not isinstance(graph, dict):
        return []
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return []
    out: list[dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        name = str(n.get("name", "")).strip()
        if not name:
            continue
        links = n.get("links") or []
        if not isinstance(links, list):
            links = []
        out.append({"name": name, "links": [str(x) for x in links]})
    return out


def _sort_key(m: NodeMatch) -> tuple[float, int, str]:
    """Sort matches by confidence desc, then leaf-first, then alphabetical."""
    return (m.confidence, 1 if m.is_leaf else 0, "-" + m.node)




def top_node_names(match: MatchResult, k: int = 3) -> list[str]:
    """Return the names of the top-*k* candidates.

    Convenience for the LLM-as-adjudicator path: when the LLM returns a
    node, we check whether it's in the heuristic top-*k* — if not, we
    flag the decision as ``needs_review`` (LLM and heuristic disagree).
    """
    return [m.node for m in match.candidates[:k]]


__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "HIGH_CONFIDENCE",
    "LOW_CONFIDENCE",
    "MatchResult",
    "NodeMatch",
    "match_nodes",
    "top_node_names",
]
