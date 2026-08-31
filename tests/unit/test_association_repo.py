"""Unit tests for AssociationRepository (associations.json CRUD)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.domain.graph.association import (
    Association,
    AssociationGraph,
    ConceptNode,
    EdgeIntensity,
    RelationType,
    ResourceNode,
    ResourceType,
    make_concept_id,
    make_resource_id,
)
from src.infrastructure.repository.association_repo import AssociationRepository


class TestAssociationRepository:
    @pytest.mark.asyncio
    async def test_read_missing_returns_empty(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        g = await repo.read("nonexistent")
        assert g.domain == "nonexistent"
        assert g.concepts == {}
        assert g.associations == []
        assert g.resources == {}

    @pytest.mark.asyncio
    async def test_write_then_read(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        g = AssociationGraph(domain="d1")
        g.concepts["A"] = ConceptNode(
            id=make_concept_id("A"), name="A", domain="d1"
        )
        await repo.write("d1", g)

        loaded = await repo.read("d1")
        assert "A" in loaded.concepts

    @pytest.mark.asyncio
    async def test_upsert_concept(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        c = ConceptNode(id=make_concept_id("A"), name="A", domain="d1")
        await repo.upsert_concept("d1", c)

        c2 = ConceptNode(id=make_concept_id("A"), name="A", domain="d1", level=2)
        await repo.upsert_concept("d1", c2)

        loaded = await repo.read("d1")
        assert loaded.concepts["A"].level == 2

    @pytest.mark.asyncio
    async def test_upsert_resource(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        r = ResourceNode(
            id=make_resource_id(ResourceType.NOTE, "X"),
            type=ResourceType.NOTE, node="X", domain="d1",
        )
        await repo.upsert_resource("d1", r)
        loaded = await repo.read("d1")
        assert make_resource_id(ResourceType.NOTE, "X") in loaded.resources

    @pytest.mark.asyncio
    async def test_delete_concept_cascades_edges(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        g = AssociationGraph(domain="d1")
        g.concepts["A"] = ConceptNode(id=make_concept_id("A"), name="A", domain="d1")
        g.concepts["B"] = ConceptNode(id=make_concept_id("B"), name="B", domain="d1")
        g.associations.append(
            Association(source="A", target="B", relation=RelationType.PART_OF)
        )
        g.associations.append(
            Association(source="B", target="C", relation=RelationType.SIMILAR_TO)
        )
        await repo.write("d1", g)

        await repo.delete_concept("d1", "A")
        loaded = await repo.read("d1")
        assert "A" not in loaded.concepts
        # 所有涉及 A 的边都被清理
        assert all(a.source != "A" and a.target != "A" for a in loaded.associations)

    @pytest.mark.asyncio
    async def test_delete_resource(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        rid = make_resource_id(ResourceType.NOTE, "X")
        g = AssociationGraph(domain="d1")
        g.resources[rid] = ResourceNode(
            id=rid, type=ResourceType.NOTE, node="X", domain="d1"
        )
        await repo.write("d1", g)

        await repo.delete_resource("d1", rid)
        loaded = await repo.read("d1")
        assert rid not in loaded.resources

    @pytest.mark.asyncio
    async def test_add_association_dedup(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        a = Association(source="A", target="B", relation=RelationType.PART_OF)
        await repo.add_association("d1", a)
        await repo.add_association("d1", a)  # dedup
        loaded = await repo.read("d1")
        assert len(loaded.associations) == 1

    @pytest.mark.asyncio
    async def test_add_associations_batch(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        a1 = Association(source="A", target="B", relation=RelationType.PART_OF)
        a2 = Association(source="B", target="C", relation=RelationType.SIMILAR_TO)
        await repo.add_associations_batch("d1", [a1, a2])
        loaded = await repo.read("d1")
        assert len(loaded.associations) == 2

    @pytest.mark.asyncio
    async def test_delete_association(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        await repo.add_association(
            "d1",
            Association(source="A", target="B", relation=RelationType.PART_OF),
        )
        await repo.delete_association("d1", "A", "B", RelationType.PART_OF)
        loaded = await repo.read("d1")
        assert loaded.associations == []

    @pytest.mark.asyncio
    async def test_mark_events_derived(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        await repo.mark_events_derived("d1", ["e1", "e2"])
        loaded = await repo.read("d1")
        assert "e1" in loaded.metadata.derived_events
        assert "e2" in loaded.metadata.derived_events

    @pytest.mark.asyncio
    async def test_clear(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        await repo.write(
            "d1",
            AssociationGraph(
                domain="d1",
                concepts={"A": ConceptNode(id="concept:A", name="A", domain="d1")},
            ),
        )
        await repo.clear("d1")
        # 读不到 — 返回空 AssociationGraph
        loaded = await repo.read("d1")
        assert loaded.concepts == {}

    @pytest.mark.asyncio
    async def test_read_corrupted_returns_empty(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        # 写入损坏的 JSON
        path = tmp_kb_root / "d1"
        path.mkdir(parents=True, exist_ok=True)
        (path / "associations.json").write_text("{not json", encoding="utf-8")
        loaded = await repo.read("d1")
        assert loaded.concepts == {}

    @pytest.mark.asyncio
    async def test_read_raw(self, tmp_kb_root: Path):
        repo = AssociationRepository(tmp_kb_root)
        g = AssociationGraph(domain="d1")
        g.concepts["A"] = ConceptNode(id="concept:A", name="A", domain="d1")
        await repo.write("d1", g)
        raw = await repo.read_raw("d1")
        assert "concepts" in raw
        assert "A" in raw["concepts"]


__all__ = []