"""Agent memory subsystem — conversation logging + workspace backend.

Architecture
============

Two complementary memory layers backed by a single ``FilesystemBackend``
rooted at :func:`src.config.settings.get_workspace_dir`:

1. **Conversation history** (short-term, per-session)
   - Every SSE stream is logged to disk as JSON-Lines.
   - Path: ``<workspace>/.agent_memory/conversations/{YYYY-MM-DD}/{uuid16}.jsonl``
   - Append-only within a session; new session → new file.

2. **Workspace AGENTS.md** (long-term, curated)
   - The single source of truth is ``<workspace>/AGENTS.md`` — git-tracked,
     hand-edited by the user, and **injected into the system prompt at
     agent build time** by :func:`src.agent.system_prompt.compose_system_prompt`.
   - DeepAgents' ``MemoryMiddleware`` exposes the same file as the
     read/edit virtual path ``/AGENTS.md`` so the agent can also read /
     amend it via tools on demand.
   - There is **no** ``.agent_memory/AGENTS.md`` any more — that copy was
     historically auto-generated from a 4-section template and only led
     to "wrote to .agent_memory but never reached workspace" confusion.
     See commit history / ``load_workspace_agents_md`` for the full story.

The ``FilesystemBackend`` root is the workspace itself (so ``/AGENTS.md``
maps to ``workspace/AGENTS.md``); ``.agent_memory/`` remains the home for
conversation logs + transient files only.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from deepagents.backends import FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver

from src.config import get_agent_memory_dir, get_workspace_dir

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Path helpers
# ------------------------------------------------------------------

_CONVERSATIONS_SUBDIR = "conversations"
_TMP_SUBDIR = "tmp"

#: Default retention window for ``tmp/`` files. Files older than this
#: are deleted by :func:`cleanup_tmp`. Override per-call via the same
#: parameter when an operator wants a different threshold.
TMP_MAX_AGE_DAYS: int = 7


def get_memory_root() -> Path:
    """Return the agent memory root directory.

    Reads ``KG_AGENT_MEMORY_DIR`` (default ``.agent_memory``), resolved
    against ``KG_KB_ROOT``. Created on first access.
    """
    root = get_agent_memory_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_conversations_dir() -> Path:
    """Return the base conversations directory (without date)."""
    d = get_memory_root() / _CONVERSATIONS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_tmp_dir() -> Path:
    """Return the transient-file directory under the agent memory root.

    The directory sits alongside ``conversations/`` but is **distinct**:
    anything dropped here is considered throwaway by the agent and is
    subject to automatic cleanup by :func:`cleanup_tmp`. Use it for
    scratch files the agent needs within a single session — never for
    curated knowledge.
    """
    d = get_memory_root() / _TMP_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup_tmp(max_age_days: int = TMP_MAX_AGE_DAYS) -> int:
    """Delete files under ``.agent_memory/tmp/`` older than *max_age_days*.

    Used by the periodic background task registered in
    :mod:`src.api.server`'s lifespan so the directory doesn't grow
    unbounded. Safe to call multiple times — only files whose mtime is
    older than the cutoff are removed; the directory itself is kept.

    Returns the number of files deleted.
    """
    if max_age_days <= 0:
        raise ValueError(f"max_age_days must be positive, got {max_age_days}")
    tmp_root = get_tmp_dir()
    cutoff = datetime.now().timestamp() - max_age_days * 86400
    removed = 0
    for path in tmp_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
        except OSError:
            # Stale file (deleted by another process between rglob and
            # stat) — skip silently.
            continue
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.warning("cleanup_tmp: failed to delete %s (%s)", path, exc)
    # Best-effort prune of now-empty sub-directories.
    for path in sorted(tmp_root.rglob("*"), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    return removed


def get_conversation_path(session_id: str, *, when: datetime | None = None) -> Path:
    """Return the file path for a specific session's conversation log.

    Structure: ``conversations/{YYYY-MM-DD}/{session_id}.jsonl``
    """
    dt = when or datetime.now()
    date_dir = get_conversations_dir() / dt.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    safe_id = _sanitize_session_id(session_id)
    return date_dir / f"{safe_id}.jsonl"


def generate_session_id() -> str:
    """Return a new session ID — a 16-character hex prefix of uuid4.

    Example: ``a3f1c2d4e5b60718``.
    """
    return uuid.uuid4().hex[:16]


def _sanitize_session_id(raw: str) -> str:
    """Make a session ID safe for use as a filename.

    If ``raw`` is empty / whitespace / None, a fresh 16-char UUID is
    generated. Otherwise ``raw`` is reduced to its safe character set
    (alnum / dash / underscore / dot) so it can be used as a filename.
    """
    if not raw or not raw.strip():
        return generate_session_id()
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in raw)
    return safe or generate_session_id()


# ------------------------------------------------------------------
# Workspace AGENTS.md locator (single source of truth)
# ------------------------------------------------------------------

_WORKSPACE_AGENTS_MD = "AGENTS.md"


def get_workspace_agents_md_path() -> Path:
    """Return the canonical path to ``<workspace_dir>/AGENTS.md``.

    Replaces the legacy ``.agent_memory/AGENTS.md`` location: that copy
    was a 4-section template that diverged silently from the user's
    hand-maintained file. There is exactly one AGENTS.md per workspace
    now, and it is ``<workspace>/AGENTS.md``.
    """
    return get_workspace_dir() / _WORKSPACE_AGENTS_MD


# ------------------------------------------------------------------
# Backend & checkpointer singletons
# ------------------------------------------------------------------

_fs_backend: FilesystemBackend | None = None
_checkpointer: MemorySaver | None = None


def get_filesystem_backend() -> FilesystemBackend:
    """Return a shared ``FilesystemBackend`` rooted at the workspace.

    The root is :func:`get_workspace_dir` (default ``./workspace``),
    **not** the legacy ``.agent_memory/`` subdirectory. This means the
    virtual path ``/AGENTS.md`` resolves to ``<workspace>/AGENTS.md`` —
    the single curated memory source. Conversation logs and transient
    files live in ``<workspace>/.agent_memory/`` but they are not
    exposed as virtual paths (they are managed by ``ConversationLogger``
    + ``cleanup_tmp`` using direct filesystem calls).

    This backend is shared by:
    - ``MemoryMiddleware`` (loads/exposes the curated AGENTS.md)
    - ``FilesystemMiddleware`` (provides read_file / write_file / edit_file
      tools to the agent)
    - The skills middleware mount, via :func:`src.agent.skills_setup.get_skills_backend`.
    """
    global _fs_backend
    if _fs_backend is None:
        root = str(get_workspace_dir())
        _fs_backend = FilesystemBackend(root_dir=root, virtual_mode=True)
        logger.info("FilesystemBackend root: %s", root)
    return _fs_backend


def get_checkpointer() -> MemorySaver:
    """Return a shared in-memory checkpointer for session persistence.

    Enables thread-based conversation continuity: if the user reconnects
    with the same ``thread_id``, the agent resumes the prior context.

    Note: state is in-process only. For cross-restart persistence, swap
    this for a ``SqliteSaver`` or ``PostgresSaver``.
    """
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = MemorySaver()
        logger.info("MemorySaver checkpointer initialized")
    return _checkpointer


def reset_memory_subsystem() -> None:
    """Drop cached singletons. Tests use this between cases."""
    global _fs_backend, _checkpointer
    _fs_backend = None
    _checkpointer = None


# ------------------------------------------------------------------
# Conversation logger
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# Conversation logger
# ------------------------------------------------------------------


class ConversationLogger:
    """Async append-only JSON-Lines logger for agent conversations.

    Each session gets one file under
    ``conversations/{date}/{uuid16}.jsonl``. Records are appended as
    they stream — every record is a single JSON object on its own line
    (JSON-Lines format, see https://jsonlines.org/), so even interrupted
    sessions leave a parseable partial log.

    **Record types** (line ``type`` field):
      - ``session_start``  — once per file (when first created)
      - ``session_resume``  — when an existing file is reopened
      - ``user``           — user prompt
      - ``agent``          — assistant text
      - ``tool_call``      — tool invocation
      - ``tool_result``    — tool result
      - ``error``          — runtime error
      - ``session_end``    — last line on close

    **Async design (queue + background writer):**

    ``__init__`` creates an ``asyncio.Queue`` and starts a background
    ``_writer`` task that consumes from the queue and writes JSONL
    lines to disk via ``aiofiles``.  Each ``log_*`` method is
    ``async`` and simply puts a JSON-serialised dict on the queue —
    it never blocks the event loop or the SSE stream.  ``close()``
    puts a sentinel on the queue and awaits the writer task to drain
    + finish.

    Usage::

        logger = ConversationLogger(session_id, user_message)
        await logger.log_user("帮我展开 RAG 节点")
        await logger.log_tool("kg_view_graph", {"domain": "AI 应用开发"})
        await logger.log_tool_result("kg_view_graph", "...")
        await logger.log_agent("已展开 5 个子节点...")
        await logger.close()
    """

    _SENTINEL = object()  # signals the writer to drain and exit
    _MAX_RESULT_CHARS = 2000  # truncate tool results to keep file size sane

    def __init__(self, session_id: str, initial_prompt: str = "") -> None:
        import asyncio

        # If the caller didn't pass a session_id (or passed a sentinel
        # like 'demo-1'), mint a fresh 16-char UUID.  Existing files
        # keep their name so we don't lose partial logs.
        self.session_id = session_id or generate_session_id()
        self.path = get_conversation_path(self.session_id)
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._started = False
        self._closed = False
        # Start the background writer immediately so the header is
        # written as soon as the event loop picks up the task.
        self._task = asyncio.create_task(self._writer())
        self._started = True
        if initial_prompt:
            self._enqueue(
                "user",
                {"content": initial_prompt},
            )

    def _enqueue(self, type_: str, payload: dict[str, Any]) -> None:
        """Build a JSONL record and put it on the queue."""
        import json

        record = {
            "type": type_,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "session_id": self.session_id,
            **payload,
        }
        self._queue.put_nowait(json.dumps(record, ensure_ascii=False) + "\n")

    async def _writer(self) -> None:
        """Background consumer — opens the file with ``aiofiles`` and
        writes queued JSONL lines until the sentinel is received.

        Resilient: if a write fails, the exception is logged but the
        writer keeps going so a transient disk error doesn't crash
        the agent stream.
        """
        import aiofiles
        import json as _json

        try:
            is_new = not self.path.exists()
            header_type = "session_start" if is_new else "session_resume"

            async with aiofiles.open(self.path, "a", encoding="utf-8") as f:
                header_line = (
                    _json.dumps(
                        {
                            "type": header_type,
                            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "session_id": self.session_id,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                await f.write(header_line)

                while True:
                    item = await self._queue.get()
                    if item is self._SENTINEL:
                        break
                    await f.write(item)
                await self._write_end_record(f)
        except Exception:
            logger.exception("ConversationLogger writer failed")

    async def _write_end_record(self, f) -> None:
        import json

        await f.write(
            json.dumps(
                {
                    "type": "session_end",
                    "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "session_id": self.session_id,
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    async def log_user(self, content: str) -> None:
        if not self._started or self._closed:
            return
        self._enqueue("user", {"content": content})

    async def log_agent(self, content: str) -> None:
        if not self._started or self._closed:
            return
        self._enqueue("agent", {"content": content})

    async def log_tool(self, name: str, args: dict) -> None:
        if not self._started or self._closed:
            return
        self._enqueue("tool_call", {"name": name, "args": args or {}})

    async def log_tool_result(self, name: str, result: str) -> None:
        if not self._started or self._closed:
            return
        display = (
            result
            if len(result) <= self._MAX_RESULT_CHARS
            else result[: self._MAX_RESULT_CHARS] + "\n... (truncated)"
        )
        self._enqueue("tool_result", {"name": name, "result": display})

    async def log_error(self, message: str) -> None:
        if not self._started or self._closed:
            return
        self._enqueue("error", {"message": message})

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._started = False
        await self._queue.put(self._SENTINEL)
        try:
            await self._task
        except Exception:
            pass


__all__ = [
    "ConversationLogger",
    "TMP_MAX_AGE_DAYS",
    "cleanup_tmp",
    "generate_session_id",
    "get_checkpointer",
    "get_conversation_path",
    "get_conversations_dir",
    "get_filesystem_backend",
    "get_memory_root",
    "get_tmp_dir",
    "get_workspace_agents_md_path",
    "reset_memory_subsystem",
]
