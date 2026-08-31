"""kg_repair_json — chat-callable JSON repair tool.

When a model output (or any upstream string) is malformed JSON, the LLM
can call this tool with the raw text + a one-line schema hint.  We:

  1. Try cheap char-level repair (``try_parse_json``).
  2. If that still fails, ask the configured LLM to re-emit valid JSON.
  3. Return the parsed value as JSON, or an explicit error envelope so
     the caller knows the repair did NOT converge.

Why expose this as a tool at all?  Because many LLM-driven tools in this
project (graph node creation, quiz generation, intent parsing, etc.)
sometimes produce JSON that fails downstream parsing.  Rather than
letting the failure surface as "❌ ...", the agent can call
``kg_repair_json`` once, get clean JSON, and continue the chain.

Important: this tool does NOT loop.  It performs exactly one LLM
re-emit.  If the re-emit is still broken it returns
``{"ok": false, "error": "..."}`` so the caller can fall back.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from src.agent.dependencies import get_llm
from src.observability.logged_tool import logged_tool
from src.utils.json_repair import (
    DEFAULT_SCHEMA_HINT,
    try_parse_json,
    try_parse_json_with_llm,
)


@tool
@logged_tool
async def kg_repair_json(
    raw: str,
    schema_hint: str = DEFAULT_SCHEMA_HINT,
) -> str:
    """修复模型输出的 JSON 字符串并返回可解析的 JSON。

    用法：当一个工具的 result / LLM 的中间输出无法被下游 ``json.loads``
    解析时，先把那段原始字符串原样塞进 ``raw``（不要预先裁剪——带上
    思考块、Markdown fence、截断尾部都可以），再给一句 ``schema_hint``
    描述期望的形状，工具会：
      1. 先做字符级修复（尾逗号、Python bool、单引号、fence）。
      2. 若失败，再调一次 LLM 让它按 schema_hint 重写。
      3. 成功后直接返回可解析的 JSON 字符串，失败则返回 ``{"ok": false, "error": "..."}``。

    Args:
        raw: 待修复的原始字符串。允许包含 Markdown fence、思考块、尾部截断等。
        schema_hint: 期望的 JSON schema 的一句话描述，例如
            ``"a StructuredNote: {document_info, chapters[], knowledge_graph{nodes,edges}}"``。

    Returns:
        JSON 字符串。成功时是修复后的 JSON；失败时是 ``{"ok": false, "error": "..."}`` 包裹的诊断。
    """
    if not raw or not raw.strip():
        return json.dumps(
            {"ok": False, "error": "raw 为空，无法修复"}, ensure_ascii=False
        )

    # Tier 1: cheap char-level parse.  No LLM call needed.
    parsed: Any = try_parse_json(raw)
    if parsed is not None:
        return json.dumps(
            {"ok": True, "repaired_by": "char_level", "value": parsed},
            ensure_ascii=False,
        )

    # Tier 2: one LLM re-emit.
    try:
        llm = get_llm()
    except Exception as e:  # pragma: no cover - dependency wiring failure
        return json.dumps(
            {"ok": False, "error": f"无法获取 LLM 客户端: {e}"},
            ensure_ascii=False,
        )

    try:
        repaired = await try_parse_json_with_llm(llm, raw, schema_hint=schema_hint)
    except Exception as e:  # pragma: no cover - defensive
        return json.dumps(
            {"ok": False, "error": f"LLM 修复调用失败: {type(e).__name__}: {e}"},
            ensure_ascii=False,
        )

    if repaired is None:
        return json.dumps(
            {
                "ok": False,
                "error": "字符级与 LLM 修复均失败；请检查原始输出或更换 prompt",
                "raw_head": raw[:300],
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {"ok": True, "repaired_by": "llm", "value": repaired},
        ensure_ascii=False,
    )


__all__ = ["kg_repair_json"]
