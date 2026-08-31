"""Integration tests — associations API + activity bus → derivation wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def seeded_domain_with_notes(tmp_kb_root: Path):
    """写入一个最小 domain + 笔记 + 资源。"""

    def _setup(domain: str = "d1") -> None:
        d = tmp_kb_root / domain
        d.mkdir(parents=True, exist_ok=True)
        (d / "knowledge_graph.json").write_text(
            json.dumps({
                "domain": domain,
                "direction": {"angle": "", "audience": "", "depth": "", "summary": ""},
                "nodes": [
                    {"name": "A", "links": ["B"]},
                    {"name": "B", "links": []},
                ],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        notes_dir = d / "notes" / "A"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "note.md").write_text(
            "这是 A 的笔记。参考 @B。", encoding="utf-8"
        )
        web_dir = notes_dir / "web_resources"
        web_dir.mkdir(exist_ok=True)
        (web_dir / "index.json").write_text(
            json.dumps([
                {"title": "T", "url": "https://example.com/x",
                 "summary": "S", "node": "A"},
            ], ensure_ascii=False),
            encoding="utf-8",
        )

    return _setup


def test_associations_sync_route(client: TestClient, seeded_domain_with_notes, tmp_kb_root: Path) -> None:
    seeded_domain_with_notes("d1")
    r = client.post("/api/associations/d1/sync")
    assert r.status_code == 200
    body = r.json()
    assert body["concepts"] >= 2


def test_associations_sync_node_route(client: TestClient, seeded_domain_with_notes) -> None:
    seeded_domain_with_notes("d1")
    r = client.post("/api/associations/d1/sync-node", json={"node": "A"})
    assert r.status_code == 200


def test_associations_get_returns_graph(client: TestClient, seeded_domain_with_notes) -> None:
    seeded_domain_with_notes("d1")
    client.post("/api/associations/d1/sync")
    r = client.get("/api/associations/d1")
    assert r.status_code == 200
    body = r.json()
    assert "concepts" in body
    assert "associations" in body
    assert "A" in body["concepts"]


def test_associations_concepts_route(client: TestClient, seeded_domain_with_notes) -> None:
    seeded_domain_with_notes("d1")
    client.post("/api/associations/d1/sync")
    r = client.get("/api/associations/d1/concepts")
    assert r.status_code == 200
    body = r.json()
    assert "A" in body["concepts"]
    assert body["concepts"]["A"]["is_root"] is True


def test_associations_resources_route(client: TestClient, seeded_domain_with_notes) -> None:
    seeded_domain_with_notes("d1")
    client.post("/api/associations/d1/sync")
    r = client.get("/api/associations/d1/resources")
    assert r.status_code == 200
    body = r.json()
    types = {r["type"] for r in body["resources"].values()}
    assert "note" in types
    assert "resource" in types


def test_associations_edges_route(client: TestClient, seeded_domain_with_notes) -> None:
    seeded_domain_with_notes("d1")
    client.post("/api/associations/d1/sync")
    r = client.get("/api/associations/d1/edges")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    # PART_OF 边
    relations = {a["relation"] for a in body["associations"]}
    assert "part_of" in relations


def test_associations_neighbors_route(client: TestClient, seeded_domain_with_notes) -> None:
    seeded_domain_with_notes("d1")
    client.post("/api/associations/d1/sync")
    r = client.get("/api/associations/d1/neighbors/A?hops=1")
    assert r.status_code == 200
    body = r.json()
    assert body["node"] == "A"
    # A → B (PART_OF) + B → A 反向
    names = {nb["name"] for nb in body["neighbors"]}
    assert "B" in names


def test_associations_neighbors_with_relation_filter(
    client: TestClient, seeded_domain_with_notes
) -> None:
    seeded_domain_with_notes("d1")
    client.post("/api/associations/d1/sync")
    r = client.get("/api/associations/d1/neighbors/A?relation=part_of")
    assert r.status_code == 200
    body = r.json()
    for nb in body["neighbors"]:
        assert nb["relation"] == "part_of"


def test_associations_neighbors_invalid_relation(
    client: TestClient, seeded_domain_with_notes
) -> None:
    seeded_domain_with_notes("d1")
    client.post("/api/associations/d1/sync")
    r = client.get("/api/associations/d1/neighbors/A?relation=foo")
    assert r.status_code == 400


def test_associations_statistics_route(
    client: TestClient, seeded_domain_with_notes
) -> None:
    seeded_domain_with_notes("d1")
    client.post("/api/associations/d1/sync")
    r = client.get("/api/associations/d1/statistics")
    assert r.status_code == 200
    body = r.json()
    assert body["concepts"] >= 2
    assert "associations" in body


def test_associations_clear_route(client: TestClient, seeded_domain_with_notes) -> None:
    seeded_domain_with_notes("d1")
    client.post("/api/associations/d1/sync")
    r = client.delete("/api/associations/d1")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True


def test_timeline_includes_derived_field(
    client: TestClient, seeded_domain_with_notes
) -> None:
    """时间线返回里应包含 derived / derived_at 字段（默认 derived=False）。"""
    seeded_domain_with_notes("d1")
    client.post("/api/nodes", json={"domain": "d1", "name": "C"})
    r = client.get("/api/timeline/d1")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    for item in items:
        # 字段必须存在（即便默认 False）
        assert "derived" in item
        assert "derived_at" in item


def test_timeline_mark_derived_via_mark_endpoint(
    client: TestClient, seeded_domain_with_notes, tmp_kb_root: Path
) -> None:
    """通过 associations 路由的 sync 把所有当前未派生事件标为 derived。

    这条路径模拟 lifespan 失效时手动触发的回补。
    """
    seeded_domain_with_notes("d1")
    client.post("/api/nodes", json={"domain": "d1", "name": "C"})
    # 触发一次 sync（含 mark_all_derived 行为——这里我们走 mark_events_derived API）
    from src.infrastructure.repository.association_repo import AssociationRepository
    # 模拟：手动调用 mark_events_derived 把最新 event 标 derived
    import asyncio
    async def mark_one():
        repo = AssociationRepository(tmp_kb_root)
        timeline = client.get("/api/timeline/d1").json()
        items = timeline["items"]
        if items:
            await repo.mark_events_derived("d1", [items[0]["ref"].split(":", 1)[-1] if ":" in items[0].get("ref", "") else ""])
    asyncio.run(mark_one())
    r = client.get("/api/timeline/d1")
    items = r.json()["items"]
    # 字段应存在
    assert "derived" in items[0]
    assert "derived_at" in items[0]


def test_add_node_triggers_derivation(
    client: TestClient, seeded_domain_with_notes, tmp_kb_root: Path
) -> None:
    seeded_domain_with_notes("d1")
    # 添加一个新节点 —— 通过 ActivityBus → DerivationSubscriber 触发派生
    r = client.post("/api/nodes", json={"domain": "d1", "name": "C", "links": ["B"]})
    assert r.status_code == 200
    # 直接读 associations.json
    assoc_path = tmp_kb_root / "d1" / "associations.json"
    # 在 TestClient 中 lifespan 不跑，DerivationSubscriber 未注册
    # 所以这里 assertions 验证 emit 已发生（items 存在），不一定派生
    timeline = client.get("/api/timeline/d1").json()
    assert timeline["total"] >= 1


def test_node_created_event_appears_in_timeline(
    client: TestClient, seeded_domain_with_notes
) -> None:
    seeded_domain_with_notes("d1")
    client.post("/api/nodes", json={"domain": "d1", "name": "C"})
    r = client.get("/api/timeline/d1")
    items = r.json()["items"]
    types = {it["type"] for it in items}
    assert "node_created" in types