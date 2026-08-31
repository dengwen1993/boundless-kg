"""Learned search-backend preference — adaptive fallback ordering.

The legacy :class:`DualSearchClient` always probed backends in a fixed
order (DDG → mmx → Bocha).  This module adds an adaptive layer on top
that:

  1. **Promotes** the backend that actually returned results on the
     last successful query — subsequent queries skip the slower /
     unreachable primaries.
  2. **Quarantines** a backend the moment it raises a network / auth
     error — no more "ping DDG 100 times before falling through".  The
     failed backend stays skipped until the periodic probe window
     elapses (default 6 h), at which point we try it once more.
  3. **Records every failure** with an error summary so ops can
     grep ``<KG_AGENT_MEMORY_DIR>/search_preference.json`` to see
     *why* a backend was quarantined — satisfies the user's
     "失败要有明确的问题记录，方便后续排查" requirement.

Persistence
-----------
State lives at ``<KG_AGENT_MEMORY_DIR>/search_preference.json`` —
resolved by :func:`src.config.get_agent_memory_dir`, which sits under
:func:`src.config.get_workspace_dir` (default
``./workspace/.agent_memory``). That directory is co-located with the
conversation logs + ``tmp/`` transient files so the agent's runtime
state is in one place, away from the curated ``knowledge_bases/``
domain tree. The whole workspace is bind-mounted in the container
(see :doc:`/docs/Docker部署运行指南`), so failures recorded inside
the container are visible on the Windows host without ``docker cp``.

On-disk schema (single JSON object, UTF-8, indented):

    {
      "version": 1,
      "updated_at": "2026-08-12T10:30:00",
      "preferred": "bocha",           // last successful backend
      "backends": {
        "duckduck": {
          "success_count": 3,
          "failure_count": 1,
          "last_success_ts": "...",
          "last_failure_ts": "...",
          "last_error": "httpx.ConnectError: ...",  // most recent
          "quarantined_until": "2026-08-12T16:30:00" // ISO; absent = active
        },
        ...
      },
      "history": [                    // bounded ring buffer (last 50)
        {"ts": "...", "source": "duckduck",
         "outcome": "failure", "error": "..."}
      ]
    }

The ``history`` list is the "问题记录" the user asked for — every
failure (and success) is appended, capped at 50 entries so the file
never grows unbounded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Recognised backend names — keep in sync with the source="" field
# that each AsyncSearchClient populates on its SearchResult.
BACKEND_DDG = "duckduck"
BACKEND_MMX = "mmx"
BACKEND_BOCHA = "bocha"
ALL_BACKENDS = (BACKEND_DDG, BACKEND_MMX, BACKEND_BOCHA)

#: Max entries kept in the on-disk failure history.
HISTORY_CAP = 50

#: When a backend is quarantined, probe it again after this many
#: seconds.  Long enough to avoid hammering a broken upstream,
#: short enough to recover automatically once the upstream is fixed.
QUARANTINE_SEC = int(os.environ.get("KG_SEARCH_QUARANTINE_SEC", str(6 * 3600)))


def _now() -> datetime:
    """Single source of truth for "now" — tests monkeypatch this."""
    return datetime.now()


@dataclass(slots=True)
class BackendState:
    """Per-backend counters and quarantine state."""

    source: str
    success_count: int = 0
    failure_count: int = 0
    last_success_ts: str = ""
    last_failure_ts: str = ""
    last_error: str = ""
    # ISO timestamp; empty string means "not quarantined".
    quarantined_until: str = ""
    # Always at least one entry — keeps the on-disk shape stable.
    extra: dict[str, Any] = field(default_factory=dict)

    def is_quarantined(self, now: datetime | None = None) -> bool:
        if not self.quarantined_until:
            return False
        until = _parse_ts(self.quarantined_until)
        if until is None:
            return False
        return (now or _now()) < until

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_success_ts": self.last_success_ts,
            "last_failure_ts": self.last_failure_ts,
            "last_error": self.last_error,
            "quarantined_until": self.quarantined_until,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BackendState:
        return cls(
            source=str(d.get("source", "")),
            success_count=int(d.get("success_count", 0) or 0),
            failure_count=int(d.get("failure_count", 0) or 0),
            last_success_ts=str(d.get("last_success_ts", "") or ""),
            last_failure_ts=str(d.get("last_failure_ts", "") or ""),
            last_error=str(d.get("last_error", "") or ""),
            quarantined_until=str(d.get("quarantined_until", "") or ""),
        )


class SearchPreferenceStore:
    """In-memory cache + JSON persistence for backend preference.

    Thread/async safe: a single ``asyncio.Lock`` guards all mutations
    so concurrent ``record_*`` calls don't trample each other.
    Persistence is fire-and-forget — failures to write the JSON file
    are logged but do not block the calling coroutine.
    """

    PERSIST_FILENAME = "search_preference.json"
    SCHEMA_VERSION = 1

    def __init__(self, kb_root: Path | str) -> None:
        self._kb_root = Path(kb_root)
        self._path = self._kb_root / ".agent_memory" / self.PERSIST_FILENAME
        self._lock = asyncio.Lock()
        self._preferred: str = ""
        self._backends: dict[str, BackendState] = {
            name: BackendState(source=name) for name in ALL_BACKENDS
        }
        self._history: list[dict[str, Any]] = []
        self._loaded = False

    # ────────────────────────── Persistence ──────────────────────────

    def load(self) -> None:
        """Load persisted state from disk. Idempotent; missing file is OK."""
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            logger.debug("SearchPreferenceStore: no persisted file at %s", self._path)
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            # Corrupt file → log loud, start fresh. Don't crash startup.
            logger.warning(
                "SearchPreferenceStore: failed to parse %s (%s); starting fresh",
                self._path, e,
            )
            return
        if not isinstance(raw, dict):
            return
        self._preferred = str(raw.get("preferred", "") or "")
        backends = raw.get("backends") or {}
        if isinstance(backends, dict):
            for name, bd in backends.items():
                if isinstance(bd, dict) and name in self._backends:
                    self._backends[name] = BackendState.from_dict(bd)
        history = raw.get("history")
        if isinstance(history, list):
            self._history = [h for h in history if isinstance(h, dict)][-HISTORY_CAP:]

    def _snapshot(self) -> dict[str, Any]:
        return {
            "version": self.SCHEMA_VERSION,
            "updated_at": _now().isoformat(timespec="seconds"),
            "preferred": self._preferred,
            "backends": {name: state.to_dict() for name, state in self._backends.items()},
            "history": list(self._history),
        }

    async def _persist_locked(self) -> None:
        """Atomic JSON write. Caller must hold ``self._lock``."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._snapshot(), ensure_ascii=False, indent=2)
        tmp = self._path.with_suffix(".json.tmp")
        try:
            # atomic_write_text would be ideal, but using tempfile +
            # os.replace keeps this module self-contained without an
            # infrastructure dep (no circular imports).
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self._path.parent),
                prefix=".search_preference_",
                suffix=".tmp",
                delete=False,
            ) as f:
                f.write(payload)
                tmp_path = Path(f.name)
            os.replace(tmp_path, self._path)
        except OSError as e:
            logger.warning("SearchPreferenceStore: failed to persist %s (%s)", self._path, e)

    async def _persist(self) -> None:
        """Fire-and-forget persistence helper."""
        async with self._lock:
            await self._persist_locked()

    # ────────────────────────── Recording ──────────────────────────

    async def record_success(self, source: str) -> None:
        """Mark ``source`` as the most recently successful backend.

        Updates counters, stamps timestamps, and promotes ``source``
        to the head of the chain for the next query.
        """
        if source not in self._backends:
            return
        now = _now()
        async with self._lock:
            state = self._backends[source]
            state.success_count += 1
            state.last_success_ts = now.isoformat(timespec="seconds")
            # A successful call proves the backend is alive — clear
            # any prior quarantine so it can re-enter the rotation.
            state.quarantined_until = ""
            self._preferred = source
            self._append_history_locked({
                "ts": state.last_success_ts,
                "source": source,
                "outcome": "success",
            })
            await self._persist_locked()

    async def record_failure(self, source: str, error: str) -> None:
        """Quarantine ``source`` immediately and persist the error."""
        if source not in self._backends:
            return
        now = _now()
        until = now + timedelta(seconds=QUARANTINE_SEC)
        async with self._lock:
            state = self._backends[source]
            state.failure_count += 1
            state.last_failure_ts = now.isoformat(timespec="seconds")
            state.last_error = (error or "")[:500]
            state.quarantined_until = until.isoformat(timespec="seconds")
            self._append_history_locked({
                "ts": state.last_failure_ts,
                "source": source,
                "outcome": "failure",
                "error": state.last_error,
                "quarantined_until": state.quarantined_until,
            })
            # If the just-failed backend was the preferred one, drop
            # the preference so the chain falls back to the next tier.
            if self._preferred == source:
                self._preferred = ""
            await self._persist_locked()

    def _append_history_locked(self, entry: dict[str, Any]) -> None:
        """Bounded ring buffer — caller holds the lock."""
        self._history.append(entry)
        if len(self._history) > HISTORY_CAP:
            self._history = self._history[-HISTORY_CAP:]

    # ────────────────────────── Querying ──────────────────────────

    def is_quarantined(self, source: str) -> bool:
        self.load()
        state = self._backends.get(source)
        return bool(state and state.is_quarantined())

    def preferred_source(self) -> str:
        """Return the most-recently-successful backend, or ``""`` if none.

        Honours quarantine: a preferred backend that's currently
        quarantined returns ``""`` so the caller falls back to the
        rest of the chain.
        """
        self.load()
        if self._preferred and not self.is_quarantined(self._preferred):
            return self._preferred
        return ""

    def chain_for(self, default_chain: tuple[str, ...]) -> list[str]:
        """Return the ordered backend list to try, with ``preferred``
        hoisted to the front (when not quarantined).

        Quarantined backends are dropped from the returned list so
        the caller never even attempts them this round.  Backends
        that are simply absent from ``default_chain`` (e.g. Bocha not
        configured) are also dropped.
        """
        self.load()
        preferred = self.preferred_source()
        chain = [b for b in default_chain if b in self._backends and not self.is_quarantined(b)]
        if preferred and preferred in chain:
            chain.remove(preferred)
            chain.insert(0, preferred)
        return chain

    # ────────────────────────── Introspection ──────────────────────────

    def summary(self) -> dict[str, Any]:
        """Read-only snapshot for ops / debugging."""
        self.load()
        return {
            "preferred": self._preferred,
            "backends": {n: s.to_dict() for n, s in self._backends.items()},
            "history_tail": list(self._history[-10:]),
        }

    def reset(self) -> None:
        """Clear in-memory state (for tests). Does NOT delete the file."""
        self._preferred = ""
        self._backends = {name: BackendState(source=name) for name in ALL_BACKENDS}
        self._history = []
        self._loaded = True


# ────────────────────────── Helpers ──────────────────────────


def _parse_ts(value: str) -> datetime | None:
    try:
        # Accept both ``...Z`` (UTC) and naive ISO strings.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


__all__ = [
    "SearchPreferenceStore",
    "BackendState",
    "BACKEND_DDG",
    "BACKEND_MMX",
    "BACKEND_BOCHA",
    "ALL_BACKENDS",
    "QUARANTINE_SEC",
    "HISTORY_CAP",
]