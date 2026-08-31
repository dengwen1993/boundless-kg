"""JSON repair — char-level + LLM-level."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.utils.json_repair import (
    llm_repair_json,
    try_parse_json,
    try_parse_json_with_llm,
)


# ───────────────────── helpers ─────────────────────


class _ScriptedLLM:
    """Returns one canned response per ``chat`` call.

    Mirrors the ``AsyncLLMClient.chat(system, user, **kwargs)`` shape used
    elsewhere in the test suite.  No asyncio mocking needed.
    """

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, system: str, user: str, **kwargs: Any) -> str:
        self.calls.append({"system": system, "user": user, "kwargs": kwargs})
        if not self._replies:
            return ""  # simulate an empty model output
        return self._replies.pop(0)


# ───────────────────── char-level (existing, regress) ─────────────────────


def test_char_level_repairs_trailing_comma() -> None:
    raw = '{"a": 1, "b": 2,}'
    assert try_parse_json(raw) == {"a": 1, "b": 2}


def test_char_level_returns_none_on_truncated_object() -> None:
    # Char-level cannot recover from a truncated body — LLM must intervene.
    assert try_parse_json('{"a": 1, "b": [1, 2,') is None


# ───────────────────── LLM-level ─────────────────────


@pytest.mark.asyncio
async def test_llm_repair_recovers_from_truncated_json() -> None:
    raw = '{"a": 1, "b": [1, 2,'  # truncated — char-level fails
    fixed = '{"a": 1, "b": [1, 2]}'
    llm = _ScriptedLLM([fixed])
    out = await llm_repair_json(llm, raw)
    assert out == {"a": 1, "b": [1, 2]}
    assert len(llm.calls) == 1
    # Prompt must include the raw text and forbid Markdown fence.
    assert "原始输出" in llm.calls[0]["user"] or "---" in llm.calls[0]["user"]
    assert llm.calls[0]["kwargs"].get("json_mode") is True


@pytest.mark.asyncio
async def test_llm_repair_returns_none_when_model_still_garbled() -> None:
    raw = "{nonsense"
    llm = _ScriptedLLM(["still not json"])
    assert await llm_repair_json(llm, raw) is None


@pytest.mark.asyncio
async def test_llm_repair_returns_none_on_empty_input() -> None:
    llm = _ScriptedLLM([])
    assert await llm_repair_json(llm, "") is None
    # No LLM call should have been made.
    assert llm.calls == []


@pytest.mark.asyncio
async def test_llm_repair_returns_none_when_model_empty() -> None:
    raw = "totally broken"
    llm = _ScriptedLLM([""])
    assert await llm_repair_json(llm, raw) is None


@pytest.mark.asyncio
async def test_llm_repair_handles_llm_exception() -> None:
    class _Boom:
        async def chat(self, *a: Any, **kw: Any) -> str:
            raise RuntimeError("upstream 5xx")

    assert await llm_repair_json(_Boom(), "{bad") is None


# ───────────────────── combined (cheap + LLM) ─────────────────────


@pytest.mark.asyncio
async def test_try_parse_json_with_llm_skips_llm_on_char_success() -> None:
    raw = '{"a": 1,}'  # char-level can fix
    llm = _ScriptedLLM([])  # would fail if called
    out = await try_parse_json_with_llm(llm, raw)
    assert out == {"a": 1}
    assert llm.calls == []  # crucial: no extra LLM call


@pytest.mark.asyncio
async def test_try_parse_json_with_llm_falls_back_to_llm() -> None:
    raw = '{"a": 1, "b": [1,'  # truncated
    llm = _ScriptedLLM(['{"a": 1, "b": [1]}'])
    out = await try_parse_json_with_llm(llm, raw)
    assert out == {"a": 1, "b": [1]}
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_try_parse_json_with_llm_returns_none_on_empty() -> None:
    llm = _ScriptedLLM([])
    assert await try_parse_json_with_llm(llm, "") is None
    assert await try_parse_json_with_llm(llm, "   ") is None


@pytest.mark.asyncio
async def test_llm_repair_uses_schema_hint_in_prompt() -> None:
    raw = '{"chaptrs": [{'
    llm = _ScriptedLLM(['{"chapters": []}'])
    out = await llm_repair_json(
        llm, raw, schema_hint="a StructuredNote: {chapters: [...]}"
    )
    assert out == {"chapters": []}
    assert "StructuredNote" in llm.calls[0]["user"]


@pytest.mark.asyncio
async def test_llm_repair_truncates_huge_input() -> None:
    # 10 000 chars of pure noise; the prompt must clip.
    raw = "x" * 10_000
    llm = _ScriptedLLM(['{"ok": true}'])
    out = await llm_repair_json(llm, raw)
    assert out == {"ok": True}
    user_msg = llm.calls[0]["user"]
    # The user prompt should NOT contain all 10 000 chars verbatim.
    assert len(user_msg) < 9_000
    assert "已截断" in user_msg or "truncat" in user_msg.lower() or "---" in user_msg
