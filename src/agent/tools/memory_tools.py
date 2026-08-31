"""Memory tools — search and recall conversation history.

Provides ``kg_search_memory`` for querying past session logs, and
``kg_recall_recent`` for quickly viewing the most recent conversation.

Session files are stored as JSON-Lines under
``conversations/{YYYY-MM-DD}/{uuid16}.jsonl``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from langchain_core.tools import tool

from src.agent.memory import get_conversations_dir
from src.observability.logged_tool import logged_tool

_MAX_CONTEXT_RECORDS = 2  # records before/after each match


def _list_session_files(since_days: int) -> list[Path]:
    """Return session .jsonl files from the last N days, newest first."""
    root = get_conversations_dir()
    cutoff = datetime.now() - timedelta(days=since_days)
    files: list[Path] = []
    if not root.exists():
        return files
    for date_dir in sorted(root.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        try:
            dt = datetime.strptime(date_dir.name, "%Y-%m-%d")
            if dt < cutoff:
                continue
        except ValueError:
            continue
        for f in sorted(date_dir.glob("*.jsonl"), reverse=True):
            if f.is_file():
                files.append(f)
    return files


def _iter_records(path: Path) -> list[dict]:
    """Decode a JSONL file into a list of records (skipping bad lines)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _record_text(rec: dict) -> str:
    """Flatten a record to its searchable text (content/result/message)."""
    parts: list[str] = []
    for k in ("content", "name", "result", "message"):
        v = rec.get(k)
        if isinstance(v, str):
            parts.append(v)
        elif v is not None:
            parts.append(json.dumps(v, ensure_ascii=False))
    return "\n".join(parts)


