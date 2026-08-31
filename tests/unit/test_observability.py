"""Unit tests for the activity-timeline observability primitives.

Covers the three building blocks in ``src/observability/``:

  * :mod:`activity_bus` — in-process pub/sub.
  * :mod:`activity_log` — per-day JSONL file subscriber.
  * :mod:`activity_reader` — read-side helper used by the
    ``GET /api/timeline/<domain>`` route.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.observability.activity_bus import (
    ActivityBus,
    ActivityKind,
    ALL_KINDS,
    get_activity_bus,
    reset_activity_bus,
)
from src.observability.activity_log import (
    FileActivityLog,
    get_activity_log,
    reset_activity_log,
    start_activity_log,
    stop_activity_log,
)
from src.observability.activity_reader import (
    ActivityReader,
    get_activity_reader,
    reset_activity_reader,
)


# ----------------------------------------------------------------------
# Bus
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bus_emit_returns_event_and_dispatches_to_subscribers() -> None:
    bus = ActivityBus()
    received: list[dict] = []

    async def handler(ev):
        received.append(ev)

    await bus.subscribe(handler)
    ev = await bus.emit(
        ActivityKind.NODE_CREATED,
        domain="D",
        node="N",
        title="新建了节点 N",
    )
    assert ev["type"] == ActivityKind.NODE_CREATED
    assert ev["domain"] == "D"
    assert ev["node"] == "N"
    assert ev["id"] and isinstance(ev["id"], str)
    assert ev["ts"].count(":") >= 1  # ISO timestamp
    assert ev["date"] == ev["ts"][:10]

    # Allow the fire-and-forget task to run.
    await asyncio.sleep(0)
    assert len(received) == 1
    assert received[0]["id"] == ev["id"]


@pytest.mark.asyncio
async def test_bus_subscriber_exception_is_isolated() -> None:
    """A failing subscriber must NOT crash the bus or other subscribers."""
    bus = ActivityBus()
    received_ok: list[dict] = []

    async def broken(_ev):
        raise RuntimeError("boom")

    async def fine(_ev):
        received_ok.append({"ok": True})

    await bus.subscribe(broken)
    await bus.subscribe(fine)

    # The originating emit() must not raise, even though broken() will.
    await bus.emit(ActivityKind.PLAN_CREATED, domain="D", node="N")
    await asyncio.sleep(0)
    assert received_ok == [{"ok": True}]


@pytest.mark.asyncio
async def test_bus_unsubscribe_stops_delivery() -> None:
    bus = ActivityBus()
    received: list[dict] = []

    async def handler(ev):
        received.append(ev)

    await bus.subscribe(handler)
    await bus.emit(ActivityKind.NODE_DELETED, domain="D", node="N")
    await asyncio.sleep(0)
    assert len(received) == 1

    await bus.unsubscribe(handler)
    await bus.emit(ActivityKind.NODE_DELETED, domain="D", node="N")
    await asyncio.sleep(0)
    assert len(received) == 1  # unchanged


def test_activity_kind_constants_have_no_typos() -> None:
    """All kinds live in ALL_KINDS so callers can validate event types."""
    assert ActivityKind.NODE_CREATED == "node_created"
    assert ActivityKind.NODE_RENAMED == "node_renamed"
    assert ActivityKind.NODE_DELETED == "node_deleted"
    assert ActivityKind.WEB_RESOURCE_ADDED == "web_resource_added"
    assert ActivityKind.UPLOAD_ADDED == "upload_added"
    assert ActivityKind.PLAN_CREATED == "plan_created"
    assert ActivityKind.PLAN_ACTION_DONE == "plan_action_done"
    assert ActivityKind.NOTE_GENERATED == "note_generated"
    # ALL_KINDS is derived from the class attrs; sanity-check the count.
    assert len(ALL_KINDS) >= 10


# ----------------------------------------------------------------------
# Log (per-day JSONL subscriber)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_activity_log_appends_to_correct_daily_file(
    tmp_path: Path,
) -> None:
    log = FileActivityLog(tmp_path)
    await log.start()
    try:
        bus = get_activity_bus()
        await bus.emit(
            ActivityKind.NODE_CREATED,
            domain="D1",
            node="A",
            title="新建 A",
        )
        await bus.emit(
            ActivityKind.WEB_RESOURCE_ADDED,
            domain="D1",
            node="A",
            title="添加资料",
        )
        # Allow the fire-and-forget tasks to drain.
        await asyncio.sleep(0.05)
    finally:
        await log.stop()

    log_dir = tmp_path / "D1" / "activity"
    files = sorted(log_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert {p["type"] for p in parsed} == {
        ActivityKind.NODE_CREATED,
        ActivityKind.WEB_RESOURCE_ADDED,
    }


@pytest.mark.asyncio
async def test_file_activity_log_isolates_per_domain(
    tmp_path: Path,
) -> None:
    log = FileActivityLog(tmp_path)
    await log.start()
    try:
        bus = get_activity_bus()
        await bus.emit(ActivityKind.NODE_CREATED, domain="DA", node="x")
        await bus.emit(ActivityKind.NODE_CREATED, domain="DB", node="y")
        await asyncio.sleep(0.05)
    finally:
        await log.stop()

    for dom, expected_node in (("DA", "x"), ("DB", "y")):
        files = list((tmp_path / dom / "activity").glob("*.jsonl"))
        assert len(files) == 1
        rec = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert rec["node"] == expected_node


@pytest.mark.asyncio
async def test_file_activity_log_drops_event_without_domain(
    tmp_path: Path,
) -> None:
    log = FileActivityLog(tmp_path)
    await log.start()
    try:
        bus = get_activity_bus()
        await bus.emit(ActivityKind.NODE_CREATED, domain="", node="x")
        await asyncio.sleep(0.05)
    finally:
        await log.stop()
    # No file should be created for an empty-domain event.
    assert not (tmp_path / "" / "activity").exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_file_activity_log_start_is_idempotent(tmp_path: Path) -> None:
    log = FileActivityLog(tmp_path)
    await log.start()
    await log.start()  # second call must be a no-op
    await log.stop()


# ----------------------------------------------------------------------
# Reader
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activity_reader_filters_by_node_and_type(tmp_path: Path) -> None:
    """Seed JSONL directly (no bus involvement) and verify the reader."""
    activity_dir = tmp_path / "D" / "activity"
    activity_dir.mkdir(parents=True, exist_ok=True)
    path = activity_dir / "2026-07-30.jsonl"
    rows = [
        {
            "id": "1",
            "ts": "2026-07-30T06:30:00",
            "date": "2026-07-30",
            "domain": "D",
            "type": "node_created",
            "node": "A",
            "title": "新建 A",
            "source": "manual",
            "status": "",
            "ref": "node:A",
            "extra": {},
        },
        {
            "id": "2",
            "ts": "2026-07-30T06:31:00",
            "date": "2026-07-30",
            "domain": "D",
            "type": "web_resource_added",
            "node": "A",
            "title": "添加资料",
            "source": "agent",
            "status": "",
            "ref": "web#x",
            "extra": {},
        },
        {
            "id": "3",
            "ts": "2026-07-30T06:32:00",
            "date": "2026-07-30",
            "domain": "D",
            "type": "node_created",
            "node": "B",
            "title": "新建 B",
            "source": "manual",
            "status": "",
            "ref": "node:B",
            "extra": {},
        },
    ]
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )

    reader = ActivityReader(tmp_path)

    # No filter — all rows, newest first.
    items = await reader.read("D")
    assert len(items) == 3
    assert items[0]["id"] == "3"
    assert items[-1]["id"] == "1"

    # Filter by node.
    items_a = await reader.read("D", node="A")
    assert {it["id"] for it in items_a} == {"1", "2"}

    # Filter by type.
    items_node = await reader.read("D", type_="node_created")
    assert {it["id"] for it in items_node} == {"1", "3"}

    # Combined.
    items_both = await reader.read("D", node="A", type_="node_created")
    assert {it["id"] for it in items_both} == {"1"}


@pytest.mark.asyncio
async def test_activity_reader_returns_empty_when_no_log(tmp_path: Path) -> None:
    reader = ActivityReader(tmp_path)
    assert await reader.read("missing") == []
    assert await reader.read("missing", date="2026-07-30") == []


@pytest.mark.asyncio
async def test_activity_reader_tolerates_corrupt_lines(tmp_path: Path) -> None:
    activity_dir = tmp_path / "D" / "activity"
    activity_dir.mkdir(parents=True, exist_ok=True)
    path = activity_dir / "2026-07-30.jsonl"
    path.write_text(
        "not json\n"
        + json.dumps(
            {
                "id": "ok",
                "ts": "2026-07-30T00:00:00",
                "date": "2026-07-30",
                "domain": "D",
                "type": "node_created",
                "node": "x",
                "title": "ok",
                "source": "manual",
                "status": "",
                "ref": "",
                "extra": {},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    reader = ActivityReader(tmp_path)
    items = await reader.read("D")
    assert len(items) == 1
    assert items[0]["id"] == "ok"


# ----------------------------------------------------------------------
# Singletons (lazy init + reset)
# ----------------------------------------------------------------------


def test_get_activity_bus_is_singleton() -> None:
    reset_activity_bus()
    a = get_activity_bus()
    b = get_activity_bus()
    assert a is b


def test_reset_activity_bus_drops_singleton() -> None:
    a = get_activity_bus()
    reset_activity_bus()
    b = get_activity_bus()
    assert a is not b


def test_get_activity_log_is_singleton(tmp_path: Path) -> None:
    import os

    os.environ["KG_KB_ROOT"] = str(tmp_path)
    from src.config import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    try:
        reset_activity_log()
        a = get_activity_log()
        b = get_activity_log()
        assert a is b
        assert a._kb_root == tmp_path
    finally:
        settings_mod.get_settings.cache_clear()
        reset_activity_log()


def test_get_activity_reader_is_singleton(tmp_path: Path) -> None:
    import os

    os.environ["KG_KB_ROOT"] = str(tmp_path)
    from src.config import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    try:
        reset_activity_reader()
        a = get_activity_reader()
        b = get_activity_reader()
        assert a is b
        assert a._kb_root == tmp_path
    finally:
        settings_mod.get_settings.cache_clear()
        reset_activity_reader()


@pytest.mark.asyncio
async def test_start_stop_activity_log_lifecycle(tmp_path: Path) -> None:
    """start() / stop() register and unregister against the bus."""
    import os

    os.environ["KG_KB_ROOT"] = str(tmp_path)
    from src.config import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    reset_activity_bus()
    reset_activity_log()
    try:
        log = await start_activity_log()
        assert log._registered is True

        bus = get_activity_bus()
        await bus.emit(
            ActivityKind.PLAN_CREATED,
            domain="x",
            node="n",
            title="t",
        )
        await asyncio.sleep(0.05)

        await stop_activity_log()
        assert log._registered is False
    finally:
        settings_mod.get_settings.cache_clear()
        reset_activity_bus()
        reset_activity_log()