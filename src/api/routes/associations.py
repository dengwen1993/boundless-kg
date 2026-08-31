"""Associations API — read / derive ``associations.json``.

API contract (matches the Vue frontend's ``api/index.ts``):

  GET    /api/associations/{domain}             → raw associations.json
  GET    /api/associations/{domain}/concepts    → concepts dict
  GET    /api/associations/{domain}/resources   → resources dict
  GET    /api/associations/{domain}/edges       → associations list
  GET    /api/associations/{domain}/neighbors?node=...&hops=&relation=
                                                  → BFS 邻居
  GET    /api/associations/{domain}/statistics  → stats dict
  POST   /api/associations/{domain}/sync        → 手动触发全量派生
  POST   /api/associations/{domain}/sync-node   → 手动触发单节点派生
  POST   /api/associations/{domain}/flush-llm   → 立即 flush LLM buffer
  DELETE /api/associations/{domain}             → 清空（谨慎）

派生方向：所有 mutation 都由 ``GraphSyncOrchestrator`` 完成；本路由**不**
直接写 ``associations.json``。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.agent import dependencies as agent_deps
from src.config.settings import get_associations_source
from src.observability.activity_bus import ActivityKind, get_activity_bus
from src.domain.graph.association import (
    Association,
    DEFAULT_INTENSITY_BY_RELATION,
    EdgeIntensity,
    RelationType,
)
from src.infrastructure.graph_store.client import GraphStoreClient
from src.infrastructure.repository.association_repo import AssociationRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["associations"])


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------


class SyncNodeReq(BaseModel):
    node: str
    enqueue_llm: bool = True


class ManualAssociationReq(BaseModel):
    """Payload for manually creating one association edge.

    The user picks source / target nodes from the concept set and selects
    a relation type; intensity and weight fall back to sensible defaults
    based on the relation.
    """

    source: str = Field(..., min_length=1, description="源节点名（概念节点）")
    target: str = Field(..., min_length=1, description="目标节点名（概念节点）")
    relation: str = Field(..., description="RelationType 之一；非法则降级为 related_to")
    weight: float = Field(default=0.8, ge=0.0, le=1.0)
    intensity: Optional[str] = Field(default=None, description="HARD / SOFT / STRUCTURAL")
    evidence: str = Field(default="")


# ----------------------------------------------------------------------
# Source resolution helper
# ----------------------------------------------------------------------


async def _resolve_source() -> str:
    """Return the active associations source: ``"falkordb"`` / ``"json"``.

    Honours the :envvar:`KG_ASSOCIATIONS_SOURCE` flag. ``"auto"`` (the
    default) means: use FalkorDB when it's enabled and connected,
    otherwise fall back to the ``associations.json`` file.
    """
    flag = get_associations_source()
    if flag == "falkordb":
        return "falkordb"
    if flag == "json":
        return "json"
    # auto: probe FalkorDB
    store = agent_deps.get_graph_store()
    try:
        if await store.ensure_available():
            return "falkordb"
    except Exception as e:
        logger.debug("FalkorDB availability probe failed: %s", e)
    return "json"


async def _import_to_graph_store(domain: str) -> dict[str, Any]:
    """Mirror the freshly-derived ``associations.json`` into FalkorDB.

    Returns a ``{"skipped": ...}`` marker instead of raising when the graph
    store is unreachable, so derivation still succeeds without it.
    """
    store: GraphStoreClient = agent_deps.get_graph_store()
    if not await store.ensure_available():
        return {"skipped": "graph store unavailable"}
    repo: AssociationRepository = agent_deps.get_association_repo()
    data = await repo.read_raw(domain)
    return await asyncio.to_thread(store.import_association_graph, domain, data)


async def _read_graph_via_source(domain: str) -> dict[str, Any]:
    """Return the AssociationGraph-shaped dict for *domain*.

    - Preferred source: FalkorDB (via :meth:`GraphStoreClient.export_graph`).
    - Fallback source: ``associations.json`` via
      :meth:`AssociationRepository.read_raw`.
    """
    if await _resolve_source() == "falkordb":
        store: GraphStoreClient = agent_deps.get_graph_store()
        return await asyncio.to_thread(store.export_graph, domain)
    repo: AssociationRepository = agent_deps.get_association_repo()
    return await repo.read_raw(domain)


# ----------------------------------------------------------------------
# 工厂
# ----------------------------------------------------------------------


def _build_orchestrator(domain: str):
    """按领域构造 GraphSyncOrchestrator。

    同步函数（route handler 在 async 上下文里 await）。
    如果 buffer 未初始化，则会同步创建一个新的——通常 lifespan 会预创建，
    但手动调用时（如 CLI / 测试）这里兜底。
    """
    from src.application.graph_sync_orchestrator import GraphSyncOrchestrator
    from src.api.routes._buffer_singleton import (
        get_buffer_for_domain,
        _default_flush_cb,
    )

    graph_repo = agent_deps.get_graph_repo()
    note_repo = agent_deps.get_note_repo()
    resource_repo = agent_deps.get_resource_repo()
    plan_repo = agent_deps.get_plan_repo()
    assoc_repo = agent_deps.get_association_repo()

    # 优先复用 lifespan 创建的 buffer；否则兜底创建一个
    buffer = get_buffer_for_domain(domain)
    if buffer is None:
        assoc_svc = agent_deps.get_association_service()
        from src.application.pending_nodes_buffer import PendingNodesBuffer
        flush_cb = _default_flush_cb(domain, assoc_svc)
        buffer = PendingNodesBuffer(flush_cb)

    return GraphSyncOrchestrator(
        domain,
        graph_repo=graph_repo,
        note_repo=note_repo,
        resource_repo=resource_repo,
        plan_repo=plan_repo,
        assoc_repo=assoc_repo,
        buffer=buffer,
    )


# ----------------------------------------------------------------------
# 读
# ----------------------------------------------------------------------


@router.get("/associations/{domain}")
async def get_associations(domain: str):
    """返回关联图完整内容（前端可视化用）。

    优先从 FalkorDB 图存储读；当 :envvar:`KG_ASSOCIATIONS_SOURCE=json`
    或 FalkorDB 不可用时降级到 ``associations.json`` 文件。
    """
    return await _read_graph_via_source(domain)


@router.get("/associations/{domain}/concepts")
async def get_concepts(domain: str):
    if await _resolve_source() == "falkordb":
        store: GraphStoreClient = agent_deps.get_graph_store()
        full = await asyncio.to_thread(store.export_graph, domain)
        return {"domain": full["domain"], "concepts": full["concepts"]}
    repo: AssociationRepository = agent_deps.get_association_repo()
    graph = await repo.read(domain)
    return {
        "domain": graph.domain,
        "concepts": {k: v.model_dump(mode="json") for k, v in graph.concepts.items()},
    }


@router.get("/associations/{domain}/resources")
async def get_resources(domain: str):
    if await _resolve_source() == "falkordb":
        store: GraphStoreClient = agent_deps.get_graph_store()
        full = await asyncio.to_thread(store.export_graph, domain)
        return {"domain": full["domain"], "resources": full["resources"]}
    repo: AssociationRepository = agent_deps.get_association_repo()
    graph = await repo.read(domain)
    return {
        "domain": graph.domain,
        "resources": {k: v.model_dump(mode="json") for k, v in graph.resources.items()},
    }


@router.get("/associations/{domain}/edges")
async def get_edges(domain: str):
    if await _resolve_source() == "falkordb":
        store: GraphStoreClient = agent_deps.get_graph_store()
        full = await asyncio.to_thread(store.export_graph, domain)
        return {
            "domain": full["domain"],
            "associations": full["associations"],
            "total": len(full["associations"]),
        }
    repo: AssociationRepository = agent_deps.get_association_repo()
    graph = await repo.read(domain)
    return {
        "domain": graph.domain,
        "associations": [a.model_dump(mode="json") for a in graph.associations],
        "total": len(graph.associations),
    }


@router.get("/associations/{domain}/neighbors")
async def get_neighbors(
    domain: str,
    node: str = Query(..., description="节点名；可包含 /"),
    hops: int = Query(1, ge=1, le=5),
    relation: str = Query("", description="RelationType 过滤；空=全部"),
    direction: str = Query("any", pattern="^(any|out|in)$"),
):
    rel_enum: RelationType | None = None
    if relation:
        try:
            rel_enum = RelationType(relation)
        except ValueError:
            raise HTTPException(400, f"未知 relation: {relation}")

    if await _resolve_source() == "falkordb":
        store: GraphStoreClient = agent_deps.get_graph_store()
        node_id_candidates = [
            f"concept:{node}",
            f"note:{node}",
            f"resource:{node}",
            f"plan:{node}",
            node,
        ]
        # Pick the first id that actually exists in the graph.
        graph_obj = store._get_graph(domain)  # internal helper; OK for read
        found_id = node
        try:
            if graph_obj is not None:
                result = graph_obj.query(
                    "MATCH (n) WHERE n.id IN $ids OR n.name = $name "
                    "RETURN n.id AS id LIMIT 1",
                    params={"ids": node_id_candidates, "name": node},
                )
                rows = (
                    result.result_set if hasattr(result, "result_set") else []
                )
                if rows and rows[0] and rows[0][0]:
                    found_id = rows[0][0]
        except Exception:
            pass

        # FalkorDB N-hop neighbours (variable-length paths aren't
        # unbounded; the client already loops hop-by-hop).
        neighbours = await asyncio.to_thread(
            store.neighbors, domain, found_id, hops=hops,
        )
        if rel_enum is not None:
            wanted = rel_enum.value
            neighbours = [
                n for n in neighbours if str(n.get("relation", "")).lower() == wanted
            ]
        return {
            "domain": domain,
            "node": node,
            "hops": hops,
            "neighbors": [
                {
                    "name": n.get("name") or n.get("neighbor_id") or "",
                    "relation": str(n.get("relation", "")).lower(),
                    "intensity": "SOFT",
                    "weight": float(n.get("weight", 1.0))
                    if n.get("weight") is not None
                    else 1.0,
                    "hops": int(n.get("hops", 1)),
                }
                for n in neighbours
            ],
        }

    repo: AssociationRepository = agent_deps.get_association_repo()
    graph = await repo.read(domain)
    results = graph.neighbors(
        node,
        relation=rel_enum,
        max_hops=hops,
        direction=direction,
    )
    return {
        "domain": graph.domain,
        "node": node,
        "hops": hops,
        "neighbors": [
            {
                "name": name,
                "relation": assoc.relation.value,
                "intensity": assoc.intensity.value,
                "weight": assoc.weight,
                "hops": h,
            }
            for name, assoc, h in results
        ],
    }


@router.get("/associations/{domain}/statistics")
async def get_statistics(domain: str):
    if await _resolve_source() == "falkordb":
        store: GraphStoreClient = agent_deps.get_graph_store()
        return await asyncio.to_thread(store.statistics, domain)
    repo: AssociationRepository = agent_deps.get_association_repo()
    graph = await repo.read(domain)
    return graph.statistics()


# ----------------------------------------------------------------------
# 写（触发派生）
# ----------------------------------------------------------------------


@router.post("/associations/{domain}/sync")
async def sync_full(domain: str):
    """全量派生。"""
    orch = _build_orchestrator(domain)
    result = await orch.sync_full()
    return {**result, "falkordb": await _import_to_graph_store(domain)}


@router.post("/associations/{domain}/sync-node")
async def sync_node(domain: str, req: SyncNodeReq):
    """单节点增量派生。"""
    orch = _build_orchestrator(domain)
    return await orch.sync_for_node(req.node, enqueue_llm=req.enqueue_llm)


@router.post("/associations/{domain}/flush-llm")
async def flush_llm(domain: str):
    """立即 flush PendingNodesBuffer（不等阈值/时间）。"""
    from src.api.routes._buffer_singleton import get_buffer_for_domain

    buf = get_buffer_for_domain(domain)
    if buf is None:
        return {"flushed": 0, "message": "no buffer"}
    return await buf.force_flush()


@router.post("/associations/{domain}/extract-now")
async def extract_now(domain: str):
    """立即跑一次全量 LLM 抽取（不依赖 buffer 触发）。"""
    from src.api.routes._buffer_singleton import get_buffer_for_domain

    buf = get_buffer_for_domain(domain)
    if buf is None:
        raise HTTPException(400, "buffer 未初始化")
    # 强制把 buffer 里的内容 flush 后再触发一次 domain 全量抽取
    # 这里只 flush buffer；domain 全量抽取不在本路由做
    return await buf.force_flush()


@router.delete("/associations/{domain}")
async def clear_associations(domain: str):
    """清空整个关联图（含 FalkorDB）。"""
    repo: AssociationRepository = agent_deps.get_association_repo()
    await repo.clear(domain)
    # Mirror the wipe into FalkorDB so the two stores stay in sync.
    # Without this the next GET would resurrect the graph because the
    # FalkorDB mirror still holds every node/edge.
    store: GraphStoreClient = agent_deps.get_graph_store()
    falkordb_cleared = False
    if await store.ensure_available():
        falkordb_cleared = await asyncio.to_thread(store.clear_domain, domain)
    return {
        "ok": True,
        "message": "关联图已清空（associations.json + FalkorDB）",
        "falkordb_cleared": falkordb_cleared,
    }


# ----------------------------------------------------------------------
# 手动增删边（UI 右键）
# ----------------------------------------------------------------------


def _coerce_relation(raw: str) -> RelationType:
    """解析 relation 字符串；非法值降级为 RELATED_TO。

    与 ``_req_to_relation`` 的策略一致：宁可兜底也不要 400，让 UI
    可以无缝支持 enum 演进过程中的过渡值。
    """
    try:
        return RelationType(raw)
    except ValueError:
        return RelationType.RELATED_TO


def _coerce_intensity(raw: Optional[str], fallback: EdgeIntensity) -> EdgeIntensity:
    if raw:
        try:
            return EdgeIntensity(raw.upper())
        except ValueError:
            return fallback
    return fallback


@router.post("/associations/{domain}/manual")
async def add_manual_association(domain: str, req: ManualAssociationReq):
    """手动添加一条关联边（UI 右键菜单）。

    会自动同步到 FalkorDB（如果可用）；不依赖 sync 流程。
    """
    # 必须引用真实存在的概念节点；has_* 关系另说（需要 resource id），本期不支持手动
    rel = _coerce_relation(req.relation)
    if rel in (RelationType.HAS_NOTE, RelationType.HAS_RESOURCE, RelationType.HAS_PLAN,
               RelationType.CITES, RelationType.REFERENCES):
        raise HTTPException(
            400,
            "手动添加仅支持概念 ↔ 概念 关系；has_*/cites/references 由系统派生",
        )

    src = req.source.strip()
    tgt = req.target.strip()
    if not src or not tgt:
        raise HTTPException(400, "source / target 不能为空")
    if src == tgt:
        raise HTTPException(400, "source 与 target 不能相同")

    # 节点存在性检查：与读路径一致，优先 FalkorDB；否则 associations.json
    # —— UI 看到的所有节点都来自这里。如果只看 associations.json 会漏掉
    # FalkorDB-only 的概念（用户当前碰到的就是这种情况）。
    source_data = await _read_graph_via_source(domain)
    concept_keys = set((source_data.get("concepts") or {}).keys())
    if src not in concept_keys:
        raise HTTPException(404, f"源节点「{src}」不存在")
    if tgt not in concept_keys:
        raise HTTPException(404, f"目标节点「{tgt}」不存在")

    repo: AssociationRepository = agent_deps.get_association_repo()

    intensity = _coerce_intensity(
        req.intensity, DEFAULT_INTENSITY_BY_RELATION.get(rel, EdgeIntensity.SOFT)
    )

    assoc = Association(
        source=src,
        target=tgt,
        relation=rel,
        weight=req.weight,
        intensity=intensity,
        evidence=req.evidence or "manually added",
        created_by="system",
    )
    new_graph = await repo.add_association(domain, assoc, dedupe=True)

    # Activity timeline — manual edge added via UI right-click. Emit
    # only after the repo write succeeded, so a rejected add (duplicate,
    # missing node) never pollutes the log.
    await get_activity_bus().emit(
        ActivityKind.ASSOCIATION_CREATED,
        domain=domain,
        node=src,
        title=f"手动新增了关联 {src} --{rel.value}--> {tgt}",
        source="manual",
        ref=f"edge:{src}->{tgt}",
        extra={"source": src, "target": tgt, "relation": rel.value},
    )

    # Mirror into FalkorDB so the visualization reflects the change without
    # requiring a full sync. Failure here is non-fatal — the next
    # ``sync_full`` will reconcile.
    falkordb_ok = None
    store: GraphStoreClient = agent_deps.get_graph_store()
    if await store.ensure_available():
        falkordb_ok = await asyncio.to_thread(
            store.add_edge_any,
            domain,
            source=f"concept:{src}",
            target=f"concept:{tgt}",
            relation=rel.value,
            weight=assoc.weight,
            intensity=intensity.value,
            evidence=assoc.evidence,
            created_by="system",
        )

    return {
        "ok": True,
        "message": f"已添加关联 {src} --{rel.value}--> {tgt}",
        "total": len(new_graph.associations),
        "falkordb_synced": falkordb_ok,
    }


@router.delete("/associations/{domain}/manual")
async def delete_manual_association(
    domain: str,
    source: str = Query(..., description="源节点名"),
    target: str = Query(..., description="目标节点名"),
    relation: str = Query(..., description="RelationType 之一"),
):
    """手动删除一条关联边（UI 右键菜单）。"""
    repo: AssociationRepository = agent_deps.get_association_repo()
    rel = _coerce_relation(relation)

    new_graph = await repo.delete_association(domain, source.strip(), target.strip(), rel.value)

    # Activity timeline — manual edge removed via UI right-click.
    await get_activity_bus().emit(
        ActivityKind.ASSOCIATION_DELETED,
        domain=domain,
        node=source.strip(),
        title=f"手动删除了关联 {source.strip()} --{rel.value}--> {target.strip()}",
        source="manual",
        ref=f"edge:{source.strip()}->{target.strip()}",
        extra={"source": source.strip(), "target": target.strip(), "relation": rel.value},
    )

    # Mirror the removal into FalkorDB
    falkordb_ok = None
    store: GraphStoreClient = agent_deps.get_graph_store()
    if await store.ensure_available():
        falkordb_ok = await asyncio.to_thread(
            store.delete_edge,
            domain,
            source=f"concept:{source.strip()}",
            target=f"concept:{target.strip()}",
            relation=rel.value,
        )

    return {
        "ok": True,
        "message": f"已删除关联 {source} --{rel.value}--> {target}",
        "total": len(new_graph.associations),
        "falkordb_synced": falkordb_ok,
    }


# ----------------------------------------------------------------------
# 删除概念节点（UI 右键）
# ----------------------------------------------------------------------


@router.delete("/associations/{domain}/concept")
async def delete_concept_endpoint(domain: str, name: str = Query(...)):
    """从关联图 + 主图谱删除一个概念节点。

    处理两种情况：
      1. 节点**只在关联图**（派生残留 / 大纲里找不到的「幽灵」节点）
         —— 只清理 associations.json 即可。
      2. 节点**同时存在于主图谱** —— 同步删除主图谱 + 关联图 + FalkorDB，
         否则下次 sync 又会把它派生回来。

    允许删除领域根节点（前端会弹二次确认）；领域本身（目录 + 配置文件）
    不受影响，只有同名的概念节点被移除。
    """
    if not name.strip():
        raise HTTPException(400, "节点名不能为空")

    assoc_repo: AssociationRepository = agent_deps.get_association_repo()
    graph_repo = agent_deps.get_graph_repo()
    store: GraphStoreClient = agent_deps.get_graph_store()

    # 1. Always remove from associations.json
    await assoc_repo.delete_concept(domain, name)

    # 2. Try to remove from main knowledge_graph.json if it exists there.
    #    注意 graph.find_node 有 bug（详见 graph_repo.py）—— 但这里用
    #    list 遍历精确判断，避开那个 bug；包括领域根节点也允许删除。
    main_deleted = False
    try:
        graph = await graph_repo.read_graph(domain)
        exists_in_main = any(n.name == name for n in graph.nodes)
        if exists_in_main:
            await graph_repo.delete_node(domain, name)
            main_deleted = True
    except Exception as e:
        logger.warning("delete_concept_endpoint: main graph delete skipped: %s", e)

    # 3. Mirror to FalkorDB
    falkordb_ok = None
    if await store.ensure_available():
        falkordb_ok = await asyncio.to_thread(store.delete_concept_by_name, domain, name)

    return {
        "ok": True,
        "message": f"已删除节点「{name}」",
        "main_deleted": main_deleted,
        "falkordb_synced": falkordb_ok,
    }


__all__ = ["router"]