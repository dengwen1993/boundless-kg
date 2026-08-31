"""Graph decoration — BFS levels → tier → synthetic L0 root.

Moved from ``src/api/routes/_helpers.py`` so the domain layer owns all
graph structural logic.  The API and service layers call these
functions to produce the decorated dict shape the Vue frontend expects.
"""

from __future__ import annotations

from typing import Any


def _compute_levels(nodes: list[dict]) -> dict[str, int]:
    """BFS to compute node levels.  Returns ``{name: level}``."""
    incoming: dict[str, set[str]] = {}
    for n in nodes:
        incoming.setdefault(n["name"], set())
    for n in nodes:
        for child in n.get("links", []):
            incoming.setdefault(child, set()).add(n["name"])
    roots = [n["name"] for n in nodes if not incoming.get(n["name"])]
    level: dict[str, int] = {}
    queue: list[tuple[str, int]] = [(r, 1) for r in roots]
    while queue:
        name, lvl = queue.pop(0)
        if name in level:
            continue
        level[name] = lvl
        node = next((n for n in nodes if n["name"] == name), None)
        if node:
            for child in node.get("links", []):
                if child not in level:
                    queue.append((child, lvl + 1))
    for n in nodes:
        if n["name"] not in level:
            level[n["name"]] = 1
    return level


def _classify(lvl: int) -> str:
    if lvl == 1:
        return "L1"
    if lvl == 2:
        return "L2"
    if lvl == 3:
        return "L3"
    return "leaf"


def decorate_graph(graph: dict) -> dict:
    """Add ``level`` / ``tier`` / ``childCount`` to each node and prepend
    a synthetic L0 domain-root node.
    """
    nodes = graph.get("nodes", [])
    level = _compute_levels(nodes)

    decorated: list[dict] = []
    for n in nodes:
        lv = level.get(n["name"], 1)
        decorated.append(
            {
                **n,
                "level": lv,
                "tier": _classify(lv),
                "childCount": len(n.get("links", [])),
            }
        )

    roots = [n["name"] for n in decorated if n["level"] == 1]
    domain_root: dict[str, Any] = {
        "name": graph.get("domain", ""),
        "links": roots,
        "level": 0,
        "tier": "L0",
        "childCount": len(roots),
        "isDomainRoot": True,
    }

    n_links = sum(len(n.get("links", [])) for n in nodes)
    return {
        "domain": graph.get("domain", ""),
        "direction": graph.get("direction", {}),
        "nodes": [domain_root] + decorated,
        "meta": {
            "n_nodes": len(decorated),
            "n_links": n_links,
        },
    }


def node_tier(decorated: dict, name: str) -> str:
    """Return the tier (L0/L1/L2/L3/leaf) of *name* in a decorated graph."""
    for n in decorated.get("nodes", []):
        if n.get("name") == name:
            return n.get("tier", "leaf")
    return "leaf"


def infer_hierarchy(graph: dict, node_name: str) -> tuple[list[str], list[str], list[str]]:
    """Return ``(parents, siblings, hierarchy_chain)`` from the graph."""
    parents_of: dict[str, list[str]] = {}
    children_of: dict[str, list[str]] = {}
    for n in graph.get("nodes", []):
        nm = n.get("name")
        if not nm:
            continue
        for c in n.get("links", []):
            parents_of.setdefault(c, []).append(nm)
            children_of.setdefault(nm, []).append(c)
    parents = parents_of.get(node_name, [])
    siblings: list[str] = []
    if parents:
        siblings = [x for x in children_of.get(parents[0], []) if x != node_name]
    chain: list[str] = []
    cur, seen = node_name, set()
    while cur and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        ps = parents_of.get(cur, [])
        cur = ps[0] if ps else ""
    chain.reverse()
    return parents, siblings, chain


def gather_graph_context(graph: dict, domain: str, node_name: str) -> dict[str, Any]:
    """Collect full graph context for a node (used by note generation)."""
    ctx: dict[str, Any] = {"domain": domain}
    direction = graph.get("direction", {})
    if isinstance(direction, dict):
        parts = []
        for k in ("angle", "audience", "depth", "summary"):
            v = direction.get(k)
            if v:
                parts.append(f"{k}={v}")
        if parts:
            ctx["direction_summary"] = "；".join(parts)
    ctx["total_nodes"] = len(graph.get("nodes", []))

    parents_of: dict[str, list[str]] = {}
    children_of: dict[str, list[str]] = {}
    for n in graph.get("nodes", []):
        nm = n.get("name")
        if not nm:
            continue
        for c in n.get("links", []):
            parents_of.setdefault(c, []).append(nm)
            children_of.setdefault(nm, []).append(c)

    ctx["parents"] = parents_of.get(node_name, [])
    ctx["children"] = children_of.get(node_name, [])
    parents = ctx["parents"]
    if parents:
        ctx["siblings"] = [
            x for x in children_of.get(parents[0], []) if x != node_name
        ]
    else:
        ctx["siblings"] = []

    chain: list[str] = []
    cur, seen = node_name, set()
    while cur and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        ps = parents_of.get(cur, [])
        cur = ps[0] if ps else ""
    chain.reverse()
    if not any(domain in c for c in chain):
        chain.insert(0, domain)
    ctx["hierarchy_path"] = (
        " - ".join(chain) if chain else f"{domain} - {node_name}"
    )
    return ctx


__all__ = [
    "decorate_graph",
    "node_tier",
    "infer_hierarchy",
    "gather_graph_context",
]
