"""Rule-based query builder — skeleton × modifier matrix."""

from __future__ import annotations

from src.domain.intent.models import IntentMeta

SKELETONS: list[str] = [
    "{topic}",
    "{topic} 入门",
    "{topic} 进阶",
    "{topic} 实战",
    "{topic} 应用场景",
    "{topic} 最新进展",
    "{topic} 面试题",
    "{topic} 原理",
    "{topic} 与 {topic} 相关",
    "{topic} 学习路线",
]

MODIFIERS: list[str] = [
    "",
    "最佳实践",
    "踩坑总结",
    "教程",
    "对比",
    "原理解析",
    "案例",
]


def build_queries(meta: IntentMeta, *, max_queries: int = 50) -> list[str]:
    """Build a query list from the intent metadata.

    The matrix is ``SKELETONS × MODIFIERS`` filtered down to
    ``max_queries`` entries; duplicates removed via ``dedup_strings``.
    """
    topic = meta.topic.strip()
    if not topic:
        return []
    base = [s.format(topic=topic) for s in SKELETONS]
    with_mod = [f"{q} {m}".strip() for q in base for m in MODIFIERS if m]
    queries = base + with_mod
    return dedup(queries)[:max_queries]


def dedup(queries: list[str]) -> list[str]:
    """Simple case-folded dedup."""
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower().strip()
        if key in seen or not key:
            continue
        seen.add(key)
        out.append(q)
    return out


__all__ = ["build_queries", "SKELETONS", "MODIFIERS"]