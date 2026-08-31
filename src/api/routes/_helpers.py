"""Compatibility shim — delegates to domain/services.

Previously this module contained file IO, graph decoration, and text
helpers.  All of those have been moved:

* Graph decoration → ``src.domain.graph.decorator``
* Text helpers → ``src.domain.note.text_utils``
* File IO → ``GraphRepository`` / ``NoteRepository`` / ``ResourceRepository``
* ``kb_root()`` → use ``get_graph_repo().kb_root`` or ``get_kb_root()`` from config

This module re-exports the decoration and text functions so any
lingering imports from other modules don't break.
"""

from __future__ import annotations

# Re-export for backwards compatibility (code that still imports from _helpers)
from src.domain.graph.decorator import (
    decorate_graph,
    gather_graph_context,
    infer_hierarchy,
    node_tier,
)
from src.domain.note.text_utils import (
    count_words,
    extract_first_definition_summary,
    extract_source,
)


__all__ = [
    "decorate_graph",
    "node_tier",
    "infer_hierarchy",
    "gather_graph_context",
    "extract_first_definition_summary",
    "count_words",
    "extract_source",
]
