"""Domain layer — pure business logic, no IO.

The domain layer imports only standard library + pydantic. It never
imports from ``infrastructure``, ``application``, ``api``, or ``agent``.
"""

from . import graph, hot_keyword, intent, note, resource
from .protocols import (
    LLMClientProtocol,
    SearchClientProtocol,
    SearchResult,
    WikiClientProtocol,
)

__all__ = [
    "graph",
    "intent",
    "hot_keyword",
    "note",
    "resource",
    "LLMClientProtocol",
    "SearchClientProtocol",
    "SearchResult",
    "WikiClientProtocol",
]