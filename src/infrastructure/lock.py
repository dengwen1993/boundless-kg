"""Single shared asyncio.Lock for graph file IO.

ENGINEERING_PLAN.md §1.2 / §3.4 — the entire codebase must funnel file
IO through this one instance.

The lock is created lazily, **per event loop**, so it binds to whichever
loop is currently running. This matters under ``pytest-asyncio``
(per-test loop) and under any framework that recreates the loop; an
eagerly-created module-level lock would otherwise bake onto a stale loop
and every later ``acquire()`` would raise ``RuntimeError: ... is bound
to a different event loop``.

Production behaviour (single long-running loop) is unchanged: every
call returns the same ``asyncio.Lock`` instance, callers can compare
with ``is`` to verify they have the canonical instance for that loop.

Public surface:

  * ``graph_lock()`` — returns the shared ``asyncio.Lock`` instance.
  * ``_reset_locks_for_tests()`` — clear the per-loop cache. Tests use
    this between cases to guarantee a clean slate.
"""

from __future__ import annotations

import asyncio
from typing import Optional

_locks_by_loop: dict[int, asyncio.Lock] = {}


def graph_lock() -> asyncio.Lock:
    """Return the SHARED async lock for graph file IO.

    Creates the lock lazily on first call within the currently-running
    event loop. Each loop gets its own canonical lock instance.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — return (and lazily create) a sentinel lock
        # bound to no loop. Acquire on this would fail; that's the
        # correct behaviour since no async work is happening.
        sentinel_id = 0
        lock = _locks_by_loop.get(sentinel_id)
        if lock is None:
            lock = asyncio.Lock()
            _locks_by_loop[sentinel_id] = lock
        return lock

    loop_id = id(loop)
    lock = _locks_by_loop.get(loop_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks_by_loop[loop_id] = lock
    return lock


def _reset_locks_for_tests() -> None:
    """Drop every cached lock. Tests call this between cases."""
    _locks_by_loop.clear()


__all__ = ["graph_lock", "_reset_locks_for_tests"]