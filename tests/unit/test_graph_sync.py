"""Unit tests for GraphSyncOrchestrator end-to-end derivation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain.graph.models import Graph, Node
from src.infrastructure.repository.association_repo import AssociationRepository
from src.infrastructure.repository.graph_repo import GraphRepository
from src.infrastructure.repository.note_repo import NoteRepository
from src.infrastructure.repository.plan_repo import PlanRepository
from src.infrastructure.repository.resource_repo import ResourceRepository


@pytest.fixture
def kb_with_domain(tmp_kb_root: Path):
    """预先写入一个最小 domain：3 个节点 + 笔记 + 资源 + 计划。"""
    domain = "d1"
    d = tmp_kb_root / domain
    d.mkdir(parents=True, exist_ok=True)
    g = Graph(domain=domain, nodes=[
        Node(name="A", links=["B"]),
        Node(name="B", links=["C"]),
        Node(name="C", links=[]),
    ])
    (d / "knowledge_graph.json").write_text(
        g.model_dump_json(indent=2), encoding="utf-8"
    )

    # 笔记：@C
    notes_root = d / "notes"
    (notes_root / "A").mkdir(parents=True)
    (notes_root / "A" / "note.md").write_text(
        "这是 A 的笔记。参见 @C。", encoding="utf-8"
    )
    (notes_root / "B").mkdir(parents=True)
    (notes_root / "B" / "note.md").write_text(
        "B 的笔记。", encoding="utf-8"
    )

    # 资源
    web_idx_dir = notes_root / "A" / "web_resources"
    web_idx_dir.mkdir(parents=True, exist_ok=True)
    (web_idx_dir / "index.json").write_text(
        json.dumps([
            {"title": "Article X", "url": "https://example.com/x",
             "summary": "X 文章", "node": "A"},
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    # 计划
    (notes_root / "A" / "plan.json").write_text(
        json.dumps([
            {
                "id": "p001", "goal": "Goal", "actions": [
                    {"id": "a0", "status": "done"},
                    {"id": "a1", "status": "pending"},
                ],
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    return domain


class TestGraphSyncOrchestrator:
    @pytest.mark.asyncio
    async def test_sync_full_derives_all(self, tmp_kb_root: Path, kb_with_domain: str):
        from src.application.graph_sync_orchestrator import GraphSyncOrchestrator

        domain = kb_with_domain
        orch = GraphSyncOrchestrator(
            domain,
            graph_repo=GraphRepository(tmp_kb_root),
            note_repo=NoteRepository(tmp_kb_root),
            resource_repo=ResourceRepository(tmp_kb_root),
            plan_repo=PlanRepository(tmp_kb_root),
            assoc_repo=AssociationRepository(tmp_kb_root),
        )
        stats = await orch.sync_full()
        assert stats["concepts"] == 3
        assert stats["associations"] > 0

        # 验证 associations.json 实际写入
        assoc_repo = AssociationRepository(tmp_kb_root)
        g = await assoc_repo.read(domain)
        names = set(g.concepts.keys())
        assert names == {"A", "B", "C"}

        # PART_OF 边
        part_of = [a for a in g.associations if a.relation.value == "part_of"]
        assert any(a.source == "A" and a.target == "B" for a in part_of)
        assert any(a.source == "B" and a.target == "C" for a in part_of)

        # Note ResourceNode
        notes = [r for r in g.resources.values() if r.type.value == "note"]
        assert len(notes) >= 1

        # 笔记中 @C → REFERENCES 边（note_id → concept:C）
        ref_edges = [
            a for a in g.associations
            if a.relation.value == "references" and a.target == "C"
        ]
        assert len(ref_edges) >= 1

        # HAS_NOTE 边（concept:A → note:A）
        has_note = [
            a for a in g.associations
            if a.relation.value == "has_note"
        ]
        assert any(
            a.source == "concept:A" and a.target == "note:A"
            for a in has_note
        )

        # HAS_RESOURCE 边（concept:A → resource:xxx）
        has_resource = [
            a for a in g.associations
            if a.relation.value == "has_resource"
        ]
        assert any(a.source == "concept:A" for a in has_resource)

        # HAS_PLAN 边（concept:A → plan:A:p001）
        has_plan = [
            a for a in g.associations
            if a.relation.value == "has_plan"
        ]
        assert any(a.source == "concept:A" for a in has_plan)

        # Web ResourceNode
        web_res = [r for r in g.resources.values() if r.type.value == "resource"]
        assert len(web_res) == 1

        # Plan ResourceNode
        plan_res = [r for r in g.resources.values() if r.type.value == "plan"]
        assert len(plan_res) == 1

    @pytest.mark.asyncio
    async def test_sync_for_node_updates_one(self, tmp_kb_root: Path, kb_with_domain: str):
        from src.application.graph_sync_orchestrator import GraphSyncOrchestrator

        domain = kb_with_domain
        orch = GraphSyncOrchestrator(
            domain,
            graph_repo=GraphRepository(tmp_kb_root),
            note_repo=NoteRepository(tmp_kb_root),
            resource_repo=ResourceRepository(tmp_kb_root),
            plan_repo=PlanRepository(tmp_kb_root),
            assoc_repo=AssociationRepository(tmp_kb_root),
        )
        await orch.sync_full()
        await orch.sync_for_node("A", enqueue_llm=False)
        # 不抛异常即可

    @pytest.mark.asyncio
    async def test_delete_node_derived(self, tmp_kb_root: Path, kb_with_domain: str):
        from src.application.graph_sync_orchestrator import GraphSyncOrchestrator

        domain = kb_with_domain
        orch = GraphSyncOrchestrator(
            domain,
            graph_repo=GraphRepository(tmp_kb_root),
            note_repo=NoteRepository(tmp_kb_root),
            resource_repo=ResourceRepository(tmp_kb_root),
            plan_repo=PlanRepository(tmp_kb_root),
            assoc_repo=AssociationRepository(tmp_kb_root),
        )
        await orch.sync_full()
        result = await orch.delete_node_derived("A")
        assert "concepts_remaining" in result

        assoc_repo = AssociationRepository(tmp_kb_root)
        g = await assoc_repo.read(domain)
        assert "A" not in g.concepts

    @pytest.mark.asyncio
    async def test_sync_note_assets(self, tmp_kb_root: Path, kb_with_domain: str):
        from src.application.graph_sync_orchestrator import GraphSyncOrchestrator

        domain = kb_with_domain
        orch = GraphSyncOrchestrator(
            domain,
            graph_repo=GraphRepository(tmp_kb_root),
            note_repo=NoteRepository(tmp_kb_root),
            resource_repo=ResourceRepository(tmp_kb_root),
            plan_repo=PlanRepository(tmp_kb_root),
            assoc_repo=AssociationRepository(tmp_kb_root),
        )
        stats = await orch.sync_note_assets("A")
        assert "resources" in stats

    @pytest.mark.asyncio
    async def test_sync_resource_assets(self, tmp_kb_root: Path, kb_with_domain: str):
        from src.application.graph_sync_orchestrator import GraphSyncOrchestrator

        domain = kb_with_domain
        orch = GraphSyncOrchestrator(
            domain,
            graph_repo=GraphRepository(tmp_kb_root),
            note_repo=NoteRepository(tmp_kb_root),
            resource_repo=ResourceRepository(tmp_kb_root),
            plan_repo=PlanRepository(tmp_kb_root),
            assoc_repo=AssociationRepository(tmp_kb_root),
        )
        stats = await orch.sync_resource_assets("A")
        assert "resources" in stats

    @pytest.mark.asyncio
    async def test_sync_plan_assets(self, tmp_kb_root: Path, kb_with_domain: str):
        from src.application.graph_sync_orchestrator import GraphSyncOrchestrator

        domain = kb_with_domain
        orch = GraphSyncOrchestrator(
            domain,
            graph_repo=GraphRepository(tmp_kb_root),
            note_repo=NoteRepository(tmp_kb_root),
            resource_repo=ResourceRepository(tmp_kb_root),
            plan_repo=PlanRepository(tmp_kb_root),
            assoc_repo=AssociationRepository(tmp_kb_root),
        )
        stats = await orch.sync_plan_assets("A")
        assert "resources" in stats


class TestLevelInference:
    def test_root_level_is_zero(self):
        from src.application.graph_sync_orchestrator import _infer_level_from_node
        g = Graph(domain="d1", nodes=[
            Node(name="A", links=[]),
        ])
        node = g.find_node("A")
        assert _infer_level_from_node(node, g) == 0

    def test_child_level_increments(self):
        from src.application.graph_sync_orchestrator import _infer_level_from_node
        g = Graph(domain="d1", nodes=[
            Node(name="A", links=["B"]),
            Node(name="B", links=["C"]),
            Node(name="C", links=[]),
        ])
        assert _infer_level_from_node(g.find_node("A"), g) == 0
        assert _infer_level_from_node(g.find_node("B"), g) == 1
        assert _infer_level_from_node(g.find_node("C"), g) == 2


class TestSyncForNodeChildConcepts:
    """回归 BUG-2026-08-17-001：sync_for_node 必须让子节点也拥有 ConceptNode。"""

    @pytest.mark.asyncio
    async def test_sync_for_node_creates_child_concepts(self, tmp_kb_root: Path):
        from src.application.graph_sync_orchestrator import GraphSyncOrchestrator

        domain = "d1"
        d = tmp_kb_root / domain
        d.mkdir(parents=True, exist_ok=True)
        # 根 → 1 子节点；模拟 sync_for_node("A") 时 B 的 ConceptNode 还未派生
        g = Graph(domain=domain, nodes=[
            Node(name="A", links=["B"]),
            Node(name="B", links=[]),
        ])
        (d / "knowledge_graph.json").write_text(
            g.model_dump_json(indent=2), encoding="utf-8"
        )

        orch = GraphSyncOrchestrator(
            domain,
            graph_repo=GraphRepository(tmp_kb_root),
            note_repo=NoteRepository(tmp_kb_root),
            resource_repo=ResourceRepository(tmp_kb_root),
            plan_repo=PlanRepository(tmp_kb_root),
            assoc_repo=AssociationRepository(tmp_kb_root),
        )
        # 注意：跳过 sync_full，直接 sync_for_node("A")
        stats = await orch.sync_for_node("A", enqueue_llm=False)

        assoc_repo = AssociationRepository(tmp_kb_root)
        result = await assoc_repo.read(domain)

        # 关键断言：A 和 B 都在 concepts 字典里（修复前只有 A）
        assert "A" in result.concepts
        assert "B" in result.concepts, (
            "BUG-2026-08-17-001 回归：sync_for_node 没有派生子节点 ConceptNode"
        )
        # PART_OF 边应保留
        part_of = [a for a in result.associations if a.relation.value == "part_of"]
        assert any(a.source == "A" and a.target == "B" for a in part_of)
        assert stats["concepts"] == 2

    @pytest.mark.asyncio
    async def test_ensure_part_of_edges_backfills_missing_concepts(
        self, tmp_kb_root: Path,
    ):
        """_ensure_part_of_edges 兜底：边两端 concept 缺失时补齐。"""
        from src.application.graph_sync_orchestrator import (
            GraphSyncOrchestrator,
        )
        from src.domain.graph.association import AssociationGraph

        domain = "d1"
        d = tmp_kb_root / domain
        d.mkdir(parents=True, exist_ok=True)
        g = Graph(domain=domain, nodes=[
            Node(name="A", links=["B"]),
            Node(name="B", links=[]),
        ])
        (d / "knowledge_graph.json").write_text(
            g.model_dump_json(indent=2), encoding="utf-8"
        )

        # 初始 associations.json 完全空白（concepts={}, associations=[]）
        empty = AssociationGraph(domain=domain)
        orch = GraphSyncOrchestrator(
            domain,
            graph_repo=GraphRepository(tmp_kb_root),
            note_repo=NoteRepository(tmp_kb_root),
            resource_repo=ResourceRepository(tmp_kb_root),
            plan_repo=PlanRepository(tmp_kb_root),
            assoc_repo=AssociationRepository(tmp_kb_root),
        )
        result = orch._ensure_part_of_edges(empty, g)

        # 兜底：A 和 B 的 ConceptNode 都被自动创建
        assert "A" in result.concepts
        assert "B" in result.concepts
        # PART_OF 边存在
        assert any(
            a.source == "A" and a.target == "B"
            and a.relation.value == "part_of"
            for a in result.associations
        )


__all__ = []