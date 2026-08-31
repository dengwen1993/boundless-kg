"""Resource category constants — single source of truth.

Used by:
- ``kg_add_learning_resources`` docstring / validation
- ``SearchResult.category`` default value
- system prompt category guidance

Keeping the list in one place prevents the three from drifting.
"""

from __future__ import annotations

#: Ordered tuple of all valid resource categories.
#: Order matters for display (frontend dropdown, filter chips).
RESOURCE_CATEGORIES: tuple[str, ...] = (
    "论文",
    "视频",
    "课程",
    "代码",
    "文档",
    "教程",
    "书籍",
    "网页",
    "其他",
)

#: Default category when none is specified or recognised.
DEFAULT_CATEGORY: str = "网页"

#: Frozen set for fast membership checks.
RESOURCE_CATEGORY_SET: frozenset[str] = frozenset(RESOURCE_CATEGORIES)


__all__ = [
    "RESOURCE_CATEGORIES",
    "DEFAULT_CATEGORY",
    "RESOURCE_CATEGORY_SET",
]
