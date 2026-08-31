"""Deterministic archive metadata extraction from a parsed upload.

The :mod:`src.application.tmp_parser` module already turns any supported
file into ``{text, format, size, hints}``.  This module takes that one
step further and turns the raw text + structured hints into a compact
**archive metadata** record that the rest of the auto-place pipeline can
match against the knowledge graph *without* asking an LLM to interpret
free-form prose.

What we extract — and why each piece exists:

* ``filename_stem`` — the file's base name without extension. Used to
  derive the *suggested* node name when none of the existing nodes
  match. Also feeds into the topic keyword bag.
* ``format`` — propagated from the parser (``md`` / ``pdf`` / ``pptx``
  ...). The classifier later uses this to pick the appropriate category.
* ``size`` / ``chars`` — file size + parsed text length. Surfaced so the
  caller can sanity-check the result.
* ``title`` — extracted deterministically:
    * Markdown: first ``# heading`` line.
    * Office (PDF/DOCX/PPTX): ``hints.title`` from the metadata block
      if present, otherwise first non-empty line that *looks* like a
      title (short, no period, capitalised if English).
    * Otherwise: falls back to the filename stem.
* ``summary`` — first ~280 chars of body text after the title, used as
  a one-line blurb in the index entry.
* ``topic_keywords`` — a small bag of terms:
    * English tokens 2+ chars from title + first 1.5KB of body.
    * Chinese bigrams / single chars 2+ from the same window (CJK has
      no word boundary, so we use overlapping bigrams as features).
    * Filename stem tokens (split on ``-``/``_``/space).
  Stop words in both English and Chinese are filtered out so generic
  terms don't dominate the score.

The output is intentionally **plain Python types** (no Pydantic) so this
module has zero IO and can be unit-tested with pure strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any


_CN_STOPWORDS: frozenset[str] = frozenset({
    "的", "了", "和", "是", "在", "我", "有", "不", "这", "也", "就",
    "都", "而", "及", "与", "或", "为", "于", "对", "上", "下", "中",
    "以", "把", "被", "但", "而且", "或者", "通过", "可以", "如何",
    "怎么", "什么", "为什么", "一种", "一个", "一些", "我们", "你们",
    "他们", "它们", "因为", "所以", "如果", "虽然", "但是", "然后",
    "接着", "此外", "另外", "同时", "因此", "其中", "对于", "关于",
    "来自", "属于", "成为", "进行", "需要", "应该", "可能", "一定",
    "已经", "正在", "将要", "一直", "一起", "这些", "那些",
    "这样", "那样", "这里", "那里", "这种", "那种", "怎样",
})

_EN_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "had", "her", "was", "one", "our", "out", "day", "get", "has",
    "him", "his", "how", "man", "new", "now", "old", "see", "two",
    "way", "who", "boy", "did", "its", "let", "put", "say", "she",
    "too", "use", "this", "that", "with", "from", "have", "they",
    "will", "what", "when", "your", "than", "them", "been", "into",
    "more", "some", "such", "very", "also", "just", "over", "only",
    "then", "most", "each", "both", "any", "here", "there", "where",
})


@dataclass(slots=True)
class ArchiveMetadata:
    """Compact record describing one uploaded file for archiving."""

    filename: str
    filename_stem: str
    format: str
    size: int
    chars: int
    title: str = ""
    summary: str = ""
    topic_keywords: list[str] = field(default_factory=list)
    extra_hints: dict[str, Any] = field(default_factory=dict)


def extract_archive_metadata(
    filename: str,
    parsed: dict[str, Any],
) -> ArchiveMetadata:
    """Turn the parser output into structured archive metadata."""
    text = str(parsed.get("text") or "")
    fmt = str(parsed.get("format") or "unknown")
    size = int(parsed.get("size") or 0)
    chars = int(parsed.get("chars") or len(text))
    hints = parsed.get("hints") or {}
    if not isinstance(hints, dict):
        hints = {}

    stem = _safe_stem(filename)
    title = _extract_title(text, hints, stem)
    summary = _extract_summary(text, title)
    keywords = _extract_topic_keywords(filename=stem, text=text, title=title)

    return ArchiveMetadata(
        filename=filename,
        filename_stem=stem,
        format=fmt,
        size=size,
        chars=chars,
        title=title,
        summary=summary,
        topic_keywords=keywords,
        extra_hints=_clean_hints(hints),
    )


def _safe_stem(filename: str) -> str:
    name = PurePosixPath(filename).name
    stem = PurePosixPath(name).stem
    stem = re.sub(r"[\\/:\x00-\x1f]", "", stem).strip()
    return stem or filename


_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_TITLE_HEURISTIC_RE = re.compile(r"^(.{4,80}?)\s*[\.。！？!?]?\s*$")


def _extract_title(text: str, hints: dict[str, Any], stem: str) -> str:
    meta_title = str(hints.get("title") or "").strip()
    if meta_title and meta_title.lower() != "untitled":
        return _trim_title(meta_title)

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _MD_HEADING_RE.match(line)
        if m:
            return _trim_title(m.group(1))
        if line.startswith("```") or line.startswith(">"):
            continue
        if _TITLE_HEURISTIC_RE.match(line):
            return _trim_title(line)
        return _trim_title(stem)

    return _trim_title(stem) or stem


def _trim_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    return t.strip("#").strip()


def _extract_summary(text: str, title: str) -> str:
    body_lines: list[str] = []
    skipped_title = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not skipped_title:
            if _MD_HEADING_RE.match(stripped) or stripped == title:
                skipped_title = True
                continue
            skipped_title = True
            continue
        if stripped.startswith("```") or stripped.startswith("#"):
            continue
        body_lines.append(stripped)
    body = " ".join(body_lines)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) > 280:
        body = body[:277].rstrip() + "..."
    return body


def _extract_topic_keywords(filename: str, text: str, title: str) -> list[str]:
    body_window = text[:1500]
    blob = " ".join([filename, title, body_window]).lower()

    en_tokens: set[str] = set()
    for m in re.finditer(r"[a-z][a-z0-9]+", blob):
        tok = m.group(0)
        if tok in _EN_STOPWORDS:
            continue
        if len(tok) < 3 and tok not in {"ai", "ml", "llm", "rag", "ui", "ux", "ci", "cd"}:
            continue
        en_tokens.add(tok)

    cn_tokens: set[str] = set()
    for m in re.finditer(r"[一-鿿]{2,4}", blob):
        seg = m.group(0)
        if seg in _CN_STOPWORDS:
            continue
        for i in range(len(seg) - 1):
            cn_tokens.add(seg[i : i + 2])
        if len(seg) <= 3:
            cn_tokens.add(seg)

    fname_tokens: set[str] = set()
    for raw in re.split(r"[\s\-_\.　]+", filename.lower()):
        if not raw:
            continue
        if raw in _EN_STOPWORDS:
            continue
        if re.fullmatch(r"[a-z0-9]+", raw):
            fname_tokens.add(raw)
        else:
            fname_tokens.add(raw)

    ordered: list[str] = []
    seen: set[str] = set()
    for bag in (fname_tokens, en_tokens, cn_tokens):
        for tok in bag:
            if tok in seen:
                continue
            seen.add(tok)
            ordered.append(tok)

    return ordered[:80]


def _clean_hints(hints: dict[str, Any]) -> dict[str, Any]:
    keep = {"pages", "slides", "sheets", "author", "engine", "kind"}
    out: dict[str, Any] = {}
    for k in keep:
        if k in hints and hints[k] not in (None, "", 0):
            out[k] = hints[k]
    return out


__all__ = ["ArchiveMetadata", "extract_archive_metadata"]
