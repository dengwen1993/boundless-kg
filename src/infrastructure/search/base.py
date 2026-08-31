"""Search client abstract base.

``SearchResult`` is now defined in ``src.domain.protocols`` so the
 domain layer can reference it without importing infrastructure. This
 module re-exports it for backwards compatibility.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.protocols import SearchResult  # noqa: F401 — re-export
from src.domain.resource.categories import DEFAULT_CATEGORY

# Ensure the default category is set on SearchResult instances that
# don't specify one (preserves old behaviour).
SearchResult.__dataclass_fields__["category"].default = DEFAULT_CATEGORY


class AsyncSearchClient(ABC):
    """Async search backend."""

    @abstractmethod
    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        """Run a web search; return up to ``num_results`` results."""


__all__ = ["AsyncSearchClient", "SearchResult"]