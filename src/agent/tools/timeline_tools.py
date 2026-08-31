"""Timeline tool — view aggregated activity stream."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from src.agent.dependencies import get_timeline_service
from src.observability.logged_tool import logged_tool


@tool
@logged_tool
async def kg_view_timeline(
    domain: str,
    date: str = "",
    node: str = "",
    activity_type: str = "",
) -> str:
    """查看领域活动时间线（计划+资料+笔记），按时间倒序。

    可选过滤：
      date         — 某天 YYYY-MM-DD
      node         — 节点名
      activity_type — plan / resource / note
    """
    svc = get_timeline_service()
    items = await svc.feed(
        domain,
        date=date or None,
        node=node or None,
        type_=activity_type or None,
    )
    return json.dumps(
        {"domain": domain, "items": items, "count": len(items)},
        ensure_ascii=False,
    )


__all__ = ["kg_view_timeline"]
