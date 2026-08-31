"""JSON repair helpers — extracted from the baseline note_tools._repair_json_string.

Two layers of repair, in increasing cost:

1. **Char-level** — :func:`repair_json_string` handles the easy glitches
   (trailing commas, single quotes, Python booleans, code fences, BOM).
2. **LLM-level** — :func:`llm_repair_json` is the last resort: when char
   repair can't make sense of the output (truncation, schema drift,
   interleaved prose), ask the model to re-emit a clean JSON of the
   expected shape.  Costs one extra LLM call; callers must opt in.

Use :func:`try_parse_json` for the cheap path.  For pipelines that have
access to an LLM client, prefer :func:`try_parse_json_with_llm` which
falls through to the LLM on failure.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# Cap how much of a malformed response we re-feed to the model.  Anything
# beyond this is almost certainly noise (the model wandered off-task) and
# would just dilute the repair prompt.
_MAX_RAW_CHARS = 8_000


def repair_json_string(text: str) -> str:
    """Best-effort repair of malformed JSON produced by LLMs.

    Handles common glitches: trailing commas, single quotes, Python
    booleans, comments, control chars inside strings.
    """
    if not text:
        return text
    # Strip BOM and code fences.
    text = text.lstrip("﻿").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    # Replace Python booleans.
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)
    # Drop trailing commas in objects/arrays.
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # Replace single quotes with double quotes (only outside existing
    # double quotes — the heuristic below is conservative).
    if '"' not in text and "'" in text:
        text = text.replace("'", '"')
    return text


def try_parse_json(text: str) -> dict[str, Any] | list[Any] | None:
    """Parse JSON, repairing first if the direct attempt fails."""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    repaired = repair_json_string(text)
    try:        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


# ----------------------------------------------------------------------
# LLM-level repair
# ----------------------------------------------------------------------


#: Schema description injected into the LLM repair prompt.  Callers should
#: override this with a one-line hint specific to their data shape, e.g.
#: "a StructuredNote: {document_info, chapters[], knowledge_graph{nodes,edges}}".
DEFAULT_SCHEMA_HINT = (
    "a single JSON object or array — re-emit it cleanly, preserving all "
    "facts but fixing the syntax.  Do not add new fields."
)


async def llm_repair_json(
    llm: Any,
    raw: str,
    *,
    schema_hint: str = DEFAULT_SCHEMA_HINT,
    max_tokens: int = 4000,
) -> Any | None:
    """Ask the LLM to re-emit ``raw`` as valid JSON.

    Cheap char-level repair (see :func:`try_parse_json`) is tried first
    by the caller; this function ONLY handles the case where that has
    already failed.  Returns the parsed object, or ``None`` if the model
    still produced something unparseable.

    The ``llm`` argument must expose ``await llm.chat(system, user, ...)``
    — the same interface the rest of the codebase uses.  We accept any
    object that has a ``chat`` coroutine, so tests can inject a mock.
    """
    if not raw:
        return None
    # Truncate so a runaway response doesn't blow the prompt budget.
    clipped = raw[:_MAX_RAW_CHARS]
    if len(raw) > _MAX_RAW_CHARS:
        clipped += "\n\n[... 原输出过长已截断 ...]"

    prompt = (
        "下面是模型上一次输出，未能解析为合法 JSON（可能截断、缺括号、"
        "夹带说明文字或字段错位）。\n"
        f"请**严格只输出**符合以下 schema 的合法 JSON：\n{schema_hint}\n\n"
        "要求：\n"
        "1. 保留原文中所有事实信息，不要凭空添加。\n"
        "2. 字段名与原 schema 一致，不要新增、不要改名。\n"
        "3. **只输出 JSON**，禁止 Markdown fence、禁止任何解释文字。\n\n"
        "--- 原始输出 ---\n"
        f"{clipped}\n"
        "--- 修正后的 JSON ---"
    )
    try:
        text = await llm.chat(
            "你是一个 JSON 修复专家，只输出合法 JSON。",
            prompt,
            json_mode=True,
            max_tokens=max_tokens,
        )
    except Exception:
        logger.warning("llm_repair_json: LLM call failed", exc_info=True)
        return None
    return try_parse_json(str(text) if text is not None else "")


async def try_parse_json_with_llm(
    llm: Any,
    text: str,
    *,
    schema_hint: str = DEFAULT_SCHEMA_HINT,
    max_tokens: int = 4000,
) -> Any | None:
    """Char-level parse, then LLM-level parse on failure.

    Convenience wrapper used by the digest / pipeline stages.  Keeps the
    cheap path cheap — the LLM is only consulted when char-level parse
    fails AND the input isn't trivially empty.
    """
    parsed = try_parse_json(text)
    if parsed is not None:
        return parsed
    if not text or not text.strip():
        return None
    logger.info(
        "try_parse_json_with_llm: char-level repair failed (%d chars), "
        "falling back to LLM repair",
        len(text),
    )
    return await llm_repair_json(
        llm, text, schema_hint=schema_hint, max_tokens=max_tokens
    )


__all__ = [
    "repair_json_string",
    "try_parse_json",
    "llm_repair_json",
    "try_parse_json_with_llm",
    "DEFAULT_SCHEMA_HINT",
]