"""Association graph tools (LangChain @tool wrappers).

LLM-facing 接口，让 Agent 能查询 / 触发派生 ``associations.json``
以及手动维护关联边。
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from langchain_core.tools import tool

from src.agent import dependencies as agent_deps
from src.agent.dependencies import get_association_repo
from src.domain.graph.association import (
    Association,
    DEFAULT_INTENSITY_BY_RELATION,
    EdgeIntensity,
    RelationType,
)
from src.observability.activity_bus import ActivityKind, get_activity_bus
from src.observability.logged_tool import logged_tool


@tool
@logged_tool
async def kg_view_associations(domain: str) -> str:
    """查看某领域的完整关联图（JSON 字符串）。

    返回::

        {
          "domain": "...",
          "concepts": {"Transformer": {...}, ...},
          "resources": {"note:Transformer": {...}, ...},
          "associations": [{"source":..., "target":..., "relation":..., ...}],
          "metadata": {"derived_events": {...}, ...}
        }

    包含：

      - ConceptNode：思维导图节点（含 level / description）
      - ResourceNode：note / resource / plan
      - 关联边：PART_OF（结构性）+ PREREQUISITE_OF / SIMILAR_TO / 等（语义性）
      - metadata.derived_events：event_id → 派生完成时间

    ⚠️ 写关联数据请走 :func:`kg_add_edge` / :func:`kg_delete_edge` 或
    ``kg_shell_exec`` + python，**不要**用 ``read_file`` / ``edit_file``
    / ``write_file`` 直接修改 ``associations.json``——这些工具走 deepagents
    SDK 的虚拟 FS，与后端 kg_engine 操作的真实磁盘不一致
    （BUG-2026-08-20-001 / BUG-2026-08-19-003）。
    """
    repo = get_association_repo()
    return json.dumps(await repo.read_raw(domain), ensure_ascii=False)


@tool
@logged_tool
async def kg_query_neighbors(
    domain: str,
    node: str,
    hops: int = 1,
    relation: str = "",
    direction: str = "any",
) -> str:
    """查询节点的 N 跳邻居。

    Args:
        domain: 领域名
        node: 起始节点名
        hops: 跳数（1~5）
        relation: 关系过滤（"prerequisite_of" / "similar_to" / ...）；空=全部
        direction: ``any`` / ``out`` / ``in``

    ⚠️ **无持久缓存**——本工具每次调用都打磁盘
    ``associations.json``（见 :class:`AssociationRepository`）。如果返回
    数据看起来"陈旧"，请优先检查：是否用 ``read_file`` / ``edit_file``
    改过 JSON（deepagents 虚拟 FS 与真实磁盘不同步）；不要尝试加
    ``kg_reload_cache`` 之类的工具——缓存不存在，重读即新数据。
    """
    repo = get_association_repo()
    graph = await repo.read(domain)

    rel_enum: RelationType | None = None
    if relation:
        try:
            rel_enum = RelationType(relation)
        except ValueError:
            return json.dumps(
                {"ok": False, "error": f"未知 relation: {relation!r}"},
                ensure_ascii=False,
            )

    results = graph.neighbors(
        node,
        relation=rel_enum,
        max_hops=min(max(hops, 1), 5),
        direction=direction if direction in ("any", "out", "in") else "any",
    )
    return json.dumps(
        {
            "ok": True,
            "domain": domain,
            "node": node,
            "hops": hops,
            "neighbors": [
                {
                    "name": name,
                    "relation": assoc.relation.value,
                    "intensity": assoc.intensity.value,
                    "weight": assoc.weight,
                    "hops": h,
                    "evidence": assoc.evidence,
                }
                for name, assoc, h in results
            ],
        },
        ensure_ascii=False,
    )


@tool
@logged_tool
async def kg_sync_associations(domain: str) -> str:
    """触发某个领域的全量派生（重建 associations.json）。

    一般不需要主动调用——EventBus 自动驱动 DerivationSubscriber。本工具
    用于 CLI / 调试时手动触发。
    """
    from src.api.routes.associations import _build_orchestrator

    orch = _build_orchestrator(domain)
    stats = await orch.sync_full()
    return json.dumps(
        {"ok": True, "domain": domain, **stats},
        ensure_ascii=False,
    )


@tool
@logged_tool
async def kg_sync_node_associations(
    domain: str,
    node: str,
    enqueue_llm: bool = True,
) -> str:
    """单节点增量派生（与 NODE_CREATED 等事件等效）。"""
    from src.api.routes.associations import _build_orchestrator

    orch = _build_orchestrator(domain)
    stats = await orch.sync_for_node(node, enqueue_llm=enqueue_llm)
    return json.dumps(
        {"ok": True, "domain": domain, "node": node, **stats},
        ensure_ascii=False,
    )


# ----------------------------------------------------------------------
# 手动边 CRUD（写关联数据请走这两个工具，不要直接编辑 JSON）
# ----------------------------------------------------------------------


def _coerce_relation(raw: str) -> RelationType:
    """解析 relation 字符串；非法值降级为 RELATED_TO。

    与 ``associations.py`` 的 ``_coerce_relation`` 策略一致——宁可兜底也
    不要 400，方便 LLM 拼写偏差时仍能继续。
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


