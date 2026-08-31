"""Dossier tools — Agent 节点档案工具。

工具集:
  kg_add_dossier_entry    — 添加一条经验条目(Agent 自动 / 用户手动)
  kg_view_dossier         — 查看节点的全部档案
  kg_search_dossier       — 跨节点搜档案(Buckett 衰减 + 老兵加成)
  kg_update_dossier_entry — 更新条目
  kg_remove_dossier_entry — 删除条目
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from src.agent import dependencies as agent_deps
from src.domain.graph.dossier import DossierEntryType
from src.observability.activity_bus import ActivityKind, get_activity_bus

logger = logging.getLogger(__name__)


@tool
async def kg_add_dossier_entry(
    domain: str,
    node: str,
    type: str,
    title: str,
    body: str,
    tags: str = "",
    evidence: str = "",
    score: float = 0.5,
) -> str:
    """往节点的档案里追加一条经验条目。

    用于:Agent 识别到对话中有可复用经验(SOP / 陷阱 / 技巧等)
    时调用;用户也可以手动调。归档后会触发 FalkorDB 同步 + 时间线事件。

    Args:
        domain: 领域名
        node: 节点名(必须存在于 knowledge_graph.json)
        type: 条目类型 — sop / pitfall / tip / term / pattern / link / note
        title: 一句话标题
        body: 正文(支持 markdown,建议 < 500 字)
        tags: 逗号分隔的关键词,例如 "asyncio,cancel,concurrency"
        evidence: 归档依据(用户原话 / Agent 反思),便于后续追溯
        score: 重要性 0~1,默认 0.5

    Returns:
        JSON,形如 {"ok": true, "entry_id": "de_xxxxxxxx"}
    """
    svc = agent_deps.get_dossier_service()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    try:
        entry = await svc.add_entry(
            domain=domain, node=node,
            type=type, title=title, body=body,
            tags=tag_list, evidence=evidence, score=score,
            created_by="agent",
        )
    except ValueError as e:
        return json.dumps(
            {"ok": False, "error": f"参数错误: {e}"},
            ensure_ascii=False,
        )
    except Exception as e:
        logger.warning("kg_add_dossier_entry failed: %s", e)
        return json.dumps(
            {"ok": False, "error": f"归档失败: {e}"},
            ensure_ascii=False,
        )

    # 异步触发 FalkorDB 同步(后台)
    import asyncio
    async def _sync_later():
        try:
            sync_svc = agent_deps.get_graph_sync_service(domain)
            await sync_svc.sync_for_node(node)
        except Exception as e:
            logger.warning("dossier sync_to_falkordb failed: %s", e)

    asyncio.create_task(_sync_later())

    # 发时间线事件
    type_label = entry.type.value
    await get_activity_bus().emit(
        ActivityKind.DOSSIER_ENTRY_ADDED,
        domain=domain, node=node,
        title=f"🤖 学到了 [{type_label}]: {entry.title}",
        source="agent",
        ref=f"entry:{entry.id}",
        extra={"type": type_label, "score": entry.score},
    )

    return json.dumps(
        {
            "ok": True,
            "entry_id": entry.id,
            "node": node,
            "type": type_label,
            "title": entry.title,
            "message": (
                f"✅ 已归档到「{node}」的档案"
                f"({type_label}: {entry.title})"
            ),
        },
        ensure_ascii=False,
    )


@tool
async def kg_view_dossier(domain: str, node: str) -> str:
    """查看一个节点的完整档案(按类型分组)。

    Args:
        domain: 领域名
        node: 节点名

    Returns:
        Markdown 格式的档案文本,按 type 分章节。
    """
    svc = agent_deps.get_dossier_service()
    dossier = await svc.view_dossier(domain, node)
    if not dossier.entries:
        return f"节点「{node}」暂无档案条目。"

    # 按 type 分组
    by_type: dict[str, list] = {}
    for e in dossier.entries:
        by_type.setdefault(e.type.value, []).append(e)

    type_labels = {
        "sop": "📋 SOP",
        "pitfall": "⚠️ 陷阱",
        "tip": "💡 技巧",
        "term": "📖 术语",
        "pattern": "🧩 模式",
        "link": "🔗 链接",
        "note": "📝 备忘",
    }

    lines = [f"# 节点档案:{node}", ""]
    for type_value, entries in by_type.items():
        label = type_labels.get(type_value, type_value)
        lines.append(f"## {label}({len(entries)} 条)")
        for e in entries:
            lines.append(f"### {e.title}")
            lines.append(f"{e.body}")
            meta = []
            if e.tags:
                meta.append(f"tags: {', '.join(e.tags)}")
            if e.use_count:
                meta.append(f"use_count: {e.use_count}")
            if e.score:
                meta.append(f"score: {e.score}")
            meta.append(f"id: {e.id}")
            lines.append(f"_{'; '.join(meta)}_")
            lines.append("")

    return "\n".join(lines)


@tool
async def kg_search_dossier(
    domain: str,
    query: str,
    type_filter: str = "",
    top_k: int = 5,
) -> str:
    """跨节点搜档案条目。

    自动应用时间衰减(半衰期 180 天)+ 老兵加成(use_count 加权)。

    Args:
        domain: 领域名
        query: 搜索关键词(支持 trigger_keywords 高优先级匹配)
        type_filter: 逗号分隔的类型过滤,如 "sop,pitfall"
        top_k: 返回条数,默认 5

    Returns:
        JSON 数组,每条形如
        {"node": "...", "entry_id": "...", "type": "...",
         "title": "...", "body": "...",
         "score": 0.85, "use_count": 3}
    """
    svc = agent_deps.get_dossier_service()
    types = (
        [t.strip() for t in type_filter.split(",") if t.strip()]
        if type_filter else None
    )
    hits = await svc.search(
        domain=domain, query=query,
        top_k=top_k, type_filter=types,
    )
    return json.dumps(
        {
            "ok": True,
            "query": query,
            "total": len(hits),
            "hits": [h.to_dict() for h in hits],
        },
        ensure_ascii=False,
    )


@tool
async def kg_update_dossier_entry(
    domain: str,
    node: str,
    entry_id: str,
    title: str = "",
    body: str = "",
    tags: str = "",
    score: float = -1.0,
) -> str:
    """更新一条档案条目(改标题/正文/标签/重要性)。

    Args:
        domain: 领域名
        node: 节点名
        entry_id: 条目 ID(8 字符短 ID)
        title: 新标题(空字符串 = 不改)
        body: 新正文(空字符串 = 不改)
        tags: 新标签,逗号分隔(空字符串 = 不改)
        score: 新重要性 0~1(< 0 表示不改)
    """
    svc = agent_deps.get_dossier_service()
    updates: dict[str, Any] = {}
    if title:
        updates["title"] = title
    if body:
        updates["body"] = body
    if tags:
        updates["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if score >= 0:
        updates["score"] = max(0.0, min(1.0, score))

    ok = await svc.update_entry(domain, node, entry_id, **updates)
    if not ok:
        return json.dumps(
            {"ok": False, "error": f"条目 {entry_id} 不存在"},
            ensure_ascii=False,
        )
    return json.dumps(
        {"ok": True, "entry_id": entry_id, "updated": list(updates.keys())},
        ensure_ascii=False,
    )


@tool
async def kg_remove_dossier_entry(
    domain: str,
    node: str,
    entry_id: str,
) -> str:
    """删除一条档案条目。

    Args:
        domain: 领域名
        node: 节点名
        entry_id: 条目 ID
    """
    svc = agent_deps.get_dossier_service()
    ok = await svc.remove_entry(domain, node, entry_id)
    if not ok:
        return json.dumps(
            {"ok": False, "error": f"条目 {entry_id} 不存在"},
            ensure_ascii=False,
        )
    return json.dumps(
        {"ok": True, "entry_id": entry_id, "deleted": True},
        ensure_ascii=False,
    )


__all__ = [
    "kg_add_dossier_entry",
    "kg_view_dossier",
    "kg_search_dossier",
    "kg_update_dossier_entry",
    "kg_remove_dossier_entry",
]