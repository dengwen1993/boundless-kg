"""Plan repository — async CRUD over per-node ``notes/{node}/plan.json``.

Storage contract (shared with :mod:`src.api.routes.plans` and the Vue
frontend):  each node directory holds a ``plan.json`` containing a JSON
**array** of plan items::

    [
      {
        "id": "p202607271034310",
        "created_at": "2026-07-27T10:34:31",
        "date": "2026-07-28",
        "goal": "Prompt 工程一日系统学习",
        "actions": [
          {"id": "a0", "content": "...", "status": "pending", "done_at": null}
        ],
        "status": "pending",
        "source": "agent",
        "note": ""
      }
    ]

Historically the agent layer wrote a *domain-level* ``{domain}/plan.json``
holding ``{"plans": [...]}`` with ``title`` / ``steps`` / ``due`` fields.
That file was invisible to both the HTTP API and the activity timeline.
:meth:`PlanRepository.migrate_legacy_domain_file` converts it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiofiles

from src.infrastructure.lock import graph_lock

from ._atomic import atomic_write_text

_ACTION_STATUSES = ("pending", "done", "skipped")


def recompute_plan_status(plan: dict[str, Any]) -> None:
    """Derive a plan's rollup status from its actions (in place)."""
    actions = plan.get("actions") or []
    plan["status"] = (
        "done"
        if actions and all(a.get("status") == "done" for a in actions)
        else "pending"
    )


def legacy_to_plan_item(old: dict[str, Any]) -> dict[str, Any]:
    """Convert a legacy domain-level plan into the per-node item shape."""
    steps = [s for s in (old.get("steps") or []) if str(s).strip()]
    status = old.get("status", "pending")
    action_status = status if status in _ACTION_STATUSES else "pending"
    actions = [
        {
            "id": f"a{i}",
            "content": str(s).strip(),
            "status": action_status,
            "done_at": None,
        }
        for i, s in enumerate(steps)
    ]
    if not actions:
        actions = [
            {
                "id": "a0",
                "content": old.get("title", ""),
                "status": action_status,
                "done_at": None,
            }
        ]
    created = old.get("created_at", "")
    item = {
        "id": old.get("id", ""),
        "created_at": created,
        "date": old.get("due") or (created[:10] if created else ""),
        "goal": old.get("title", ""),
        "actions": actions,
        "status": status,
        "source": "agent",
        "note": "",
    }
    if status not in ("skipped",):
        recompute_plan_status(item)
    return item


class PlanRepository:
    """Per-node learning plan CRUD."""

    def __init__(self, kb_root: Path) -> None:
        self._kb_root = Path(kb_root)

    # -- paths ------------------------------------------------------

    def _node_dir(self, domain: str, node: str) -> Path:
        return self._kb_root / domain / "notes" / node

    def _plan_path(self, domain: str, node: str) -> Path:
        return self._node_dir(domain, node) / "plan.json"

    def _notes_root(self, domain: str) -> Path:
        return self._kb_root / domain / "notes"

    def legacy_domain_path(self, domain: str) -> Path:
        """Pre-migration domain-level plan file."""
        return self._kb_root / domain / "plan.json"

    # -- io ---------------------------------------------------------

    async def _read(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            async with aiofiles.open(path, encoding="utf-8") as f:
                items = json.loads(await f.read())
        except Exception:
            return []
        return items if isinstance(items, list) else []

    async def _write(self, path: Path, items: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        await atomic_write_text(path, json.dumps(items, ensure_ascii=False, indent=2))

    # -- read -------------------------------------------------------

    async def list_plans(self, domain: str, node: str) -> list[dict[str, Any]]:
        """Plans for one node."""
        return await self._read(self._plan_path(domain, node))

    async def list_domain_plans(self, domain: str) -> list[dict[str, Any]]:
        """Plans across every node of *domain*, each tagged with ``node``."""
        out: list[dict[str, Any]] = []
        root = self._notes_root(domain)
        if not root.exists():
            return out
        for node_dir in sorted(root.iterdir()):
            if not node_dir.is_dir():
                continue
            for p in await self._read(node_dir / "plan.json"):
                out.append({**p, "node": node_dir.name})
        out.sort(key=lambda p: (p.get("date", ""), p.get("created_at", "")), reverse=True)
        return out

    # -- write ------------------------------------------------------

    async def add_plan(self, domain: str, node: str, plan: dict[str, Any]) -> None:
        async with graph_lock():
            path = self._plan_path(domain, node)
            items = await self._read(path)
            items.append(plan)
            await self._write(path, items)

    async def update_action_status(
        self, domain: str, node: str, plan_id: str, action_id: str, status: str
    ) -> dict[str, Any] | None:
        """Set one action's status; returns the parent plan or ``None``."""
        return await self._update_actions(
            domain, node, plan_id, status, only_action=action_id
        )

    async def update_plan_status(
        self, domain: str, node: str, plan_id: str, status: str
    ) -> dict[str, Any] | None:
        """Set *every* action of a plan to *status*; returns the plan."""
        return await self._update_actions(domain, node, plan_id, status)

    async def _update_actions(
        self,
        domain: str,
        node: str,
        plan_id: str,
        status: str,
        *,
        only_action: str | None = None,
    ) -> dict[str, Any] | None:
        from datetime import datetime

        if status not in _ACTION_STATUSES:
            return None
        async with graph_lock():
            path = self._plan_path(domain, node)
            items = await self._read(path)
            for plan in items:
                if plan.get("id") != plan_id:
                    continue
                matched = False
                for a in plan.get("actions") or []:
                    if only_action is not None and a.get("id") != only_action:
                        continue
                    matched = True
                    was_done = a.get("status") == "done"
                    a["status"] = status
                    if status == "done":
                        if not was_done:
                            a["done_at"] = datetime.now().isoformat(timespec="seconds")
                    else:
                        a["done_at"] = None
                if only_action is not None and not matched:
                    return None
                recompute_plan_status(plan)
                await self._write(path, items)
                return {**plan, "node": node}
            return None

    # -- migration --------------------------------------------------

    async def migrate_legacy_domain_file(self, domain: str) -> int:
        """Move ``{domain}/plan.json`` entries into per-node plan files.

        Converts the legacy ``{"plans": [{title, steps, due, status}]}``
        shape into the array/``goal``+``actions`` shape the API and the
        timeline understand.  Idempotent: the legacy file is renamed to
        ``plan.legacy.json`` once drained.  Returns the number of plans
        migrated.
        """
        legacy = self.legacy_domain_path(domain)
        if not legacy.exists():
            return 0
        try:
            async with aiofiles.open(legacy, encoding="utf-8") as f:
                data = json.loads(await f.read())
        except Exception:
            return 0
        if not isinstance(data, dict):
            return 0
        plans = data.get("plans") or []
        migrated = 0
        for old in plans:
            node = (old.get("node") or "").strip()
            if not node:
                continue
            item = legacy_to_plan_item(old)
            existing = await self.list_plans(domain, node)
            if any(it.get("id") == item["id"] for it in existing):
                migrated += 1
                continue
            await self.add_plan(domain, node, item)
            migrated += 1
        if migrated:
            legacy.rename(legacy.with_name("plan.legacy.json"))
        return migrated

    async def delete_plan(self, domain: str, node: str, plan_id: str) -> bool:
        async with graph_lock():
            path = self._plan_path(domain, node)
            items = await self._read(path)
            kept = [it for it in items if it.get("id") != plan_id]
            if len(kept) == len(items):
                return False
            await self._write(path, kept)
            return True


__all__ = ["PlanRepository", "recompute_plan_status", "legacy_to_plan_item"]
