"""In-process observability primitives.

This package hosts the **activity event bus** that backs the
「活动时间线」 (activity timeline) feature:

  * :mod:`activity_bus`     — async pub/sub: write-points ``emit()``
    events, subscribers receive them.
  * :mod:`activity_log`     — a subscriber that persists each event as a
    JSON line into ``<kb_root>/<domain>/activity/<YYYY-MM-DD>.jsonl``.
  * :mod:`activity_reader`  — read-side helper for the
    ``GET /api/timeline/<domain>`` endpoint.

The design follows the user's decision:

  * **Trigger layer = Service + API hooks** — write-points emit from the
    route layer (covers manual UI clicks) AND from the agent-tool layer
    (covers agent-driven writes).  Both feed into the same bus.
  * **Storage = one JSONL per day per domain** —
    ``<kb_root>/<domain>/activity/<YYYY-MM-DD>.jsonl``.  Append-only,
    cheap to tail, easy to archive.
  * **Single-track** — the timeline route no longer scans
    ``plan.json`` / ``web_resources/index.json`` / ``note.md`` mtime.
    Only the JSONL log is the source of truth; old data will not
    appear automatically.
"""

from .activity_bus import ActivityBus, ActivityEvent, get_activity_bus
from .activity_log import FileActivityLog, get_activity_log
from .activity_reader import ActivityReader, get_activity_reader

__all__ = [
    "ActivityBus",
    "ActivityEvent",
    "get_activity_bus",
    "FileActivityLog",
    "get_activity_log",
    "ActivityReader",
    "get_activity_reader",
]