async def _read_concept_keys(domain: str) -> set[str]:
    """读 concept 节点名集合（优先 FalkorDB，回退 associations.json）。

    与 :func:`src.api.routes.associations._read_graph_via_source` 行为一致——
    只看 associations.json 会漏掉 FalkorDB-only 的概念节点。
    """
    from src.api.routes.associations import _read_graph_via_source

    data = await _read_graph_via_source(domain)
    return set((data.get("concepts") or {}).keys())


@tool
@logged_tool
async def kg_add_edge(
    domain: str,
    source: str,
    target: str,
    relation: str = "related_to",
    weight: float = 0.8,
    intensity: str = "",
    evidence: str = "manually added by agent",
) -> str:
    """手动新增一条概念 ↔ 概念 关联边。

    这是写入关联边**唯一推荐**的入口——直接改 ``associations.json``
    会绕过 FalkorDB 镜像与活动总线。

    Args:
        domain: 领域名
        source: 源概念节点名（必须已存在）
        target: 目标概念节点名（必须已存在）
        relation: RelationType 之一；非法值降级为 ``related_to``
            可选：``part_of`` / ``prerequisite_of`` / ``enables`` /
            ``similar_to`` / ``contrasts_with`` / ``applies_to`` /
            ``derived_from`` / ``related_to``
        weight: 边权重，0~1
        intensity: ``HARD`` / ``SOFT`` / ``STRUCTURAL``；空 = 按 relation 默认值
        evidence: 边的依据说明（写进 associations.json 供溯源）

    Returns:
        JSON ``{"ok": true, "message": "...", "total": N}``；
        节点不存在或 source==target 返回 ``{"ok": false, "error": ...}``。

    不支持的关系类型：``has_note`` / ``has_resource`` / ``has_plan`` /
    ``cites`` / ``references`` —— 这些由 sync 派生，不允许手动加。
    """
    src = source.strip()
    tgt = target.strip()
    if not src or not tgt:
        return json.dumps(
            {"ok": False, "error": "source / target 不能为空"}, ensure_ascii=False
        )
    if src == tgt:
        return json.dumps(
            {"ok": False, "error": "source 与 target 不能相同"}, ensure_ascii=False
        )

    rel = _coerce_relation(relation)
    if rel in (
        RelationType.HAS_NOTE,
        RelationType.HAS_RESOURCE,
        RelationType.HAS_PLAN,
        RelationType.CITES,
        RelationType.REFERENCES,
    ):
        return json.dumps(
            {
                "ok": False,
                "error": (
                    "手动添加仅支持概念 ↔ 概念 关系；"
                    "has_*/cites/references 由系统派生"
                ),
            },
            ensure_ascii=False,
        )

    # 节点存在性检查（与 API 路由 manual 端点一致：优先 FalkorDB）
    try:
        concept_keys = await _read_concept_keys(domain)
    except Exception as e:
        return json.dumps(
            {"ok": False, "error": f"读取概念节点失败: {e}"}, ensure_ascii=False
        )
    if src not in concept_keys:
        return json.dumps(
            {"ok": False, "error": f"源节点「{src}」不存在"}, ensure_ascii=False
        )
    if tgt not in concept_keys:
        return json.dumps(
            {"ok": False, "error": f"目标节点「{tgt}」不存在"}, ensure_ascii=False
        )

    repo = get_association_repo()
    intensity_enum = _coerce_intensity(
        intensity, DEFAULT_INTENSITY_BY_RELATION.get(rel, EdgeIntensity.SOFT)
    )

    assoc = Association(
        source=src,
        target=tgt,
        relation=rel,
        weight=max(0.0, min(1.0, weight)),
        intensity=intensity_enum,
        evidence=evidence or "manually added by agent",
        created_by="system",
    )
    new_graph = await repo.add_association(domain, assoc, dedupe=True)

    # 活动总线 + FalkorDB 镜像（与 API 路由 manual 一致）
    try:
        await get_activity_bus().emit(
            ActivityKind.ASSOCIATION_CREATED,
            domain=domain,
            node=src,
            title=f"手动新增了关联 {src} --{rel.value}--> {tgt}",
            source="manual",
            ref=f"edge:{src}->{tgt}",
            extra={"source": src, "target": tgt, "relation": rel.value},
        )
    except Exception as e:  # 活动总线失败不阻塞主流程
        pass

    falkordb_ok: Optional[bool] = None
    try:
        store = agent_deps.get_graph_store()
        if await store.ensure_available():
            falkordb_ok = await asyncio.to_thread(
                store.add_edge_any,
                domain,
                source=f"concept:{src}",
                target=f"concept:{tgt}",
                relation=rel.value,
                weight=assoc.weight,
                intensity=intensity_enum.value,
                evidence=assoc.evidence,
                created_by="system",
            )
    except Exception:
        falkordb_ok = False

    return json.dumps(
        {
            "ok": True,
            "message": f"已添加关联 {src} --{rel.value}--> {tgt}",
            "total": len(new_graph.associations),
            "falkordb_synced": falkordb_ok,
        },
        ensure_ascii=False,
    )


