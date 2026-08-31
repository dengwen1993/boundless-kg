"""Agent @tool wrappers for graph CRUD — covers argument-shape normalization
and the batch subtree tool's full flow with a real ``GraphService``.

Bug we're guarding against
-------------------------
``kg_add_subtree`` receives ``nodes: list[dict]`` from the model.  Some
adapters wrap each element as ``{"$text": "<json string>"}`` instead of
``{"name": "...", "links": [...]}`` (seen in production — see the
``$text`` patch above and the regression below).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.agent.tools import graph_tools as gt_mod
from src.agent.tools.graph_tools import (
    _coerce_links,
    _coerce_node,
    _find_hierarchy_path,
    kg_add_node,
    kg_add_subtree,
    kg_open_node,
    kg_update_node,
)
from src.application.graph_service import GraphService
from src.domain.graph import Graph, Node
from src.infrastructure.repository.graph_repo import GraphRepository
from src.observability.activity_bus import (
    ActivityKind,
    get_activity_bus,
    reset_activity_bus,
)


# ----------------------------------------------------------------------
# _coerce_node — pure-function shape normalization
# ----------------------------------------------------------------------


def test_coerce_passthrough_normal_dict() -> None:
    n = {"name": "alpha", "links": ["beta"]}
    assert _coerce_node(n, "nodes[0]") is n


def test_coerce_returns_input_unchanged_when_name_already_present() -> None:
    """``_coerce_node`` only normalizes shape — links defaulting is the caller's job."""
    n = {"name": "alpha"}  # no links key
    assert _coerce_node(n, "nodes[0]") is n


def test_coerce_unwraps_dollar_text_with_valid_json() -> None:
    # Mirrors what one real model call delivered — leading tab included.
    raw = '\t{"name": "混淆矩阵与TP_FP_TN_FN", "links": []}'
    out = _coerce_node({"$text": raw}, "nodes[0]")
    assert out["name"] == "混淆矩阵与TP_FP_TN_FN"
    assert out["links"] == []


def test_coerce_unwraps_dollar_text_with_links() -> None:
    raw = json.dumps({"name": "ROC与AUC", "links": ["评估基础"]})
    assert _coerce_node({"$text": raw}, "nodes[2]") == {"name": "ROC与AUC", "links": ["评估基础"]}


def test_coerce_raises_when_dollar_text_is_invalid_json() -> None:
    with pytest.raises(KeyError, match=r"nodes\[3\]: \$text 不是合法 JSON"):
        _coerce_node({"$text": "{not json"}, "nodes[3]")


def test_coerce_raises_when_dollar_text_lacks_name() -> None:
    with pytest.raises(KeyError, match=r"nodes\[1\]:"):
        _coerce_node({"$text": json.dumps({"foo": "bar"})}, "nodes[1]")


@pytest.mark.parametrize("bad", [None, "raw-string", 42, ["a", "b"], {"foo": "bar"}])
def test_coerce_raises_on_unrecognized_shape(bad) -> None:
    with pytest.raises(KeyError):
        _coerce_node(bad, "nodes[0]")


# ----------------------------------------------------------------------
# _coerce_links — links-field shape normalization
# ----------------------------------------------------------------------


def test_coerce_links_passthrough_list() -> None:
    assert _coerce_links(["a", "b"]) == ["a", "b"]


def test_coerce_links_unwraps_item_container() -> None:
    """Regression — the exact shape from logs/error.log 23:38:52.

    ``{"item": [...]}`` reached ``Node(links=...)`` and raised a pydantic
    ``list_type`` error, killing a 9-node batch.
    """
    raw = {"item": ["Harness 核心定义与心智模型", "评估驱动开发EDD"]}
    assert _coerce_links(raw) == ["Harness 核心定义与心智模型", "评估驱动开发EDD"]


@pytest.mark.parametrize("empty", ["", "   ", None, [], {}])
def test_coerce_links_empty_forms_become_empty_list(empty) -> None:
    """``links: ""`` — also from the same production failure."""
    assert _coerce_links(empty) == []


def test_coerce_links_parses_json_string_array() -> None:
    assert _coerce_links('["a", "b"]') == ["a", "b"]


def test_coerce_links_splits_delimited_string() -> None:
    assert _coerce_links("a, b、c；d") == ["a", "b", "c", "d"]


def test_coerce_links_extracts_names_from_dict_items() -> None:
    assert _coerce_links([{"name": "a"}, {"name": "b"}]) == ["a", "b"]


def test_coerce_links_degrades_unknown_shape_to_empty() -> None:
    """A malformed optional field must not cost us the whole node."""
    assert _coerce_links(42) == []


# ----------------------------------------------------------------------
# kg_add_subtree — full @tool flow against a real GraphService
# ----------------------------------------------------------------------


@pytest.fixture
def graph_svc(tmp_kb_root: Path) -> GraphService:
    return GraphService(GraphRepository(tmp_kb_root))


