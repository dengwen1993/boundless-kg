"""DerivationSubscriber — ActivityBus → GraphSyncService 的桥。

设计
----

监听 ActivityBus 上的事件，根据事件类型触发对应的同步行为，
将真源数据同步到 FalkorDB + Embedding + BM25 索引。

**完全不阻塞** originator（fire-and-forget）。

事件 → 同步行为映射：

  - NODE_CREATED / NODE_RENAMED / NODE_RELINKED → sync_for_node
  - NODE_DELETED → delete_node
  - NOTE_GENERATED / NOTE_REBUILT / NOTE_UPDATED → sync_note
  - WEB_RESOURCE_ADDED / UPLOAD_ADDED → sync_resources
  - PLAN_CREATED / PLAN_ACTION_DONE / PLAN_ACTION_SKIPPED / PLAN_DELETED → sync_plans
  - 其他事件 → 跳过
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.application.graph_sync_service import GraphSyncService

from .activity_bus import ActivityBus, ActivityEvent, ActivityKind, get_activity_bus

logger = logging.getLogger(__name__)


#: 需要派生的 event kinds — 集合，O(1) 查找
DERIVABLE_KINDS: frozenset[str] = frozenset({
    ActivityKind.NODE_CREATED,
    ActivityKind.NODE_RENAMED,
    ActivityKind.NODE_RELINKED,
    ActivityKind.NODE_DELETED,
    ActivityKind.WEB_RESOURCE_ADDED,
    ActivityKind.UPLOAD_ADDED,
    ActivityKind.PLAN_CREATED,
    ActivityKind.PLAN_ACTION_DONE,
    ActivityKind.PLAN_ACTION_SKIPPED,
    ActivityKind.PLAN_DELETED,
    ActivityKind.NOTE_GENERATED,
    ActivityKind.NOTE_REBUILT,
    ActivityKind.NOTE_UPDATED,
})


class DerivationSubscriber:
    """订阅 ActivityBus 并触发 FalkorDB + Embedding 同步。

    一个领域一个实例。``start()`` 时注册，``stop()`` 时取消注册。
    """

    def __init__(
        self,
        domain: str,
        *,
        sync_service: GraphSyncService,
        bus: Optional[ActivityBus] = None,
    ) -> None:
        self._domain = domain
        self._sync = sync_service
        self._bus = bus or get_activity_bus()
        self._registered = False

    # ---- 生命周期 ----

    async def start(self) -> None:
        """注册到 bus。幂等。"""
        if self._registered:
            return
        await self._bus.subscribe(self.handle)
        self._registered = True
        logger.info("DerivationSubscriber started (domain=%s)", self._domain)

    async def stop(self) -> None:
        """取消注册。幂等。"""
        if not self._registered:
            return
        await self._bus.unsubscribe(self.handle)
        self._registered = False

    # ---- 订阅入口 ----

    async def handle(self, event: ActivityEvent) -> None:
        """订阅者回调 — 必须立即返回（不阻塞 emit 调用方）。

        派生在 background task 里跑，失败不影响其他订阅者。
        """
        if event.get("domain") != self._domain:
            return
        kind = event.get("type", "")
        if kind not in DERIVABLE_KINDS:
            return
        node = event.get("node", "") or ""
        event_id = event.get("id", "") or ""

        # fire-and-forget — 当前 task 立即返回
        asyncio.create_task(
            self._derive_safely(
                domain=self._domain,
                node=node,
                kind=kind,
                event_id=event_id,
            ),
            name=f"derivation:{self._domain}:{kind}:{event_id or 'no-id'}",
        )

    async def _derive_safely(
        self,
        *,
        domain: str,
        node: str,
        kind: str,
        event_id: str,
    ) -> None:
        """实际同步 — 异常隔离，不影响其他订阅者。"""
        try:
            if kind in (
                ActivityKind.NODE_CREATED,
                ActivityKind.NODE_RENAMED,
                ActivityKind.NODE_RELINKED,
            ):
                if not node:
                    return
                await self._sync.sync_for_node(node, event_id=event_id)
            elif kind == ActivityKind.NODE_DELETED:
                if not node:
                    return
                await self._sync.delete_node(node, event_id=event_id)
            elif kind in (
                ActivityKind.NOTE_GENERATED,
                ActivityKind.NOTE_REBUILT,
                ActivityKind.NOTE_UPDATED,
            ):
                if not node:
                    return
                await self._sync.sync_note(node, event_id=event_id)
            elif kind in (
                ActivityKind.WEB_RESOURCE_ADDED,
                ActivityKind.UPLOAD_ADDED,
            ):
                if not node:
                    return
                await self._sync.sync_resources(node, event_id=event_id)
            elif kind in (
                ActivityKind.PLAN_CREATED,
                ActivityKind.PLAN_ACTION_DONE,
                ActivityKind.PLAN_ACTION_SKIPPED,
                ActivityKind.PLAN_DELETED,
            ):
                if not node:
                    return
                await self._sync.sync_plans(node, event_id=event_id)

        except Exception as e:
            logger.warning(
                "DerivationSubscriber failed for domain=%s kind=%s node=%s: %s",
                domain, kind, node, e, exc_info=True,
            )


# ----------------------------------------------------------------------
# Singleton accessor（按 domain 缓存）
# ----------------------------------------------------------------------

_subscribers: dict[str, DerivationSubscriber] = {}
_subscribers_lock = asyncio.Lock()

# 全局 dispatcher — 用于自动注册新 domain 的 subscriber
_dispatcher: Optional["MultiDomainDerivationDispatcher"] = None


async def get_or_create_derivation_subscriber(
    domain: str,
    *,
    sync_service: GraphSyncService,
) -> DerivationSubscriber:
    """取得或创建一个 :class:`DerivationSubscriber`（按 domain 缓存）。"""
    async with _subscribers_lock:
        sub = _subscribers.get(domain)
        if sub is None:
            sub = DerivationSubscriber(domain, sync_service=sync_service)
            _subscribers[domain] = sub
        else:
            # 替换 sync_service（避免闭包 stale 引用）
            sub._sync = sync_service
        return sub


async def reset_derivation_subscribers() -> None:
    """停止并丢弃所有 subscriber（测试用）。"""
    global _dispatcher
    async with _subscribers_lock:
        for sub in list(_subscribers.values()):
            await sub.stop()
        _subscribers.clear()
        if _dispatcher is not None:
            await _dispatcher.stop()
            _dispatcher = None


# ----------------------------------------------------------------------
# MultiDomainDerivationDispatcher — 自动为新 domain 注册 subscriber
# ----------------------------------------------------------------------


class MultiDomainDerivationDispatcher:
    """全局 subscriber — 监听所有 domain 的事件，自动为新 domain 创建同步服务。

    解决问题：server.py lifespan 在启动时遍历已有 domains 注册 subscriber，
    但用户之后创建新 domain 时没有 subscriber 为其同步。

    本 dispatcher 注册一个全局 subscriber，遇到未注册 domain 的事件时
    动态创建 GraphSyncService + subscriber，然后委派给该 domain 的 subscriber。
    """

    def __init__(self, bus: Optional[ActivityBus] = None) -> None:
        self._bus = bus or get_activity_bus()
        self._registered = False

    async def start(self) -> None:
        """注册到 bus。幂等。"""
        if self._registered:
            return
        await self._bus.subscribe(self.handle)
        self._registered = True
        logger.info("MultiDomainDerivationDispatcher started")

    async def stop(self) -> None:
        """取消注册。幂等。"""
        if not self._registered:
            return
        await self._bus.unsubscribe(self.handle)
        self._registered = False
        logger.info("MultiDomainDerivationDispatcher stopped")

    async def handle(self, event: ActivityEvent) -> None:
        """全局事件入口 — 确保对应 domain 的 subscriber 已注册到 bus。

        **不**调用 ``sub.handle(event)``——一旦 sub 通过 ``bus.subscribe()``
        注册成功，ActivityBus 会自动把事件路由给 sub.handle，避免重复派发。
        """
        domain = event.get("domain", "")
        if not domain:
            return
        kind = event.get("type", "")
        if kind not in DERIVABLE_KINDS:
            return

        async with _subscribers_lock:
            sub = _subscribers.get(domain)
            if sub is not None and sub._registered:
                return

            # 延迟导入避免循环依赖
            from src.agent import dependencies as agent_deps

            try:
                sync_svc = agent_deps.get_graph_sync_service(domain)
                sub = DerivationSubscriber(domain, sync_service=sync_svc)
                await sub.start()  # 注册到 bus
                _subscribers[domain] = sub
                logger.info(
                    "MultiDomainDerivationDispatcher: auto-registered "
                    "subscriber for new domain=%s", domain,
                )
            except Exception:
                logger.warning(
                    "MultiDomainDerivationDispatcher: failed to create "
                    "subscriber for domain=%s", domain, exc_info=True,
                )


__all__ = [
    "DERIVABLE_KINDS",
    "DerivationSubscriber",
    "MultiDomainDerivationDispatcher",
    "get_or_create_derivation_subscriber",
    "reset_derivation_subscribers",
]
