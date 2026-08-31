"""File-backed activity log — JSONL subscriber.

Subscribes to :class:`ActivityBus` and appends each event as a single
JSON line into ``<kb_root>/<domain>/activity/<YYYY-MM-DD>.jsonl``.

File layout
-----------

::

  <kb_root>/
    <domain>/
      activity/
        2026-07-29.jsonl
        2026-07-30.jsonl

Each file is a stream of newline-delimited JSON objects, one event per
line.  ``ActivityReader`` (separate module) consumes these files.

Concurrency
-----------

We use the shared :func:`src.infrastructure.lock.graph_lock` to
serialise writes — same lock the repositories already use.  That
guarantees:

  * a write-point that holds the lock to update ``plan.json`` /
    ``knowledge_graph.json`` cannot lose its sibling activity event,
  * two concurrent emit()s for the same domain are appended in a
    deterministic order.

Failure handling
----------------

If the JSONL append fails (disk full, permission denied, ...), we log
at WARNING and drop the event.  Per the design contract, observability
NEVER blocks the originator.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.config import get_kb_root
from src.infrastructure.lock import graph_lock

from .activity_bus import ActivityBus, ActivityEvent, get_activity_bus

logger = logging.getLogger(__name__)


class FileActivityLog:
    """Subscriber that persists events to per-day JSONL files.

    Lifetime: a single instance is created at app startup and
    registered with the bus.  It owns no background tasks — it just
    reacts to whatever the bus dispatches.
    """

    def __init__(
        self,
        kb_root: Path,
        *,
        bus: Optional[ActivityBus] = None,
    ) -> None:
        self._kb_root = Path(kb_root)
        self._bus = bus or get_activity_bus()
        # Per-domain flush queue: events for the same domain are
        # serialised so the file write order matches the emit() order.
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()
        self._registered = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Register with the bus.  Idempotent — calling twice is a no-op."""
        if self._registered:
            return
        await self._bus.subscribe(self.handle)
        self._registered = True
        logger.info("FileActivityLog started (kb_root=%s)", self._kb_root)

    async def stop(self) -> None:
        """Unsubscribe from the bus.  Idempotent."""
        if not self._registered:
            return
        await self._bus.unsubscribe(self.handle)
        self._registered = False

    # ------------------------------------------------------------------
    # Subscriber entrypoint
    # ------------------------------------------------------------------

    async def handle(self, event: ActivityEvent) -> None:
        """Append *event* to the per-day JSONL for its domain.

        Per-domain order is preserved with a per-domain lock so that
        events from the same domain are written in emission order
        even under concurrency.
        """
        domain = event.get("domain") or ""
        if not domain:
            # Events without a domain cannot be routed to a file; log
            # and drop — still better than blocking the originator.
            logger.warning("activity event has no domain; dropped: %s", event)
            return

        lock = await self._lock_for(domain)
        async with lock:
            try:
                await self._append(event)
            except Exception:
                # Per contract: log + drop, never propagate.
                logger.warning(
                    "failed to append activity event for domain=%s date=%s",
                    domain,
                    event.get("date"),
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _lock_for(self, domain: str) -> asyncio.Lock:
        async with self._locks_lock:
            lk = self._domain_locks.get(domain)
            if lk is None:
                lk = asyncio.Lock()
                self._domain_locks[domain] = lk
            return lk

    async def _append(self, event: ActivityEvent) -> None:
        date = event.get("date") or datetime.now().strftime("%Y-%m-%d")
        path = self._path_for(event.get("domain", ""), date)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False)
        # Hold the shared graph lock while appending so a parallel
        # repository write doesn't leave the JSONL out of sync with
        # the file it describes.
        async with graph_lock():
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def _path_for(self, domain: str, date: str) -> Path:
        return self._kb_root / domain / "activity" / f"{date}.jsonl"


# ----------------------------------------------------------------------
# Singleton accessor
# ----------------------------------------------------------------------

_log_singleton: Optional[FileActivityLog] = None


def get_activity_log() -> FileActivityLog:
    """Return the process-wide :class:`FileActivityLog` (lazy-init)."""
    global _log_singleton
    if _log_singleton is None:
        _log_singleton = FileActivityLog(Path(get_kb_root()))
    return _log_singleton


async def start_activity_log() -> FileActivityLog:
    """Build (if needed) and register the activity log subscriber.

    Called once from FastAPI's ``lifespan`` startup.
    """
    log = get_activity_log()
    await log.start()
    return log


async def stop_activity_log() -> None:
    """Unregister the activity log subscriber (used in tests + shutdown)."""
    global _log_singleton
    if _log_singleton is not None:
        await _log_singleton.stop()


def reset_activity_log() -> None:
    """Drop the singleton — tests use this between cases."""
    global _log_singleton
    _log_singleton = None


__all__ = [
    "FileActivityLog",
    "get_activity_log",
    "start_activity_log",
    "stop_activity_log",
    "reset_activity_log",
]