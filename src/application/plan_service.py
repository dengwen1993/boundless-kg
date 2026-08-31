"""PlanService — CRUD over per-node learning plans.

Plans live in ``{kb_root}/{domain}/notes/{node}/plan.json`` as an array of
``{id, created_at, date, goal, actions[], status, source, note}`` items —
the same contract the HTTP API (:mod:`src.api.routes.plans`) and the Vue
frontend use, so anything the agent writes is immediately visible in the
plan panel and the activity timeline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.infrastructure.repository.plan_repo import PlanRepository, recompute_plan_status


class PlanService:
    def __init__(self, repo: PlanRepository) -> None:
        self._repo = repo

    async def add(
        self,
        domain: str,
        *,
        node: str,
        goal: str,
        steps: list[str] | None = None,
        date: str = "",
        note: str = "",
        source: str = "agent",
    ) -> dict[str, Any]:
        """Create a plan under *node*.

        *steps* become the plan's actions; an empty list falls back to a
        single action carrying the goal text so the plan is still
        checkable in the UI.
        """
        node = (node or "").strip()
        if not node:
            raise ValueError("node 不能为空：计划必须挂在某个知识节点下")

        now = datetime.now()
        existing = await self._repo.list_plans(domain, node)
        clean_steps = [str(s).strip() for s in (steps or []) if str(s).strip()]
        actions = [
            {"id": f"a{i}", "content": s, "status": "pending", "done_at": None}
            for i, s in enumerate(clean_steps)
        ]
        if not actions:
            actions = [
                {"id": "a0", "content": goal, "status": "pending", "done_at": None}
            ]

        plan = {
            "id": f"p{now.strftime('%Y%m%d%H%M%S')}{len(existing)}",
            "created_at": now.isoformat(timespec="seconds"),
            "date": (date or "").strip() or now.strftime("%Y-%m-%d"),
            "goal": goal,
            "actions": actions,
            "status": "pending",
            "source": source if source in ("manual", "agent") else "agent",
            "note": note,
        }
        recompute_plan_status(plan)
        await self._repo.add_plan(domain, node, plan)
        return {**plan, "node": node}

    async def list(self, domain: str, node: str = "") -> list[dict[str, Any]]:
        """Plans for one node, or every node when *node* is empty."""
        if node:
            plans = await self._repo.list_plans(domain, node)
            return [{**p, "node": node} for p in plans]
        return await self._repo.list_domain_plans(domain)

    async def update_status(
        self,
        domain: str,
        node: str,
        plan_id: str,
        status: str,
        action_id: str = "",
    ) -> dict[str, Any] | None:
        """Update one action, or the whole plan when *action_id* is empty."""
        if action_id:
            return await self._repo.update_action_status(
                domain, node, plan_id, action_id, status
            )
        return await self._repo.update_plan_status(domain, node, plan_id, status)

    async def find_plan(
        self, domain: str, plan_id: str
    ) -> dict[str, Any] | None:
        """Locate a plan across all nodes (agents only remember the id)."""
        for p in await self._repo.list_domain_plans(domain):
            if p.get("id") == plan_id:
                return p
        return None

    async def delete(self, domain: str, node: str, plan_id: str) -> bool:
        return await self._repo.delete_plan(domain, node, plan_id)

    async def migrate_legacy(self, domain: str) -> int:
        """Drain the legacy domain-level ``plan.json`` into node files."""
        return await self._repo.migrate_legacy_domain_file(domain)


__all__ = ["PlanService"]
