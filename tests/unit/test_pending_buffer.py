"""Unit tests for PendingNodesBuffer."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import pytest

from src.application.pending_nodes_buffer import (
    FLUSH_INTERVAL_SEC,
    FLUSH_THRESHOLD_COUNT,
    PendingItem,
    PendingNodesBuffer,
)


class TestPendingNodesBuffer:
    @pytest.mark.asyncio
    async def test_add_and_force_flush(self):
        flushed: list[PendingItem] = []

        async def cb(items: list[PendingItem]) -> dict:
            flushed.extend(items)
            return {"ok": True, "count": len(items)}

        buf = PendingNodesBuffer(cb, flush_count=100, flush_interval=600)
        await buf.add("d1", "A")
        await buf.add("d1", "B")
        await buf.add("d1", "A")   # 去重
        assert buf.stats()["pending_count"] == 2

        result = await buf.force_flush()
        assert flushed and len(flushed) == 2
        assert result.get("flushed") == 2
        assert buf.stats()["pending_count"] == 0

    @pytest.mark.asyncio
    async def test_threshold_triggers_flush(self):
        flushed: list[PendingItem] = []

        async def cb(items: list[PendingItem]) -> dict:
            flushed.extend(items)
            return {}

        buf = PendingNodesBuffer(cb, flush_count=3, flush_interval=600)
        await buf.add("d1", "A")
        await buf.add("d1", "B")
        await buf.add("d1", "C")   # 触发 flush
        # flush 是同步等待回调完成
        await asyncio.sleep(0.05)
        assert len(flushed) == 3
        assert buf.stats()["pending_count"] == 0

    @pytest.mark.asyncio
    async def test_timer_triggers_flush(self):
        flushed: list[PendingItem] = []

        async def cb(items: list[PendingItem]) -> dict:
            flushed.extend(items)
            return {}

        # 最短 1s interval
        buf = PendingNodesBuffer(cb, flush_count=100, flush_interval=1)
        await buf.add("d1", "X")
        await asyncio.sleep(1.5)  # 等待 timer 触发
        assert len(flushed) == 1

    @pytest.mark.asyncio
    async def test_flush_failure_isolated(self):
        async def cb(items: list[PendingItem]) -> dict:
            raise RuntimeError("boom")

        buf = PendingNodesBuffer(cb, flush_count=10, flush_interval=600)
        await buf.add("d1", "A")
        await buf.add("d1", "B")
        # 不抛异常
        result = await buf.force_flush()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_close_flushes_remaining(self):
        flushed: list[PendingItem] = []

        async def cb(items: list[PendingItem]) -> dict:
            flushed.extend(items)
            return {}

        buf = PendingNodesBuffer(cb, flush_count=100, flush_interval=600)
        await buf.add("d1", "A")
        await buf.close()
        assert len(flushed) == 1

    @pytest.mark.asyncio
    async def test_add_after_close_logged_but_noop(self):
        flushed: list[PendingItem] = []

        async def cb(items: list[PendingItem]) -> dict:
            flushed.extend(items)
            return {}

        buf = PendingNodesBuffer(cb, flush_count=100, flush_interval=600)
        await buf.close()
        await buf.add("d1", "X")
        assert flushed == []

    @pytest.mark.asyncio
    async def test_dedup_same_domain_node(self):
        flushed: list[PendingItem] = []

        async def cb(items: list[PendingItem]) -> dict:
            flushed.extend(items)
            return {}

        buf = PendingNodesBuffer(cb, flush_count=100, flush_interval=600)
        await buf.add("d1", "A", event_id="e1")
        await buf.add("d1", "A", event_id="e2")  # 同 node+domain，去重
        await buf.add("d1", "B", event_id="e3")
        await buf.force_flush()
        # e1 / e2 都指向 A；只保留一个
        assert len(flushed) == 2

    @pytest.mark.asyncio
    async def test_force_flush_empty(self):
        async def cb(items):
            return {}

        buf = PendingNodesBuffer(cb)
        result = await buf.force_flush()
        assert result["flushed"] == 0


class TestPendingItem:
    def test_equality_by_domain_and_node(self):
        a = PendingItem(domain="d1", node="A", event_id="e1")
        b = PendingItem(domain="d1", node="A", event_id="e2")
        c = PendingItem(domain="d1", node="B")
        d = PendingItem(domain="d2", node="A")
        assert a == b
        assert a != c
        assert a != d
        assert hash(a) == hash(b)

    def test_event_id_default(self):
        a = PendingItem(domain="d1", node="A")
        assert a.event_id == ""


__all__ = []