@pytest.fixture
def captured_activity_events() -> list[dict]:
    """Fresh activity bus per test, subscriber that captures every event."""
    reset_activity_bus()
    bus = get_activity_bus()
    seen: list[dict] = []

    async def handler(ev: dict) -> None:
        seen.append(ev)

    async def _wire() -> None:
        await bus.subscribe(handler)

    asyncio.run(_wire())
    yield seen
    reset_activity_bus()


def _patch_graph_service(monkeypatch, svc: GraphService) -> None:
    """Redirect the lazy ``get_graph_service`` + ``get_graph_repo`` singletons.

    Both are ``lru_cache``d in ``src.agent.dependencies``; without the
    second patch the tool's pre-write ``domain_exists`` / ``read_graph``
    probes would route to whichever ``tmp_kb_root`` the first test in
    the session cached, making ``is_new_domain`` flaky across tests.
    """
    monkeypatch.setattr(gt_mod, "get_graph_service", lambda: svc)
    monkeypatch.setattr(gt_mod, "get_graph_repo", lambda: svc._repo)


async def test_add_subtree_accepts_plain_dict_nodes(
    monkeypatch, graph_svc: GraphService, captured_activity_events: list[dict], tmp_kb_root: Path
) -> None:
    _patch_graph_service(monkeypatch, graph_svc)

    payload = [
        {"name": "混淆矩阵与TP_FP_TN_FN", "links": []},
        {"name": "精确率_Precision", "links": ["混淆矩阵与TP_FP_TN_FN"]},
        {"name": "召回率_Recall", "links": []},
    ]

    msg = await kg_add_subtree.ainvoke({"domain": "AI 应用开发", "nodes": payload})

    assert msg.startswith("✅ 已插入 3 个节点")
    persisted = await graph_svc.view("AI 应用开发")
    assert {n.name for n in persisted.nodes} == {
        "混淆矩阵与TP_FP_TN_FN",
        "精确率_Precision",
        "召回率_Recall",
    }

    # One NODE_CREATED event per node, all carrying the right domain.
    kinds = [e["type"] for e in captured_activity_events]
    assert kinds.count(ActivityKind.NODE_CREATED) == 3
    assert all(e["domain"] == "AI 应用开发" for e in captured_activity_events)
    assert {e["node"] for e in captured_activity_events} == {
        "混淆矩阵与TP_FP_TN_FN",
        "精确率_Precision",
        "召回率_Recall",
    }


async def test_add_subtree_accepts_dollar_text_wrapped_nodes(
    monkeypatch, graph_svc: GraphService, captured_activity_events: list[dict]
) -> None:
    """Regression — this is the exact shape that originally crashed."""
    _patch_graph_service(monkeypatch, graph_svc)

    raw_nodes = [
        '\t{"name": "混淆矩阵与TP_FP_TN_FN", "links": []}',
        '\t{"name": "精确率_Precision", "links": []}',
        '\t{"name": "召回率_Recall", "links": []}',
    ]
    payload = [{"$text": raw} for raw in raw_nodes]

    msg = await kg_add_subtree.ainvoke({"domain": "AI 应用开发", "nodes": payload})

    assert msg.startswith("✅ 已插入 3 个节点")
    persisted = await graph_svc.view("AI 应用开发")
    assert len(persisted.nodes) == 3
    assert persisted.nodes[0].name == "混淆矩阵与TP_FP_TN_FN"

    assert len(captured_activity_events) == 4
    assert all(e["type"] == ActivityKind.NODE_CREATED for e in captured_activity_events if e["type"] != ActivityKind.DOMAIN_CREATED)
    # Exactly one DOMAIN_CREATED + 3 NODE_CREATEDs (the domain is
    # created fresh on tmp_kb_root for every test).
    kinds = [e["type"] for e in captured_activity_events]
    assert kinds.count(ActivityKind.DOMAIN_CREATED) == 1
    assert kinds.count(ActivityKind.NODE_CREATED) == 3


