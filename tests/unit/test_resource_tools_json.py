"""``kg_add_learning_resources`` — JSON tolerance for truncated LLM output.

Bug we're guarding against
--------------------------

``logs/error.log`` (2026-08-02 23:42/23:43) shows two failures::

    items_json 解析失败: Expecting ':' delimiter: line 1 column 4172 (char 4171)
    items_json 解析失败: Expecting ':' delimiter: line 1 column 1002 (char 1001)

The error offset equals the string length in both cases — the model ran
out of tokens mid-object.  The old code did a bare ``json.loads`` and
threw away *everything*, including the complete items that arrived
before the cut.  ``_salvage_truncated_array`` recovers that prefix.
"""

from __future__ import annotations

import json

from src.agent.tools.resource_tools import _salvage_truncated_array


def _item(title: str) -> dict:
    return {
        "title": title,
        "url": f"https://example.com/{title}",
        "summary": f"summary for {title}",
        "category": "网页",
    }


def test_salvage_recovers_complete_prefix_of_truncated_array() -> None:
    full = json.dumps([_item("a"), _item("b"), _item("c")], ensure_ascii=False)
    # Cut mid-way through the third object.
    truncated = full[: full.rindex("{") + 30]

    out = _salvage_truncated_array(truncated)

    assert out is not None
    assert [i["title"] for i in out] == ["a", "b"]
    assert out[0]["url"] == "https://example.com/a"


def test_salvage_is_a_noop_passthrough_for_intact_json() -> None:
    full = json.dumps([_item("a"), _item("b")], ensure_ascii=False)
    out = _salvage_truncated_array(full)
    assert out is not None
    assert [i["title"] for i in out] == ["a", "b"]


def test_salvage_ignores_braces_inside_string_values() -> None:
    """A ``{`` inside a title must not be counted as nesting depth."""
    items = [{"title": "why {this} breaks", "url": "u1"}, {"title": "second", "url": "u2"}]
    full = json.dumps(items, ensure_ascii=False)
    truncated = full[:-8]  # lop off the tail of the 2nd object

    out = _salvage_truncated_array(truncated)

    assert out is not None
    assert [i["title"] for i in out] == ["why {this} breaks"]


def test_salvage_returns_none_when_first_object_never_closes() -> None:
    assert _salvage_truncated_array('[{"title": "a", "url') is None


def test_salvage_returns_none_without_an_array() -> None:
    assert _salvage_truncated_array("not json at all") is None
    assert _salvage_truncated_array("") is None
