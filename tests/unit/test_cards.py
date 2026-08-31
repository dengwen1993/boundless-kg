"""Unit tests for the cards package.

Covers the loader (front-matter parsing + directory scan), the selector
(keyword ∪ tool-stack activation + rendering), and the middleware dispatch
(pass-through when disabled, augmentation when enabled). Network-free and
DB-free — all dependencies are local files + a fake ``ModelRequest``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.cards import (
    CardLibrary,
    CardsMiddleware,
    extract_used_tools,
    last_user_message,
    parse_card,
    render_cards,
    select_cards,
)
from src.agent.cards.models import Card, CardParseError


# ── parse_card / front-matter ────────────────────────────────────────────


def test_parse_card_happy(tmp_path: Path) -> None:
    p = tmp_path / "plans.md"
    p.write_text(
        '---\n'
        'id: plans\n'
        'title: 学习计划原子拆分\n'
        'triggers: ["计划", "学习计划"]\n'
        'applies_to_tools: ["kg_add_plan"]\n'
        'priority: 10\n'
        '---\n'
        '\n'
        'Plan rules go here.\n',
        encoding="utf-8",
    )
    card = parse_card(p)
    assert card.id == "plans"
    assert card.title == "学习计划原子拆分"
    assert card.triggers == ("计划", "学习计划")
    assert card.applies_to_tools == ("kg_add_plan",)
    assert card.priority == 10
    assert "Plan rules" in card.body


def test_parse_card_default_id_is_stem(tmp_path: Path) -> None:
    p = tmp_path / "wildcard.md"
    p.write_text(
        '---\ntitle: anything\n---\nbody\n',
        encoding="utf-8",
    )
    card = parse_card(p)
    assert card.id == "wildcard"


def test_parse_card_rejects_no_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "nofront.md"
    p.write_text("just body\n", encoding="utf-8")
    with pytest.raises(CardParseError):
        parse_card(p)


def test_parse_card_empty_triggers_ok(tmp_path: Path) -> None:
    p = tmp_path / "toolsonly.md"
    p.write_text(
        '---\nid: x\ntitle: t\napplies_to_tools: ["kg_run_skill"]\n---\nb\n',
        encoding="utf-8",
    )
    card = parse_card(p)
    assert card.triggers == ()


# ── CardLibrary ─────────────────────────────────────────────────────────


def test_library_sorts_by_priority(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text(
        '---\nid: b\ntitle: B\npriority: 50\n---\nbody\n', encoding="utf-8",
    )
    (tmp_path / "a.md").write_text(
        '---\nid: a\ntitle: A\npriority: 10\n---\nbody\n', encoding="utf-8",
    )
    (tmp_path / "c.md").write_text(
        '---\nid: c\ntitle: C\npriority: 10\n---\nbody\n', encoding="utf-8",
    )
    lib = CardLibrary.from_directory(tmp_path)
    ids = [c.id for c in lib]
    # priority 10 first (a then c by id), then 50 (b)
    assert ids == ["a", "c", "b"]


def test_library_skips_parse_errors(tmp_path: Path) -> None:
    (tmp_path / "good.md").write_text(
        '---\nid: good\ntitle: G\n---\nbody\n', encoding="utf-8",
    )
    (tmp_path / "bad.md").write_text("no front matter\n", encoding="utf-8")
    lib = CardLibrary.from_directory(tmp_path)
    assert len(lib) == 1
    assert lib.get("good") is not None


def test_library_missing_dir_is_empty(tmp_path: Path) -> None:
    lib = CardLibrary.from_directory(tmp_path / "absent")
    assert len(lib) == 0


# ── selector ─────────────────────────────────────────────────────────────


def _make_card(
    *,
    id: str,
    triggers: tuple[str, ...] = (),
    applies: tuple[str, ...] = (),
) -> Card:
    return Card(
        id=id,
        title=id,
        triggers=triggers,
        applies_to_tools=applies,
        body=f"{id} body",
    )


def test_select_user_message_match() -> None:
    lib = CardLibrary((_make_card(id="plans", triggers=("计划",)),))
    out = select_cards(lib, user_message="帮我做一个学习计划")
    assert [c.id for c in out] == ["plans"]


def test_select_tool_stack_match() -> None:
    lib = CardLibrary((_make_card(id="trees", applies=("kg_add_subtree",)),))
    out = select_cards(lib, user_message="无关的话", used_tools=("kg_add_subtree",))
    assert [c.id for c in out] == ["trees"]


def test_select_uses_or_semantics() -> None:
    lib = CardLibrary((
        _make_card(id="a", triggers=("foo",), applies=()),
        _make_card(id="b", triggers=(), applies=("kg_x",)),
    ))
    # user msg matches a; tool stack matches b; both active.
    out = select_cards(
        lib,
        user_message="foo bar",
        used_tools=("kg_x", "kg_y"),
    )
    assert {c.id for c in out} == {"a", "b"}


def test_select_empty_inputs() -> None:
    lib = CardLibrary((_make_card(id="a", triggers=("x",)),))
    assert select_cards(lib) == []
    assert select_cards(lib, user_message="") == []


def test_select_empty_triggers_never_match_user() -> None:
    lib = CardLibrary((_make_card(id="toolonly", applies=("kg_x",)),))
    # Only user message provided ⇒ must NOT match (card is tool-only).
    assert select_cards(lib, user_message="任意") == []


def test_select_empty_applies_never_match_tools() -> None:
    lib = CardLibrary((_make_card(id="msgs", triggers=("计划",)),))
    # Only tool stack provided ⇒ must NOT match.
    assert select_cards(lib, user_message="", used_tools=("kg_x",)) == []


# ── render ──────────────────────────────────────────────────────────────


def test_render_empty() -> None:
    assert render_cards([]) == ""


def test_render_has_headers() -> None:
    out = render_cards([_make_card(id="plans", triggers=("计划",))])
    assert "## plans" in out
    assert "<active_cards>" in out
    assert "</active_cards>" in out


# ── extract_used_tools / last_user_message ───────────────────────────────


class _FakeAIMessage:
    def __init__(self, tool_calls: list[dict]) -> None:
        self.tool_calls = tool_calls


class _FakeHumanMessage:
    def __init__(self, content: str) -> None:
        self.content = content


def test_extract_used_tools_dedups_in_order() -> None:
    msgs = [
        _FakeAIMessage([{"name": "kg_view_graph"}]),
        _FakeAIMessage([{"name": "kg_add_node"}, {"name": "kg_view_graph"}]),
    ]
    assert extract_used_tools(msgs) == ["kg_view_graph", "kg_add_node"]


def test_extract_used_tools_handles_missing_attr() -> None:
    assert extract_used_tools([object(), object()]) == []


def test_last_user_message_returns_last() -> None:
    msgs = [
        _FakeHumanMessage("first"),
        _FakeAIMessage([{"name": "x"}]),
        _FakeHumanMessage("second"),
    ]
    assert last_user_message(msgs) == "second"


# ── CardsMiddleware dispatch ─────────────────────────────────────────────


class _StubRequest:
    """Minimal stand-in for langchain's ModelRequest — only the fields our
    middleware touches are exposed."""

    def __init__(self, messages, system_message) -> None:
        self.state = {"messages": messages}
        self.system_message = system_message
        self.override_calls = []

    def override(self, *, system_message):
        self.override_calls.append(system_message)
        return _StubRequest(self.state["messages"], system_message)


def test_middleware_disabled_passes_through() -> None:
    mw = CardsMiddleware(enabled=False, cards_dir=Path("/nonexistent"))
    req = _StubRequest([], None)

    def handler(r):
        return ("ok", id(r))

    out = mw.wrap_model_call(req, handler)
    assert out == ("ok", id(req))
    assert req.override_calls == []


def test_middleware_enabled_appends_when_match() -> None:
    lib = CardLibrary((_make_card(id="plans", triggers=("计划",)),))
    mw = CardsMiddleware(library=lib, enabled=True)
    msgs = [_FakeHumanMessage("做个学习计划")]
    req = _StubRequest(msgs, None)

    captured = {}

    def handler(r):
        captured["sm"] = r.system_message
        return "ok"

    out = mw.wrap_model_call(req, handler)
    assert out == "ok"
    assert len(req.override_calls) == 1
    # Appended SystemMessage should mention plans and reflect the priority order.
    sm = captured["sm"]
    assert sm is not None
    # SystemMessage stores content as a list of content blocks; flatten to text.
    blocks = sm.content if isinstance(sm.content, list) else [{"text": sm.content}]
    text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))
    assert "plans body" in text
    assert "## plans" in text


def test_middleware_enabled_no_match_no_override() -> None:
    lib = CardLibrary((_make_card(id="plans", triggers=("计划",)),))
    mw = CardsMiddleware(library=lib, enabled=True)
    msgs = [_FakeHumanMessage("今天天气真好")]
    req = _StubRequest(msgs, None)

    def handler(r):
        return r

    mw.wrap_model_call(req, handler)
    # No active card → no override call.
    assert req.override_calls == []


def test_middleware_handles_missing_messages() -> None:
    lib = CardLibrary((_make_card(id="plans", triggers=("计划",)),))
    mw = CardsMiddleware(library=lib, enabled=True)

    class BadReq:
        state = None  # type: ignore[assignment]
        system_message = None

        def override(self, **kw):
            raise AssertionError("should not override on empty state")

    def handler(r):
        return r

    # Should not raise — middleware defensively handles state=None.
    mw.wrap_model_call(BadReq(), handler)