async def test_add_subtree_emits_domain_created_when_first_write_into_new_domain(
    monkeypatch, graph_svc: GraphService, captured_activity_events: list[dict]
) -> None:
    """First call into a never-seen domain must surface a DOMAIN_CREATED
    line on the activity bus, alongside the per-node events.

    Regression guard: ``kg_add_subtree`` previously only emitted
    NODE_CREATED, so the activity timeline was blind to "user created a
    new domain today" — the very gap that motivated this whole pass.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    payload = [
        {"name": "alpha", "links": []},
        {"name": "beta", "links": ["alpha"]},
    ]
    await kg_add_subtree.ainvoke({"domain": "fresh_domain", "nodes": payload})

    kinds = [e["type"] for e in captured_activity_events]
    # 1× DOMAIN_CREATED + 2× NODE_CREATED = 3 lines total.
    assert kinds.count(ActivityKind.DOMAIN_CREATED) == 1, kinds
    assert kinds.count(ActivityKind.NODE_CREATED) == 2, kinds
    domain_event = next(e for e in captured_activity_events if e["type"] == ActivityKind.DOMAIN_CREATED)
    assert domain_event["domain"] == "fresh_domain"
    assert domain_event["source"] == "agent"
    assert domain_event["ref"] == "domain:fresh_domain"


async def test_add_subtree_skips_bad_entry_and_keeps_the_rest(
    monkeypatch, graph_svc: GraphService, captured_activity_events: list[dict]
) -> None:
    """One malformed item must not discard the items the model got right."""
    _patch_graph_service(monkeypatch, graph_svc)

    payload = [
        {"name": "a"},  # ok
        {"name": "b"},  # ok
        {"$text": "{not json"},  # breaks at index 2
        {"name": "c"},
    ]

    msg = await kg_add_subtree.ainvoke({"domain": "d", "nodes": payload})

    # The 3 good nodes land; the bad one is reported by index so the
    # LLM can retry just that entry.
    assert msg.startswith("✅ 已插入 3 个节点")
    assert "nodes[2]" in msg
    assert "跳过 1 个" in msg

    persisted = await graph_svc.view("d")
    assert {n.name for n in persisted.nodes} == {"a", "b", "c"}
    # 1× DOMAIN_CREATED + 3× NODE_CREATED = 4 events.
    assert len(captured_activity_events) == 4


async def test_add_subtree_errors_only_when_every_entry_is_bad(
    monkeypatch, graph_svc: GraphService, captured_activity_events: list[dict]
) -> None:
    _patch_graph_service(monkeypatch, graph_svc)

    msg = await kg_add_subtree.ainvoke(
        {"domain": "d", "nodes": [{"$text": "{not json"}, {"foo": "bar"}]}
    )

    assert msg.startswith("❌ kg_add_subtree 失败")
    assert msg != "❌ 批量追加失败：'name'"  # the old, useless diagnostic

    persisted = await graph_svc.view("d")
    assert persisted.nodes == []
    assert captured_activity_events == []


async def test_add_subtree_survives_container_wrapped_links(
    monkeypatch, graph_svc: GraphService
) -> None:
    """Regression for logs/error.log 23:38:52 — a 9-node batch died on
    ``links: {"item": [...]}`` plus ``links: ""``."""
    _patch_graph_service(monkeypatch, graph_svc)

    payload = [
        {"name": "Harness Engineering", "links": {"item": ["EDD", "Agent Loop"]}},
        {"name": "EDD", "links": ""},
        {"name": "Agent Loop", "links": ""},
    ]

    msg = await kg_add_subtree.ainvoke({"domain": "AI", "nodes": payload})

    assert msg.startswith("✅ 已插入 3 个节点")
    persisted = await graph_svc.view("AI")
    root = next(n for n in persisted.nodes if n.name == "Harness Engineering")
    assert root.links == ["EDD", "Agent Loop"]
    assert persisted.nodes[1].links == []


# ----------------------------------------------------------------------
# kg_add_subtree — tree shape + atomic auto-create-parent + dangling-link
# pre-validation.  One atomic call wires an entire subtree end-to-end.
# ----------------------------------------------------------------------


async def test_add_subtree_accepts_tree_shape_and_wires_parent_links(
    monkeypatch, graph_svc: GraphService
) -> None:
    """Tree-shaped input: each parent's ``links`` must contain its children
    AFTER the call, with no follow-up ``kg_update_node`` required.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    payload = [
        {
            "name": "Harness Engineering",
            "children": [
                {
                    "name": "EDD",
                    "children": [
                        {"name": "评估目标设定"},
                        {"name": "评估指标选择"},
                    ],
                },
                {"name": "Agent Loop"},
            ],
        },
        {"name": "孤立顶层节点", "links": []},
    ]

    msg = await kg_add_subtree.ainvoke({"domain": "AI", "nodes": payload})

    assert msg.startswith("✅ 已插入"), msg
    assert "已为" in msg  # the internal-parent write-back line

    persisted = await graph_svc.view("AI")
    by_name = {n.name: n for n in persisted.nodes}
    assert set(by_name) == {
        "Harness Engineering",
        "EDD",
        "Agent Loop",
        "评估目标设定",
        "评估指标选择",
        "孤立顶层节点",
    }
    # Tree wiring:
    assert by_name["Harness Engineering"].links == ["EDD", "Agent Loop"]
    assert by_name["EDD"].links == ["评估目标设定", "评估指标选择"]
    assert by_name["Agent Loop"].links == []
    assert by_name["评估目标设定"].links == []
    # Sibling with no parent:
    assert by_name["孤立顶层节点"].links == []