@tool
@logged_tool
async def kg_delete_edge(
    domain: str,
    source: str,
    target: str,
    relation: str,
) -> str:
    """手动删除一条关联边。

    Args:
        domain: 领域名
        source: 源概念节点名
        target: 目标概念节点名
        relation: RelationType 之一；非法值降级为 ``related_to``

    Returns:
        JSON ``{"ok": true, "message": "...", "total": N, "removed": bool}``。
        边不存在时 ``removed=false`` 但 ``ok=true``（幂等）。
    """
    src = source.strip()
    tgt = target.strip()
    if not src or not tgt:
        return json.dumps(
            {"ok": False, "error": "source / target 不能为空"}, ensure_ascii=False
        )

    rel = _coerce_relation(relation)
    repo = get_association_repo()

    # 计算 before 数，便于报告"是否真的删了一条"
    before = await repo.read(domain)
    before_count = sum(
        1
        for a in before.associations
        if a.source == src and a.target == tgt and a.relation == rel
    )

    new_graph = await repo.delete_association(domain, src, tgt, rel.value)
    removed = len(new_graph.associations) < len(before.associations)

    try:
        await get_activity_bus().emit(
            ActivityKind.ASSOCIATION_DELETED,
            domain=domain,
            node=src,
            title=f"手动删除了关联 {src} --{rel.value}--> {tgt}",
            source="manual",
            ref=f"edge:{src}->{tgt}",
            extra={"source": src, "target": tgt, "relation": rel.value},
        )
    except Exception:
        pass

    falkordb_ok: Optional[bool] = None
    try:
        store = agent_deps.get_graph_store()
        if await store.ensure_available():
            falkordb_ok = await asyncio.to_thread(
                store.delete_edge,
                domain,
                source=f"concept:{src}",
                target=f"concept:{tgt}",
                relation=rel.value,
            )
    except Exception:
        falkordb_ok = False

    return json.dumps(
        {
            "ok": True,
            "message": (
                f"已删除关联 {src} --{rel.value}--> {tgt}"
                if removed
                else f"未找到关联 {src} --{rel.value}--> {tgt}（幂等）"
            ),
            "removed": removed,
            "matched_before": before_count,
            "total": len(new_graph.associations),
            "falkordb_synced": falkordb_ok,
        },
        ensure_ascii=False,
    )


__all__ = [
    "kg_view_associations",
    "kg_query_neighbors",
    "kg_sync_associations",
    "kg_sync_node_associations",
    "kg_add_edge",
    "kg_delete_edge",
]
