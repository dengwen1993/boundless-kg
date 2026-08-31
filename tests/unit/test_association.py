"""Unit tests for the association layer domain models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.domain.graph.association import (
    Association,
    AssociationGraph,
    AssociationMetadata,
    ConceptNode,
    DEFAULT_INTENSITY_BY_RELATION,
    EdgeIntensity,
    RelationType,
    ResourceNode,
    ResourceType,
    _utcnow,
    concept_id_to_name,
    make_association_id,
    make_concept_id,
    make_resource_id,
)


class TestRelationType:
    def test_values(self):
        assert RelationType.PART_OF.value == "part_of"
        assert RelationType.PREREQUISITE_OF.value == "prerequisite_of"

    def test_default_intensity(self):
        assert DEFAULT_INTENSITY_BY_RELATION[RelationType.PART_OF] == EdgeIntensity.STRUCTURAL
        assert DEFAULT_INTENSITY_BY_RELATION[RelationType.PREREQUISITE_OF] == EdgeIntensity.HARD
        assert DEFAULT_INTENSITY_BY_RELATION[RelationType.SIMILAR_TO] == EdgeIntensity.SOFT


class TestIdMakers:
    def test_make_concept_id(self):
        assert make_concept_id("Transformer") == "concept:Transformer"

    def test_concept_id_to_name(self):
        assert concept_id_to_name("concept:Transformer") == "Transformer"

    def test_make_resource_id(self):
        rid = make_resource_id(ResourceType.NOTE, "Transformer")
        assert rid == "note:Transformer"

        rid2 = make_resource_id(ResourceType.PLAN, "Transformer:p001")
        assert rid2 == "plan:Transformer:p001"

    def test_make_association_id(self):
        aid = make_association_id("A", "B", RelationType.PREREQUISITE_OF)
        assert aid == "assoc:prerequisite_of:A→B"


class TestConceptNode:
    def test_basic(self):
        c = ConceptNode(id="concept:X", name="X", domain="d1")
        assert c.name == "X"
        assert c.level == 0
        # level=0 ⇒ is_root 自动为 True
        assert c.is_root is True
        assert isinstance(c.updated_at, datetime)

    def test_root_flag(self):
        c = ConceptNode(id="concept:X", name="X", domain="d1", level=0)
        assert c.is_root is True

    def test_non_root(self):
        c = ConceptNode(id="concept:X", name="X", domain="d1", level=1)
        assert c.is_root is False

    def test_negative_level_clamped(self):
        c = ConceptNode(id="concept:X", name="X", domain="d1", level=-5)
        assert c.level == 0


class TestResourceNode:
    def test_note(self):
        r = ResourceNode(
            id="note:X", type=ResourceType.NOTE, node="X", domain="d1",
            payload={"path": "notes/X/note.md"},
        )
        assert r.type == ResourceType.NOTE
        assert r.node == "X"
        assert r.payload["path"] == "notes/X/note.md"


class TestAssociation:
    def test_basic(self):
        a = Association(source="A", target="B", relation=RelationType.SIMILAR_TO)
        assert a.weight == 1.0
        assert a.intensity == EdgeIntensity.SOFT
        assert a.created_by == "llm"
        assert isinstance(a.created_at, datetime)

    def test_empty_name_rejected(self):
        with pytest.raises(Exception):  # ValidationError
            Association(source="", target="B", relation=RelationType.RELATED_TO)

    def test_invalid_created_by_normalized(self):
        a = Association(
            source="A", target="B", relation=RelationType.RELATED_TO,
            created_by="agent",
        )
        assert a.created_by == "llm"

    def test_weight_bounds(self):
        with pytest.raises(Exception):
            Association(
                source="A", target="B", relation=RelationType.RELATED_TO,
                weight=1.5,
            )
        with pytest.raises(Exception):
            Association(
                source="A", target="B", relation=RelationType.RELATED_TO,
                weight=-0.1,
            )


class TestAssociationGraph:
    def _build(self) -> AssociationGraph:
        return AssociationGraph(
            domain="d1",
            concepts={
                "A": ConceptNode(id="concept:A", name="A", domain="d1", level=0),
                "B": ConceptNode(id="concept:B", name="B", domain="d1", level=1),
            },
            resources={},
            associations=[
                Association(source="A", target="B", relation=RelationType.PART_OF),
                Association(source="B", target="A", relation=RelationType.SIMILAR_TO),
                Association(source="C", target="A", relation=RelationType.PREREQUISITE_OF),
            ],
        )

    def test_statistics(self):
        g = self._build()
        stats = g.statistics()
        assert stats["concepts"] == 2
        assert stats["associations"] == 3

    def test_neighbors_1hop(self):
        g = self._build()
        nb = g.neighbors("A", max_hops=1)
        names = {n for n, _, _ in nb}
        assert "B" in names

    def test_neighbors_with_relation_filter(self):
        g = self._build()
        nb = g.neighbors("A", relation=RelationType.SIMILAR_TO, max_hops=1)
        names = {n for n, _, _ in nb}
        assert names == {"B"}

    def test_edges_for_node(self):
        g = self._build()
        edges = list(g.edges_for_node("A"))
        assert len(edges) == 3  # PART_OF A->B, SIMILAR_TO B->A, PREREQUISITE_OF C->A


class TestAssociationMetadata:
    def test_mark_derived(self):
        m = AssociationMetadata()
        m.mark_derived(["e1", "e2"])
        assert m.is_derived("e1")
        assert m.is_derived("e2")
        assert not m.is_derived("e3")

    def test_mark_derived_idempotent(self):
        m = AssociationMetadata()
        m.mark_derived(["e1"])
        m.mark_derived(["e1"])
        assert len(m.derived_events) == 1


__all__ = []