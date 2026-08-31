"""Read-side helper for the activity timeline.

The route ``GET /api/timeline/<domain>`` reads back events from the
per-day JSONL files written by :class:`FileActivityLog`.

Single-track decision
---------------------

Per the design contract, the reader ONLY consults the JSONL files —
it does NOT scan ``plan.json`` / ``web_resources/index.json`` /
``user_uploads/index.json`` / ``note.md`` mtime.  Any historical data
that was never emitted is invisible until something emits it.

Filtering
---------

  * ``date``  — if provided, only events whose ``date`` field matches
    are returned (exact match, ``YYYY-MM-DD``).
  * ``node``  — if provided, only events whose ``node`` field matches.
  * ``type``  — if provided, only events whose ``type`` field matches.

Without filters, the reader returns everything in the domain's
``activity/`` directory, sorted by ``ts`` descending.

Date window expansion
---------------------

If ``date`` is provided, we open exactly that day's file.  If not
provided, we open every file in ``activity/`` for the domain — cheap
because each daily file is small (one JSON line per event).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from src.config import get_kb_root

logger = logging.getLogger(__name__)


class ActivityReader:
    """Read-side view of the JSONL activity log for a single domain."""

    def __init__(self, kb_root: Path) -> None:
        self._kb_root = Path(kb_root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def read(
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
        items = self._iter_events(domain, date=date)
        if node:
            items = (it for it in items if it.get("node") == node)
        if type_:
            items = (it for it in items if it.get("type") == type_)
        out = list(items)
        out.sort(key=lambda it: it.get("ts", ""), reverse=True)
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _iter_events(
        self,
        domain: str,
        *,
        date: str | None,
    ) -> Iterable[dict[str, Any]]:
        activity_dir = self._kb_root / domain / "activity"
        if not activity_dir.exists():
            return ()

        if date:
            files = [activity_dir / f"{date}.jsonl"]
            files = [f for f in files if f.exists()]
        else:
            # Newest file first; we sort the items by ts at the end
            # anyway but reading newest-first keeps memory pressure
            # low when the caller only wants the head.
            files = sorted(
                activity_dir.glob("*.jsonl"),
                key=lambda p: p.name,
                reverse=True,
            )
        return _chain_files(files)


def _chain_files(files: list[Path]) -> Iterable[dict[str, Any]]:
    """Yield parsed events from each JSONL file in order."""
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(
                            "activity log: skipping corrupt line in %s", path
                        )
                        continue
                    if isinstance(ev, dict):
                        yield ev
        except FileNotFoundError:
            # Concurrent rotation; safe to ignore.
            continue
        except OSError:
            logger.warning("activity log: cannot read %s", path, exc_info=True)


# ----------------------------------------------------------------------
# Singleton accessor
# ----------------------------------------------------------------------

_reader_singleton: ActivityReader | None = None


def get_activity_reader() -> ActivityReader:
    """Return the process-wide :class:`ActivityReader` (lazy-init)."""
    global _reader_singleton
    if _reader_singleton is None:
        _reader_singleton = ActivityReader(Path(get_kb_root()))
    return _reader_singleton


def reset_activity_reader() -> None:
    """Drop the singleton — tests use this between cases."""
    global _reader_singleton
    _reader_singleton = None


__all__ = [
    "ActivityReader",
    "get_activity_reader",
    "reset_activity_reader",
]