def _find_session_file(session_id: str, since_days: int = 365) -> Path | None:
    """Locate the JSONL file for ``session_id`` by scanning date folders.

    Conversation logs live at
    ``conversations/{YYYY-MM-DD}/{session_id}.jsonl`` — we walk the
    date folders newest-first (bounded by ``since_days``) until we
    find a hit.  Returns ``None`` if the session does not exist (or
    the conversations directory hasn't been created yet).
    """
    if not session_id or not session_id.strip():
        return None
    root = get_conversations_dir()
    if not root.exists():
        return None
    cutoff = datetime.now() - timedelta(days=since_days)
    for date_dir in sorted(root.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        try:
            dt = datetime.strptime(date_dir.name, "%Y-%m-%d")
            if dt < cutoff:
                continue
        except ValueError:
            continue
        candidate = date_dir / f"{session_id}.jsonl"
        if candidate.is_file():
            return candidate
    return None


def _render_records(records: list[dict]) -> str:
    """Render parsed JSONL records into a human-readable transcript.

    Mirrors :func:`kg_recall_recent`'s rendering so output format is
    consistent across the two recall tools.
    """
    rendered: list[str] = []
    for rec in records:
        ts = rec.get("ts", "")
        rtype = rec.get("type", "?")
        if rtype in ("user", "agent"):
            rendered.append(f"[{ts}] {rtype}: {rec.get('content', '')}")
        elif rtype == "tool_call":
            rendered.append(
                f"[{ts}] tool_call: {rec.get('name', '')} "
                f"args={json.dumps(rec.get('args', {}), ensure_ascii=False)}"
            )
        elif rtype == "tool_result":
            rendered.append(
                f"[{ts}] tool_result: {rec.get('name', '')} "
                f"-> {(rec.get('result') or '')[:300]}"
            )
        elif rtype == "error":
            rendered.append(f"[{ts}] error: {rec.get('message', '')}")
        elif rtype in ("session_start", "session_resume", "session_end"):
            continue
        else:
            rendered.append(f"[{ts}] {rtype}")
    return "\n".join(rendered)


def _search_file(
    path: Path, query: str, context: int = _MAX_CONTEXT_RECORDS
) -> list[dict]:
    """Search a single JSONL file for ``query``, returning matches with
    surrounding record context."""
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    records = _iter_records(path)
    if not records:
        return []
    matches: list[dict] = []
    for idx, rec in enumerate(records):
        hay = _record_text(rec)
        if pattern.search(hay):
            start = max(0, idx - context)
            end = min(len(records), idx + context + 1)
            snippet = "\n".join(
                f"  [{records[i].get('ts', '')}] "
                f"{records[i].get('type', '')}: "
                f"{_record_text(records[i])[:200]}"
                for i in range(start, end)
            )
            matches.append({
                "line": idx + 1,
                "match": hay[:300],
                "context": snippet,
                "type": rec.get("type", ""),
            })
    return matches


@tool
@logged_tool
async def kg_search_memory(
    query: str,
    days: int = 7,
    max_results: int = 10,
) -> str:
    """搜索历史对话记忆，在过往会话记录中查找相关内容。

    参数：
      query       — 搜索关键词（必填，支持中英文）
      days        — 搜索最近 N 天的记录（默认 7 天）
      max_results — 最多返回多少条结果（默认 10）

    返回匹配的对话片段及其上下文，按时间倒序排列。
    适用于：回忆之前讨论过的话题、查找用户提过的偏好、定位历史操作。
    """
    files = _list_session_files(days)
    if not files:
        return json.dumps(
            {"query": query, "matches": [], "hint": "没有找到近期的会话记录"},
            ensure_ascii=False,
        )

    all_matches: list[dict] = []
    for f in files:
        file_matches = _search_file(f, query, _MAX_CONTEXT_RECORDS)
        for m in file_matches:
            m["session"] = f.stem
            m["date"] = f.parent.name
            m["file"] = str(f.relative_to(get_conversations_dir()))
            all_matches.append(m)
        if len(all_matches) >= max_results:
            break

    all_matches = all_matches[:max_results]
    return json.dumps(
        {
            "query": query,
            "searched_days": days,
            "total_matches": len(all_matches),
            "matches": all_matches,
        },
        ensure_ascii=False,
        indent=2,
    )


@tool
@logged_tool
async def kg_recall_recent(lines: int = 60) -> str:
    """回顾最近的对话历史，默认返回最新会话文件的最后 60 条记录。

    参数：
      lines — 返回的记录条数（默认 60，最大 200）

    返回最新会话文件的尾部内容，用于快速回忆"刚才在聊什么"。
    """
    lines = min(max(lines, 10), 200)
    files = _list_session_files(since_days=30)
    if not files:
        return json.dumps(
            {"content": "", "hint": "没有找到近期的会话记录"},
            ensure_ascii=False,
        )

    latest = files[0]
    records = _iter_records(latest)
    if not records:
        return json.dumps(
            {"content": "", "hint": f"无法读取 {latest.name}"},
            ensure_ascii=False,
        )

    tail = records[-lines:] if len(records) > lines else records

    content = _render_records(tail)

    return json.dumps(
        {
            "session": latest.stem,
            "date": latest.parent.name,
            "total_records": len(records),
            "shown_records": len(tail),
            "content": content,
        },
        ensure_ascii=False,
        indent=2,
    )


@tool
@logged_tool
async def kg_recall_session(
    session_id: str,
    date: str | None = None,
    max_chars: int = 20000,
) -> str:
    """按 session_id 加载指定历史会话的完整对话记录。

    参数：
      session_id — 会话 ID（16 位 hex，例如 d7e4cfebba8f4fc8）
      date       — 可选，YYYY-MM-DD；传了直接定位 conversations/{date}/{id}.jsonl，
                   不传则跨日期遍历
      max_chars  — 返回内容的最大字符数（默认 20000，超过会截断并标记）

    返回该会话的完整记录（user / agent / tool_call / tool_result /
    error），适合"切到历史会话后"自动恢复上下文。
    """
    if not session_id or not session_id.strip():
        return json.dumps(
            {"ok": False, "error": "session_id 不能为空"},
            ensure_ascii=False,
        )

    path: Path | None = None
    if date:
        import re as _re

        if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return json.dumps(
                {
                    "ok": False,
                    "error": f"date 参数格式错误（应是 YYYY-MM-DD，got {date!r}）",
                },
                ensure_ascii=False,
            )
        candidate = get_conversations_dir() / date / f"{session_id}.jsonl"
        if candidate.is_file():
            path = candidate

    if path is None:
        path = _find_session_file(session_id)

    if path is None:
        return json.dumps(
            {
                "ok": False,
                "error": f"找不到 session_id={session_id} 的历史会话"
                + (f"（date={date}）" if date else ""),
                "hint": "session_id 是 16 位 hex，可在历史会话查询中查看",
            },
            ensure_ascii=False,
        )

    records = _iter_records(path)
    if not records:
        return json.dumps(
            {"ok": False, "session": session_id, "error": f"无法读取 {path.name}"},
            ensure_ascii=False,
        )

    content = _render_records(records)
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars] + "\n... (truncated)"

    return json.dumps(
        {
            "ok": True,
            "session": session_id,
            "date": path.parent.name,
            "total_records": len(records),
            "shown_chars": len(content),
            "truncated": truncated,
            "content": content,
        },
        ensure_ascii=False,
        indent=2,
    )


__all__ = ["kg_search_memory", "kg_recall_recent", "kg_recall_session"]
