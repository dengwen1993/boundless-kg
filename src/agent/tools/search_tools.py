"""Search tools — Agent tools for global semantic search.

Tools:
  kg_global_search  — hybrid BM25 + vector search with graph neighbors
  kg_graph_neighbors — query N-hop neighbors of a node
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from src.agent import dependencies as agent_deps

logger = logging.getLogger(__name__)


@tool
def kg_global_search(domain: str, query: str, top_k: int = 10) -> dict:
    """全局语义搜索 — 理解领域内所有相关资源及其关联。

    当你需要理解"这个领域有什么、都关联了什么"时调用此工具。
    返回匹配的节点/笔记/资源 + 每个结果的 1 跳关联图（邻居 + 边类型）。
    用于做大规划前的全局上下文理解。

    Args:
        domain: 领域名
        query: 自然语言查询（如 "分布式训练相关的一切"）
        top_k: 返回结果数（默认 10，最大 30）

    Returns:
        {
          "query": "...",
          "results": [
            {
              "id": "concept:...",
              "name": "...",
              "type": "concept|note|resource",
              "score": 0.85,
              "snippet": "...",
              "neighbors": [
                {"name": "...", "relation": "PREREQUISITE_OF", "hops": 1}
              ]
            }
          ]
        }
    """
    import asyncio

    async def _run() -> dict:
        svc = agent_deps.get_search_service()
        return await svc.global_search(domain, query, top_k=top_k)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in an async context — schedule and wait
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _run()).result()
        else:
            return asyncio.run(_run())
    except RuntimeError:
        import asyncio
        return asyncio.run(_run())


@tool
def kg_graph_neighbors(domain: str, node: str, hops: int = 1) -> dict:
    """查询节点的 N 跳邻居（图遍历）。

    用于理解某个节点的关联上下文：哪些节点是前置知识、哪些相似、哪些对比。

    Args:
        domain: 领域名
        node: 节点名
        hops: 跳数（1=直接邻居，2=二跳，默认 1，最大 5）

    Returns:
        {
          "node": "...",
          "hops": 1,
          "neighbors": [
            {"neighbor_id": "...", "name": "...", "relation": "PART_OF", "hops": 1}
          ]
        }
    """
    store = agent_deps.get_graph_store()
    if not store.is_available:
        return {"error": "Graph store (FalkorDB) not available"}

    node_id = f"concept:{node}"
    neighbors = store.neighbors(domain, node_id, hops=hops)
    return {
        "domain": domain,
        "node": node,
        "hops": hops,
        "neighbors": neighbors,
    }


__all__ = ["kg_global_search", "kg_graph_neighbors"]