async def test_add_subtree_pre_validates_dangling_link_targets(
    monkeypatch, graph_svc: GraphService
) -> None:
    """If a node's ``links`` references a name that doesn't exist anywhere
    in the new batch or in the graph, the call must REFUSE — listing the
    dangling targets — instead of silently writing a broken graph.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    # "ghost" is neither in the new batch nor in any existing graph.
    payload = [
        {"name": "Real Node", "links": ["ghost", "another_ghost"]},
        {"name": "Sibling", "links": []},
    ]

    msg = await kg_add_subtree.ainvoke({"domain": "X", "nodes": payload})

    assert msg.startswith("❌"), msg
    assert "ghost" in msg, msg
    assert "another_ghost" in msg, msg
    assert "Real Node" in msg, msg  # which node the dangling comes from

    # Nothing was written — atomic refuse.
    persisted = await graph_svc.view("X")
    assert persisted.nodes == []


async def test_add_subtree_dangling_link_allowed_when_target_is_in_same_batch(
    monkeypatch, graph_svc: GraphService
) -> None:
    """If two siblings in the same batch link to each other, that's fine —
    pre-validation must accept cross-refs within the flat+tree combined set.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    payload = [
        {"name": "A", "links": ["B"]},
        {"name": "B", "links": ["A"]},
    ]

    msg = await kg_add_subtree.ainvoke({"domain": "X", "nodes": payload})

    assert msg.startswith("✅"), msg
    persisted = await graph_svc.view("X")
    by_name = {n.name: n for n in persisted.nodes}
    assert by_name["A"].links == ["B"]
    assert by_name["B"].links == ["A"]


async def test_add_subtree_dangling_link_allowed_when_target_is_pre_existing(
    monkeypatch, graph_svc: GraphService
) -> None:
    """Cross-batch: link to a node that already exists in the domain graph
    is fine.  Only names that are missing AFTER the call are reported.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    # Seed an existing node first.
    await kg_add_subtree.ainvoke(
        {"domain": "X", "nodes": [{"name": "Old Node", "links": []}]}
    )

    payload = [{"name": "New Node", "links": ["Old Node"]}]
    msg = await kg_add_subtree.ainvoke({"domain": "X", "nodes": payload})

    assert msg.startswith("✅"), msg


async def test_add_subtree_auto_creates_parent_when_missing(
    monkeypatch, graph_svc: GraphService
) -> None:
    """When ``parent`` names a node that doesn't exist, the tool creates
    it (as an empty-links shell) AND wires back the new children's names
    so the frontend can expand it.  No more "kg_add_node + kg_add_subtree
    + kg_update_node" three-step dance.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    payload = [
        {"name": "child1", "links": []},
        {"name": "child2", "links": []},
    ]

    msg = await kg_add_subtree.ainvoke(
        {"domain": "AI", "parent": "My Brand New Parent", "nodes": payload}
    )

    assert msg.startswith("✅"), msg
    assert "自动新建" in msg, msg
    persisted = await graph_svc.view("AI")
    parent = next(n for n in persisted.nodes if n.name == "My Brand New Parent")
    assert sorted(parent.links) == ["child1", "child2"]
    # The auto-created parent should also be present:
    assert {n.name for n in persisted.nodes} == {
        "My Brand New Parent",
        "child1",
        "child2",
    }


async def test_add_subtree_rejects_duplicate_names_in_same_batch(
    monkeypatch, graph_svc: GraphService
) -> None:
    """Same name twice in the same batch is an error, not silent dedup."""
    _patch_graph_service(monkeypatch, graph_svc)

    payload = [
        {"name": "A", "links": []},
        {"name": "A", "links": []},  # duplicate
    ]
    msg = await kg_add_subtree.ainvoke({"domain": "X", "nodes": payload})

    assert msg.startswith("❌"), msg
    assert "A" in msg, msg


async def test_add_subtree_duplicate_against_existing_is_silent_skip(
    monkeypatch, graph_svc: GraphService
) -> None:
    """A name that already exists in the graph is OK to include — it
    simply isn't re-added.  The tool surfaces this in the returned message.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    await kg_add_subtree.ainvoke(
        {"domain": "X", "nodes": [{"name": "Existing", "links": []}]}
    )
    msg = await kg_add_subtree.ainvoke(
        {"domain": "X", "nodes": [
            {"name": "Existing", "links": []},
            {"name": "Fresh", "links": []},
        ]}
    )
    assert msg.startswith("✅"), msg
    assert "Existing" in msg  # surfaced via the dedup indicator
    assert "Fresh" in msg

    persisted = await graph_svc.view("X")
    assert {n.name for n in persisted.nodes} == {"Existing", "Fresh"}


# ----------------------------------------------------------------------
# SDK serialisation quirks (defense in depth — Node validator already
# handles these, but the tool itself must also be lenient so the error
# message stays actionable when the SDK is at its most imaginative).
# ----------------------------------------------------------------------


async def test_add_subtree_accepts_single_node_dict_instead_of_list(
    monkeypatch, graph_svc: GraphService
) -> None:
    """SDK sometimes delivers ``{name, links}`` as the *whole* ``nodes``
    argument instead of ``[{name, links}]``.  Auto-wrap so the call
    succeeds and yields one node.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    msg = await kg_add_subtree.ainvoke(
        {"domain": "X", "nodes": {"name": "solo", "links": []}}
    )

    assert msg.startswith("✅"), msg
    assert "solo" in msg
    persisted = await graph_svc.view("X")
    assert {n.name for n in persisted.nodes} == {"solo"}


# ----------------------------------------------------------------------
# _find_hierarchy_path — pure BFS helper used by kg_open_node
# ----------------------------------------------------------------------


