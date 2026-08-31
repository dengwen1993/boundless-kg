"""Layered auto-classification for parsed uploads.

Pipeline (each layer only runs if the previous one returned nothing
usable):

1. **Deterministic keyword match** — :func:`src.domain.resource.keyword_matcher`
   scores every existing node against the parsed file's archive
   metadata. When the top match clears ``min_confidence`` we return
   that node directly — no LLM call.
2. **LLM classifier** — only consulted when the deterministic pass
   returns ``has_high_confidence=False`` (or the graph is empty). The
   LLM receives the archive metadata + node tree and picks (or names)
   a node.
3. **Safe failure** — when even the LLM can't return parseable JSON,
   we return a decision with ``node=""`` and a non-empty ``candidates``
   list (the deterministic top-K) and ``status="needs_review"``. The
   caller is responsible for surfacing this to the user; we **never**
   create a placeholder node like ``未命名资料``.

The new decision shape:

* ``status`` — one of ``"matched"`` / ``"needs_review"`` / ``"llm_failed"`` /
  ``"no_graph"``. Callers can switch on this instead of inferring
  intent from string contents.
* ``node`` — the chosen existing node name, or empty string.
* ``new_node_name`` — the proposed new-node name, or empty string.
* ``confidence`` — deterministic confidence in ``[0,1]`` when matched.
* ``candidates`` — top-K deterministic candidates (for UI / review).
* ``rationale`` — short human-readable reason for the decision.
* ``error`` — non-empty iff ``status in {"llm_failed", "no_graph"}``.

The old decision kept ``node="__new_node__"`` + ``new_node_name="未命名资料"``
as a hard fallback — that's what produced the garbage-node pollution
fixed by `BUG-2026-08-26-001`. This rewrite removes that fallback and
makes the failure mode explicit and caller-actionable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.domain.protocols import LLMClientProtocol
from src.domain.resource.archive_metadata import ArchiveMetadata, extract_archive_metadata
from src.domain.resource.keyword_matcher import (
    DEFAULT_MIN_CONFIDENCE,
    HIGH_CONFIDENCE,
    LOW_CONFIDENCE,
    MatchResult,
    NodeMatch,
    match_nodes,
    top_node_names,
)


SYSTEM_PROMPT = """你是知识库资料归类助手。
给定一份已抽取好的资料元数据（标题/摘要/主题词）+ 当前领域的节点层次结构（按"根 → 子 → 叶"列出），
请判断这份资料最适合放在哪个**已有节点**下。

输出 JSON（只返回 JSON，不要任何解释）：
{
  "node": "<已有节点名；如果没有合适的就填 \"__new_node__\">",
  "new_node_name": "<建议的新节点名；node 为 __new_node__ 时必填，其他情况填空串>",
  "rationale": "<一句话中文理由，≤60字>"
}

