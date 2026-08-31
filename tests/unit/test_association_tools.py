"""Unit tests for ``kg_add_edge`` / ``kg_delete_edge`` agent tools.

回归覆盖
--------
1. ``kg_add_edge`` 写入 associations.json + 触发 ActivityBus + 调 FalkorDB 镜像（无 FalkorDB 时非阻塞）
2. ``kg_delete_edge`` 幂等删除（不存在的边返回 ok=true 但 removed=false）
3. 参数校验：source == target / 空字符串 / has_* 关系拒绝
4. FalkorDB 不可用时返回 falkordb_synced=None 而非抛异常
5. 与 ``get_association_repo`` 集成：写入后 ``kg_query_neighbors`` 立即可见
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.agent import dependencies as agent_deps
from src.agent.tools import association_tools as at_mod
from src.domain.graph.association import (
    Association,
    AssociationGraph,
    ConceptNode,
    EdgeIntensity,
    RelationType,
    make_concept_id,
)
from src.infrastructure.repository.association_repo import AssociationRepository
from src.observability.activity_bus import ActivityKind, reset_activity_bus


# ----------------------------------------------------------------------
# Test helpers
# ----------------------------------------------------------------------


class _FakeStore:
    """Stand-in for ``GraphStoreClient`` — records ``add_edge_any`` / ``delete_edge`` calls.

    默认 ``available=True``；要模拟 FalkorDB 不可达，传 ``available=False``。
    当 ``available=True`` 时，kg_add_edge 会先调 ``export_graph`` 读概念列表
    做存在性检查，所以要传 ``export_data`` 让它返回正确的 concept 字典。
    """

    def __init__(
        self,
        available: bool = True,
        export_data: dict | None = None,
    ) -> None:
        self.available = available
        self.export_data = export_data or {
            "domain": "",
            "concepts": {},
            "resources": {},
            "associations": [],
        }
        self.added: list[dict] = []
        self.deleted: list[dict] = []

    async def ensure_available(self) -> bool:
        return self.available

    def export_graph(self, *args, **kwargs) -> dict:
        return self.export_data

    def add_edge_any(self, *args, **kwargs) -> bool:
        self.added.append({"args": args, "kwargs": kwargs})
        return True

    def delete_edge(self, *args, **kwargs) -> bool:
        self.deleted.append({"args": args, "kwargs": kwargs})
        return True


def _NullStore() -> _FakeStore:
    """``ensure_available`` 返回 False —— FalkorDB 不可达，所有读路径走 JSON repo。"""
    return _FakeStore(available=False)


@pytest.fixture
def reset_bus():
    """Reset the singleton activity bus around each test."""
    reset_activity_bus()
    yield
    reset_activity_bus()


@pytest.fixture
def patched_repo(tmp_path: Path, monkeypatch, clean_env):
    """Per-test kb_root + cleared singletons + 替换 graph_store 为 fake 默认行为。

    默认 patch 到 ``_NullStore()``（FalkorDB 不可达 → 走 JSON repo 读路径）。
    需要 FalkorDB 可用的测试可以再调一次 ``_swap_store``。
    """
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    monkeypatch.setenv("KG_KB_ROOT", str(kb_root))
    from src.config import reload_settings

    reload_settings()
    agent_deps.reset_dependencies()

    # 默认 patch 成 NullStore（不可达），让所有读路径走 JSON repo
    monkeypatch.setattr(agent_deps, "get_graph_store", lambda: _NullStore())
    monkeypatch.setattr(at_mod, "agent_deps", agent_deps)

    yield kb_root


def _swap_store(monkeypatch, store: _FakeStore) -> None:
    """在已 patched 的 fixture 里再换一次 store。"""
    monkeypatch.setattr(agent_deps, "get_graph_store", lambda: store)
    monkeypatch.setattr(at_mod, "agent_deps", agent_deps)


async def _seed_two_concepts(kb_root: Path, domain: str, a: str, b: str) -> None:
    """把两个概念节点塞进 associations.json。"""
    repo = AssociationRepository(kb_root)
    g = AssociationGraph(domain=domain)
    for n in (a, b):
        g.concepts[n] = ConceptNode(id=make_concept_id(n), name=n, domain=domain)
    await repo.write(domain, g)


# ----------------------------------------------------------------------
# kg_add_edge
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_edge_writes_to_json(patched_repo, reset_bus) -> None:
    """核心路径：写入 associations.json + 默认 intensity 兜底 + FalkorDB 不可用时不阻塞。"""
    kb_root = patched_repo
    await _seed_two_concepts(kb_root, "d1", "A", "B")

    out = await at_mod.kg_add_edge.ainvoke(
        {
            "domain": "d1",
            "source": "A",
            "target": "B",
            "relation": "prerequisite_of",
            "weight": 0.9,
            "intensity": "HARD",
            "evidence": "test",
        }
    )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["total"] == 1
    assert "已添加" in payload["message"]
    assert payload["falkordb_synced"] is None  # FalkorDB 不可用 → None

    fresh = await AssociationRepository(kb_root).read("d1")
    assert len(fresh.associations) == 1
    a = fresh.associations[0]
    assert a.source == "A"
    assert a.target == "B"
    assert a.relation == RelationType.PREREQUISITE_OF
    assert a.intensity == EdgeIntensity.HARD
    assert a.weight == 0.9
    assert a.evidence == "test"
    assert a.created_by == "system"


@pytest.mark.asyncio
async def test_add_edge_dedupes_same_triple(patched_repo, reset_bus) -> None:
    """按 (source, target, relation) 去重——再次添加不增加边数。"""
    kb_root = patched_repo
    await _seed_two_concepts(kb_root, "d2", "X", "Y")

    out1 = await at_mod.kg_add_edge.ainvoke(
        {"domain": "d2", "source": "X", "target": "Y", "relation": "related_to"}
    )
    out2 = await at_mod.kg_add_edge.ainvoke(
        {"domain": "d2", "source": "X", "target": "Y", "relation": "related_to"}
    )
    p1, p2 = json.loads(out1), json.loads(out2)
    assert p1["ok"] and p2["ok"]
    assert p1["total"] == 1
    assert p2["total"] == 1  # dedupe 生效


@pytest.mark.asyncio
async def test_add_edge_default_intensity_per_relation(patched_repo, reset_bus) -> None:
    """intensity 空时按 DEFAULT_INTENSITY_BY_RELATION 兜底。"""
    kb_root = patched_repo
    await _seed_two_concepts(kb_root, "d3", "P", "Q")

    out = await at_mod.kg_add_edge.ainvoke(
        {"domain": "d3", "source": "P", "target": "Q", "relation": "part_of"}
    )
    assert json.loads(out)["ok"] is True

    fresh = await AssociationRepository(kb_root).read("d3")
    assert fresh.associations[0].intensity == EdgeIntensity.STRUCTURAL


@pytest.mark.asyncio
async def test_add_edge_rejects_self_loop(patched_repo, reset_bus) -> None:
    """source == target 直接拒。"""
    kb_root = patched_repo
    await _seed_two_concepts(kb_root, "d4", "S", "T")

    out = await at_mod.kg_add_edge.ainvoke(
        {"domain": "d4", "source": "S", "target": "S", "relation": "related_to"}
    )
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "不能相同" in payload["error"]


@pytest.mark.asyncio
async def test_add_edge_rejects_empty_source_or_target(patched_repo, reset_bus) -> None:
    """空字符串 / 纯空格 → 拒绝。"""
    out1 = await at_mod.kg_add_edge.ainvoke(
        {"domain": "d", "source": "  ", "target": "B", "relation": "related_to"}
    )
    assert json.loads(out1)["ok"] is False

    out2 = await at_mod.kg_add_edge.ainvoke(
        {"domain": "d", "source": "A", "target": "", "relation": "related_to"}
    )
    assert json.loads(out2)["ok"] is False


@pytest.mark.asyncio
async def test_add_edge_rejects_has_relations(patched_repo, reset_bus) -> None:
    """has_note / has_resource / has_plan / cites / references 不允许手动加。"""
    kb_root = patched_repo
    await _seed_two_concepts(kb_root, "d5", "A", "B")

    for bad_rel in ("has_note", "has_resource", "has_plan", "cites", "references"):
        out = await at_mod.kg_add_edge.ainvoke(
            {"domain": "d5", "source": "A", "target": "B", "relation": bad_rel}
        )
        payload = json.loads(out)
        assert payload["ok"] is False, f"{bad_rel} should be rejected"
        assert "has_*/cites/references" in payload["error"]


@pytest.mark.asyncio
async def test_add_edge_rejects_unknown_node(patched_repo, reset_bus) -> None:
    """节点不存在 → 返回 ok=false + 错误信息。"""
    kb_root = patched_repo
    await _seed_two_concepts(kb_root, "d6", "A", "B")  # B 存在，但 Ghost 不存在

    out = await at_mod.kg_add_edge.ainvoke(
        {"domain": "d6", "source": "A", "target": "Ghost", "relation": "related_to"}
    )
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "Ghost" in payload["error"]


@pytest.mark.asyncio
async def test_add_edge_downgrades_unknown_relation(patched_repo, reset_bus) -> None:
    """未知 relation 字符串 → 降级为 related_to，不抛错。"""
    kb_root = patched_repo
    await _seed_two_concepts(kb_root, "d7", "U", "V")

    out = await at_mod.kg_add_edge.ainvoke(
        {"domain": "d7", "source": "U", "target": "V", "relation": "totally_bogus_rel"}
    )
    assert json.loads(out)["ok"] is True
    fresh = await AssociationRepository(kb_root).read("d7")
    assert fresh.associations[0].relation == RelationType.RELATED_TO


@pytest.mark.asyncio
async def test_add_edge_clamps_weight(patched_repo, reset_bus) -> None:
    """weight 越界自动 clamp 到 [0, 1]（避免 Pydantic ValidationError）。"""
    kb_root = patched_repo
    await _seed_two_concepts(kb_root, "d8", "M", "N")

    out = await at_mod.kg_add_edge.ainvoke(
        {"domain": "d8", "source": "M", "target": "N", "relation": "related_to", "weight": 1.7}
    )
    assert json.loads(out)["ok"] is True
    fresh = await AssociationRepository(kb_root).read("d8")
    assert fresh.associations[0].weight == 1.0


@pytest.mark.asyncio
async def test_add_edge_mirrors_to_falkordb_when_available(
    patched_repo, monkeypatch, reset_bus
) -> None:
    """FalkorDB 可用时 → store.add_edge_any 被调一次，参数正确。"""
    kb_root = patched_repo
    await _seed_two_concepts(kb_root, "d9", "F", "G")

    # 替换 default NullStore → 让 FalkorDB 模拟可用
    fake = _FakeStore(
        available=True,
        export_data={
            "domain": "d9",
            "concepts": {"F": {}, "G": {}},
            "resources": {},
            "associations": [],
        },
    )
    _swap_store(monkeypatch, fake)

    out = await at_mod.kg_add_edge.ainvoke(
        {"domain": "d9", "source": "F", "target": "G", "relation": "similar_to"}
    )
    payload = json.loads(out)
    assert payload["ok"] is True, payload
    assert payload["falkordb_synced"] is True
    assert len(fake.added) == 1
    # add_edge_any(domain, *, source, target, relation, ...) —— domain 位置参数，其余 kwargs
    args, kwargs = fake.added[0]["args"], fake.added[0]["kwargs"]
    assert args == ("d9",)
    assert kwargs["source"] == "concept:F"
    assert kwargs["target"] == "concept:G"
    assert kwargs["relation"] == "similar_to"


@pytest.mark.asyncio
async def test_add_edge_visible_to_query_neighbors(patched_repo, reset_bus) -> None:
    """端到端：kg_add_edge 后立即可被 kg_query_neighbors 读到。"""
    kb_root = patched_repo
    await _seed_two_concepts(kb_root, "d10", "Root", "Child")

    add_out = await at_mod.kg_add_edge.ainvoke(
        {
            "domain": "d10",
            "source": "Root",
            "target": "Child",
            "relation": "part_of",
        }
    )
    assert json.loads(add_out)["ok"] is True

    q_out = await at_mod.kg_query_neighbors.ainvoke(
        {"domain": "d10", "node": "Root", "hops": 1}
    )
    q = json.loads(q_out)
    assert q["ok"] is True
    assert len(q["neighbors"]) == 1
    assert q["neighbors"][0]["name"] == "Child"
    assert q["neighbors"][0]["relation"] == "part_of"


# ----------------------------------------------------------------------
# kg_delete_edge
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_edge_removes_edge(patched_repo, reset_bus) -> None:
    """基本路径：删除已存在的边。"""
    kb_root = patched_repo
    repo = AssociationRepository(kb_root)
    g = AssociationGraph(domain="e1")
    for n in ("A", "B"):
        g.concepts[n] = ConceptNode(id=make_concept_id(n), name=n, domain="e1")
    g.associations.append(
        Association(source="A", target="B", relation=RelationType.PREREQUISITE_OF)
    )
    await repo.write("e1", g)

    out = await at_mod.kg_delete_edge.ainvoke(
        {
            "domain": "e1",
            "source": "A",
            "target": "B",
            "relation": "prerequisite_of",
        }
    )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["removed"] is True
    assert payload["matched_before"] == 1
    assert payload["total"] == 0

    fresh = await AssociationRepository(kb_root).read("e1")
    assert fresh.associations == []


@pytest.mark.asyncio
async def test_delete_edge_is_idempotent(patched_repo, reset_bus) -> None:
    """边不存在时返回 ok=true, removed=false（幂等），不抛错。"""
    kb_root = patched_repo
    await _seed_two_concepts(kb_root, "e2", "A", "B")

    out = await at_mod.kg_delete_edge.ainvoke(
        {
            "domain": "e2",
            "source": "A",
            "target": "B",
            "relation": "related_to",
        }
    )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["removed"] is False
    assert payload["matched_before"] == 0
    assert "幂等" in payload["message"]


@pytest.mark.asyncio
async def test_delete_edge_only_removes_named_relation(patched_repo, reset_bus) -> None:
    """精确按 (source, target, relation) 三元组匹配——不动其它 relation 的边。"""
    kb_root = patched_repo
    repo = AssociationRepository(kb_root)
    g = AssociationGraph(domain="e3")
    for n in ("A", "B"):
        g.concepts[n] = ConceptNode(id=make_concept_id(n), name=n, domain="e3")
    g.associations.append(
        Association(source="A", target="B", relation=RelationType.PREREQUISITE_OF)
    )
    g.associations.append(
        Association(source="A", target="B", relation=RelationType.SIMILAR_TO)
    )
    await repo.write("e3", g)

    out = await at_mod.kg_delete_edge.ainvoke(
        {
            "domain": "e3",
            "source": "A",
            "target": "B",
            "relation": "prerequisite_of",
        }
    )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["removed"] is True

    fresh = await AssociationRepository(kb_root).read("e3")
    assert len(fresh.associations) == 1
    assert fresh.associations[0].relation == RelationType.SIMILAR_TO


@pytest.mark.asyncio
async def test_delete_edge_rejects_empty_args(patched_repo, reset_bus) -> None:
    """空字符串直接拒。"""
    out = await at_mod.kg_delete_edge.ainvoke(
        {"domain": "d", "source": "", "target": "B", "relation": "related_to"}
    )
    assert json.loads(out)["ok"] is False


@pytest.mark.asyncio
async def test_delete_edge_mirrors_to_falkordb_when_available(
    patched_repo, monkeypatch, reset_bus
) -> None:
    """FalkorDB 可用时 → store.delete_edge 被调一次。"""
    kb_root = patched_repo
    repo = AssociationRepository(kb_root)
    g = AssociationGraph(domain="e5")
    for n in ("A", "B"):
        g.concepts[n] = ConceptNode(id=make_concept_id(n), name=n, domain="e5")
    g.associations.append(
        Association(source="A", target="B", relation=RelationType.SIMILAR_TO)
    )
    await repo.write("e5", g)

    fake = _FakeStore(available=True)
    _swap_store(monkeypatch, fake)

    out = await at_mod.kg_delete_edge.ainvoke(
        {"domain": "e5", "source": "A", "target": "B", "relation": "similar_to"}
    )
    assert json.loads(out)["ok"] is True
    assert len(fake.deleted) == 1
    args, kwargs = fake.deleted[0]["args"], fake.deleted[0]["kwargs"]
    assert args == ("e5",)
    assert kwargs["source"] == "concept:A"
    assert kwargs["target"] == "concept:B"
    assert kwargs["relation"] == "similar_to"


@pytest.mark.asyncio
async def test_delete_edge_emits_activity_event(patched_repo, reset_bus) -> None:
    """删除时往 ActivityBus 发一条 ASSOCIATION_DELETED。"""
    kb_root = patched_repo
    repo = AssociationRepository(kb_root)
    g = AssociationGraph(domain="e4")
    for n in ("A", "B"):
        g.concepts[n] = ConceptNode(id=make_concept_id(n), name=n, domain="e4")
    g.associations.append(
        Association(source="A", target="B", relation=RelationType.RELATED_TO)
    )
    await repo.write("e4", g)

    from src.observability.activity_bus import get_activity_bus

    bus = get_activity_bus()
    captured: list[dict] = []

    async def _capture(event):
        captured.append(event)

    await bus.subscribe(_capture)

    await at_mod.kg_delete_edge.ainvoke(
        {"domain": "e4", "source": "A", "target": "B", "relation": "related_to"}
    )

    # emit() 用 asyncio.create_task 异步派发；等所有任务跑完
    await asyncio.sleep(0.1)

    assoc_events = [
        e for e in captured if e.get("type") == ActivityKind.ASSOCIATION_DELETED
    ]
    assert len(assoc_events) >= 1
    assert "A" in assoc_events[0]["title"] and "B" in assoc_events[0]["title"]