def _decorated_fixture() -> list[dict]:
    """Return the decorated-graph node list (after ``decorate_graph``) for:
        L0 (synthetic)
          └─ L1 root
              ├─ L2 child A
              │   └─ leaf X
              └─ L2 child B (orphan leaf Z references B but B doesn't link back)
    """
    from src.domain.graph.decorator import decorate_graph

    raw = {
        "domain": "测试领域",
        "nodes": [
            {"name": "root", "links": ["childA", "childB"]},
            {"name": "childA", "links": ["leafX"]},
            {"name": "childB", "links": []},
            {"name": "leafX", "links": []},
            # orphan: not reachable from L0
            {"name": "orphan", "links": []},
        ],
    }
    return decorate_graph(raw)["nodes"]


def test_find_hierarchy_path_root_to_leaf() -> None:
    nodes = _decorated_fixture()
    path = _find_hierarchy_path(nodes, "leafX")
    assert path == ["测试领域", "root", "childA", "leafX"]


def test_find_hierarchy_path_l1_target() -> None:
    nodes = _decorated_fixture()
    path = _find_hierarchy_path(nodes, "root")
    assert path == ["测试领域", "root"]


def test_find_hierarchy_path_l0_only() -> None:
    nodes = _decorated_fixture()
    path = _find_hierarchy_path(nodes, "测试领域")
    assert path == ["测试领域"]


def test_find_hierarchy_path_unknown_node_is_empty() -> None:
    nodes = _decorated_fixture()
    assert _find_hierarchy_path(nodes, "no-such-node") == []


def test_find_hierarchy_path_l1_root_with_no_incoming() -> None:
    """A node with no incoming links becomes an L1 root — it's still
    reachable from L0 (L0 links to *all* L1 roots, not just one)."""
    nodes = _decorated_fixture()
    path = _find_hierarchy_path(nodes, "orphan")
    # "orphan" is its own L1 root; the BFS path is L0 → orphan.
    assert path == ["测试领域", "orphan"]


def test_find_hierarchy_path_orphan_target_prepends_synthetic_l0() -> None:
    """BUG-004: when a target is unreachable from L0 (the domain's graph is
    a forest with multiple independent roots), the synthetic L0 root is
    NOT in the BFS path.  Frontend's el-tree then never expands L0 and
    ``setCurrentKey(target)`` silently fails because the target's key
    isn't registered in the tree store.  The fix prepends L0 so the
    frontend expands it first.
    """
    from src.domain.graph.decorator import decorate_graph

    raw = {
        "domain": "森林领域",
        "nodes": [
            # Root A — the "main" branch that L0 will reach.
            {"name": "main", "links": ["childA"]},
            {"name": "childA", "links": []},
            # Orphan root B — not referenced by anything; L0's links are
            # exactly the L1 roots with no incoming edges, so an *extra*
            # node with no incoming links ALSO becomes an L1 root and is
            # still reachable from L0.  To produce a true orphan we
            # need a node that L0's BFS cannot reach, which is only
            # possible if it has *incoming* edges from somewhere *not*
            # in the graph — that's impossible in a closed graph.
            #
            # So the actual scenario is the one in the BUG-004 ticket:
            # ``kg_add_node`` failed silently, leaving an "orphan" node
            # in memory that's also unreachable from L0 because the
            # reverse-link was never written.  We simulate that with
            # a domain where L0's ``roots`` list does NOT include the
            # target: this requires injecting extra nodes that look
            # like L1 but were never linked to by anyone.
        ],
    }
    # Simulate the BUG-004 state: hand-build the decorated payload with
    # an orphan that ``decorate_graph`` would not normally produce.
    base = decorate_graph(raw)
    base["nodes"].append(
        {
            "name": "真正的孤根",
            "links": [],
            "level": 3,
            "tier": "leaf",
            "childCount": 0,
            "isDomainRoot": False,
        }
    )
    path = _find_hierarchy_path(base["nodes"], "真正的孤根")
    assert path == ["森林领域", "真正的孤根"], (
        "BUG-004: orphan targets must include the synthetic L0 root so "
        "the frontend's OutlineView can expand L0 and register the "
        "target in el-tree's store before setCurrentKey is called."
    )


def test_find_hierarchy_path_handles_cycles() -> None:
    """BFS visits each node at most once even with back-edges."""
    from src.domain.graph.decorator import decorate_graph

    # root has no incoming links → L1 root.  a->b and b->a form a cycle
    # and the BFS must not infinite-loop.
    raw = {
        "domain": "D",
        "nodes": [
            {"name": "root", "links": ["a"]},
            {"name": "a", "links": ["b"]},
            {"name": "b", "links": ["a", "c"]},  # back-edge creates a cycle
            {"name": "c", "links": []},
        ],
    }
    nodes = decorate_graph(raw)["nodes"]
    path = _find_hierarchy_path(nodes, "c")
    assert path[0] == "D"
    assert path[-1] == "c"
    # Path traverses the cycle correctly: D → root → a → b → c
    # (or D → root → a → c if BFS picks a direct route, but with the
    # fixture above, c is only reachable via b.)
    assert path == ["D", "root", "a", "b", "c"]