判定规则：
- 优先选择**叶子节点**或**最具体的已有节点**，不要选大而泛的根节点，除非文件本身就很宽泛。
- 如果资料内容明显属于多个子主题，挑选其中**占比最大或最核心**的那个节点。
- 只有当现有节点列表里没有任何一个能与资料内容产生合理关联时，才返回 __new_node__，并给出一个能体现资料主题的新节点名。
- 节点名必须**完全匹配**列表里出现的字符串，不要翻译、不要改写、不要省略前后缀。"""


#: Names the LLM should NEVER return — they're pure garbage fallbacks
#: that pollute the node tree. The string list is exposed so tests can
#: confirm it's enforced in the new-node branch too.
FORBIDDEN_NEW_NODE_NAMES: frozenset[str] = frozenset({
    "未命名资料",
    "未分类资料",
    "未知资料",
    "unknown",
    "untitled",
    "new_node",
    "新节点",
})


# ----------------------------------------------------------------------
# Decision
# ----------------------------------------------------------------------


@dataclass(slots=True)
class AutoClassifyDecision:
    """Outcome of :func:`auto_classify_async`.

    ``status`` is the single source of truth for what happened. The
    legacy fields ``node`` / ``new_node_name`` / ``rationale`` are kept
    for backwards compatibility with callers that already switch on
    them, but new code should prefer ``status``.
    """

    status: str = "needs_review"  # one of: matched, needs_review, llm_failed, no_graph
    node: str = ""
    new_node_name: str = ""
    rationale: str = ""
    confidence: float = 0.0
    error: str = ""
    candidates: list[NodeMatch] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_new(self) -> bool:
        """True iff the caller should create a brand-new node."""
        # Only the matched-LLM path may suggest a new node, and only when
        # the name is plausible (not in FORBIDDEN_NEW_NODE_NAMES).
        return (
            self.status == "matched"
            and self.node == "__new_node__"
            and bool(self.new_node_name)
            and self.new_node_name not in FORBIDDEN_NEW_NODE_NAMES
        )


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


async def auto_classify_async(
    *,
    filename: str,
    parsed: dict[str, Any],
    graph: dict[str, Any] | None,
    llm_client: LLMClientProtocol,
    extra_hints: dict[str, Any] | None = None,
    high_confidence: float = HIGH_CONFIDENCE,
    low_confidence: float = LOW_CONFIDENCE,
    adjudicator_window: int = 3,
    temperature: float = 0.2,
    max_tokens: int = 400,
) -> AutoClassifyDecision:
    """Classify *parsed* against *graph* — heuristic primary, LLM adjudicator.

    Two-tier decision (each layer only runs when the previous one
    returned nothing usable):

    1. **Heuristic strong match** (``best.confidence >= high_confidence``)
       — skip the LLM entirely; deterministic keyword matching is
       already trustworthy enough.  This is the fast path.
    2. **Heuristic weak hint** (``low_confidence <= best < high_confidence``)
       — call the LLM.  If the LLM's pick is in the heuristic's
       top-``adjudicator_window`` candidates (i.e. they broadly agree),
       accept the LLM's pick as ``matched``.  If the LLM picks a node
       far outside the heuristic's top-k (LLM hallucinated or saw
       something the heuristic missed), downgrade to ``needs_review``
       with both lists surfaced for the user.
    3. **No heuristic hint** (``best < low_confidence`` or no candidates)
       — call the LLM; if it returns a node from the graph, accept.
       This is the catch-all path.
    4. **Empty graph** — short-circuit to ``no_graph`` without calling
       the LLM (no useful work to do).

    The LLM is fed **structured metadata** (title / summary / topic
    keywords) rather than the raw document text — see
    :mod:`src.domain.resource.archive_metadata`.  This keeps the
    prompt short and focused, dramatically reducing the failure rate
    that caused `BUG-2026-08-26-001`.

    Args:
        filename: the on-disk file name.
        parsed: the dict returned by
            :func:`src.application.tmp_parser.parse_file_to_text`.
        graph: the ``knowledge_graph.json`` dict for the target domain.
        llm_client: anything implementing :class:`LLMClientProtocol`.
        extra_hints: optional extra metadata (rarely needed).
        high_confidence: heuristic-alone threshold (skip LLM when met).
        low_confidence: heuristic hint threshold (consult LLM when met).
        adjudicator_window: how many top heuristic candidates must
            contain the LLM's pick for the decision to be ``matched``.
    """
    metadata = extract_archive_metadata(filename, parsed)

    # Always compute the heuristic ranking — even when we'll defer to
    # the LLM, the candidates are surfaced in the response so the UI
    # can offer them as alternatives.
    match_result = match_nodes(metadata, graph, min_confidence=0.0)
    best = match_result.best

    # --- Empty graph short-circuit ----------------------------------
    nodes = (graph or {}).get("nodes") if isinstance(graph, dict) else None
    if not nodes:
        return AutoClassifyDecision(
            status="no_graph",
            rationale="领域下还没有任何节点 — 必须先建节点再归档。",
            candidates=match_result.candidates,
        )

    # --- Layer 1: heuristic strong match (skip LLM) -----------------
    if best is not None and best.confidence >= high_confidence:
        return AutoClassifyDecision(
            status="matched",
            node=best.node,
            confidence=best.confidence,
            rationale=(
                f"启发式匹配命中（{best.confidence:.0%}）："
                f"匹配 token {best.matched_tokens[:6]}"
            ),
            candidates=match_result.candidates[:5],
        )

    # --- Layer 2 & 3: consult LLM (always below HIGH_CONFIDENCE) ----
    user_msg = _build_user_msg(metadata, graph, extra_hints)
    try:
        raw_text = await llm_client.chat(
            SYSTEM_PROMPT,
            user_msg,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
    except Exception as exc:
        return AutoClassifyDecision(
            status="llm_failed",
            error=f"llm_call_error: {type(exc).__name__}: {exc}",
            rationale="LLM 归类器调用失败；调用方应让用户手动选择节点。",
            candidates=match_result.candidates,
        )

    decision = _parse_llm_response(str(raw_text or ""))
    decision.candidates = match_result.candidates[:5]

    # LLM parse failure / forbidden name → llm_failed; never trust.
    if decision.status == "llm_failed":
        return decision

    # If LLM wants to create a *new* node, accept it — but only if the
    # new name is plausible (already enforced in is_new).
    if decision.is_new:
        return decision

    # LLM picked an existing node. Adjudicate against heuristic:
    heuristic_topk = top_node_names(match_result, k=adjudicator_window)
    if decision.node in heuristic_topk:
        # Heuristic and LLM broadly agree — accept LLM's pick.
        return decision

    # LLM picked a node outside the heuristic's top-k. Two interpretations:
    #   (a) LLM saw semantic signal heuristic missed (LLM is right).
    #   (b) LLM hallucinated (LLM is wrong).
    # We can't tell which without a human. Surface both for review.
    if best is None or best.confidence < low_confidence:
        # Heuristic has *no* candidate at all → trust the LLM (it
        # probably caught something heuristic can't).
        return decision

    return AutoClassifyDecision(
        status="needs_review",
        node="",
        rationale=(
            f"启发式（{best.confidence:.0%}：{best.node}）与 LLM（{decision.node}）"
            f"结论不一致 — 请手动选择。"
        ),
        confidence=0.0,
        candidates=match_result.candidates[:5]
        + [
            # Surface the LLM's pick as an extra candidate so the UI can
            # show "LLM 建议 X" alongside the heuristic ranking.
            type(best)(
                node=decision.node,
                score=0.0,
                confidence=0.0,
                matched_tokens=[],
                is_leaf=False,
            )
        ],
        raw=decision.raw,
    )


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def build_node_tree(graph: dict[str, Any] | None) -> str:
    """Render a ``knowledge_graph.json`` dict as a tree string.

    Kept exported (was used by callers) — same format as before.
    """
    if not graph or not isinstance(graph, dict):
        return "（空 — 领域下还没有任何节点）"

    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return "（空 — 领域下还没有任何节点）"

    children_of: dict[str, list[str]] = {}
    roots: list[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        name = str(n.get("name", "")).strip()
        if not name:
            continue
        links = n.get("links") or []
        if not isinstance(links, list):
            links = []
        children_of[name] = [str(x).strip() for x in links if str(x).strip()]
        if name not in children_of:
            roots.append(name)

    mentioned = {child for kids in children_of.values() for child in kids}
    roots = [n for n in roots if n not in mentioned]
    if not roots and nodes:
        first = nodes[0]
        if isinstance(first, dict):
            first_name = str(first.get("name", "")).strip()
            if first_name:
                roots = [first_name]

    lines: list[str] = []
    visited: set[str] = set()

    def _walk(name: str, prefix: str, is_last: bool) -> None:
        if name in visited:
            return
        visited.add(name)
        branch = "└─ " if is_last else "├─ "
        lines.append(f"{prefix}{branch}{name}")
        kids = children_of.get(name, [])
        new_prefix = prefix + ("   " if is_last else "│  ")
        for i, kid in enumerate(kids):
            _walk(kid, new_prefix, i == len(kids) - 1)

    for i, root in enumerate(roots):
        _walk(root, "", i == len(roots) - 1)

    return "\n".join(lines) if lines else "（空）"


def _build_user_msg(
    metadata: ArchiveMetadata,
    graph: dict[str, Any] | None,
    extra_hints: dict[str, Any] | None,
) -> str:
    tree_text = build_node_tree(graph)
    hints = dict(extra_hints or {})
    hints.setdefault("format", metadata.format)
    hints.setdefault("size", metadata.size)
    hints.setdefault("title", metadata.title)
    hint_lines: list[str] = []
    for k in ("format", "size", "pages", "slides", "sheets", "author", "title"):
        v = hints.get(k)
        if v not in (None, "", 0):
            hint_lines.append(f"  - {k}: {v}")
    hint_block = ("\n文件元数据：\n" + "\n".join(hint_lines)) if hint_lines else ""
    summary = metadata.summary or "（无）"
    keywords = metadata.topic_keywords[:25]
    kw_block = (
        "\n主题词（前 25 个）：" + ", ".join(keywords) if keywords else ""
    )
    return (
        f"文件名：{metadata.filename}\n"
        f"{hint_block}\n"
        f"标题：{metadata.title}\n"
        f"摘要：{summary}\n"
        f"{kw_block}\n"
        f"\n当前领域的节点层次结构：\n{tree_text}\n"
        "\n请根据这些信息判断这份资料应该放在哪个节点下。"
    )


def _parse_llm_response(raw_text: str) -> AutoClassifyDecision:
    """Parse the LLM response, surfacing parse failures explicitly.

    We **never** silently fall back to a "未命名资料" node — that was the
    BUG-2026-08-26-001 root cause. When the model output can't be made
    sense of, we return ``status="llm_failed"`` with the raw text in
    ``error`` so the caller (or its UI) can ask the user to pick.
    """
    from src.utils.json_repair import try_parse_json

    data = try_parse_json(raw_text)
    if not isinstance(data, dict):
        return AutoClassifyDecision(
            status="llm_failed",
            error=f"llm_parse_failed: could not parse JSON from response (first 200 chars: {raw_text[:200]!r})",
            rationale="LLM 返回无法解析 — 触发方应让用户手动选择节点。",
            raw={"raw_text": raw_text[:2000]},
        )

    node = str(data.get("node", "") or "").strip()
    new_name = str(data.get("new_node_name", "") or "").strip()
    rationale = str(data.get("rationale", "") or "").strip()

    # Guard: refuse to honour a forbidden "new_node" name — instead
    # treat it as parse failure so the caller can review.
    if node == "__new_node__" and new_name in FORBIDDEN_NEW_NODE_NAMES:
        return AutoClassifyDecision(
            status="llm_failed",
            error=f"llm_returned_forbidden_new_node_name: {new_name!r}",
            rationale="LLM 试图建占位节点 — 触发方应让用户手动选择或命名。",
            raw=data,
        )

    return AutoClassifyDecision(
        status="matched",
        node=node,
        new_node_name=new_name,
        rationale=rationale,
        raw=data,
    )


# ----------------------------------------------------------------------
# Back-compat shim — kept so callers that imported these still work.
# ----------------------------------------------------------------------


def _dump_for_debug(dec: AutoClassifyDecision) -> str:
    """Render the decision as JSON — handy for log lines."""
    return json.dumps(
        {
            "status": dec.status,
            "node": dec.node,
            "new_node_name": dec.new_node_name,
            "rationale": dec.rationale,
            "confidence": dec.confidence,
            "error": dec.error,
            "is_new": dec.is_new,
        },
        ensure_ascii=False,
    )


__all__ = [
    "AutoClassifyDecision",
    "FORBIDDEN_NEW_NODE_NAMES",
    "auto_classify_async",
    "build_node_tree",
]
