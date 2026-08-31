"""Domain-layer protocol interfaces — break reverse dependencies on infrastructure.

The domain layer must not import infrastructure types.  These Protocol
classes define the *structural* contracts that domain modules need
(LLM, Search, Wiki).  Infrastructure implementations satisfy them
structurally — no runtime import from domain → infrastructure is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


# ------------------------------------------------------------------
# SearchResult — moved here from infrastructure so domain is self-contained
# ------------------------------------------------------------------


@dataclass(slots=True)
class SearchResult:
    """A single web-search result item.

    Defined in the domain layer (not ``infrastructure.search.base``) so
    that domain modules (note generator, hot-keyword extractor) can
    reference it without importing infrastructure.  The infrastructure
    ``AsyncSearchClient`` re-exports this class for backwards
    compatibility.
    """

    title: str
    link: str
    snippet: str
    source: str = ""
    category: str = ""


# ------------------------------------------------------------------
# LLM client protocol
# ------------------------------------------------------------------


@runtime_checkable
class LLMClientProtocol(Protocol):
    """Structural contract for async LLM clients.

    ``infrastructure.llm.AsyncLLMClient`` and ``MockLLMClient`` both
    satisfy this protocol — domain code should type-hint against it
    rather than the concrete infrastructure class.
    """

    async def chat(
        self,
        system_prompt: str,
        user_msg: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> str:
        """Generate a completion. Returns the text body."""
        ...

    async def chat_with_reasoning(
        self,
        system_prompt: str,
        user_msg: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Generate with a separate reasoning trace.

        The returned object has a ``.text`` attribute (str) and an
        optional ``.reasoning`` attribute (str).
        """
        ...


# ------------------------------------------------------------------
# Search client protocol
# ------------------------------------------------------------------


@runtime_checkable
class SearchClientProtocol(Protocol):
    """Structural contract for async search backends."""

    async def search(
        self, query: str, *, num_results: int = 10
    ) -> list[SearchResult]:
        ...


# ------------------------------------------------------------------
# Wiki client protocol
# ------------------------------------------------------------------


@runtime_checkable
class WikiClientProtocol(Protocol):
    """Structural contract for async Wikipedia / reference clients."""

    async def lookup(self, title: str) -> str:
        ...


__all__ = [
    "SearchResult",
    "LLMClientProtocol",
    "SearchClientProtocol",
    "WikiClientProtocol",
]
