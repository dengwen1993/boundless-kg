"""Plan + action CRUD routes (per-node ``plan.json``).

API contract:
  GET    /api/plans/{domain}?node=...                          → {items, total}
  POST   /api/plans/{domain}?node=...                          → add plan (goal+actions)
  PUT    /api/plans/{domain}/plan/{plan_id}?node=...           → update plan goal/note/date
  DELETE /api/plans/{domain}/plan/{plan_id}?node=...           → delete plan
  POST   /api/plans/{domain}/plan/{plan_id}/actions?node=...   → add action
  PUT    /api/plans/{domain}/plan/{plan_id}/actions/{aid}?node=...    → update action status/content
  DELETE /api/plans/{domain}/plan/{plan_id}/actions/{aid}?node=...    → delete action

``node`` is a query parameter (not a path segment) so node names
containing ``/`` (e.g. GitHub ``owner/repo``) are accepted verbatim.

Activity timeline
-----------------

Plan creation emits ``plan_created``; marking an action done emits
``plan_action_done`` (or ``plan_action_skipped``).

All plan IO goes through ``PlanRepository`` — the route layer never
touches ``aiofiles`` / ``atomic_write_text`` / ``graph_lock`` directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.agent import dependencies as agent_deps
from src.infrastructure.repository.plan_repo import recompute_plan_status
from src.observability.activity_bus import ActivityKind, get_activity_bus

router = APIRouter(prefix="/api", tags=["plans"])


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------


class AddPlanReq(BaseModel):
    date: str = ""
    goal: str
    actions: list[str] = []
    note: str = ""
    source: str = "manual"


class UpdatePlanReq(BaseModel):
    date: Optional[str] = None
    goal: Optional[str] = None
    note: Optional[str] = None


class AddActionReq(BaseModel):
    content: str


class UpdateActionReq(BaseModel):
    status: str
    content: Optional[str] = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _migrate_legacy_plan(it: dict) -> None:
    """Compat: old plans without ``actions`` / ``goal`` / ``date`` fields."""
    if "actions" not in it:
        steps = [str(s).strip() for s in (it.get("steps") or []) if str(s).strip()]
        status = it.get("status", "pending")
        if status not in ("pending", "done", "skipped"):
            status = "pending"
        if steps:
            it["actions"] = [
                {"id": f"a{i}", "content": s, "status": status, "done_at": None}
                for i, s in enumerate(steps)
            ]
        else:
            it["actions"] = [
                {
                    "id": "a0",
                    "content": it.get("content", "") or it.get("title", ""),
                    "status": status,
                    "done_at": it.get("done_at"),
                }
            ]
    if not it.get("goal"):
        it["goal"] = it.get("content", "") or it.get("title", "")
    if not it.get("date"):
        it["date"] = it.get("due", "") or it.get("created_at", "")[:10]


# ------------------------------------------------------------------
# Plan CRUD
# ------------------------------------------------------------------


@router.get("/plans/{domain}")
async def get_plans(
    domain: str,
    node: str = Query(""),
    date: str = "",
):
    """List plans for a node, or every node when *node* is empty (date desc).

    ``node`` is a query parameter so names with ``/`` (e.g. GitHub
    ``owner/repo``) are matched verbatim.
    """
    repo = agent_deps.get_plan_repo()
    if node:
        items = await repo.list_plans(domain, node)
    else:
        items = await repo.list_domain_plans(domain)
    for it in items:
        _migrate_legacy_plan(it)
    if date:
        items = [it for it in items if it.get("date") == date]
    items = sorted(
        items,
        key=lambda it: (it.get("date", ""), it.get("created_at", "")),
        reverse=True,
    )
    return {"items": items, "total": len(items)}


@router.post("/plans/{domain}")
async def add_plan(domain: str, node: str = Query(...), req: AddPlanReq = ...):
    """Add a plan (goal + actions) to the node's plan.json."""
    note_repo = agent_deps.get_note_repo()
    await note_repo.ensure_node_dir(domain, node)
    repo = agent_deps.get_plan_repo()
    items = await repo.list_plans(domain, node)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    actions = []
    for i, c in enumerate(req.actions):
        if c.strip():
            actions.append(
                {"id": f"a{i}", "content": c.strip(), "status": "pending", "done_at": None}
            )
    if not actions:
        raise HTTPException(400, "至少需要1条行动")
    item: dict[str, Any] = {
        "id": f"p{now.strftime('%Y%m%d%H%M%S')}{len(items)}",
        "created_at": now.isoformat(timespec="seconds"),
        "date": req.date.strip() or today,
        "goal": req.goal,
        "actions": actions,
        "status": "pending",
        "source": req.source if req.source in ("manual", "agent") else "manual",
        "note": req.note,
    }
    recompute_plan_status(item)
    await repo.add_plan(domain, node, item)

    # Activity timeline
    await get_activity_bus().emit(
        ActivityKind.PLAN_CREATED,
        domain=domain,
        node=node,
        title=f"新增了计划「{item['goal']}」",
        source=item.get("source", "manual"),
        status=item["status"],
        ref=f"plan.json#{item['id']}",
        extra={"actions": [a["content"] for a in item["actions"]]},
    )

    return {"ok": True, "item": {**item, "node": node}, "total": len(items) + 1}