def test_find_hierarchy_path_handles_diamond() -> None:
    """Two parents point to the same child — BFS returns the first path."""
    from src.domain.graph.decorator import decorate_graph

    raw = {
        "domain": "D",
        "nodes": [
            {"name": "p1", "links": ["shared"]},
            {"name": "p2", "links": ["shared"]},
            {"name": "shared", "links": []},
        ],
    }
    nodes = decorate_graph(raw)["nodes"]
    # BFS visits siblings in order; both p1 and p2 are L1 roots.
    path = _find_hierarchy_path(nodes, "shared")
    assert path[0] == "D"
    assert path[-1] == "shared"
    assert path[1] in ("p1", "p2")
    assert len(path) == 3


# ----------------------------------------------------------------------
# kg_open_node — full @tool flow against a real GraphService
# ----------------------------------------------------------------------


async def test_open_node_returns_hierarchy_path(
    monkeypatch, graph_svc: GraphService
) -> None:
    _patch_graph_service(monkeypatch, graph_svc)
    # Seed: domain root → L1 root → L2 → leaf.
    await kg_add_subtree.ainvoke(
        {
            "domain": "测试领域",
            "nodes": [
                {"name": "root", "links": ["childA"]},
                {"name": "childA", "links": ["leafX"]},
                {"name": "leafX", "links": []},
            ],
        }
    )

    result = json.loads(
        await kg_open_node.ainvoke({"domain": "测试领域", "node_name": "leafX"})
    )

    assert result["ok"] is True
    assert result["node"] == "leafX"
    assert result["path"] == ["测试领域", "root", "childA", "leafX"]
    assert result["tier"] in ("L3", "leaf")
    assert result["is_domain_root"] is False


async def test_open_node_unknown_node_returns_failure(
    monkeypatch, graph_svc: GraphService
) -> None:
    _patch_graph_service(monkeypatch, graph_svc)
    await kg_add_subtree.ainvoke(
        {"domain": "测试领域", "nodes": [{"name": "root", "links": []}]}
    )

    result = json.loads(
        await kg_open_node.ainvoke(
            {"domain": "测试领域", "node_name": "幻影节点"}
        )
    )

    assert result["ok"] is False
    assert result["node"] == "幻影节点"
    assert "找不到" in result["message"]
    assert "root" in result["available_sample"]


async def test_open_node_unknown_domain_does_not_crash(
    monkeypatch, graph_svc: GraphService
) -> None:
    _patch_graph_service(monkeypatch, graph_svc)
    # Domain doesn't exist — should be a friendly failure, not a stack trace.
    result_str = await kg_open_node.ainvoke(
        {"domain": "no-such-domain", "node_name": "anything"}
    )
    # If the repo raises, the @tool wrapper returns the error string.
    # The frontend is defensive either way (parses ok==true only).
    assert "no-such-domain" in result_str or "Error" in result_str


async def test_open_node_orphan_target_path_includes_synthetic_l0(
    monkeypatch, graph_svc: GraphService
) -> None:
    """BUG-004 end-to-end: a domain whose graph has only an orphan root
    (no L1 edges from the synthetic L0) must still return a ``path``
    that begins with the synthetic L0 root, so the frontend can expand
    it and el-tree will register the target key.
    """
    from src.domain.graph.decorator import decorate_graph

    _patch_graph_service(monkeypatch, graph_svc)
    # Seed an empty domain so the repo returns a valid (empty) Graph.
    await kg_add_subtree.ainvoke(
        {"domain": "孤根领域", "nodes": []}
    )
    # Bypass the repo: monkeypatch ``svc.view`` to return a hand-built
    # graph that mimics the production state captured in BUG-004.
    orphan_only = {
        "domain": "孤根领域",
        "nodes": [
            # "真正的孤根" has no incoming edges from any node — but
            # in a closed graph, ``decorate_graph`` will still classify
            # it as an L1 root and L0 will link to it.  The BUG-004
            # production state was an *in-memory* orphan that never
            # made it into ``knowledge_graph.json``, which we can't
            # reproduce from the repo.  We approximate by making
            # ``view`` return a graph whose only node is unreachable
            # from any L1 edge.
            {"name": "真正的孤根", "links": []},
        ],
    }

    async def fake_view(_domain: str) -> Graph:  # type: ignore[return-value]
        return Graph.model_validate(
            {
                "domain": orphan_only["domain"],
                "nodes": orphan_only["nodes"],
            }
        )

    monkeypatch.setattr(graph_svc, "view", fake_view)

    result = json.loads(
        await kg_open_node.ainvoke(
            {"domain": "孤根领域", "node_name": "真正的孤根"}
        )
    )

    assert result["ok"] is True
    assert result["node"] == "真正的孤根"
    # BUG-004 invariant: path[0] must be the synthetic L0 so the
    # frontend expands L0 first and el-tree registers the orphan
    # node's key before setCurrentKey fires.
    assert result["path"][0] == "孤根领域"
    assert result["path"][-1] == "真正的孤根"
    # Belt-and-braces: confirm the decorator really marks the domain
    # name as the synthetic L0 (regression guard if ``decorate_graph``
    # ever stops prepending it).
    decorated = decorate_graph(orphan_only)
    assert decorated["nodes"][0]["name"] == "孤根领域"
    assert decorated["nodes"][0].get("isDomainRoot") is True


