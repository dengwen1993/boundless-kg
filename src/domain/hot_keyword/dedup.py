"""Keyword-level deduplication + Jaccard similarity."""

from __future__ import annotations


def jaccard(a: str, b: str) -> float:
    """Token-level Jaccard similarity between two strings."""
    sa = set(_tokens(a))
    sb = set(_tokens(b))
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def dedup_strings(items: list[str], *, threshold: float = 0.85) -> list[str]:
    """Drop near-duplicates above ``threshold`` Jaccard."""
    kept: list[str] = []
    for it in items:
        if not any(jaccard(it, k) >= threshold for k in kept):
            kept.append(it)
    return kept


def _tokens(s: str) -> list[str]:
    """Crude tokeniser: split on whitespace + CJK char boundaries."""
    out: list[str] = []
    buf: list[str] = []
    for ch in s.strip():
        if ch.isspace() or ch in "，。！？；：「」、":
            if buf:
                out.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [t for t in out if t]


__all__ = ["jaccard", "dedup_strings"]