"""Timeline repository — reads activity stream from JSONL logs.

Previously this module scanned ``plan.json`` / ``web_resources/index.json``
/ ``note.md`` mtime to build a timeline.  Now it delegates exclusively
to the JSONL activity log written by ``FileActivityLog``.

This eliminates filesystem mtime scanning (unreliable, can't filter by
event type) and makes the timeline a single-source read of the
``ActivityBus`` event stream.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

import aiofiles

logger = logging.getLogger(__name__)


class TimelineRepository:
    """Read-only activity-stream reader backed by per-day JSONL files.

    Reads ``{kb_root}/{domain}/activity/{YYYY-MM-DD}.jsonl`` files
    written by :class:`src.observability.activity_log.FileActivityLog`.
    """

    def __init__(self, kb_root: Path) -> None:
        self._kb_root = Path(kb_root)

    async def get_timeline(
        self,
        domain: str,
        *,
        date: str | None = None,
        node: str | None = None,
        type_: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return matching activity events, newest first.

        ``date``, ``node`` and ``type_`` are AND-combined; pass ``None``
        to skip a filter.
        """
        items: list[dict[str, Any]] = []
        async for ev in self._iter_events(domain, date=date):
            if node and ev.get("node") != node:
                continue
            if type_ and ev.get("type") != type_:
                continue
            items.append(ev)

        items.sort(key=lambda it: it.get("ts", ""), reverse=True)
        return items

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _iter_events(
        self,
        domain: str,
        *,
        date: str | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async generator yielding parsed events from JSONL files."""
        activity_dir = self._kb_root / domain / "activity"
        if not activity_dir.exists():
            return

        if date:
            files = [activity_dir / f"{date}.jsonl"]
            files = [f for f in files if f.exists()]
        else:
            files = sorted(
                activity_dir.glob("*.jsonl"),
                key=lambda p: p.name,
                reverse=True,
            )

        for path in files:
            try:
                async with aiofiles.open(path, encoding="utf-8") as f:
                    async for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning(
                                "timeline: skipping corrupt line in %s", path
                            )
                            continue
                        if isinstance(ev, dict):
                            yield ev
            except FileNotFoundError:
                continue
            except OSError:
                logger.warning("timeline: cannot read %s", path, exc_info=True)


__all__ = ["TimelineRepository"]