async def test_open_node_unknown_with_notes_dir_suggests_kg_add_node(
    monkeypatch, graph_svc: GraphService, tmp_kb_root: Path
) -> None:
    """BUG-004 follow-up: when the requested name exists as a
    ``notes/{name}/`` directory but is NOT in ``knowledge_graph.json``,
    the failure message should tell the LLM to call ``kg_add_node``
    instead of looping on ``kg_open_node``.  This is the case that
    caused the original ticket — ``kg_read_note`` succeeded but
    ``kg_open_node`` returned ok=false.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    domain = "notes_orphan_领域"
    target = "Harness Engineering 驾驭工程"
    # Seed a minimal graph (the orphan target is *not* in it).
    await kg_add_subtree.ainvoke(
        {"domain": domain, "nodes": [{"name": "已有节点", "links": []}]}
    )
    # Create the notes/ directory exactly as production did.
    notes_dir = tmp_kb_root / domain / "notes" / target
    notes_dir.mkdir(parents=True)
    (notes_dir / "note.md").write_text("# orphaned\n", encoding="utf-8")
    (notes_dir / "plan.json").write_text("{}", encoding="utf-8")

    result = json.loads(
        await kg_open_node.ainvoke(
            {"domain": domain, "node_name": target}
        )
    )

    assert result["ok"] is False
    assert result["node"] == target
    assert "kg_add_node" in result["message"], (
        "When a notes/ directory exists but the node is missing from "
        "the graph, the failure message must suggest kg_add_node so "
        "the LLM can graft the orphan onto the graph instead of "
        "looping on kg_open_node."
    )
    # Substring suggestion list still surfaces existing nodes.
    assert "已有节点" in result.get("suggestions", []) or \
        "已有节点" in result.get("available_sample", [])


async def test_open_node_unknown_without_notes_dir_still_friendly(
    monkeypatch, graph_svc: GraphService
) -> None:
    """BUG-004 regression guard: when the target name is not in the
    graph AND has no notes/ directory, the tool must still return a
    useful ok=false response (no crash, no missing fields).
    """
    _patch_graph_service(monkeypatch, graph_svc)
    await kg_add_subtree.ainvoke(
        {"domain": "测试领域", "nodes": [{"name": "root", "links": []}]}
    )

    result = json.loads(
        await kg_open_node.ainvoke(
            {"domain": "测试领域", "node_name": "完全不存在的节点"}
        )
    )

    assert result["ok"] is False
    assert result["node"] == "完全不存在的节点"
    # No notes/ exists, so the orphan hint must not be appended.
    assert "kg_add_node" not in result["message"]
    assert "找不到" in result["message"]
    assert "root" in result["suggestions"]


# ----------------------------------------------------------------------
# kg_add_node(parent=...) — auto-write-back to parent.links
# ----------------------------------------------------------------------


async def test_add_node_with_parent_auto_writes_back_to_parent_links(
    monkeypatch, graph_svc: GraphService
) -> None:
    """``kg_add_node(parent=...)`` MUST append the new node name to
    ``parent.links``.  Without this the frontend cannot expand the
    parent to reveal the new child.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    # Seed parent.
    await kg_add_node.ainvoke({"domain": "AI 智能体", "name": "Agent 智能体"})
    # Add child with parent=.
    await kg_add_node.ainvoke(
        {"domain": "AI 智能体", "name": "Agent自进化", "parent": "Agent 智能体"}
    )

    persisted = await graph_svc.view("AI 智能体")
    parent_node = next(n for n in persisted.nodes if n.name == "Agent 智能体")
    assert "Agent自进化" in parent_node.links, (
        "kg_add_node(parent=...) must write-back to parent.links "
        "so the frontend can expand the parent and see the new child."
    )


# ----------------------------------------------------------------------
# SDK serialisation quirks — ``links`` may arrive as ``{"item": [...]}``
# or a bare JSON string instead of ``list[str]``.  The tool must accept
# all forms; this is the bug class that turned 4/4 kg_add_node calls
# into "links serialisation" failures on the deepagents SDK path.
# ----------------------------------------------------------------------


async def test_add_node_accepts_container_wrapped_links(
    monkeypatch, graph_svc: GraphService
) -> None:
    _patch_graph_service(monkeypatch, graph_svc)
    # SDK serialised ``links`` as the XML/JSON-schema round-trip form.
    msg = await kg_add_node.ainvoke(
        {
            "domain": "X",
            "name": "lonely",
            "links": {"item": ["peer_a", "peer_b"]},
        }
    )
    assert msg.startswith("✅"), msg
    persisted = await graph_svc.view("X")
    by_name = {n.name: n for n in persisted.nodes}
    assert by_name["lonely"].links == ["peer_a", "peer_b"]


