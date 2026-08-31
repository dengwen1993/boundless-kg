"""Link fix-up logic. Pure function on ``Graph``.

The graph invariant: ``node.links`` contains ONLY the node's children
(forward edges).  This module normalises a graph toward that invariant
by BFS-ing from the domain root and stripping every edge that is not a
recorded parent edge.  Cycles, sibling cross-links, and upward edges
are all dropped.
"""

from __future__ import annotations

from .models import Graph, LinkFixResult


def fix_missing_reverse_links(graph: Graph) -> tuple[Graph, LinkFixResult]:
    """Strip edges that don't belong in a forward-only tree.

    Algorithm:
      1. Pick the root (domain-name match; otherwise first node).
      2. BFS from the root.  The first edge that reaches a node marks
         its parent edge in ``parent_edge[child] = parent``; subsequent
         edges to an already-visited node (sibling cross-link / dup)
         are not parent edges and are stripped.
      3. The BFS also drops links to nodes already on the current
         search path (cycle / upward edge).
      4. Strip every edge that wasn't recorded as a parent edge.

    Returns a NEW ``Graph`` plus a ``LinkFixResult`` summary; the input
    graph is not mutated.
    """
    nodes = [n.model_copy(deep=True) for n in graph.nodes]
    name_to_node = {n.name: n for n in nodes}

    roots: list[str] = []
    if graph.domain and graph.domain in name_to_node:
        roots.append(graph.domain)
    if not roots and nodes:
        roots = [nodes[0].name]

    # parent_edge[child] = parent name; records the first forward edge
    # that reached each non-root node during BFS.
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
                continue  # sibling cross-link / dup
            visited.add(child)
            bfs_queue.append((child, cur))

    # Strip every edge that isn't a recorded parent edge.
    removed = 0
    for n in nodes:
        cleaned: list[str] = []
        seen: set[str] = set()
        for ln in n.links:
            if not ln or ln == n.name or ln not in name_to_node:
                continue
            if ln in seen:
                continue
            seen.add(ln)
            # Keep iff this edge was recorded as the parent edge of ln.
            if parent_edge.get(ln) == n.name:
                cleaned.append(ln)
            else:
                removed += 1
        if cleaned != n.links:
            n.links = cleaned

    new_graph = graph.model_copy(update={"nodes": nodes})
    return new_graph, LinkFixResult(added=removed, scanned=len(nodes))


__all__ = ["fix_missing_reverse_links"]