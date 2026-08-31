"""Text utilities — normalisation + Jaccard similarity."""

from __future__ import annotations

import re
import unicodedata


def normalize_text(s: str) -> str:
    """NFKC-normalise, strip, collapse whitespace."""
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def jaccard(a: str, b: str) -> float:
    sa = set(_tokens(a))
    sb = set(_tokens(b))
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _tokens(s: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for ch in s:
        if ch.isspace() or ch in "，。！？；：「」、()[]{}":
            if buf:
                out.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [t for t in out if t]


__all__ = ["normalize_text", "jaccard"]