async def test_add_node_accepts_delimited_string_links(
    monkeypatch, graph_svc: GraphService
) -> None:
    _patch_graph_service(monkeypatch, graph_svc)
    msg = await kg_add_node.ainvoke(
        {"domain": "X", "name": "lonely", "links": "peer_a、peer_b"}
    )
    assert msg.startswith("✅"), msg
    persisted = await graph_svc.view("X")
    n = next(n for n in persisted.nodes if n.name == "lonely")
    assert n.links == ["peer_a", "peer_b"]


async def test_update_node_accepts_container_wrapped_new_links(
    monkeypatch, graph_svc: GraphService
) -> None:
    _patch_graph_service(monkeypatch, graph_svc)
    # Seed a node first.
    await kg_add_node.ainvoke({"domain": "X", "name": "tgt"})

    # Update with the wrapped form of new_links.
    msg = await kg_update_node.ainvoke(
        {
            "domain": "X",
            "name": "tgt",
            "new_links": {"item": ["a", "b", "c"]},
        }
    )
    assert msg.startswith("✅"), msg

    persisted = await graph_svc.view("X")
    n = next(n for n in persisted.nodes if n.name == "tgt")
    assert n.links == ["a", "b", "c"]


# ----------------------------------------------------------------------
# kg_update_node — derived graph sync
# ----------------------------------------------------------------------


async def test_update_node_calls_sync_for_node_synchronously(
    monkeypatch, graph_svc: GraphService
) -> None:
    """``kg_update_node`` must synchronously trigger ``sync_for_node`` on
    the GraphSyncService so the derived graph (FalkorDB + BM25) is
    consistent BEFORE the tool returns.  This eliminates the race where
    ``kg_query_neighbors`` called immediately after the update reads
    stale data.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    sync_calls: list[str] = []

    class FakeSyncService:
        async def sync_for_node(self, node: str, event_id: str = "") -> dict:
            sync_calls.append(node)
            return {"synced": True}

    fake_sync = FakeSyncService()
    monkeypatch.setattr(gt_mod, "get_graph_sync_service", lambda _domain: fake_sync)

    # Seed a node, then update its links.
    await kg_add_subtree.ainvoke(
        {"domain": "AI", "nodes": [
            {"name": "child"},
            {"name": "parent", "links": ["child"]},
        ]}
    )

    msg = await kg_update_node.ainvoke(
        {
            "domain": "AI",
            "name": "parent",
            "new_links": ["child", "newly_added"],
        }
    )

    assert msg.startswith("✅ 已更新节点")
    # Sync must be called BEFORE return — and for the **final** node name.
    assert sync_calls == ["parent"], (
        f"kg_update_node must call sync_for_node for the final node name "
        f"before returning; observed calls: {sync_calls}"
    )

    # Real source is durably updated.
    persisted = await graph_svc.view("AI")
    p = next(n for n in persisted.nodes if n.name == "parent")
    assert p.links == ["child", "newly_added"]


async def test_update_node_survives_sync_failure(
    monkeypatch, graph_svc: GraphService
) -> None:
    """Sync failure must NOT break the write — the DerivationSubscriber
    is still subscribed and will retry on subsequent events.  We just
    surface a warning in the return message.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    class BrokenSyncService:
        async def sync_for_node(self, node: str, event_id: str = "") -> dict:
            raise RuntimeError("FalkorDB down")

    monkeypatch.setattr(
        gt_mod, "get_graph_sync_service", lambda _domain: BrokenSyncService()
    )

    await kg_add_subtree.ainvoke(
        {"domain": "AI", "nodes": [{"name": "parent", "links": []}]}
    )

    msg = await kg_update_node.ainvoke(
        {"domain": "AI", "name": "parent", "new_links": ["kid"]}
    )

    # Write succeeded.
    assert msg.startswith("✅ 已更新节点")
    # Failure was surfaced, not swallowed silently.
    assert "派生图同步失败" in msg
    assert "RuntimeError" in msg or "FalkorDB down" in msg
    # Real source is updated regardless.
    p = next(n for n in (await graph_svc.view("AI")).nodes if n.name == "parent")
    assert p.links == ["kid"]


async def test_update_node_renames_then_syncs_under_new_name(
    monkeypatch, graph_svc: GraphService
) -> None:
    """When renaming, sync_for_node must be called with the **new** name
    so the FalkorDB concept node reflects the rename.
    """
    _patch_graph_service(monkeypatch, graph_svc)

    sync_calls: list[str] = []

    class FakeSyncService:
        async def sync_for_node(self, node: str, event_id: str = "") -> dict:
            sync_calls.append(node)
            return {"synced": True}

    monkeypatch.setattr(gt_mod, "get_graph_sync_service", lambda _domain: FakeSyncService())

    await kg_add_subtree.ainvoke(
        {"domain": "AI", "nodes": [{"name": "old_name", "links": []}]}
    )

    msg = await kg_update_node.ainvoke(
        {"domain": "AI", "name": "old_name", "new_name": "new_name"}
    )

    assert msg.startswith("✅ 已更新节点")
    assert sync_calls == ["new_name"], (
        f"After rename, sync_for_node must use the NEW name; got: {sync_calls}"
    )

