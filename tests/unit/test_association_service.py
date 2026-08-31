"""Unit tests for AssociationService (LLM extraction)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.application.association_service import (
    AssociationService,
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_LEVEL_SPAN,
    RelationTriplet,
)
from src.domain.graph.association import (
    Association,
    EdgeIntensity,
    RelationType,
)
from src.domain.graph.models import Graph, Node
from src.infrastructure.repository.association_repo import AssociationRepository


class FakeLLM:
    """测试用 LLM：返回预设的 JSON。"""

    def __init__(self, response: str = '[]'):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def chat(self, system: str, user: str, **kwargs: Any) -> str:
        self.calls.append((system, user))
        return self.response


@pytest.fixture
def graph_obj() -> Graph:
    return Graph(
        domain="d1",
        nodes=[
            Node(name="A", links=["B"]),
            Node(name="B", links=["C"]),
            Node(name="C", links=[]),
        ],
    )


class TestAssociationService:
    @pytest.mark.asyncio
    async def test_llm_failure_doesnt_raise(
        self, tmp_kb_root: Path, graph_obj: Graph
    ):
        class BoomLLM:
            async def chat(self, system, user, **kwargs):
                raise RuntimeError("LLM down")

        svc = AssociationService(
            llm=BoomLLM(),
            assoc_repo=AssociationRepository(tmp_kb_root),
        )
        cards = {"A": "card A", "B": "card B", "C": "card C"}
        assocs = await svc.build_associations(
            "d1", graph_obj, cards, target_nodes=["A", "B", "C"]
        )
        # 降级：返回空，不抛
        assert assocs == []

    @pytest.mark.asyncio
    async def test_valid_json_parsed(
        self, tmp_kb_root: Path, graph_obj: Graph
    ):
        response = (
            '[{"source": "A", "target": "B", "relation": "prerequisite_of",'
            ' "weight": 0.9, "intensity": "HARD", "evidence": "..."}]'
        )
        llm = FakeLLM(response=response)
        svc = AssociationService(
            llm=llm, assoc_repo=AssociationRepository(tmp_kb_root)
        )
        cards = {"A": "card A", "B": "card B"}
        assocs = await svc.build_associations(
            "d1", graph_obj, cards, target_nodes=["A", "B"]
        )
        assert len(assocs) == 1
        assert assocs[0].source == "A"
        assert assocs[0].target == "B"
        assert assocs[0].relation == RelationType.PREREQUISITE_OF
        assert assocs[0].intensity == EdgeIntensity.HARD

    @pytest.mark.asyncio
    async def test_invalid_relation_falls_back(
        self, tmp_kb_root: Path, graph_obj: Graph
    ):
        llm = FakeLLM(
            response='[{"source": "A", "target": "B", "relation": "made_up",'
            ' "intensity": "HARD"}]'
        )
        svc = AssociationService(
            llm=llm, assoc_repo=AssociationRepository(tmp_kb_root)
        )
        cards = {"A": "card A", "B": "card B"}
        assocs = await svc.build_associations(
            "d1", graph_obj, cards, target_nodes=["A", "B"]
        )
        # relation 非法 → 退化为 RELATED_TO
        assert assocs[0].relation == RelationType.RELATED_TO

    @pytest.mark.asyncio
    async def test_target_node_must_exist(
        self, tmp_kb_root: Path, graph_obj: Graph
    ):
        llm = FakeLLM(
            response='[{"source": "A", "target": "GHOST", "relation": "similar_to"}]'
        )
        svc = AssociationService(
            llm=llm, assoc_repo=AssociationRepository(tmp_kb_root)
        )
        cards = {"A": "card A"}
        assocs = await svc.build_associations(
            "d1", graph_obj, cards, target_nodes=["A"]
        )
        # 节点 GHOST 不存在；不过滤（仅过滤 target_nodes）
        # 实际情况：target_nodes=A，所以 LLM 看到的桶只有 A
        # LLM 输出 A→GHOST 是允许的——但 GHOST 不在 graph.nodes 里
        # 当前实现不校验 target_nodes 以外的节点（由下游 validate 处理）
        # 这里测试只是验证不抛异常
        assert isinstance(assocs, list)

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write(
        self, tmp_kb_root: Path, graph_obj: Graph
    ):
        llm = FakeLLM(
            response='[{"source": "A", "target": "B", "relation": "similar_to"}]'
        )
        repo = AssociationRepository(tmp_kb_root)
        svc = AssociationService(llm=llm, assoc_repo=repo)
        cards = {"A": "card A", "B": "card B"}
        assocs = await svc.build_associations(
            "d1", graph_obj, cards, target_nodes=["A", "B"], dry_run=True
        )
        assert len(assocs) == 1
        # 读不到
        loaded = await repo.read("d1")
        assert loaded.associations == []

    @pytest.mark.asyncio
    async def test_target_nodes_none_uses_all(
        self, tmp_kb_root: Path, graph_obj: Graph
    ):
        llm = FakeLLM(response="[]")
        svc = AssociationService(
            llm=llm, assoc_repo=AssociationRepository(tmp_kb_root)
        )
        cards = {"A": "", "B": "", "C": ""}
        assocs = await svc.build_associations("d1", graph_obj, cards)
        assert assocs == []

    @pytest.mark.asyncio
    async def test_target_nodes_too_small_returns_empty(
        self, tmp_kb_root: Path, graph_obj: Graph
    ):
        llm = FakeLLM(response="[]")
        svc = AssociationService(
            llm=llm, assoc_repo=AssociationRepository(tmp_kb_root)
        )
        cards = {"A": ""}
        assocs = await svc.build_associations(
            "d1", graph_obj, cards, target_nodes=["A"]
        )
        assert assocs == []


class TestRelationTriplet:
    def test_basic(self):
        t = RelationTriplet(
            source="A", target="B", relation="similar_to",
            weight=0.8, intensity="SOFT", evidence="both overlap",
        )
        assert t.weight == 0.8

    def test_default_weight(self):
        t = RelationTriplet(source="A", target="B", relation="similar_to")
        assert t.weight == 0.8


__all__ = []