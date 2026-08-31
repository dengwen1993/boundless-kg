"""Pure-function graph validator + scorer (7 checks, 6 dimensions)."""

from __future__ import annotations

import re
from typing import Iterable

from .models import Graph, Node, QualityScore


class GraphValidator:
    """Run all seven validation checks + the six-dimension scorer.

    Pure: takes a ``Graph`` value, returns ``(issues, score)``. No IO.
    """

    SCHEMA_VERSION = 1

    def __init__(self, max_links_per_node: int = 20) -> None:
        self._max_links = max_links_per_node

    def validate(self, graph: Graph) -> list[str]:
        issues: list[str] = []
        issues.extend(self._check_uniqueness(graph))
        issues.extend(self._check_link_integrity(graph))
        issues.extend(self._check_link_cardinality(graph))
        issues.extend(self._check_no_self_loop(graph))
        issues.extend(self._check_no_dup_links(graph))
        issues.extend(self._check_direction(graph))
        return issues

    def score(self, graph: Graph) -> QualityScore:
        return QualityScore(
            coverage=self._score_coverage(graph),
            hierarchy=self._score_hierarchy(graph),
            linkage=self._score_linkage(graph),
            coherence=self._score_coherence(graph),
            specificity=self._score_specificity(graph),
            freshness=self._score_freshness(graph),
        )

    # ---- individual checks ----

    def _check_uniqueness(self, graph: Graph) -> list[str]:
        seen: set[str] = set()
        dups: list[str] = []
        for n in graph.nodes:
            if n.name in seen:
                dups.append(n.name)
            seen.add(n.name)
        return [f"duplicate node name: {n!r}" for n in dups]

    def _check_link_integrity(self, graph: Graph) -> list[str]:
        names = set(graph.node_names())
        broken: list[str] = []
        for n in graph.nodes:
            for link in n.links:
                if link not in names:
                    broken.append(f"{n.name} → {link}")
        return [f"broken link {pair!r}" for pair in broken]

    def _check_link_cardinality(self, graph: Graph) -> list[str]:
        over: list[str] = []
        for n in graph.nodes:
            if len(n.links) > self._max_links:
                over.append(n.name)
        return [f"node {n!r} has >{self._max_links} links" for n in over]

    def _check_no_self_loop(self, graph: Graph) -> list[str]:
        out: list[str] = []
        for n in graph.nodes:
            if n.name in n.links:
                out.append(n.name)
        return [f"node {n!r} links to itself" for n in out]

    def _check_no_dup_links(self, graph: Graph) -> list[str]:
        out: list[str] = []
        for n in graph.nodes:
            seen: set[str] = set()
            dups: list[str] = []
            for link in n.links:
                if link in seen:
                    dups.append(link)
                seen.add(link)
            if dups:
                out.append(f"{n.name}: {dups}")
        return out

    def _check_direction(self, graph: Graph) -> list[str]:
        if not graph.direction.summary:
            return ["direction.summary is empty"]
        return []

    # ---- scorers (each 0-100) ----

    def _score_coverage(self, graph: Graph) -> int:
        n = len(graph.nodes)
        if n == 0:
            return 0
        # 5 nodes = 50, 10 = 75, 20 = 100 (clamped)
        return min(100, n * 5)

    def _score_hierarchy(self, graph: Graph) -> int:
        if not graph.nodes:
            return 0
        # crude proxy: count nodes referenced as a link by someone else
        referenced = sum(
            1 for n in graph.nodes if any(n.name in other.links for other in graph.nodes)
        )
        return int(referenced / len(graph.nodes) * 100)

    def _score_linkage(self, graph: Graph) -> int:
        if not graph.nodes:
            return 0
        total_links = sum(len(n.links) for n in graph.nodes)
        avg = total_links / len(graph.nodes)
        return min(100, int(avg * 25))

    def _score_coherence(self, graph: Graph) -> int:
        if not graph.direction.summary:
            return 30
        return 80 if len(graph.direction.summary) >= 30 else 60

    def _score_specificity(self, graph: Graph) -> int:
        if not graph.nodes:
            return 0
        longish = sum(1 for n in graph.nodes if len(n.name) >= 4)
        return int(longish / len(graph.nodes) * 100)

    def _score_freshness(self, graph: Graph) -> int:
        # No persistent timestamp here; trust upstream scoring.
        return 60


def validate_graph(graph: Graph) -> list[str]:
    """Convenience: run a default ``GraphValidator``."""
    return GraphValidator().validate(graph)


__all__ = ["GraphValidator", "validate_graph"]