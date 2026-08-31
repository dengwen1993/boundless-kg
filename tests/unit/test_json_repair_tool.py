"""kg_repair_json — chat-callable JSON repair tool."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from src.agent.tools.json_repair_tool import kg_repair_json


# ───────────────────── helpers ─────────────────────


class _StubLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def chat(self, system: str, user: str, **kwargs: Any) -> str:
        self.calls += 1
        return self.reply


# ───────────────────── tests ─────────────────────


@pytest.mark.asyncio
async def test_kg_repair_json_empty_input() -> None:
    out = json.loads(await kg_repair_json.ainvoke({"raw": ""}))
    assert out["ok"] is False
    assert "raw 为空" in out["error"]


@pytest.mark.asyncio
async def test_kg_repair_json_whitespace_only() -> None:
    out = json.loads(await kg_repair_json.ainvoke({"raw": "   \n  "}))
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_kg_repair_json_char_level_success() -> None:
    # Trailing comma — char-level fix, NO LLM call.
    raw = '{"a": 1,}'
    with patch("src.agent.tools.json_repair_tool.get_llm") as get_llm:
        out = json.loads(await kg_repair_json.ainvoke({"raw": raw}))
    assert out["ok"] is True
    assert out["repaired_by"] == "char_level"
    assert out["value"] == {"a": 1}
    get_llm.assert_not_called()  # critical: no LLM round-trip


@pytest.mark.asyncio
async def test_kg_repair_json_falls_back_to_llm() -> None:
    # Truncated — char-level fails, must call LLM.
    raw = '{"a": 1, "b": [1, 2,'
    stub = _StubLLM('{"a": 1, "b": [1, 2]}')
    with patch(
        "src.agent.tools.json_repair_tool.get_llm", return_value=stub
    ):
        out = json.loads(await kg_repair_json.ainvoke({"raw": raw}))
    assert out["ok"] is True
    assert out["repaired_by"] == "llm"
    assert out["value"] == {"a": 1, "b": [1, 2]}
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_kg_repair_json_returns_error_when_llm_also_fails() -> None:
    raw = "{nonsense"
    stub = _StubLLM("still not json")
    with patch(
        "src.agent.tools.json_repair_tool.get_llm", return_value=stub
    ):
        out = json.loads(await kg_repair_json.ainvoke({"raw": raw}))
    assert out["ok"] is False
    assert "字符级与 LLM 修复均失败" in out["error"]
    assert "raw_head" in out


@pytest.mark.asyncio
async def test_kg_repair_json_handles_llm_getter_failure() -> None:
    raw = "{nonsense"
    with patch(
        "src.agent.tools.json_repair_tool.get_llm",
        side_effect=RuntimeError("no llm wired"),
    ):
        out = json.loads(await kg_repair_json.ainvoke({"raw": raw}))
    assert out["ok"] is False
    assert "无法获取 LLM 客户端" in out["error"]


@pytest.mark.asyncio
async def test_kg_repair_json_passes_schema_hint() -> None:
    raw = '{"chaptrs": [{'
    stub = _StubLLM('{"chapters": []}')
    with patch(
        "src.agent.tools.json_repair_tool.get_llm", return_value=stub
    ):
        out = await kg_repair_json.ainvoke(
            {"raw": raw, "schema_hint": "a StructuredNote: {chapters: [...]}"}
        )
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert parsed["value"] == {"chapters": []}
