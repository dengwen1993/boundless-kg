"""Card management tools — let the agent create / list / view / delete
prompt cards at runtime.

These tools make the card system **self-service**: when the agent discovers
a recurring instruction pattern (e.g. "always format plan steps as
checkboxes"), it can persist that guidance as a card via ``kg_add_card``
instead of hard-coding it into the system prompt. The card is written to
disk as a ``.md`` file, the library is hot-reloaded, and the guidance
takes effect on the next turn where the triggers match — no restart
needed.

Activity timeline
-----------------

``kg_add_card`` emits ``card_created`` / ``card_updated`` (depending on
whether the card file already existed). ``kg_delete_card`` emits
``card_deleted``. Read-only tools (``kg_list_cards``, ``kg_view_card``)
do not emit.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from src.agent.dependencies import get_card_service
from src.observability.activity_bus import ActivityKind, get_activity_bus
from src.observability.logged_tool import logged_tool


@tool
@logged_tool
async def kg_add_card(
    card_id: str,
    title: str,
    body: str,
    triggers: list[str] | None = None,
    applies_to_tools: list[str] | None = None,
    priority: int = 100,
) -> str:
    """创建或更新一张提示词卡片（prompt card）。

    卡片是按意图动态注入 SystemMessage 的 Markdown 片段——当用户消息命中
    triggers 关键词、或历史中使用了 applies_to_tools 列出的工具时，卡片
    正文会被自动拼入系统提示词。

    使用场景：当你发现某种行为规范需要反复强调（如"计划步骤必须拆成原子项"），
    不要写进 system prompt，而是创建一张卡片让它在需要时自动注入。

    参数：
      card_id: 卡片唯一标识（字母/数字/下划线/连字符，如 "plans"）
      title: 卡片标题（注入时渲染为段落标题）
      body: 卡片正文（Markdown，注入时原样输出）
      triggers: 触发关键词列表（用户消息含任一关键词即激活）
      applies_to_tools: 关联工具名列表（历史中用过任一即激活）
      priority: 渲染优先级，数值小先出（默认 100）

    triggers 和 applies_to_tools 任一非空即可激活；两者为 OR 关系。
    同名 card_id 会覆盖已有卡片。
    """
    svc = get_card_service()
    result = await svc.upsert(
        card_id=card_id,
        title=title,
        body=body,
        triggers=triggers,
        applies_to_tools=applies_to_tools,
        priority=priority,
    )

    kind = ActivityKind.CARD_CREATED if result["created"] else ActivityKind.CARD_UPDATED
    verb = "创建了" if result["created"] else "更新了"
    await get_activity_bus().emit(
        kind,
        domain="system",
        title=f"{verb}卡片「{title}」({card_id})",
        source="agent",
        ref=f"cards/{card_id}.md",
        extra={
            "card_id": card_id,
            "triggers": result["triggers"],
            "applies_to_tools": result["applies_to_tools"],
            "priority": result["priority"],
        },
    )

    action = "新建" if result["created"] else "更新"
    return (
        f"✅ {action}卡片「{title}」(id={card_id})，"
        f"triggers={result['triggers']}，applies_to_tools={result['applies_to_tools']}，"
        f"priority={result['priority']}，正文 {result['body_chars']} 字。"
        f"当前库中共 {result['active_count']} 张卡片。"
    )


@tool
@logged_tool
async def kg_list_cards() -> str:
    """列出当前所有已加载的提示词卡片（id、标题、触发条件、优先级）。"""
    svc = get_card_service()
    cards = await svc.list_cards()
    return json.dumps(
        {"cards": cards, "count": len(cards)},
        ensure_ascii=False,
    )


@tool
@logged_tool
async def kg_view_card(card_id: str) -> str:
    """查看一张卡片的完整内容（含正文）。card_id 不存在时返回提示。"""
    svc = get_card_service()
    card = await svc.get_card(card_id)
    if card is None:
        return f"❌ 卡片 {card_id!r} 不存在。可用 kg_list_cards 查看所有卡片。"
    return json.dumps(card, ensure_ascii=False)


@tool
@logged_tool
async def kg_delete_card(card_id: str) -> str:
    """删除一张提示词卡片。card_id 不存在时返回提示（不报错）。"""
    svc = get_card_service()
    result = await svc.delete(card_id)
    if not result["deleted"]:
        return f"❌ 卡片 {card_id!r} 不存在，无法删除。"

    await get_activity_bus().emit(
        ActivityKind.CARD_DELETED,
        domain="system",
        title=f"删除了卡片 ({card_id})",
        source="agent",
        ref=f"cards/{card_id}.md",
        extra={"card_id": card_id},
    )

    return (
        f"✅ 已删除卡片 {card_id}。"
        f"当前库中共 {result['active_count']} 张卡片。"
    )


__all__ = [
    "kg_add_card",
    "kg_list_cards",
    "kg_view_card",
    "kg_delete_card",
]
