"""Search API — hybrid BM25 + vector search and graph queries.

Endpoints:
  GET  /api/search/{domain}?q=...&top_k=10        — hybrid search
  GET  /api/search/{domain}/global?q=...           — global search (with neighbors)
  GET  /api/graph/{domain}/neighbors?node=...&hops=2 — graph traversal
  GET  /api/graph/{domain}/statistics              — graph stats
  POST /api/graph/{domain}/sync                    — trigger full sync
  POST /api/graph/{domain}/sync-node               — trigger node sync
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.agent import dependencies as agent_deps
from src.observability.activity_bus import ActivityKind, get_activity_bus

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search/{domain}")
async def search(
    domain: str,
    q: str = Query(..., min_length=1, description="Search query"),
    top_k: int = Query(10, ge=1, le=50),
):
    """Hybrid BM25 + vector search across nodes, notes, and resources."""
    svc = agent_deps.get_search_service()
    results = await svc.search(domain, q, top_k=top_k)
    return {
        "query": q,
        "domain": domain,
        "results": [r.to_dict() for r in results],
        "total": len(results),
    }


@router.get("/search/{domain}/global")
async def global_search(
    domain: str,
    q: str = Query(..., min_length=1, description="Search query"),
    top_k: int = Query(10, ge=1, le=30),
):
    """Global search — hybrid results + 1-hop graph neighbors.

    Used by the Agent for large-scale planning: gives a complete picture
    of what exists and how it's connected.
    """
    svc = agent_deps.get_search_service()
    return await svc.global_search(domain, q, top_k=top_k)


@router.get("/graph/{domain}/neighbors")
async def graph_neighbors(
    domain: str,
    node: str = Query(..., description="节点名；可包含 /"),
    hops: int = Query(1, ge=1, le=5),
):
    """Query N-hop neighbors of a node in the graph."""
    store = agent_deps.get_graph_store()
    if not await store.ensure_available():
        raise HTTPException(503, "Graph store (FalkorDB) not available")
    node_id = f"concept:{node}"
    neighbors = store.neighbors(domain, node_id, hops=hops)
    return {
        "domain": domain,
        "node": node,
        "hops": hops,
        "neighbors": neighbors,
    }


@router.get("/graph/{domain}/statistics")
async def graph_statistics(domain: str):
    """Return graph statistics for a domain."""
    store = agent_deps.get_graph_store()
    if not await store.ensure_available():
        raise HTTPException(503, "Graph store (FalkorDB) not available")
    return store.statistics(domain)


@router.post("/graph/{domain}/sync")
async def sync_full(domain: str):
    """Trigger full sync: truth-source → FalkorDB + embedding."""
    sync_svc = agent_deps.get_graph_sync_service(domain)
    result = await sync_svc.sync_full()

    # Activity timeline — manual full sync (UI button).
    await get_activity_bus().emit(
        ActivityKind.GRAPH_SYNCED,
        domain=domain,
        node="",
        title=f"手动触发了「{domain}」的全量同步",
        source="manual",
        ref=f"domain:{domain}",
        extra={"mode": "full", **({"nodes": result.get("nodes")} if isinstance(result, dict) and "nodes" in result else {})},
    )
    return result


@router.post("/graph/{domain}/sync-node")
async def sync_node(domain: str, node: str = Query(...)):
    """Trigger incremental sync for a single node."""
    sync_svc = agent_deps.get_graph_sync_service(domain)
    result = await sync_svc.sync_for_node(node)

    # Activity timeline — manual per-node sync.
    await get_activity_bus().emit(
        ActivityKind.GRAPH_SYNCED,
        domain=domain,
        node=node,
        title=f"手动同步了节点「{node}」",
        source="manual",
        ref=f"node:{node}",
        extra={"mode": "node", "node": node},
    )
    return result


__all__ = ["router"]
