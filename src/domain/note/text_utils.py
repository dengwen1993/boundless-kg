"""Note text utility functions — moved from ``src/api/routes/_helpers.py``.

These functions extract summaries and metadata from note Markdown text.
They are pure text processing with no IO, so they belong in the domain layer.
"""

from __future__ import annotations

import re


def extract_first_definition_summary(text: str) -> str:
    """Extract the first definition text after ``## 定义`` (or legacy
    ``## 一句话定义``); truncate to 80 chars for list display.

    Captures blockquote (``>``) lines and plain-text paragraphs.  Falls
    back to the first non-header, non-empty line if no definition
    section is found.
    """
    if not text:
        return ""
    body = text
    # Skip HTML-comment frontmatter (<!-- ... -->)
    if body.startswith("#"):
        # Skip the H1 title line
        first_nl = body.find("\n")
        if first_nl >= 0:
            body = body[first_nl + 1:]
    lines = body.splitlines()
    capture = False
    collected: list[str] = []
    _DEF_HEADERS = ("## 定义", "## 一句话定义")
    for line in lines:
        stripped = line.strip()
        if not capture:
            if any(stripped.startswith(h) for h in _DEF_HEADERS):
                capture = True
            continue
        if stripped.startswith("## "):
            break
        if stripped.startswith(">"):
            content = stripped.lstrip(">").strip()
            if content:
                collected.append(content)
        elif stripped == "":
            if collected:
                break
            continue
        else:
            if stripped:
                collected.append(stripped)
            break
    summary = " ".join(collected).strip()
    if not summary:
        for line in lines:
            s = line.strip()
            if not s or s.startswith("#") or s.startswith(">") or s.startswith("<!--"):
                continue
            if s.startswith("-->"):
                continue
            summary = s
            break
    if len(summary) > 80:
        summary = summary[:80] + "…"
    return summary


def count_words(text: str) -> int:
    """Rough Chinese-word count (strip code blocks + markdown noise)."""
    if not text:
        return 0
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:]
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"[#*_>`\[\]|\-]+", "", text)
    return len(re.sub(r"\s+", "", text))


def extract_source(text: str) -> str | None:
    """Extract the ``生成方式`` line from frontmatter."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    head = text[:end]
    for line in head.splitlines():
        s = line.strip().lstrip("> ").strip()
        if s.startswith("生成方式") or s.startswith("生成"):
            return s.split("：", 1)[-1].strip() if "：" in s else s
    return None


__all__ = [
    "extract_first_definition_summary",
    "count_words",
    "extract_source",
]
