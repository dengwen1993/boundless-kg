"""Application layer — graph/note/plan/timeline services."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.application.graph_service import GraphService
from src.application.note_service import NoteService
from src.application.plan_service import PlanService
from src.application.resource_service import ResourceService
from src.application.timeline_service import TimelineService
from src.domain.graph import Direction, Graph, Node
from src.domain.note.generator import NoteGenerator
from src.infrastructure.llm import MockLLMClient
from src.infrastructure.repository.graph_repo import GraphRepository
from src.infrastructure.repository.note_repo import NoteRepository
from src.infrastructure.repository.plan_repo import PlanRepository
from src.infrastructure.repository.resource_repo import ResourceRepository
from src.infrastructure.repository.timeline_repo import TimelineRepository


# ---------- graph service ----------


async def test_graph_service_view_returns_empty_for_missing(tmp_kb_root: Path) -> None:
    svc = GraphService(GraphRepository(tmp_kb_root))
    g = await svc.view("missing")
    assert g.domain == "missing"
    assert g.nodes == []


async def test_graph_service_validate_and_score(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    await repo.write_graph(
        "d",
        Graph(
            domain="d",
            direction=Direction(summary="x" * 30),
            nodes=[Node(name="a"), Node(name="b", links=["a"])],
        ),
    )
    svc = GraphService(repo)
    issues = await svc.validate("d")
    score = await svc.score("d")
    assert isinstance(issues, list)
    assert score.coverage >= 5


async def test_graph_service_save_sets_generated_at(tmp_kb_root: Path) -> None:
    svc = GraphService(GraphRepository(tmp_kb_root))
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[Node(name="only")],
    )
    await svc.save_graph(g)
    assert g.generated_at is not None
    g2 = await svc.view("d")
    assert g2.generated_at == g.generated_at


async def test_graph_service_list_domains(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    await repo.write_graph(
        "d1",
        Graph(domain="d1", direction=Direction(summary="x" * 30), nodes=[Node(name="a")]),
    )
    svc = GraphService(repo)
    summaries = await svc.list_domains()
    assert {s.domain for s in summaries} == {"d1"}
    assert summaries[0].node_count == 1


async def test_graph_service_fix_links(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    await repo.write_graph(
        "d",
        Graph(
            domain="d",
            direction=Direction(summary="x" * 30),
            nodes=[
                Node(name="root", links=["A"]),
                Node(name="A", links=["B"]),
                Node(name="B", links=["A"]),
            ],
        ),
    )
    svc = GraphService(repo)
    removed, scanned = await svc.fix_links("d")
    assert removed == 1
    assert scanned == 3


# ---------- note service ----------


async def test_note_service_get_or_generate_caches(tmp_kb_root: Path) -> None:
    graph_repo = GraphRepository(tmp_kb_root)
    note_repo = NoteRepository(tmp_kb_root)
    await graph_repo.write_graph(
        "d",
        Graph(
            domain="d",
            direction=Direction(summary="summary path"),
            nodes=[Node(name="a")],
        ),
    )
    llm = MockLLMClient(latency_sec=0)
    svc = NoteService(llm, graph_repo, note_repo)
    res1 = await svc.get_or_generate("d", "a")
    assert res1.created is True
    res2 = await svc.get_or_generate("d", "a")
    assert res2.created is False
    assert res2.content == res1.content


async def test_note_service_force_regenerates(tmp_kb_root: Path) -> None:
    graph_repo = GraphRepository(tmp_kb_root)
    note_repo = NoteRepository(tmp_kb_root)
    await graph_repo.write_graph(
        "d",
        Graph(
            domain="d",
            direction=Direction(summary="summary path"),
            nodes=[Node(name="a")],
        ),
    )
    svc = NoteService(MockLLMClient(latency_sec=0), graph_repo, note_repo)
    await svc.get_or_generate("d", "a")
    res = await svc.get_or_generate("d", "a", force=True)
    assert res.created is True


# ---------- plan / timeline services ----------


async def test_plan_service_add_and_list(tmp_kb_root: Path) -> None:
    svc = PlanService(PlanRepository(tmp_kb_root))
    plan = await svc.add("d", node="alpha", goal="Learn X", steps=["a", "b"])
    assert plan["status"] == "pending"
    assert [a["content"] for a in plan["actions"]] == ["a", "b"]
    # Written where the API / timeline look for it.
    assert (tmp_kb_root / "d" / "notes" / "alpha" / "plan.json").exists()

    plans = await svc.list("d", "alpha")
    assert len(plans) == 1
    assert plans[0]["goal"] == "Learn X"

    # Domain-wide listing tags each plan with its node.
    all_plans = await svc.list("d")
    assert [p["node"] for p in all_plans] == ["alpha"]


async def test_plan_service_add_requires_node(tmp_kb_root: Path) -> None:
    svc = PlanService(PlanRepository(tmp_kb_root))
    with pytest.raises(ValueError):
        await svc.add("d", node="", goal="orphan")


async def test_plan_service_update_status(tmp_kb_root: Path) -> None:
    svc = PlanService(PlanRepository(tmp_kb_root))
    plan = await svc.add("d", node="alpha", goal="t", steps=["s1", "s2"])

    # Single action → plan stays pending.
    updated = await svc.update_status("d", "alpha", plan["id"], "done", "a0")
    assert updated["actions"][0]["status"] == "done"
    assert updated["actions"][0]["done_at"]
    assert updated["status"] == "pending"

    # All actions → plan rolls up to done.
    updated = await svc.update_status("d", "alpha", plan["id"], "done")
    assert updated["status"] == "done"


async def test_plan_service_find_and_delete(tmp_kb_root: Path) -> None:
    svc = PlanService(PlanRepository(tmp_kb_root))
    plan = await svc.add("d", node="alpha", goal="t")
    found = await svc.find_plan("d", plan["id"])
    assert found["node"] == "alpha"
    assert await svc.delete("d", "alpha", plan["id"]) is True
    assert await svc.find_plan("d", plan["id"]) is None


async def test_plan_service_migrates_legacy_domain_file(tmp_kb_root: Path) -> None:
    """Old ``{domain}/plan.json`` → per-node array the frontend can read."""
    legacy = tmp_kb_root / "d" / "plan.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps(
            {
                "plans": [
                    {
                        "id": "old-1",
                        "title": "Prompt 工程一日计划",
                        "node": "alpha",
                        "steps": ["09:00 基础", "14:00 结构化输出"],
                        "due": "2026-07-28",
                        "status": "pending",
                        "created_at": "2026-07-27T10:34:31",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    svc = PlanService(PlanRepository(tmp_kb_root))
    assert await svc.migrate_legacy("d") == 1

    plans = await svc.list("d", "alpha")
    assert len(plans) == 1
    p = plans[0]
    assert p["goal"] == "Prompt 工程一日计划"
    assert p["date"] == "2026-07-28"
    assert len(p["actions"]) == 2
    assert not legacy.exists()

    # Idempotent: a second pass must not duplicate.
    assert await svc.migrate_legacy("d") == 0
    assert len(await svc.list("d", "alpha")) == 1


async def test_timeline_service_aggregates(tmp_kb_root: Path) -> None:
    """Timeline now reads from JSONL activity logs, not data files.

    Write three activity events (plan_created, note_generated,
    web_resource_added) to the domain's activity JSONL and verify
    the timeline service surfaces them.
    """
    import json as _json

    activity_dir = tmp_kb_root / "d" / "activity"
    activity_dir.mkdir(parents=True, exist_ok=True)
    events = [
        {"type": "plan_created", "ts": "2024-01-01T10:00:00", "date": "2024-01-01", "domain": "d", "node": "alpha", "title": "新增了计划", "source": "manual"},
        {"type": "note_generated", "ts": "2024-01-02T10:00:00", "date": "2024-01-02", "domain": "d", "node": "alpha", "title": "生成了笔记", "source": "agent"},
        {"type": "web_resource_added", "ts": "2024-02-01T10:00:00", "date": "2024-02-01", "domain": "d", "node": "alpha", "title": "搜索了资料", "source": "agent"},
    ]
    with open(activity_dir / "2024-01-01.jsonl", "w", encoding="utf-8") as f:
        f.write(_json.dumps(events[0], ensure_ascii=False) + "\n")
        f.write(_json.dumps(events[1], ensure_ascii=False) + "\n")
    with open(activity_dir / "2024-02-01.jsonl", "w", encoding="utf-8") as f:
        f.write(_json.dumps(events[2], ensure_ascii=False) + "\n")

    svc = TimelineService(TimelineRepository(tmp_kb_root))
    items = await svc.feed("d", limit=10)
    types = sorted({it["type"] for it in items})
    assert types == ["note_generated", "plan_created", "web_resource_added"]


# ---------- resource service ----------


async def test_resource_service_search_returns_empty_when_no_client(tmp_kb_root: Path) -> None:
    svc = ResourceService(
        MockLLMClient(),
        GraphRepository(tmp_kb_root),
        ResourceRepository(tmp_kb_root),
        search_client=None,
    )
    out = await svc.search("d", "x")
    assert out == []