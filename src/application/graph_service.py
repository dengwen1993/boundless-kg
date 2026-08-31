"""GraphService — CRUD + validation + scoring on top of GraphRepository."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.domain.graph import (
    DomainSummary,
    Graph,
    GraphValidator,
    QualityLevel,
    QualityScore,
    fix_missing_reverse_links,
)
from src.infrastructure.repository.graph_repo import GraphRepository


class GraphService:
    """Async service over per-domain graphs.

    Wraps :class:`GraphRepository` with domain validation + quality
    scoring. Always returns ``Graph`` values, not raw dicts.
    """

    def __init__(self, repo: GraphRepository, *, validator: GraphValidator | None = None) -> None:
        self._repo = repo
        self._validator = validator or GraphValidator()

    async def list_domains(self) -> list[DomainSummary]:
        names = await self._repo.list_domains()
        out: list[DomainSummary] = []
        for name in names:
            g = await self._repo.read_graph(name)
            score = self._validator.score(g) if g.nodes else None
            out.append(
                DomainSummary(
                    domain=name,
                    node_count=len(g.nodes),
                    has_direction=bool(g.direction.summary),
                    generated_at=g.generated_at,
                    quality_level=score.level if score else None,
                )
            )
        return out

    async def view(self, domain: str) -> Graph:
        return await self._repo.read_graph(domain)

    async def view_raw(self, domain: str) -> dict:
        return await self._repo.read_raw(domain)

    async def add_node(
        self,
        domain: str,
        name: str,
        *,
        links: list[str] | None = None,
        parent: str | None = None,
    ) -> Graph:
        return await self._repo.add_node(domain, name, links=links, parent=parent)

    async def add_subtree(
        self,
        domain: str,
        nodes,
        root_links: list[str] | None = None,
        *,
        parent: str | None = None,
        extra_parent_links: dict[str, list[str]] | None = None,
        auto_create_parents: bool = False,
    ) -> tuple[Graph, list[str]]:
        """Bulk-append nodes with optional wiring.

        Returns ``(graph, added_names)``.  See ``GraphRepository.add_subtree``
        for the wiring semantics.  ``extra_parent_links`` lets tree-shape
        callers wire multiple parents in one atomic write; ``auto_create_parents``
        makes that wiring always effective by creating missing parents.
        """
        return await self._repo.add_subtree(
            domain,
            nodes,
            root_links=root_links,
            parent=parent,
            extra_parent_links=extra_parent_links,
            auto_create_parents=auto_create_parents,
        )

    async def fix_links(self, domain: str) -> tuple[int, int]:
        """Two-phase: in-memory fix-up + repository write."""
        g = await self._repo.read_graph(domain)
        new_g, result = fix_missing_reverse_links(g)
        await self._repo.write_graph(domain, new_g)
        return result.added, result.scanned

    async def delete_node(self, domain: str, name: str) -> Graph:
        """Delete a node and remove all incoming references."""
        return await self._repo.delete_node(domain, name)

    async def delete_node_assets(self, domain: str, name: str) -> bool:
        """Remove the on-disk ``notes/{name}/`` directory."""
        from src.application.node_migration import delete_node_assets
        return await delete_node_assets(self._repo.kb_root, domain, name)

    async def update_node(
        self,
        domain: str,
        old_name: str,
        new_name: str = "",
        new_links: list[str] | None = None,
    ) -> Graph:
        """Rename a node and/or update its links."""
        return await self._repo.update_node(domain, old_name, new_name, new_links)

    async def migrate_node_assets(
        self, domain: str, old_name: str, new_name: str
    ) -> dict[str, Any]:
        """Rename ``notes/{old_name}/`` → ``notes/{new_name}/`` in place."""
        from src.application.node_migration import migrate_node_assets
        return await migrate_node_assets(self._repo.kb_root, domain, old_name, new_name)

    async def validate(self, domain: str) -> list[str]:
        g = await self._repo.read_graph(domain)
        return self._validator.validate(g)

    async def score(self, domain: str) -> QualityScore:
        g = await self._repo.read_graph(domain)
        return self._validator.score(g)

    async def save_graph(self, graph: Graph) -> None:
        graph.generated_at = graph.generated_at or datetime.utcnow().isoformat()
        await self._repo.write_graph(graph.domain, graph)


__all__ = ["GraphService"]