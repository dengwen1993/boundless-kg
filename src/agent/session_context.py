"""SessionContextMiddleware — inject recent conversation context.

On every model request, reads the tail of the most recent session log file
and appends a brief ``<session_context>`` block to the system prompt.

This provides continuity across restarts: the agent can recall what was
being discussed in the last session without requiring the in-memory
checkpointer to survive process restarts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import (
        AgentMiddleware,
        AgentState,
        ContextT,
        ModelRequest,
        ModelResponse,
        ResponseT,
    )

from langchain.agents.middleware.types import AgentMiddleware

from deepagents.middleware._utils import append_to_system_message

from src.agent.memory import get_conversations_dir

logger = logging.getLogger(__name__)

_TAIL_LINES = 200  # lines to read from end of latest session
_SESSION_CONTEXT_TEMPLATE = """\
<session_context>
以下是你最近一次会话的尾部摘要（来自 {session_path}）。可能有助于你回忆上下文：

{session_tail}
</session_context>"""


def _find_latest_session(max_age_days: int = 14) -> Path | None:
    """Return the most recent session file, or None.

    Session files now live under ``conversations/{YYYY-MM-DD}/*.jsonl``
    — we still glob for ``*.jsonl`` only (legacy ``*.md`` files are not
    treated as new sessions).
    """
    root = get_conversations_dir()
    if not root.exists():
        return None
    cutoff = datetime.now() - timedelta(days=max_age_days)
    candidates: list[tuple[Path, datetime]] = []
    for date_dir in root.iterdir():
        if not date_dir.is_dir():
            continue
        try:
            dt = datetime.strptime(date_dir.name, "%Y-%m-%d")
            if dt < cutoff:
                continue
        except ValueError:
            continue
        for f in date_dir.glob("*.jsonl"):
            if f.is_file():
                candidates.append((f, datetime.fromtimestamp(f.stat().st_mtime)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def _read_tail(path: Path, lines: int = _TAIL_LINES) -> str:
    """Read the last N *records* of a JSONL session file and render
    them as a human-readable tail for the agent's session_context block.

    Each record is a JSON object on its own line; we pretty-print the
    ``content`` / ``result`` / ``message`` field (whichever is present)
    so the model can read it.
    """
    import json

    try:
        all_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return "(无法读取会话文件)"

    # Parse records, drop any malformed lines.
    records: list[dict] = []
    for raw in all_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            continue

    if not records:
        return "(空会话文件)"

    tail = records[-lines:]
    rendered: list[str] = []
    for rec in tail:
        ts = rec.get("ts", "")
        rtype = rec.get("type", "?")
        if rtype == "user":
            rendered.append(f"[{ts}] 👤 User: {rec.get('content', '')}")
        elif rtype == "agent":
            rendered.append(f"[{ts}] 🤖 Agent: {rec.get('content', '')}")
        elif rtype == "tool_call":
            args = rec.get("args", {})
            rendered.append(
                f"[{ts}] 🔧 Tool Call: {rec.get('name', '')}  args={args}"
            )
        elif rtype == "tool_result":
            rendered.append(
                f"[{ts}] ↩️ Result: {rec.get('name', '')}  "
                f"result={rec.get('result', '')[:500]}"
            )
        elif rtype == "error":
            rendered.append(f"[{ts}] ❌ Error: {rec.get('message', '')}")
        elif rtype in ("session_start", "session_resume", "session_end"):
            rendered.append(f"[{ts}] ─ {rtype}")
        else:
            rendered.append(f"[{ts}] {rtype}: {rec}")

    if len(records) > lines:
        return "... (earlier records omitted)\n" + "\n".join(rendered)
    return "\n".join(rendered)


def build_session_context() -> str:
    """Build the session-context string for system-prompt injection.

    Returns an empty string when there is no recent session to recall.
    """
    latest = _find_latest_session()
    if latest is None:
        return ""
    tail = _read_tail(latest, _TAIL_LINES)
    if not tail.strip():
        return ""
    rel_path = str(latest.relative_to(get_conversations_dir()))
    return _SESSION_CONTEXT_TEMPLATE.format(
        session_path=rel_path,
        session_tail=tail,
    )


class SessionContextMiddleware(AgentMiddleware):
    """Middleware that appends recent session context to each model request.

    Call ``build_session_context()`` at build time so the context is
    frozen for the lifetime of the agent (cached by LangGraph). The
    context string is pre-computed to avoid disk I/O on every turn.
    """

    def __init__(self, session_context: str = "") -> None:
        self._context = session_context

    def modify_request(
        self, request: "ModelRequest[ContextT]"
    ) -> "ModelRequest[ContextT]":
        if not self._context or not self._context.strip():
            return request
        new_system = append_to_system_message(request.system_message, self._context)
        if new_system is request.system_message:
            return request
        return request.override(system_message=new_system)

    async def awrap_model_call(
        self,
        request: "ModelRequest[ContextT]",
        handler: "Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]]",
    ) -> "ModelResponse[ResponseT]":
        modified = self.modify_request(request)
        return await handler(modified)

    def wrap_model_call(
        self,
        request: "ModelRequest[ContextT]",
        handler: "Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]]",
    ) -> "ModelResponse[ResponseT]":
        modified = self.modify_request(request)
        return handler(modified)


__all__ = ["SessionContextMiddleware", "build_session_context"]
