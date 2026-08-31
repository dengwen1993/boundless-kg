"""Domain graph layer — models, validator, link_fixer (pure functions)."""

from __future__ import annotations

import pytest

from src.domain.graph import (
    Direction,
    DomainSummary,
    Graph,
    GraphValidator,
    LinkFixResult,
    Node,
    QualityLevel,
    QualityScore,
    fix_missing_reverse_links,
    validate_graph,
)


# ---------- models ----------


def test_node_trims_name() -> None:
    n = Node(name="  hello  ")
    assert n.name == "hello"


def test_graph_node_names() -> None:
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[Node(name="a"), Node(name="b")],
    )
    assert g.node_names() == ["a", "b"]


def test_graph_add_node_duplicate_raises() -> None:
    g = Graph(domain="d", direction=Direction(summary="x" * 30), nodes=[Node(name="a")])
    with pytest.raises(ValueError):
        g.add_node(Node(name="a"))


def test_quality_score_total_and_level() -> None:
    s = QualityScore(
        coverage=90, hierarchy=90, linkage=90, coherence=90, specificity=90, freshness=90
    )
    assert s.total == 90
    assert s.level == QualityLevel.EXCELLENT

    s = QualityScore(
        coverage=75, hierarchy=75, linkage=75, coherence=75, specificity=75, freshness=75
    )
    assert s.level == QualityLevel.GOOD

    s = QualityScore()
    assert s.level == QualityLevel.POOR


def test_link_fix_result_when_is_now() -> None:
    r = LinkFixResult(added=1, scanned=2)
    assert r.added == 1
    assert r.scanned == 2


# ---------- validator ----------


def _ok_graph() -> Graph:
    return Graph(
        domain="d",
        direction=Direction(summary="A comprehensive overview " + "x" * 30),
        nodes=[
            Node(name="alpha", links=["beta"]),
            Node(name="beta", links=["alpha"]),
        ],
    )


def test_validator_clean_graph_has_no_issues() -> None:
    v = GraphValidator()
    assert v.validate(_ok_graph()) == []


def test_model_rejects_empty_domain() -> None:
    """Pydantic enforces min_length=1 at the model layer; the validator
    therefore can't see an empty domain — that's the right place to
    guard this invariant."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Graph(domain="", direction=Direction(summary="x" * 30))


def test_validator_flags_uniqueness() -> None:
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[Node(name="dup"), Node(name="dup")],
    )
    issues = GraphValidator().validate(g)
    assert any("duplicate" in i for i in issues)


def test_validator_flags_broken_links() -> None:
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[Node(name="a", links=["ghost"])],
    )
    issues = GraphValidator().validate(g)
    assert any("broken" in i for i in issues)


def test_validator_flags_self_loop() -> None:
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[Node(name="a", links=["a"])],
    )
    issues = GraphValidator().validate(g)
    assert any("itself" in i for i in issues)


def test_validator_flags_dup_links() -> None:
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[Node(name="a", links=["b", "b"])],
    )
    issues = GraphValidator().validate(g)
    assert any("a:" in i for i in issues)


def test_validator_flags_cardinality() -> None:
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[Node(name="hub", links=[f"n{i}" for i in range(25)])],
    )
    issues = GraphValidator(max_links_per_node=20).validate(g)
    assert any(">20" in i for i in issues)


def test_validator_flags_empty_direction_summary() -> None:
    g = Graph(domain="d", nodes=[Node(name="a")])
    issues = GraphValidator().validate(g)
    assert any("direction.summary" in i for i in issues)


def test_validate_graph_convenience() -> None:
    assert validate_graph(_ok_graph()) == []


def test_score_is_pure_for_empty_graph() -> None:
    g = Graph(domain="d", direction=Direction(summary=""))
    score = GraphValidator().score(g)
    assert score.coverage == 0
    assert score.hierarchy == 0


def test_score_grows_with_node_count() -> None:
    v = GraphValidator()
    small = Graph(
        domain="d",
        direction=Direction(summary="x" * 40),
        nodes=[Node(name="a")],
    )
    big = Graph(
        domain="d",
        direction=Direction(summary="x" * 40),
        nodes=[Node(name=f"n{i}") for i in range(20)],
    )
    assert v.score(small).coverage < v.score(big).coverage


# ---------- link fixer (pure) ----------


def test_fix_missing_reverse_links_strips_upward_edges() -> None:
    """Forward-only invariant: link entries pointing at ancestors are removed."""
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[
            # Chain: root → A → B → C. C mistakenly links back to A (grandparent).
            Node(name="root", links=["A"]),
            Node(name="A", links=["B"]),
            Node(name="B", links=["C"]),
            Node(name="C", links=["A"]),
        ],
    )
    new_g, result = fix_missing_reverse_links(g)
    assert result.added == 1
    assert result.scanned == 4
    c = next(n for n in new_g.nodes if n.name == "C")
    assert c.links == []
    # Forward edges are untouched.
    root = next(n for n in new_g.nodes if n.name == "root")
    a = next(n for n in new_g.nodes if n.name == "A")
    b = next(n for n in new_g.nodes if n.name == "B")
    assert root.links == ["A"]
    assert a.links == ["B"]
    assert b.links == ["C"]


def test_fix_missing_reverse_links_is_idempotent() -> None:
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[
            Node(name="A", links=["B"]),
            Node(name="B", links=[]),
        ],
    )
    _, result = fix_missing_reverse_links(g)
    assert result.added == 0


def test_fix_does_not_mutate_input() -> None:
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[Node(name="A", links=["B"]), Node(name="B", links=[])],
    )
    fix_missing_reverse_links(g)
    b = next(n for n in g.nodes if n.name == "B")
    assert b.links == []


def test_fix_skips_broken_links() -> None:
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[
            Node(name="A", links=["ghost"]),
            Node(name="B", links=[]),
        ],
    )
    new_g, result = fix_missing_reverse_links(g)
    assert result.added == 0