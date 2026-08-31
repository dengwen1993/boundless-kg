"""Timeline aggregation route.

Reads the activity stream from the per-day JSONL log written by
:class:`src.observability.activity_log.FileActivityLog`.  No
on-disk scan of ``plan.json`` / ``web_resources/index.json`` /
``note.md`` — the JSONL log is the single source of truth.

API contract (matches the Vue frontend's ``api/index.ts``):
  GET /api/timeline/{domain}?date=&node=&type= → {items, total}

Activity item shape (unchanged from before so the frontend doesn't
have to change its parser):

  {
    "node":       "<node name or empty>",
    "datetime":   "<ISO seconds>",
    "date":       "YYYY-MM-DD",
    "type":       "<activity kind, see ActivityKind>",
    "title":      "<human-readable summary>",
    "source":     "manual" | "agent",
    "status":     "<optional, plan-related>",
    "ref":        "<back-pointer>",
    "extra":      { ... },        # optional, extra payload
    "derived":    true | false,   # whether associations.json 已派生
    "derived_at": "<ISO>" | ""    # 派生完成时间
  }

The ``type`` strings flow from the bus
(:class:`src.observability.activity_bus.ActivityKind`).  The frontend
TimelinePanel.vue maps these into its own ``ActionKind`` enum.

Why JSONL instead of scanning the graph?
---------------------------------------

The original route reverse-engineered activities from the artefacts
themselves (``plan.json`` -> plan created, ``note.md`` mtime -> note
edited, ...).  That had three problems:

  1. It could only see what was on disk NOW, not what happened in the
     past — a renamed node had no historical trail.
  2. Web resources lived under per-node dirs but the route read from
     the domain root, so they never showed up.
  3. Deletions / edits were invisible (no artefact to reverse from).

The observer pattern + JSONL log solves all three: every write-point
emits an explicit event, so the timeline reflects what actually
happened, not what is currently true.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from src.agent import dependencies as agent_deps
from src.infrastructure.repository.association_repo import AssociationRepository
from src.observability.activity_reader import get_activity_reader

router = APIRouter(prefix="/api", tags=["timeline"])


@router.get("/timeline/{domain}")
async def get_timeline(
    domain: str,
    date: str = Query("", description="YYYY-MM-DD filter; empty=all"),
    node: str = Query("", description="node name filter; empty=all"),
    type: str = Query("", alias="type", description="node_created / plan_created / ..."),
):
    """Domain activity timeline, descending by ``datetime``.

    The route is intentionally thin — all aggregation logic lives in
    :class:`src.observability.activity_reader.ActivityReader` so the
    reader is unit-testable without spinning up FastAPI.
    """
    reader = get_activity_reader()
    items = await reader.read(
        domain,
        date=date or None,
        node=node or None,
        type_=type or None,
    )

    # Load derived_events from associations.json — single read, cached in
    # this request. The subscriber updates it as each event completes.
    derived_events: dict[str, str] = {}
    try:
        assoc_repo: AssociationRepository = agent_deps.get_association_repo()
        assoc_graph = await assoc_repo.read(domain)
        derived_events = assoc_graph.metadata.derived_events
    except Exception:
        # associations.json 缺失/损坏不阻塞时间线
        derived_events = {}

    # Project raw bus events into the front-end's flatter shape.
    # The shape change keeps the JS Activity type intact.
    out: list[dict[str, Any]] = []
    for ev in items:
        ev_id = ev.get("id", "")
        derived_at = derived_events.get(ev_id, "")
        out.append(
            {
                "node": ev.get("node", ""),
                "datetime": ev.get("ts", ""),
                "date": ev.get("date", (ev.get("ts", "") or "")[:10]),
                "type": ev.get("type", ""),
                "title": ev.get("title", ""),
                "source": ev.get("source", "manual"),
                "status": ev.get("status", ""),
                "ref": ev.get("ref", ""),
                "extra": ev.get("extra", {}),
                "derived": bool(derived_at),
                "derived_at": derived_at,
            }
        )
    return {"items": out, "total": len(out)}


__all__ = ["router"]