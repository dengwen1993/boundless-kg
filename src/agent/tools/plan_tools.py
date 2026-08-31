"""Plan management tools — add / list / update learning plans.

Plans are stored per knowledge node (``notes/{node}/plan.json``) in the
same shape the frontend plan panel and the activity timeline read, so a
plan created here shows up in the UI immediately.

Activity timeline
-----------------

``kg_add_plan`` emits ``plan_created``; ``kg_update_plan_status``
emits ``plan_action_done`` / ``plan_action_skipped`` when the user /
agent flips an action into a terminal state.  Edits (``update_plan``
goal / note / date) and deletes intentionally do not emit — they are
noise relative to the timeline's purpose of showing real progress.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from src.agent.dependencies import get_plan_service
from src.observability.activity_bus import ActivityKind, get_activity_bus
from src.observability.logged_tool import logged_tool


@tool
@logged_tool
async def kg_add_plan(
    domain: str,
    node: str,
    goal: str,
    steps: list[str] | None = None,
    date: str = "",
    note: str = "",
) -> str:
    """为某个知识节点添加一条学习计划。

    强约束：`steps` 必须是字符串数组（每项一条独立可勾选的原子行动），
    `goal` 是一句话的目标，`node` 必须是图谱中已存在的节点。

    完整规则（4–8 条行动、反例/正例、"一天 N 段"如何拆 N 条 plan）由
    `plans` 卡片按意图注入——本 docstring 只列骨架，详细规则不要写死。
    """
    # ── steps 参数反序列化 ──
    # LLM 可能将 steps 序列化为 JSON 字符串或 {"$text": "[...]"} 包装
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except json.JSONDecodeError:
            # 不是 JSON，当作单条步骤
            steps = [steps] if steps.strip() else None
    elif isinstance(steps, dict):
        wrapped = steps.get("$text") or steps.get("text") or steps.get("value")
        if isinstance(wrapped, str):
            try:
                steps = json.loads(wrapped)
            except json.JSONDecodeError:
                steps = None
        elif "steps" in steps:
            steps = steps["steps"]
        else:
            steps = None

    svc = get_plan_service()
    plan = await svc.add(
        domain,
        node=node,
        goal=goal,
        steps=steps or [],
        date=date,
        note=note,
    )

    # Activity timeline — emit only after PlanService.add succeeded so a
    # rejected add (e.g. duplicate step content) does not pollute the log.
    await get_activity_bus().emit(
        ActivityKind.PLAN_CREATED,
        domain=domain,
        node=node,
        title=f"新增了计划「{plan['goal']}」",
        source="agent",
        status=plan["status"],
        ref=f"plan.json#{plan['id']}",
        extra={"actions": [a["content"] for a in plan["actions"]]},
    )

    return (
        f"✅ 已在节点「{node}」下添加计划「{goal}」"
        f"（{domain}，日期={plan['date']}，{len(plan['actions'])} 条行动，id={plan['id']}）"
    )


@tool
@logged_tool
async def kg_list_plans(domain: str, node: str = "") -> str:
    """列出学习计划。node 留空=列出该领域所有节点的计划。"""
    svc = get_plan_service()
    plans = await svc.list(domain, node)
    return json.dumps(
        {"domain": domain, "node": node or "*", "plans": plans, "count": len(plans)},
        ensure_ascii=False,
    )


@tool
@logged_tool
async def kg_update_plan_status(
    domain: str,
    plan_id: str,
    status: str,
    node: str = "",
    action_id: str = "",
) -> str:
    """更新计划或单条行动的状态。

    status 可选：pending / done / skipped。
    action_id 留空则整条计划的所有行动都置为该状态；
    node 留空会自动在该领域内查找 plan_id 所属节点。
    """
    if status not in ("pending", "done", "skipped"):
        return f"❌ 无效状态 {status!r}，可选：pending / done / skipped"
    svc = get_plan_service()

    if not node:
        found = await svc.find_plan(domain, plan_id)
        if found is None:
            return f"❌ 计划 {plan_id!r} 不存在（{domain}）"
        node = found["node"]

    # Snapshot the action's prior state so we can detect a transition.
    before_plan = await svc.find_plan(domain, plan_id)
    prev_status = ""
    if before_plan and action_id:
        for a in before_plan.get("actions", []):
            if a.get("id") == action_id:
                prev_status = a.get("status", "")
                break

    plan = await svc.update_status(domain, node, plan_id, status, action_id)
    if plan is None:
        target = f"行动 {action_id!r}" if action_id else f"计划 {plan_id!r}"
        return f"❌ 未找到{target}（{domain} / {node}）"

    # Activity timeline — emit only on the transition into a terminal
    # state.  Same contract as the API route, kept here for symmetry.
    if (
        action_id
        and prev_status != status
        and status in ("done", "skipped")
    ):
        kind = (
            ActivityKind.PLAN_ACTION_DONE
            if status == "done"
            else ActivityKind.PLAN_ACTION_SKIPPED
        )
        verb = "完成" if status == "done" else "跳过"
        # Find the content of the action we just transitioned.
        action_content = ""
        for a in plan.get("actions", []):
            if a.get("id") == action_id:
                action_content = a.get("content", "")
                break
        await get_activity_bus().emit(
            kind,
            domain=domain,
            node=node,
            title=f"{verb}计划「{plan['goal']}」中的行动「{action_content}」",
            source="agent",
            status=status,
            ref=f"plan.json#{plan_id}@{action_id}",
            extra={"plan_id": plan_id, "action_id": action_id},
        )

    scope = f"行动 {action_id}" if action_id else "全部行动"
    return (
        f"✅ 已更新节点「{node}」计划 {plan_id} 的{scope}为 {status}"
        f"（计划整体状态：{plan['status']}）"
    )


@tool
@logged_tool
async def kg_delete_plan(domain: str, plan_id: str, node: str = "") -> str:
    """删除一条学习计划。node 留空会自动查找 plan_id 所属节点。"""
    svc = get_plan_service()
    if not node:
        found = await svc.find_plan(domain, plan_id)
        if found is None:
            return f"❌ 计划 {plan_id!r} 不存在（{domain}）"
        node = found["node"]
    ok = await svc.delete(domain, node, plan_id)
    if not ok:
        return f"❌ 计划 {plan_id!r} 不存在（{domain} / {node}）"

    # Activity timeline — capture deletions for completeness.
    await get_activity_bus().emit(
        ActivityKind.PLAN_DELETED,
        domain=domain,
        node=node,
        title=f"删除了计划 {plan_id}",
        source="agent",
        ref=f"plan.json#{plan_id}",
        extra={"plan_id": plan_id},
    )

    return f"✅ 已删除节点「{node}」下的计划 {plan_id}"


__all__ = [
    "kg_add_plan",
    "kg_list_plans",
    "kg_update_plan_status",
    "kg_delete_plan",
]
