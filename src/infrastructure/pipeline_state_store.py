"""Persistent pipeline state store.

Replaces the in-memory ``_TaskRegistry`` so that ``kg_check_status``
can still return task state after a backend restart.

Storage format: a single JSON file at ``{workspace_dir}/_pipeline/state.json``
containing a ``{task_id: {…fields…}}`` dict.  Writes are atomic (temp
file + rename) and guarded by ``graph_lock`` to prevent concurrent
corruption.

The state file lives under ``workspace_dir`` (not ``kb_root``) so that
operational artefacts don't pollute the knowledge-base namespace.
``kb_root`` is reserved for the curated ``<domain>/…`` data the user
actually maintains.

Only ``stage`` / ``progress`` / ``finished_at`` / ``error`` / ``domain``
are persisted — the full ``Graph`` result and ``stage_history`` are
kept in-memory only (they're too large and only useful during the
active run).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles

from src.infrastructure.lock import graph_lock
from src.infrastructure.repository._atomic import atomic_write_text

logger = logging.getLogger(__name__)

#: Fields persisted to disk (everything else stays in-memory only).
_PERSISTED_FIELDS = (
    "task_id",
    "domain",
    "stage",
    "progress",
    "started_at",
    "finished_at",
    "error",
    "stage_started_at",
)


class PipelineStateStore:
    """Persistent task registry backed by a JSON file.

    Usage::

        store = PipelineStateStore(workspace_dir)
        await store.load()          # call once at startup
        store.add(progress)         # synchronous, writes to disk
        store.update(task_id, stage="done", progress=1.0)
        p = store.get(task_id)      # in-memory read
    """

    def __init__(self, workspace_dir: Path) -> None:
        self._workspace_dir = Path(workspace_dir)
        self._state_path = self._workspace_dir / "_pipeline" / "state.json"
        self._tasks: dict[str, dict[str, Any]] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """Load persisted state from disk into memory.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._loaded:
            return
        self._loaded = True
        if not self._state_path.exists():
            return
        try:
            async with aiofiles.open(self._state_path, encoding="utf-8") as f:
                raw = await f.read()
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict):
                self._tasks = data
                logger.info(
                    "PipelineStateStore: loaded %d tasks from %s",
                    len(data),
                    self._state_path,
                )
        except Exception:
            logger.warning(
                "PipelineStateStore: failed to load state file %s, starting fresh",
                self._state_path,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # CRUD (in-memory + persist)
    # ------------------------------------------------------------------

    def add(self, task_id: str, domain: str) -> dict[str, Any]:
        """Register a new task and persist it."""
        entry: dict[str, Any] = {
            "task_id": task_id,
            "domain": domain,
            "stage": "init",
            "progress": 0.0,
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
            "error": None,
            "stage_started_at": datetime.utcnow().isoformat(),
        }
        self._tasks[task_id] = entry
        self._schedule_persist()
        return entry

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)

    def update(self, task_id: str, **kw: Any) -> None:
        entry = self._tasks.get(task_id)
        if entry is None:
            return
        for k, v in kw.items():
            if k in _PERSISTED_FIELDS:
                # Convert datetime to ISO string for JSON serialisation
                if isinstance(v, datetime):
                    entry[k] = v.isoformat()
                else:
                    entry[k] = v
        self._schedule_persist()

    def list_all(self) -> dict[str, dict[str, Any]]:
        """Return a shallow copy of all tasks (for debugging / admin)."""
        return dict(self._tasks)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _schedule_persist(self) -> None:
        """Fire-and-forget async persist.

        Uses ``asyncio.create_task`` so callers (which may be sync
        code inside the pipeline) don't need to ``await``.  If no
        event loop is running (e.g. unit tests), falls back to a
        synchronous write.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._persist())
        except RuntimeError:
            # No running loop — write synchronously
            self._persist_sync()

    async def _persist(self) -> None:
        """Atomically write the full state dict to disk."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        async with graph_lock():
            await atomic_write_text(
                self._state_path,
                json.dumps(self._tasks, ensure_ascii=False, indent=2),
            )

    def _persist_sync(self) -> None:
        """Synchronous fallback for non-async contexts."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(self._tasks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


__all__ = ["PipelineStateStore"]
