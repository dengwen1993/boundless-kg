"""Graph repository — async file IO + shared lock.

ENGINEERING_PLAN.md §3.4 — ``asyncio.Lock`` + atomic rename. The lock
instance comes from :mod:`src.infrastructure.lock`, the canonical
singleton; this module never declares its own.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiofiles

from src.domain.graph import Direction, Graph, Node
from src.infrastructure.lock import graph_lock

from ._atomic import atomic_write_text


class GraphRepository:
    """Async CRUD over per-domain ``knowledge_graph.json`` files.

    All mutating operations take the shared :func:`graph_lock`
    automatically; callers do NOT need to bracket their own critical
    sections when using this class.
    """

    def __init__(self, kb_root: Path) -> None:
        self._kb_root = Path(kb_root)

    @property
    def kb_root(self) -> Path:
        """Expose the KB root so services can delegate filesystem
        operations (e.g. node asset migration) without the caller
        needing to import ``get_kb_root`` directly."""
        return self._kb_root

    # --- paths ---

    @staticmethod
    def _safe_domain(domain: str) -> str:
        """Extract the real domain name from a possible 'domain / node' compound."""
        return domain.split(" / ")[0].split(" \\ ")[0].strip()

    def _graph_path(self, domain: str) -> Path:
        return self._kb_root / self._safe_domain(domain) / "knowledge_graph.json"

    def _domain_dir(self, domain: str) -> Path:
        return self._kb_root / self._safe_domain(domain)

    # --- read ---

    async def read_graph(self, domain: str) -> Graph:
        """Read the graph file. Returns an empty Graph if the file is missing."""
        async with graph_lock():
            path = self._graph_path(domain)
            if not path.exists():
                return Graph(domain=domain, direction=Direction(), nodes=[])
            async with aiofiles.open(path, encoding="utf-8") as f:
                raw = await f.read()
            return Graph.model_validate_json(raw)

    async def read_raw(self, domain: str) -> dict[str, Any]:
        """Read raw dict (used by API endpoints that mirror the JSON file)."""
        async with graph_lock():
            path = self._graph_path(domain)
            if not path.exists():
                return {"domain": domain, "nodes": []}
            async with aiofiles.open(path, encoding="utf-8") as f:
                raw = await f.read()
            return json.loads(raw)

    # --- write ---

    async def write_graph(self, domain: str, graph: Graph) -> None:
        """Persist the graph atomically (write to .tmp + rename)."""
        async with graph_lock():
            path = self._graph_path(domain)
            path.parent.mkdir(parents=True, exist_ok=True)
            text = graph.model_dump_json(indent=2)
            await atomic_write_text(path, text)

    # --- node-level ops ---

    async def list_domains(self) -> list[str]:
        """All domain directory names under ``kb_root``.

        Hidden directories (e.g. ``.agent_memory``, ``.git``) are skipped —
        they are operational artefacts, not user-facing knowledge bases,
        and would otherwise show up as empty entries in the domain picker.
        """
        if not self._kb_root.exists():
            return []
        return sorted(
            p.name
            for p in self._kb_root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )

    async def domain_exists(self, domain: str) -> bool:
        """True iff ``kb_root/<domain>/knowledge_graph.json`` is on disk.

        Used by callers that need to detect *first-write-into-a-new-domain*
        so they can emit a ``DOMAIN_CREATED`` event on the activity bus
        alongside the subsequent ``NODE_CREATED`` (or batch thereof).
        Cheap file-existence check — no lock needed.
        """
        return self._graph_path(domain).exists()

    async def get_node(self, domain: str, name: str) -> Node | None:
        graph = await self.read_graph(domain)
        for n in graph.nodes:
            if n.name == name:
                return n
        return None

    async def add_node(
        self,
        domain: str,
        name: str,
        *,
        links: list[str] | None = None,
        parent: str | None = None,
    ) -> Graph:
        """Append a node; if ``parent`` given, append to its links too."""
        async with graph_lock():
            graph = await self._read_locked(domain)
            if any(n.name == name for n in graph.nodes):
                raise ValueError(f"Node {name!r} already exists in domain {domain!r}")
            new_node = Node(
                name=name,
                links=list(links or []),
            )
            graph.nodes.append(new_node)
            if parent:
                for n in graph.nodes:
                    if n.name == parent and name not in n.links:
                        n.links.append(name)
                        break
            await self._write_locked(domain, graph)
            return graph

    async def add_subtree(
        self,
        domain: str,
        nodes: list[Node],
        root_links: list[str] | None = None,
        *,
        parent: str | None = None,
        extra_parent_links: dict[str, list[str]] | None = None,
        auto_create_parents: bool = False,
    ) -> tuple[Graph, list[str]]:
        """Bulk-append a list of nodes (e.g. expander output).

        Wiring behaviour — all applied atomically in one write:
          - If ``parent`` is given, every newly-appended node is also
            appended to ``parent.links`` so the front-end tree can see
            the new children (BUG-001).
          - If ``extra_parent_links`` is given, each ``{parent: [kids]}``
            entry appends the listed children to ``parent.links``. Used
            by the tree-shape API in ``kg_add_subtree`` to wire nested
            descendants.
          - If ``auto_create_parents`` is True, any parent in ``parent``
            or ``extra_parent_links`` that does not yet exist is created
            with ``links=[]`` so the wiring is effective instead of
            silently no-oping.

        Returns ``(graph, added_names)`` so callers can distinguish nodes
        that actually landed from those already present (the second list
        is only useful for activity-bus events; the file is one atomic
        write either way).
        """
        async with graph_lock():
            graph = await self._read_locked(domain)
            existing = {n.name for n in graph.nodes}
            added: list[str] = []
            for n in nodes:
                if n.name in existing:
                    continue
                graph.nodes.append(n)
                existing.add(n.name)
                added.append(n.name)

            # Merge all parent→children wirings into a single map.
            wiring: dict[str, list[str]] = {}
            if parent and added:
                wiring[parent] = list(wiring.get(parent, [])) + added
            if extra_parent_links:
                for p, kids in extra_parent_links.items():
                    wiring[p] = list(wiring.get(p, [])) + list(kids or [])

            if auto_create_parents:
                for p in wiring:
                    if p not in existing:
                        graph.nodes.append(Node(name=p, links=[]))
                        existing.add(p)

            for p, kids in wiring.items():
                if p not in existing:
                    # explicit silent no-op when caller chose not to auto-create.
                    continue
                for n in graph.nodes:
                    if n.name == p:
                        for c in kids:
                            if c and c not in n.links:
                                n.links.append(c)
                        break

            await self._write_locked(domain, graph)
            return graph, added

    async def fix_links(self, domain: str) -> tuple[int, int]:
        """Normalise the graph to forward-only tree links.

        BFS from the domain root; record the first edge that reaches
        each non-root node as its parent edge.  Every other edge is
        treated as malformed (sibling cross-link / upward / cycle) and
        stripped.  Returns ``(removed, scanned)`` counts.
        """
        async with graph_lock():
            graph = await self._read_locked(domain)

            name_to_node = {n.name: n for n in graph.nodes}
            roots: list[str] = []
            if graph.domain and graph.domain in name_to_node:
                roots.append(graph.domain)
            if not roots and graph.nodes:
                roots = [graph.nodes[0].name]

            parent_edge: dict[str, str] = {r: "" for r in roots}
            bfs_queue: list[tuple[str, str]] = [(r, "") for r in roots]
            visited: set[str] = set(roots)
            while bfs_queue:
                cur, par = bfs_queue.pop(0)
                if par and cur not in parent_edge:
                    parent_edge[cur] = par
                node = name_to_node.get(cur)
                if node is None:
                    continue
                for child in node.links:
                    if child not in name_to_node or child == cur:
                        continue
                    if child in visited:
                        continue
                    visited.add(child)
                    bfs_queue.append((child, cur))

            removed = 0
            for n in graph.nodes:
                cleaned: list[str] = []
                seen: set[str] = set()
                for ln in n.links:
                    if not ln or ln == n.name:
                        continue
                    if ln in seen:
                        continue
                    seen.add(ln)
                    if parent_edge.get(ln) == n.name:
                        cleaned.append(ln)
                    else:
                        removed += 1
                n.links = cleaned
            await self._write_locked(domain, graph)
            return removed, len(graph.nodes)

    async def delete_node(self, domain: str, name: str) -> Graph:
        """Remove a node and clean all incoming references."""
        async with graph_lock():
            graph = await self._read_locked(domain)
            graph.nodes = [n for n in graph.nodes if n.name != name]
            for n in graph.nodes:
                n.links = [l for l in n.links if l != name]
            await self._write_locked(domain, graph)
            return graph

    async def update_node(
        self,
        domain: str,
        old_name: str,
        new_name: str = "",
        new_links: list[str] | None = None,
    ) -> Graph:
        """Rename a node and/or update its links; patches all references."""
        async with graph_lock():
            graph = await self._read_locked(domain)
            target = graph.find_node(old_name)
            if target is None:
                raise ValueError(f"Node {old_name!r} not found in domain {domain!r}")
            final_name = (new_name or old_name).strip()
            if final_name != old_name:
                if any(n.name == final_name for n in graph.nodes):
                    raise ValueError(f"Node {final_name!r} already exists")
                target.name = final_name
                for n in graph.nodes:
                    n.links = [
                        final_name if l == old_name else l for l in n.links
                    ]
            if new_links is not None:
                target.links = list(new_links)
            await self._write_locked(domain, graph)
            return graph

    # --- internal helpers that MUST be called with the lock held ---

    async def _read_locked(self, domain: str) -> Graph:
        path = self._graph_path(domain)
        if not path.exists():
            return Graph(domain=domain, direction=Direction(), nodes=[])
        async with aiofiles.open(path, encoding="utf-8") as f:
            raw = await f.read()
        return Graph.model_validate_json(raw)

    async def _write_locked(self, domain: str, graph: Graph) -> None:
        path = self._graph_path(domain)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = graph.model_dump_json(indent=2)
        await atomic_write_text(path, text)


__all__ = ["GraphRepository"]