@router.put("/plans/{domain}/plan/{plan_id}")
async def update_plan(
    domain: str, plan_id: str, node: str = Query(...), req: UpdatePlanReq = ...
):
    """Update a plan's goal / note / date."""
    repo = agent_deps.get_plan_repo()
    items = await repo.list_plans(domain, node)
    for it in items:
        if it.get("id") == plan_id:
            _migrate_legacy_plan(it)
            if req.date is not None:
                it["date"] = req.date.strip() or datetime.now().strftime("%Y-%m-%d")
            if req.goal is not None:
                it["goal"] = req.goal
            if req.note is not None:
                it["note"] = req.note
            await repo._write(repo._plan_path(domain, node), items)
            return {"ok": True, "item": it, "total": len(items)}
    raise HTTPException(404, f"未找到计划：{plan_id}")


@router.delete("/plans/{domain}/plan/{plan_id}")
async def delete_plan(domain: str, plan_id: str, node: str = Query(...)):
    """Delete an entire plan (with all its actions)."""
    repo = agent_deps.get_plan_repo()
    ok = await repo.delete_plan(domain, node, plan_id)
    if not ok:
        raise HTTPException(404, f"未找到计划：{plan_id}")
    return {"ok": True}


# ------------------------------------------------------------------
# Action CRUD (sub-resource of plan)
# ------------------------------------------------------------------


@router.post("/plans/{domain}/plan/{plan_id}/actions")
async def add_action(
    domain: str, plan_id: str, node: str = Query(...), req: AddActionReq = ...
):
    """Append an action to an existing plan."""
    repo = agent_deps.get_plan_repo()
    items = await repo.list_plans(domain, node)
    for it in items:
        if it.get("id") == plan_id:
            _migrate_legacy_plan(it)
            actions = it.get("actions") or []
            actions.append(
                {
                    "id": f"a{len(actions)}",
                    "content": req.content.strip(),
                    "status": "pending",
                    "done_at": None,
                }
            )
            it["actions"] = actions
            recompute_plan_status(it)
            await repo._write(repo._plan_path(domain, node), items)
            return {"ok": True, "item": it, "total": len(items)}
    raise HTTPException(404, f"未找到计划：{plan_id}")


@router.put("/plans/{domain}/plan/{plan_id}/actions/{action_id}")
async def update_action(
    domain: str,
    plan_id: str,
    action_id: str,
    node: str = Query(...),
    req: UpdateActionReq = ...,
):
    """Update an action's status / content."""
    repo = agent_deps.get_plan_repo()
    items = await repo.list_plans(domain, node)
    for it in items:
        if it.get("id") == plan_id:
            _migrate_legacy_plan(it)
            for a in it.get("actions") or []:
                if a.get("id") == action_id:
                    old = a.get("status")
                    if req.content is not None:
                        a["content"] = req.content
                    if req.status in ("pending", "done", "skipped"):
                        a["status"] = req.status
                        if req.status == "done" and old != "done":
                            a["done_at"] = datetime.now().isoformat(
                                timespec="seconds"
                            )
                        if req.status != "done":
                            a["done_at"] = None
                    recompute_plan_status(it)
                    await repo._write(repo._plan_path(domain, node), items)

                    # Activity timeline
                    if old != req.status and req.status in ("done", "skipped"):
                        kind = (
                            ActivityKind.PLAN_ACTION_DONE
                            if req.status == "done"
                            else ActivityKind.PLAN_ACTION_SKIPPED
                        )
                        verb = "完成" if req.status == "done" else "跳过"
                        await get_activity_bus().emit(
                            kind,
                            domain=domain,
                            node=node,
                            title=f"{verb}计划「{it['goal']}」中的行动「{a['content']}」",
                            source="manual",
                            status=req.status,
                            ref=f"plan.json#{plan_id}@{action_id}",
                            extra={"plan_id": plan_id, "action_id": action_id},
                        )

                    return {"ok": True, "item": it}
            raise HTTPException(404, f"未找到行动：{action_id}")
    raise HTTPException(404, f"未找到计划：{plan_id}")


@router.delete("/plans/{domain}/plan/{plan_id}/actions/{action_id}")
async def delete_action(
    domain: str, plan_id: str, action_id: str, node: str = Query(...)
):
    """Delete an action from a plan."""
    repo = agent_deps.get_plan_repo()
    items = await repo.list_plans(domain, node)
    for it in items:
        if it.get("id") == plan_id:
            _migrate_legacy_plan(it)
            actions = it.get("actions") or []
            new_actions = [a for a in actions if a.get("id") != action_id]
            if len(new_actions) == len(actions):
                raise HTTPException(404, f"未找到行动：{action_id}")
            it["actions"] = new_actions
            recompute_plan_status(it)
            await repo._write(repo._plan_path(domain, node), items)
            return {"ok": True, "item": it}
    raise HTTPException(404, f"未找到计划：{plan_id}")


__all__ = ["router"]
