"""In-process activity event bus — async pub/sub for activity timeline.

Design goals
------------

* **Non-blocking for write points.**  ``emit()`` schedules subscriber
  handlers concurrently via ``asyncio.create_task`` so that a slow /
  failing subscriber (e.g. filesystem full) does NOT block the
  originating API request.
* **Exception isolation.**  A subscriber that raises or times out
  never crashes the bus or the calling coroutine; errors are logged
  and dropped.  The originating request still succeeds.
* **Single source of truth per process.**  One canonical
  ``ActivityBus`` is built lazily on first access via
  :func:`get_activity_bus`.  Tests can call :func:`reset_activity_bus`
  to start from a clean slate.
* **Event types are forward-compatible.**  Each ``ActivityEvent`` is a
  plain dict with a fixed schema; downstream readers tolerate missing
  fields by falling back to ``"unknown"`` / empty strings.

Event schema (dict, JSON-serialisable)
--------------------------------------

::

  {
    "id":          "<uuid4>",         # unique event id
    "ts":          "2026-07-30T06:19:00",  # ISO seconds (local time)
    "date":        "2026-07-30",       # YYYY-MM-DD; index key
    "domain":      "AI 架构师深入学习",  # knowledge domain
    "type":        "node_created",     # see ActivityKind constants
    "node":        "三层架构实践",      # affected node (or "" if N/A)
    "title":       "新建了节点 三层架构实践",  # human-readable summary
    "source":      "manual" | "agent", # what triggered the action
    "status":      "pending" | "done" | "skipped",  # optional, plan-related
    "ref":         "node:三层架构实践", # back-pointer to underlying object
    "extra":       { ... },            # optional structured payload
  }

Why a dict (not a dataclass / pydantic model)?
----------------------------------------------

The bus sits at the boundary between many loosely-coupled call-sites.
A dict makes ``emit()`` ergonomic (``emit("node_created", domain, node=name)``),
is trivially JSON-serialisable, and keeps the API stable as we add
fields.  Downstream code reads via ``event.get("key", default)``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Event type constants
# ----------------------------------------------------------------------
#: Single source of truth for every event type that flows through the bus.
#: The frontend ``TimelinePanel.vue`` knows these strings.
#:
#: Naming convention: ``<entity>_<verb_past>`` (snake_case).
class ActivityKind:
    # ---- Node CRUD --------------------------------------------------
    NODE_CREATED = "node_created"
    NODE_RENAMED = "node_renamed"
    NODE_RELINKED = "node_relinked"     # links updated, no rename
    NODE_DELETED = "node_deleted"

    # ---- Web resources (URLs) --------------------------------------
    WEB_RESOURCE_ADDED = "web_resource_added"

    # ---- File uploads ----------------------------------------------
    UPLOAD_ADDED = "upload_added"

    # ---- Plans -----------------------------------------------------
    PLAN_CREATED = "plan_created"
    PLAN_ACTION_DONE = "plan_action_done"
    PLAN_ACTION_SKIPPED = "plan_action_skipped"
    PLAN_DELETED = "plan_deleted"

    # ---- Notes -----------------------------------------------------
    NOTE_GENERATED = "note_generated"
    NOTE_REBUILT = "note_rebuilt"
    NOTE_UPDATED = "note_updated"

    # ---- Prompt Cards ----------------------------------------------
    CARD_CREATED = "card_created"
    CARD_UPDATED = "card_updated"
    CARD_DELETED = "card_deleted"

    # ---- Knowledge-digest outputs ----------------------------------
    DIGEST_STARTED = "digest_started"
    DIGEST_MINDMAP_GENERATED = "digest_mindmap_generated"
    DIGEST_SLIDES_GENERATED = "digest_slides_generated"
    DIGEST_QUIZ_GENERATED = "digest_quiz_generated"
    DIGEST_NOTES_GENERATED = "digest_notes_generated"
    DIGEST_FAILED = "digest_failed"

    # ---- Direct skill-generated assets -----------------------------
    PDF_GENERATED = "pdf_generated"
    PPTX_GENERATED = "pptx_generated"
    DOCX_GENERATED = "docx_generated"

    # ---- Domain lifecycle (top-level kb_root/<domain>/ dir) -------
    DOMAIN_CREATED = "domain_created"

    # ---- Manual associations (UI right-click) ----------------------
    ASSOCIATION_CREATED = "association_created"
    ASSOCIATION_DELETED = "association_deleted"

    # ---- System-level graph operations (manual triggers) -----------
    GRAPH_EXPORTED = "graph_exported"
    GRAPH_SYNCED = "graph_synced"
    FIX_LINKS = "fix_links"

    # ---- Dossier (节点经验档案) ------------------------------------
    DOSSIER_ENTRY_ADDED = "dossier_entry_added"
    DOSSIER_ENTRY_UPDATED = "dossier_entry_updated"
    DOSSIER_ENTRY_REMOVED = "dossier_entry_removed"


#: Set of all known event types; useful for validation in tests.
ALL_KINDS: frozenset[str] = frozenset(
    v for k, v in vars(ActivityKind).items() if not k.startswith("_") and isinstance(v, str)
)


# ----------------------------------------------------------------------
# Type aliases
# ----------------------------------------------------------------------
ActivityEvent = dict[str, Any]
Subscriber = Callable[[ActivityEvent], Awaitable[None]]


# ----------------------------------------------------------------------
# Bus
# ----------------------------------------------------------------------


class ActivityBus:
    """In-process async pub/sub for activity events.

    Subscribers are registered via :meth:`subscribe`; write-points call
    :meth:`emit` to publish.  Delivery is best-effort: a handler that
    raises is logged and dropped so it never blocks the originator.
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._lock = asyncio.Lock()

    # -- subscribe / unsubscribe --------------------------------------

    async def subscribe(self, handler: Subscriber) -> None:
        """Register *handler* to receive every future event."""
        async with self._lock:
            if handler not in self._subscribers:
                self._subscribers.append(handler)

    async def unsubscribe(self, handler: Subscriber) -> None:
        """Remove *handler* from the subscriber list.  No-op if absent."""
        async with self._lock:
            try:
                self._subscribers.remove(handler)
            except ValueError:
                pass

    # -- emit ---------------------------------------------------------

    async def emit(
        self,
        type_: str,
        *,
        domain: str,
        node: str = "",
        title: str = "",
        source: str = "manual",
        status: str = "",
        ref: str = "",
        extra: Optional[dict[str, Any]] = None,
        ts: Optional[str] = None,
    ) -> ActivityEvent:
        """Publish an event and schedule delivery to every subscriber.

        Returns the event dict (useful for tests / chaining).  The
        delivery tasks fire-and-forget; this call returns as soon as
        the event is built.
        """
        now = datetime.now()
        event: ActivityEvent = {
            "id": uuid.uuid4().hex,
            "ts": ts or now.isoformat(timespec="seconds"),
            "date": (ts or now.isoformat(timespec="seconds"))[:10],
            "domain": domain,
            "type": type_,
            "node": node,
            "title": title,
            "source": source,
            "status": status,
            "ref": ref,
            "extra": extra or {},
        }

        # Snapshot subscribers so a re-entrant subscribe() during
        # dispatch doesn't change the iteration target.
        async with self._lock:
            handlers = list(self._subscribers)

        for handler in handlers:
            self._schedule(handler, event)

        return event

    # -- internal -----------------------------------------------------

    @staticmethod
    def _schedule(handler: Subscriber, event: ActivityEvent) -> None:
        """Fire-and-forget handler dispatch with exception isolation.

        A handler that raises or hangs must never affect the originator
        or other subscribers.  Errors are logged at WARNING with the
        offending handler's qualified name for traceability.
        """

        async def _runner() -> None:
            try:
                await handler(event)
            except Exception:
                logger.warning(
                    "activity subscriber %s failed for event %s",
                    getattr(handler, "__qualname__", repr(handler)),
                    event.get("id"),
                    exc_info=True,
                )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_runner())
        except RuntimeError:
            # No running loop (e.g. emit() called from sync context).
            # Fall back to running the coroutine inline — still safe
            # because exceptions are swallowed inside _runner.
            try:
                asyncio.run(_runner())
            except Exception:
                logger.warning("activity emit() with no event loop failed", exc_info=True)


# ----------------------------------------------------------------------
# Singleton accessor
# ----------------------------------------------------------------------

_bus_singleton: Optional[ActivityBus] = None


def get_activity_bus() -> ActivityBus:
    """Return the process-wide :class:`ActivityBus` (lazy-init)."""
    global _bus_singleton
    if _bus_singleton is None:
        _bus_singleton = ActivityBus()
    return _bus_singleton


def reset_activity_bus() -> None:
    """Drop the singleton — tests use this between cases."""
    global _bus_singleton
    _bus_singleton = None


__all__ = [
    "ActivityBus",
    "ActivityEvent",
    "ActivityKind",
    "ALL_KINDS",
    "Subscriber",
    "get_activity_bus",
    "reset_activity_bus",
]