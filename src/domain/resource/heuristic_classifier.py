"""Deterministic, dependency-free classifier for parsed tmp uploads.

This module exists to fix :data:`workspace.bugs.md` ``BUG-2026-08-26-001`` —
the old path shoved the whole preview + node tree at an LLM and asked for a
JSON ``{node, new_node_name, rationale}`` back. When the model produced an
unparseable reply (think markdown fence / thinking block / truncation),
:func:`src.domain.resource.auto_classify._parse` fell back to a literal
``"未命名资料"`` placeholder, which then got created as a real node.

Design rules:

1. **Never use LLM for the primary decision.** Token overlap on filename +
   preview text vs. existing node names is enough for the common case.
2. **Never invent a placeholder node name.** When nothing in the graph
   matches, return ``is_new=True`` with a name derived from the filename
   stem (or a category-default like ``其他资料`` when the stem is empty).
3. **Caller is the decider.** This module only *classifies* — it does not
   create nodes, write files, or fire timeline events. The service layer
   is responsible for the side effects.

Tokens are extracted with stdlib regex only (no jieba / no spaCy). CJK
characters contribute both single-char and sliding bigram tokens; ASCII
runs are kept verbatim. This is crude but cheap, deterministic, and matches
the user's directive that we should call the parser, convert to metadata,
then find the right node — not throw raw bytes at an LLM and pray.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

#: Stopwords that should never contribute to a score. Cheap list — just the
#: ones we see constantly in CJK + English mixed filenames and previews.
_STOPWORDS: frozenset[str] = frozenset({
    # English
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "at",
    "is", "are", "be", "this", "that", "with", "as", "by", "from", "it",
    "we", "you", "i", "they", "he", "she", "but", "not", "if", "no", "so",
    # Common Chinese function words + chars that show up in every doc
    "的", "了", "在", "是", "我", "你", "他", "她", "它", "们", "和", "与",
    "或", "及", "为", "为", "了", "把", "被", "从", "到", "给", "让", "使",
    "这", "那", "此", "其", "以", "上", "下", "中", "内", "外", "前", "后",
    "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    # Markdown + format noise that leaked from preview text
    "http", "https", "www", "com", "md", "pdf", "docx", "pptx", "xlsx",
    "txt", "html", "json", "yaml", "xml", "csv",
})

#: Min token-overlap score to accept a node match. 1 keeps it conservative
#: — if no node shares any non-stopword token with the file, we'd rather
#: create a new node than pick a random collision.
_MIN_ACCEPT_SCORE: int = 1

#: ASCII word pattern: starts with a letter, then letters/digits/underscore/dash.
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]+")

#: CJK character range (CJK Unified Ideographs — covers the bulk of 简化字/繁體/日韓漢字).
_CJK_TOKEN_RE = re.compile(r"[一-鿿]+")


def _tokenize(text: str) -> set[str]:
    """Extract a deduped token set from *text*.

    CJK runs contribute every single char and every sliding bigram (so a
    filename like ``深度调研.md`` yields ``{"深", "度", "调", "研", "深度",
    "度调", "调研"}``). ASCII runs are kept as-is and lowercased.

    Returned set never includes stopwords or tokens shorter than 1 char.
    """
    if not text:
        return set()
    tokens: set[str] = set()

    for m in _ASCII_TOKEN_RE.finditer(text):
        t = m.group(0).lower()
        if t and t not in _STOPWORDS and len(t) >= 2:
            tokens.add(t)

    for run in _CJK_TOKEN_RE.findall(text):
        for ch in run:
            if ch not in _STOPWORDS:
                tokens.add(ch)
        if len(run) >= 2:
            for i in range(len(run) - 1):
                bigram = run[i : i + 2]
                if bigram not in _STOPWORDS:
                    tokens.add(bigram)

    return tokens


def _stem_from_filename(filename: str) -> str:
    """Return the human-readable stem, stripped of common noise.

    ``深度调研-v2-副本.md`` → ``深度调研``. Empty / pure-noise stems fall
    through to ``""`` so the caller can decide on a fallback.
    """
    stem = Path(filename).stem
    # Strip trailing version / copy / draft markers.
    stem = re.sub(r"[-_\s]*(v\d+(?:[._]\d+)*|final|副本|备份|draft|copy|\d+)$", "", stem, flags=re.IGNORECASE)
    # Replace separators with spaces (so bigram tokenizer picks both).
    stem = re.sub(r"[-_]+", " ", stem).strip()
    return stem


def _format_category(fmt: str | None) -> str:
    """Map a parser format code to a human-readable category label."""
    return {
        "pdf": "PDF 文档",
        "docx": "Word 文档",
        "pptx": "PPT",
        "xlsx": "Excel 表格",
        "csv": "CSV 表格",
        "html": "HTML 页面",
        "text": "文本文档",
        "image": "图片",
        "unsupported": "其他资料",
        "error": "其他资料",
    }.get((fmt or "").lower(), "其他资料")


@dataclass(slots=True)
class HeuristicDecision:
    """Outcome of :func:`heuristic_classify`.

    Mirrors :class:`src.domain.resource.auto_classify.AutoClassifyDecision`
    shape so callers can swap between the two without branching.
    """

    node: str
    new_node_name: str = ""
    rationale: str = ""
    score: int = 0
    matched_tokens: list[str] = field(default_factory=list)
    archive_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_new(self) -> bool:
        return self.node == "__new_node__"


def build_archive_metadata(filename: str, parsed: dict[str, Any]) -> dict[str, Any]:
    """Convert ``(filename, parsed_dict)`` into archive-ready metadata.

    No LLM, no network — just deterministic extraction. The shape mirrors
    what ``web_resources/index.json`` items look like, so the caller can
    hand the dict straight to ``ResourceRepository.write_json_index``.
    """
    fmt = (parsed or {}).get("format") or "unsupported"
    hints = (parsed or {}).get("hints") or {}
    text_preview = ((parsed or {}).get("text") or "").strip()

    stem = _stem_from_filename(filename)
    summary = text_preview[:200].replace("\n", " ").strip()

    # Title: prefer the stem, fall back to "<filename>（<category>）".
    title = stem or filename
    if not stem:
        title = f"{filename}（{_format_category(fmt)}）"

    # Tags: derive from hints + a few first-line keywords. Keep ≤ 8.
    tags: list[str] = []
    if hints.get("pages"):
        tags.append(f"{hints['pages']} 页")
    if hints.get("slides"):
        tags.append(f"{hints['slides']} 页幻灯片")
    if isinstance(hints.get("sheets"), list) and hints["sheets"]:
        tags.append(f"sheet {len(hints['sheets'])} 个")
    if fmt in {"pdf", "docx", "pptx", "xlsx"}:
        tags.append(_format_category(fmt))
    if hints.get("author"):
        tags.append(f"作者 {hints['author']}")
    if hints.get("title"):
        tags.append(hints["title"])

    return {
        "title": title,
        "category": _format_category(fmt),
        "format": fmt,
        "size": (parsed or {}).get("size", 0),
        "summary": summary,
        "tags": tags[:8],
        "hints": hints,
        "source_filename": filename,
    }


def heuristic_classify(
    filename: str,
    parsed: dict[str, Any],
    graph: dict[str, Any] | None,
    *,
    min_score: int = _MIN_ACCEPT_SCORE,
) -> HeuristicDecision:
    """Pick (or refuse to pick) a node for *filename*.

    Algorithm:
      1. Build archive metadata + a token bag from filename stem + first
         ~500 chars of preview text.
      2. For each existing node, compute token-overlap score against the
         same token bag.
      3. Highest score wins; if it's ``>= min_score`` we accept it.
         Otherwise return ``is_new=True`` with a node name derived from
         the filename stem (so the caller can decide whether to honour
         it under ``create_new_node``).
    """
    metadata = build_archive_metadata(filename, parsed)

    # Build the token bag: filename stem (high weight — implicit repeat)
    # + preview text first 500 chars (lower weight).
    text_preview = ((parsed or {}).get("text") or "")[:500]
    tokens = _tokenize(f"{metadata['title']}\n{text_preview}")
    if not tokens:
        return _new_decision(metadata, score=0, reason="无可用于匹配的关键词")

    nodes = (graph or {}).get("nodes") if graph else None
    if not isinstance(nodes, list) or not nodes:
        return _new_decision(metadata, score=0, reason="领域下尚无任何节点可匹配")

    # Score every node — prefer deeper (more specific) nodes when scores tie.
    scored: list[tuple[int, str, set[str]]] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        name = str(n.get("name", "")).strip()
        if not name:
            continue
        node_tokens = _tokenize(name)
        if not node_tokens:
            continue
        overlap = tokens & node_tokens
        if overlap:
            scored.append((len(overlap), name, overlap))

    if not scored:
        return _new_decision(metadata, score=0, reason="与现有节点无共同关键词")

    scored.sort(key=lambda x: (-x[0], -len(x[1])))
    top_score, top_name, top_overlap = scored[0]
    if top_score < min_score:
        return _new_decision(metadata, score=top_score, reason="最高匹配分低于阈值")

    # If a second-best candidate is within 1 of the winner, surface it as
    # an ambiguity flag rather than silently picking. The caller may then
    # decide to escalate to the LLM (if available) — we don't escalate
    # ourselves.
    ambiguous = len(scored) >= 2 and (scored[1][0] >= top_score - 1 and scored[1][0] > 0)

    return HeuristicDecision(
        node=top_name,
        rationale=(
            f"启发式匹配：与节点「{top_name}」共享关键词 "
            f"({', '.join(sorted(top_overlap))[:60]}，score={top_score}"
            f"{'，与第二候选相近' if ambiguous else ''})"
        ),
        score=top_score,
        matched_tokens=sorted(top_overlap),
        archive_metadata=metadata,
    )


def _new_decision(
    metadata: dict[str, Any],
    *,
    score: int,
    reason: str,
) -> HeuristicDecision:
    """Build an ``is_new=True`` decision with a sensible node name."""
    stem = metadata.get("title") or ""
    category = metadata.get("category") or "其他资料"

    # Prefer the filename stem (it's what the user actually called the
    # file). If empty (rare — only for `.` / `..` which we already
    # reject), fall back to a category-default that's far more honest
    # than the old "未命名资料" placeholder.
    if stem and not stem.endswith("（" + category + "）"):
        new_name = stem
    else:
        new_name = category

    return HeuristicDecision(
        node="__new_node__",
        new_node_name=new_name,
        rationale=f"{reason} → 建议新建节点「{new_name}」（score={score}）",
        score=score,
        archive_metadata=metadata,
    )


def node_candidates(
    graph: dict[str, Any] | None,
    filename: str,
    parsed: dict[str, Any],
    *,
    top_k: int = 5,
) -> list[tuple[str, int, set[str]]]:
    """Return the top-k candidate nodes by token overlap — for debugging /
    surfacing the heuristic result to the model. Pure function.
    """
    metadata = build_archive_metadata(filename, parsed)
    text_preview = ((parsed or {}).get("text") or "")[:500]
    tokens = _tokenize(f"{metadata['title']}\n{text_preview}")
    nodes = (graph or {}).get("nodes") if graph else None
    if not isinstance(nodes, list) or not tokens:
        return []

    out: list[tuple[str, int, set[str]]] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        name = str(n.get("name", "")).strip()
        if not name:
            continue
        node_tokens = _tokenize(name)
        if not node_tokens:
            continue
        overlap = tokens & node_tokens
        if overlap:
            out.append((name, len(overlap), overlap))
    out.sort(key=lambda x: (-x[1], -len(x[0])))
    return out[:top_k]


__all__ = [
    "HeuristicDecision",
    "build_archive_metadata",
    "heuristic_classify",
    "node_candidates",
    "_tokenize",  # exported for tests
]