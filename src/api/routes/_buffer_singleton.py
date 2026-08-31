"""Process-singleton PendingNodesBuffer 管理。

每个 domain 一个 buffer 实例；flush 回调是
``AssociationService.flush_buffer``。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from src.application.association_service import AssociationService
from src.application.pending_nodes_buffer import (
    PendingItem,
    PendingNodesBuffer,
)

logger = logging.getLogger(__name__)


_buffers: dict[str, PendingNodesBuffer] = {}
_buffers_lock = asyncio.Lock()


def _default_flush_cb(
    domain: str, assoc_svc: AssociationService
) -> Callable[[list[PendingItem]], Awaitable[dict]]:
    """默认 flush 回调：调 AssociationService.flush_buffer。

    必须返回 dict（含 flushed / errors 字段）。
    """
    async def _cb(items: list[PendingItem]) -> dict:
        if not items:
            return {"flushed": 0, "errors": 0}
        # domain 假设所有 item 同属一个 domain（buffer 已按 domain 隔离）
        nodes = [it.node for it in items]
        event_ids = [it.event_id for it in items]
        return await assoc_svc.flush_buffer(domain, nodes, event_ids=event_ids)

    return _cb


async def get_or_create_buffer(
    domain: str,
    *,
    flush_callback: Optional[Callable[[list[PendingItem]], Awaitable[dict]]] = None,
) -> PendingNodesBuffer:
    """取得或创建某 domain 的 buffer。

    Args:
        domain: 领域名
        flush_callback: 自定义 flush 回调；None = 默认调 AssociationService
    """
    async with _buffers_lock:
        buf = _buffers.get(domain)
        if buf is not None:
            return buf

        if flush_callback is None:
            from src.agent import dependencies as agent_deps
            assoc_svc = agent_deps.get_association_service()
            flush_callback = _default_flush_cb(domain, assoc_svc)

        buf = PendingNodesBuffer(flush_callback)
        _buffers[domain] = buf
        logger.info("PendingNodesBuffer created for domain=%s", domain)
        return buf


def get_buffer_for_domain(domain: str) -> Optional[PendingNodesBuffer]:
    """非异步取 buffer（用于 API 端点直接拿现有实例）。"""
    return _buffers.get(domain)


async def reset_buffers() -> None:
    """关闭并清空所有 buffer（测试用）。"""
    async with _buffers_lock:
        for buf in list(_buffers.values()):
            await buf.close()
        _buffers.clear()


__all__ = [
    "get_or_create_buffer",
    "get_buffer_for_domain",
    "reset_buffers",
]