"""Unit tests for DerivationSubscriber — ActivityBus → GraphSyncService routing.

Tests verify that:
1. Correct events trigger the right sync_service methods
2. Unknown event kinds are skipped
3. Wrong-domain events are ignored
4. The subscriber is non-blocking (fire-and-forget)

Since GraphSyncService requires FalkorDB (which is not available in CI),
we use a mock sync_service to verify routing logic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.graph.models import Graph, Node
from src.observability.activity_bus import (
    ActivityBus,
    ActivityKind,
    reset_activity_bus,
)
from src.observability.derivation_subscriber import DerivationSubscriber


@pytest.fixture
def kb_with_domain(tmp_kb_root: Path) -> str:
    domain = "d1"
    d = tmp_kb_root / domain
    d.mkdir(parents=True, exist_ok=True)
    g = Graph(domain=domain, nodes=[Node(name="A", links=[])])
    (d / "knowledge_graph.json").write_text(
        g.model_dump_json(indent=2), encoding="utf-8"
    )
    notes_dir = d / "notes" / "A"
    notes_dir.mkdir(parents=True)
    (notes_dir / "note.md").write_text("# A 的笔记", encoding="utf-8")
    return domain


@pytest.fixture
def fresh_bus() -> ActivityBus:
    reset_activity_bus()
    return ActivityBus()


@pytest.fixture
def mock_sync_service():
    """Mock GraphSyncService — records method calls without touching FalkorDB."""
    svc = MagicMock()
    svc.sync_for_node = AsyncMock(return_value={"synced": True})
    svc.delete_node = AsyncMock(return_value={"deleted": True})
    svc.sync_note = AsyncMock(return_value={"synced": True})
    svc.sync_resources = AsyncMock(return_value={"synced": True})
    svc.sync_plans = AsyncMock(return_value={"synced": True})
    return svc


class TestDerivationSubscriber:
    @pytest.mark.asyncio
    async def test_node_created_event_triggers_sync(
        self, kb_with_domain: str, mock_sync_service, fresh_bus: ActivityBus,
    ):
        sub = DerivationSubscriber(
            kb_with_domain, sync_service=mock_sync_service, bus=fresh_bus
        )
        await sub.start()

        await fresh_bus.emit(
            ActivityKind.NODE_CREATED,
            domain=kb_with_domain, node="A",
            title="新建 A",
        )
        import asyncio
        await asyncio.sleep(0.1)

        mock_sync_service.sync_for_node.assert_awaited_once_with(
            "A", event_id=mock_sync_service.sync_for_node.call_args.kwargs.get(
                "event_id", ""
            )
        )
        await sub.stop()

    @pytest.mark.asyncio
    async def test_node_deleted_event_triggers_cleanup(
        self, kb_with_domain: str, mock_sync_service, fresh_bus: ActivityBus,
    ):
        sub = DerivationSubscriber(
            kb_with_domain, sync_service=mock_sync_service, bus=fresh_bus
        )
        await sub.start()

        await fresh_bus.emit(
            ActivityKind.NODE_DELETED,
            domain=kb_with_domain, node="A",
        )
        import asyncio
        await asyncio.sleep(0.1)

        mock_sync_service.delete_node.assert_awaited_once()
        await sub.stop()

    @pytest.mark.asyncio
    async def test_note_event_triggers_sync_note(
        self, kb_with_domain: str, mock_sync_service, fresh_bus: ActivityBus,
    ):
        sub = DerivationSubscriber(
            kb_with_domain, sync_service=mock_sync_service, bus=fresh_bus
        )
        await sub.start()

        await fresh_bus.emit(
            ActivityKind.NOTE_GENERATED,
            domain=kb_with_domain, node="A",
        )
        import asyncio
        await asyncio.sleep(0.1)

        mock_sync_service.sync_note.assert_awaited_once()
        await sub.stop()

    @pytest.mark.asyncio
    async def test_resource_event_triggers_sync_resources(
        self, kb_with_domain: str, mock_sync_service, fresh_bus: ActivityBus,
    ):
        sub = DerivationSubscriber(
            kb_with_domain, sync_service=mock_sync_service, bus=fresh_bus
        )
        await sub.start()

        await fresh_bus.emit(
            ActivityKind.WEB_RESOURCE_ADDED,
            domain=kb_with_domain, node="A",
        )
        import asyncio
        await asyncio.sleep(0.1)

        mock_sync_service.sync_resources.assert_awaited_once()
        await sub.stop()

    @pytest.mark.asyncio
    async def test_unknown_kind_is_skipped(
        self, kb_with_domain: str, mock_sync_service, fresh_bus: ActivityBus,
    ):
        sub = DerivationSubscriber(
            kb_with_domain, sync_service=mock_sync_service, bus=fresh_bus
        )
        await sub.start()

        await fresh_bus.emit(
            ActivityKind.CARD_CREATED,
            domain=kb_with_domain, node="A",
        )
        import asyncio
        await asyncio.sleep(0.05)

        mock_sync_service.sync_for_node.assert_not_awaited()
        mock_sync_service.delete_node.assert_not_awaited()
        await sub.stop()

    @pytest.mark.asyncio
    async def test_wrong_domain_event_ignored(
        self, kb_with_domain: str, mock_sync_service, fresh_bus: ActivityBus,
    ):
        sub = DerivationSubscriber(
            kb_with_domain, sync_service=mock_sync_service, bus=fresh_bus
        )
        await sub.start()

        await fresh_bus.emit(
            ActivityKind.NODE_CREATED,
            domain="other", node="X",
        )
        import asyncio
        await asyncio.sleep(0.05)

        mock_sync_service.sync_for_node.assert_not_awaited()
        await sub.stop()

    @pytest.mark.asyncio
    async def test_start_stop_idempotent(
        self, kb_with_domain: str, mock_sync_service, fresh_bus: ActivityBus,
    ):
        sub = DerivationSubscriber(
            kb_with_domain, sync_service=mock_sync_service, bus=fresh_bus
        )
        # Double start should not raise
        await sub.start()
        await sub.start()
        assert sub._registered

        # Double stop should not raise
        await sub.stop()
        await sub.stop()
        assert not sub._registered


__all__ = []
