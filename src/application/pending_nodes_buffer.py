"""PendingNodesBuffer — async debounced buffer for LLM dependency extraction.

设计动机
--------

用户连续 ``kg_add_node("A") → kg_add_node("B") → ...`` 时，每次都直接调
LLM 抽取关联既慢（每次 ~5s+）又贵（prompt 重复读所有节点名）。

本 buffer 把所有"待抽取节点"先排队，**累计 10 个 OR 5 分钟** 后统一触发
一次 LLM 批抽，单次 prompt 可覆盖 12~15 个节点。

触发规则（任一满足即 flush）：

  - 累计节点数 ≥ ``FLUSH_THRESHOLD_COUNT``（默认 10）
  - 距首次 add 已超过 ``FLUSH_INTERVAL_SEC``（默认 5 分钟）
  - 显式 :meth:`force_flush` 调用（服务关闭 / 用户手动触发）

失败隔离
--------

flush 回调里抛异常**不会**污染 buffer 状态——queue/timer 在 finally
中重置。调用方需要在 flush_callback 内部 try/except。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


#: 累计节点数阈值
FLUSH_THRESHOLD_COUNT: int = 10

#: 时间阈值（秒）
FLUSH_INTERVAL_SEC: int = 300  # 5 分钟


@dataclass(frozen=True)
class PendingItem:
    """一个待抽取节点。"""

    domain: str
    node: str
    event_id: str = ""           # 来源事件 id，用于 mark derived

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PendingItem):
            return NotImplemented
        return self.domain == other.domain and self.node == other.node

    def __hash__(self) -> int:
        return hash((self.domain, self.node))


FlushCallback = Callable[[list[PendingItem]], Awaitable[dict]]


class PendingNodesBuffer:
    """异步 LLM 抽取的防抖缓冲器。

    线程安全：通过 ``asyncio.Lock`` 串行化所有 mutation。
    """

    def __init__(
        self,
        flush_callback: FlushCallback,
        *,
        flush_count: int = FLUSH_THRESHOLD_COUNT,
        flush_interval: int = FLUSH_INTERVAL_SEC,
    ) -> None:
        if flush_count < 1:
            raise ValueError("flush_count 必须 >= 1")
        if flush_interval < 1:
            raise ValueError("flush_interval 必须 >= 1 秒")
        self._flush_cb = flush_callback
        self._flush_count = flush_count
        self._flush_interval = flush_interval

        self._queue: deque[PendingItem] = deque()
        self._first_add_at: float | None = None
        self._lock = asyncio.Lock()
        self._timer_task: asyncio.Task | None = None
        self._flushing = False
        self._closed = False

    # ---------- 公开 API ----------

    async def add(
        self, domain: str, node: str, event_id: str = ""
    ) -> None:
        """添加一个待抽取节点。已存在则忽略（去重）。"""
        item = PendingItem(domain=domain, node=node, event_id=event_id)
        async with self._lock:
            if self._closed:
                logger.warning(
                    "PendingNodesBuffer.add called on closed buffer (domain=%s node=%s)",
                    domain, node,
                )
                return
            if item in self._queue:
                return
            self._queue.append(item)
            if self._first_add_at is None:
                self._first_add_at = time.monotonic()
                if self._timer_task is None:
                    self._timer_task = asyncio.create_task(
                        self._timer_fire(),
                        name="pending-nodes-buffer-timer",
                    )
            if len(self._queue) >= self._flush_count:
                await self._flush_locked()

    async def force_flush(self) -> dict:
        """立即 flush；服务关闭 / 用户手动触发时调用。"""
        async with self._lock:
            return await self._flush_locked()

    def stats(self) -> dict[str, object]:
        """快照当前状态（无锁；用于监控/日志）。"""
        elapsed = 0.0
        if self._first_add_at is not None:
            elapsed = time.monotonic() - self._first_add_at
        return {
            "pending_count": len(self._queue),
            "first_add_at": self._first_add_at,
            "elapsed_sec": elapsed,
            "flush_count_threshold": self._flush_count,
            "flush_interval_sec": self._flush_interval,
            "closed": self._closed,
        }

    async def close(self) -> dict:
        """关闭 buffer — 取消定时器 + flush 余下 + 标记 closed。"""
        async with self._lock:
            self._closed = True
            if self._timer_task and not self._timer_task.done():
                self._timer_task.cancel()
                self._timer_task = None
            return await self._flush_locked()

    # ---------- 内部 ----------

    async def _timer_fire(self) -> None:
        try:
            await asyncio.sleep(self._flush_interval)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self._queue and not self._closed:
                await self._flush_locked()

    async def _flush_locked(self) -> dict:
        """必须在 self._lock 持有时调用。

        取出 queue → 触发回调 → 重置状态。flush 回调失败被外层
        try/except 隔离；本方法不抛异常。
        """
        if self._flushing:
            return {"skipped": "already_flushing"}
        if not self._queue:
            return {"flushed": 0}
        items = list(self._queue)
        self._queue.clear()
        self._first_add_at = None
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
            self._timer_task = None

        self._flushing = True
        try:
            try:
                result = await self._flush_cb(items)
                return {**result, "flushed": len(items)}
            except Exception as e:
                logger.warning(
                    "PendingNodesBuffer flush failed for %d items: %s",
                    len(items), e, exc_info=True,
                )
                return {"flushed": len(items), "error": str(e)}
        finally:
            self._flushing = False


__all__ = [
    "FLUSH_INTERVAL_SEC",
    "FLUSH_THRESHOLD_COUNT",
    "PendingItem",
    "PendingNodesBuffer",
]