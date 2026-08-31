"""SSE ``tool-result`` payload extraction.

Bug we're guarding against
-------------------------
``on_tool_end`` hands us ``ev["data"]["output"]`` which is a LangChain
``ToolMessage``, **not** the tool's return value.  The route used to do
``str(out)``, which serialises the wrapper's repr::

    content='{"ok": true, ...}' name='kg_open_node' tool_call_id='...'

The frontend (``chat.ts:handleOpenNodeResult``) calls ``JSON.parse`` on
that string and throws ``Unexpected token 'c', "content='{"...``, so the
``kg_open_node`` navigation side-effect never fires.
"""

from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from src.api.routes.agent import _extract_tool_output


PAYLOAD = {
    "ok": True,
    "domain": "AI 架构师深入学习",
    "node": "RAG架构设计",
    "path": ["领域根", "向量数据库与RAG", "RAG架构设计"],
    "tier": "L3",
    "level": 3,
}
RAW = json.dumps(PAYLOAD, ensure_ascii=False)


def test_unwraps_tool_message() -> None:
    """The regression: a ToolMessage must yield its ``.content``, not its repr."""
    out = ToolMessage(content=RAW, name="kg_open_node", tool_call_id="call-1")
    extracted = _extract_tool_output(out)

    assert not extracted.startswith("content=")
    assert json.loads(extracted) == PAYLOAD


def test_plain_string_passes_through() -> None:
    assert _extract_tool_output(RAW) == RAW


def test_none_becomes_empty_string() -> None:
    assert _extract_tool_output(None) == ""


def test_dict_serialised_tool_message() -> None:
    assert _extract_tool_output({"content": RAW, "name": "kg_open_node"}) == RAW


def test_content_block_list() -> None:
    out = ToolMessage(
        content=[{"type": "text", "text": RAW}],
        name="kg_open_node",
        tool_call_id="call-2",
    )
    assert json.loads(_extract_tool_output(out)) == PAYLOAD


def test_unstructured_value_falls_back_to_str() -> None:
    assert _extract_tool_output(42) == "42"
