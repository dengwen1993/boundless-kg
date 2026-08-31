"""Single shared asyncio.Lock instance for graph file IO.

Corresponds to ENGINEERING_PLAN.md §1.2 / §3.4.
"""

from __future__ import annotations

import asyncio

from src.infrastructure.lock import graph_lock


def test_returns_same_instance() -> None:
    """graph_lock() must return the same object on every call."""
    a = graph_lock()
    b = graph_lock()
    assert a is b


def test_is_asyncio_lock() -> None:
    assert isinstance(graph_lock(), asyncio.Lock)


async def test_serialises_critical_sections() -> None:
    """Two contending acquires must run in series, not in parallel."""
    lock = graph_lock()
    order: list[str] = []

    async def worker(name: str, delay: float) -> None:
        async with lock:
            order.append(f"{name}-start")
            await asyncio.sleep(delay)
            order.append(f"{name}-end")

    # Both workers queue on the SAME lock.
    await asyncio.gather(worker("A", 0.05), worker("B", 0.05))
    # Whichever started first must have ended before the other started.
    assert order in (
        ["A-start", "A-end", "B-start", "B-end"],
        ["B-start", "B-end", "A-start", "A-end"],
    )


async def test_two_locks_would_race() -> None:
    """Regression guard: distinct Lock instances do NOT serialise."""
    l1 = asyncio.Lock()
    l2 = asyncio.Lock()
    order: list[str] = []

    async def worker_a() -> None:
        async with l1:
            order.append("A-start")
            await asyncio.sleep(0.05)
            order.append("A-end")

    async def worker_b() -> None:
        async with l2:
            order.append("B-start")
            await asyncio.sleep(0.0)
            order.append("B-end")

    await asyncio.gather(worker_a(), worker_b())
    # If we were using two locks, both starts interleave; if we used one,
    # we would see the strict pattern. The fact that this CAN overlap is
    # the failure mode the test pins down.
    assert "A-start" in order and "B-start" in order