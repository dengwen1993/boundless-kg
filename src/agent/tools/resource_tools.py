"""Resource tools — search, view, add.

Activity timeline
-----------------

``kg_add_learning_resources`` emits one ``web_resource_added`` event
per persisted URL so the timeline shows the agent's 「搜索资料」
activity.  ``kg_search_resources`` / ``kg_view_resources`` do NOT
emit — they're read-only.

All file IO is delegated to ``ResourceRepository`` — the tool layer
never touches ``aiofiles``, ``atomic_write_text``, or ``graph_lock``
directly.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from langchain_core.tools import tool

from src.agent.dependencies import (
    get_resource_repo,
    get_resource_service,
    get_search_client,
)
from src.domain.resource.categories import RESOURCE_CATEGORIES
from src.infrastructure.search.preference import ALL_BACKENDS
from src.observability.activity_bus import ActivityKind, get_activity_bus
from src.observability.logged_tool import logged_tool
from src.utils.json_repair import try_parse_json

logger = logging.getLogger(__name__)


@tool
@logged_tool
async def kg_search_resources(
    domain: str,
    query: str,
    node: str = "",
    num_results: int = 10,
    channel: str = "",
) -> str:
    """联网搜索学习资料；返回 JSON 数组。

    Args:
        domain: 领域标识（用于给返回结果打 ``domain`` 标签）。
        query: 自然语言搜索关键词。
        node: 节点名（可选；传入后会写入返回结果的 ``node`` 字段，
            方便后续 ``kg_add_learning_resources`` 直接落盘）。
        num_results: 返回条数，默认 10。
        channel: **临时**指定单一搜索渠道（取值
            ``"duckduck"`` / ``"mmx"`` / ``"bocha"``）。
            * 与 ``kg_set_search_channel`` 不同 —— 这里传 ``channel``
              只对**本次**调用生效，**不会**写入偏好文件。
            * **不走 fallback** —— 所选渠道不可用或搜索失败时直接
              返回空数组，**不会**切换到其他引擎，也不会触发
              quarantine。
            * 留空走默认行为（自适应 fallback 链
              ``DuckDuckGo → mmx → Bocha`` + 自动学习）。

    【注意】本工具**只返回搜索结果 JSON，不会自动落盘**。
    如果用户希望把搜索结果保存到节点，必须再用
    ``kg_add_learning_resources(domain, node, items_json)`` 显式落盘。
    """
    channel = (channel or "").strip().lower()

    if channel:
        # ─── Single-backend path ───
        # User explicitly asked for this channel — don't fall through,
        # don't update preference, don't quarantine on failure.  This
        # mirrors the docstring contract above.
        if channel not in ALL_BACKENDS:
            logger.warning(
                "kg_search_resources: invalid channel %r, returning []",
                channel,
            )
            return json.dumps([], ensure_ascii=False)

        client = get_search_client()
        try:
            results = await client.search_one(
                channel, query, num_results=num_results
            )
        except Exception as e:  # pragma: no cover — defensive net
            logger.exception(
                "kg_search_resources: search_one(%s) raised: %s", channel, e,
            )
            return json.dumps([], ensure_ascii=False)

        if not results:
            logger.info(
                "kg_search_resources: channel %r returned 0 results for %r",
                channel, query,
            )
            return json.dumps([], ensure_ascii=False)

        # Convert raw SearchResult → ResourceItem-shaped dict so the
        # downstream kg_add_learning_resources sees the same JSON shape
        # regardless of which path produced it.
        added_at = datetime.utcnow().isoformat()
        items = [
            {
                "domain": domain,
                "node": node or "",
                "title": r.title,
                "url": r.link,
                "summary": r.snippet,
                "added_at": added_at,
                "category": r.category or "",
            }
            for r in results
        ]
        return json.dumps(items, ensure_ascii=False)

    # ─── Default path — adaptive fallback chain ───
    svc = get_resource_service()
    items = await svc.search(
        domain, query, node=node or None, num_results=num_results
    )
    return json.dumps([i.to_dict() for i in items], ensure_ascii=False)


@tool
@logged_tool
async def kg_view_resources(domain: str, node: str = "") -> str:
    """查看某节点已保存的资料；返回 JSON。

    读取 node-level ``notes/{node}/web_resources/index.json``，
    与 ``kg_add_learning_resources`` 的写入路径和 API 路由保持一致。
    node 留空时扫描该领域所有节点的资料。
    """
    repo = get_resource_repo()
    if node:
        items = await repo.list_node_resources(domain, node)
    else:
        items = await repo.list_all_node_resources(domain)
    return json.dumps(items, ensure_ascii=False)


def _salvage_truncated_array(text: str) -> list | None:
    """Recover the complete leading objects from a truncated JSON array.

    The LLM serializes ``items_json`` as one long string and routinely
    blows the token budget mid-object, yielding e.g.
    ``[{"title": "a", ...}, {"title": "b", "url``.  A plain
    ``json.loads`` throws away all of it — including the 4 items that
    arrived intact.

    Strategy: scan forward tracking brace depth (string-aware, so braces
    inside titles don't confuse it), remember the offset just past each
    top-level object that closed cleanly, then parse the prefix up to
    the last such offset with a synthetic ``]``.

    Returns ``None`` when nothing salvageable is present.
    """
    if not text:
        return None
    start = text.find("[")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    last_complete = -1

    for i in range(start + 1, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0 and ch == "}":
                # A top-level element just closed cleanly.
                last_complete = i + 1

    if last_complete == -1:
        return None

    candidate = text[start:last_complete] + "]"
    parsed = try_parse_json(candidate)
    return parsed if isinstance(parsed, list) else None


@tool
@logged_tool
async def kg_add_learning_resources(domain: str, node: str, items_json: str) -> str:
    """把搜索结果批量落盘到节点级index.json。items_json 是 JSON 数组，
    每个 item 至少含 title/url/summary（兼容旧字段 link/snippet）；
    domain/node/added_at 由本函数自动补齐。

    【批量上限】单次调用**最多落盘 5 条**，超出部分会被忽略。
    请分多次调用，不要一次拼接一个很长的 items_json —— 过长的字符串
    会被模型截断成非法 JSON。summary 字段请控制在 100 字以内。

    【重要】每个 item **必须**带 ``category`` 字段，取值限定为以下之一：
    论文 / 视频 / 课程 / 代码 / 文档 / 教程 / 书籍 / 网页 / 其他
    （完整列表见 src/domain/resource/categories.py 的 RESOURCE_CATEGORIES）。
    请根据 URL 域名、标题、摘要综合判断：
      - arxiv.org / *.pdf / 含「论文/paper」关键词 → 论文
      - youtube.com / bilibili.com / youtu.be / v.qq.com → 视频
      - udemy / coursera / 慕课网 / icourse163 → 课程
      - github.com / gitee.com / gitlab.com → 代码
      - 官方文档 / docs. / developer. / api. → 文档
      - 含「教程/tutorial/入门/实战」→ 教程
      - 含「书籍/book/出版」→ 书籍
      - 通用博客/资讯 → 网页
    如果实在无法判断，回退为「网页」。"""
    # ── JSON 解析容错 ──
    # 三级降级：直接解析 → 修复常见 LLM 语法错误 → 抢救被截断数组的完整前缀。
    salvage_note = ""
    raw = try_parse_json(items_json)
    if raw is None:
        raw = _salvage_truncated_array(items_json)
        if raw is None:
            logger.error(
                "items_json 解析失败且无法抢救 (length=%d, domain=%s, node=%s)",
                len(items_json),
                domain,
                node,
            )
            return (
                f"❌ items_json 无法解析（长度={len(items_json)}）。\n"
                f"最可能的原因：JSON 字符串过长被模型截断。\n"
                f"请每次只落盘 ≤5 条，分多次调用本工具。"
            )
        logger.warning(
            "items_json 被截断，已抢救 %d 条完整条目 (length=%d, domain=%s, node=%s)",
            len(raw),
            len(items_json),
            domain,
            node,
        )
        salvage_note = (
            f"\n⚠️  items_json 疑似被截断，已抢救出 {len(raw)} 条完整条目并落盘；"
            f"其余条目请分批（每次 ≤5 条）重新提交。"
        )

    if not isinstance(raw, list):
        return f"❌ items_json 必须是 JSON 数组，实际类型：{type(raw).__name__}"

    # ── 落盘（委托给 Repository）──
    repo = get_resource_repo()
    MAX_BATCH = 5
    total_input = len(raw)
    added_count, newly_added = await repo.add_resources_batch(
        domain, node, raw, max_batch=MAX_BATCH
    )
    truncated_count = max(0, total_input - MAX_BATCH)

    # ── 读取总计（用于反馈）──
    all_items = await repo.list_node_resources(domain, node)
    total_stored = len(all_items)

    # Activity timeline — emit AFTER the write so a duplicate URL (no
    # addition) doesn't pollute the log.  One event per persisted item.
    if newly_added:
        bus = get_activity_bus()
        for item in newly_added:
            await bus.emit(
                ActivityKind.WEB_RESOURCE_ADDED,
                domain=domain,
                node=node,
                title=f"搜索了资料「{item['title']}」",
                source="agent",
                ref=f"web_resources#{item['url']}",
                extra={"url": item["url"], "category": item["category"]},
            )

    msg = f"✅ 已落盘 {added_count} 条资料到节点 '{node}' 下（总计 {total_stored} 条）"
    if truncated_count:
        msg += f"\n⚠️  原始列表超 {MAX_BATCH} 条上限，已截断后 {MAX_BATCH} 条，剩余 {truncated_count} 条请分批落盘。"
    return msg + salvage_note


__all__ = [
    "kg_search_resources",
    "kg_view_resources",
    "kg_add_learning_resources",
]
