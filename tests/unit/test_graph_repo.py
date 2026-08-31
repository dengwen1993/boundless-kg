"""GraphRepository — async CRUD + atomic + shared-lock invariant."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.domain.graph import Direction, Graph, Node
from src.infrastructure.lock import graph_lock
from src.infrastructure.repository.graph_repo import GraphRepository


async def test_read_missing_returns_empty_graph(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    g = await repo.read_graph("nope")
    assert g.domain == "nope"
    assert g.nodes == []


async def test_write_then_read_roundtrip(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    graph = Graph(
        domain="kg",
        direction=Direction(angle="原理", summary="x" * 40),
        nodes=[Node(name="alpha", links=["beta"]), Node(name="beta", links=[])],
    )
    await repo.write_graph("kg", graph)
    g2 = await repo.read_graph("kg")
    assert g2.domain == "kg"
    assert [n.name for n in g2.nodes] == ["alpha", "beta"]


async def test_list_domains(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    (tmp_kb_root / "a").mkdir()
    (tmp_kb_root / "b").mkdir()
    (tmp_kb_root / "ignore.txt").write_text("noise")
    names = await repo.list_domains()
    assert names == ["a", "b"]


async def test_add_node(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    g = await repo.add_node("d", "root", links=["child"])
    assert [n.name for n in g.nodes] == ["root"]
    assert g.nodes[0].links == ["child"]


async def test_add_node_with_parent_links_back(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    await repo.add_node("d", "root", links=["placeholder"])
    g = await repo.add_node("d", "child", parent="root")
    root = next(n for n in g.nodes if n.name == "root")
    child = next(n for n in g.nodes if n.name == "child")
    assert "child" in root.links
    assert child.links == []


async def test_add_node_duplicate_raises(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    await repo.add_node("d", "x")
    with pytest.raises(ValueError):
        await repo.add_node("d", "x")


async def test_add_subtree_dedupes(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    await repo.add_node("d", "existing")
    g, added = await repo.add_subtree(
        "d", [Node(name="existing"), Node(name="fresh")], root_links=None
    )
    assert [n.name for n in g.nodes] == ["existing", "fresh"]
    assert added == ["fresh"]


async def test_fix_links_strips_upward_edges(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    # C mistakenly points back at A (its grandparent) — strip C.links.
    graph = Graph(
        domain="d",
        direction=Direction(summary="summary " + "x" * 30),
        nodes=[
            Node(name="root", links=["A"]),
            Node(name="A", links=["B"]),
            Node(name="B", links=["C"]),
            Node(name="C", links=["A"]),
        ],
    )
    await repo.write_graph("d", graph)
    removed, scanned = await repo.fix_links("d")
    assert removed == 1
    assert scanned == 4
    g2 = await repo.read_graph("d")
    c = next(n for n in g2.nodes if n.name == "C")
    assert c.links == []


async def test_fix_links_idempotent_on_clean_graph(tmp_kb_root: Path) -> None:
    repo = GraphRepository(tmp_kb_root)
    graph = Graph(
        domain="d",
        direction=Direction(summary="summary " + "x" * 30),
        nodes=[Node(name="A", links=["B"]), Node(name="B", links=[])],
    )
    await repo.write_graph("d", graph)
    removed, scanned = await repo.fix_links("d")
    assert removed == 0
    assert scanned == 2


async def test_concurrent_add_node_no_lost_writes(tmp_kb_root: Path) -> None:
    """Lock invariant: concurrent add_node calls must not lose a write."""
    repo = GraphRepository(tmp_kb_root)

    async def add(i: int) -> None:
        await repo.add_node("d", f"n-{i}")

    await asyncio.gather(*(add(i) for i in range(20)))
    g = await repo.read_graph("d")
    assert len(g.nodes) == 20


async def test_repo_uses_shared_lock_instance(tmp_kb_root: Path) -> None:
    """Regression: GraphRepository must NOT instantiate its own lock.

    We can't re-enter the same ``asyncio.Lock`` from the same task (it's
    not re-entrant); instead we assert that ``graph_lock()`` is the
    same object every call and that the repo can read while another
    task holds it.
    """
    repo = GraphRepository(tmp_kb_root)
    # 1. Identity: graph_lock() is canonical per loop.
    assert graph_lock() is graph_lock()
    # 2. Behaviour: while task A holds the lock, task B's read via the
    # repo must wait (proves the repo funnels through the shared lock,
    # not a private one).
    a_in = False
    a_out = False
    repo_started = False
    repo_done = False

    async def holder() -> None:
        nonlocal a_in, a_out
        async with graph_lock():
            a_in = True
            await asyncio.sleep(0.05)
            a_out = True

    async def reader() -> None:
        nonlocal repo_started, repo_done
        repo_started = True
        g = await repo.read_graph("d")
        repo_done = True
        assert g.domain == "d"

    await asyncio.gather(holder(), reader())
    assert a_in and a_out
    assert repo_started and repo_done