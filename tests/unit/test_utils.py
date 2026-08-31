"""Utilities — text normalisation, JSON repair."""

from __future__ import annotations

from src.utils.json_repair import repair_json_string, try_parse_json
from src.utils.text import jaccard, normalize_text


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("  Hello\tWorld\n ") == "hello world"


def test_normalize_text_lowercases() -> None:
    assert normalize_text("ABC def") == "abc def"


def test_jaccard_same_returns_one() -> None:
    assert jaccard("alpha", "alpha") == 1.0


def test_jaccard_empty_inputs() -> None:
    assert jaccard("", "") == 1.0
    assert jaccard("", "x") == 0.0


def test_repair_strips_bom_and_fences() -> None:
    raw = "```json\n{\"a\": 1}\n```"
    assert try_parse_json(raw) == {"a": 1}


def test_repair_handles_trailing_comma() -> None:
    raw = '{"a": 1, "b": 2,}'
    assert try_parse_json(raw) == {"a": 1, "b": 2}


def test_repair_handles_python_booleans() -> None:
    raw = '{"flag": True, "none": None}'
    assert try_parse_json(raw) == {"flag": True, "none": None}


def test_repair_returns_none_on_total_garbage() -> None:
    assert try_parse_json("definitely not json") is None


def test_repair_returns_none_on_empty() -> None:
    assert try_parse_json("") is None


def test_repair_json_string_is_idempotent() -> None:
    raw = '{"a": 1, "b": True,}'
    once = repair_json_string(raw)
    twice = repair_json_string(once)
    assert once == twice