"""Memory search route — query conversation history outside the agent.

Public API for the frontend to search past session logs directly,
without going through the LLM agent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Query

from src.agent.memory import get_conversations_dir

router = APIRouter(prefix="/api", tags=["memory"])


def _list_sessions(since_days: int) -> list[Path]:
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


def _iter_jsonl_records(path: Path):
    """Yield decoded JSON objects from a .jsonl file (skipping bad lines)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _find_session_file(session_id: str, since_days: int = 365) -> Path | None:
    """Locate the JSONL file for ``session_id`` across date folders.

    Mirrors ``src.agent.tools.memory_tools._find_session_file`` — kept
    independent so the HTTP layer doesn't pull in LangChain tool deps.
    Walks ``conversations/{date}/*`` newest-first until it finds a hit.
    Returns ``None`` if the session doesn't exist.
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


def _record_to_search_text(rec: dict) -> str:
    """Flatten a JSONL record into a single searchable string."""
    parts: list[str] = []
    for key in ("content", "name", "result", "message"):
        v = rec.get(key)
        if isinstance(v, str):
            parts.append(v)
        elif v is not None:
            parts.append(json.dumps(v, ensure_ascii=False))
    return "\n".join(parts)


@router.get("/memory/sessions")
async def list_sessions(days: int = Query(14, ge=1, le=90)):
    """List available session files (date + session id)."""
    sessions: list[dict] = []
    for f in _list_sessions(days):
        sessions.append({
            "session": f.stem,
            "date": f.parent.name,
            "size": f.stat().st_size,
            "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/memory/search")
async def search_memory(
    query: str = Query(..., min_length=1, description="搜索关键词"),
    days: int = Query(7, ge=1, le=90),
    max_results: int = Query(10, ge=1, le=50),
):
    """Search conversation history logs for a keyword.

    Returns matching records with surrounding context, newest first.
    JSONL files are searched record-by-record rather than line-by-line.
    """
    files = _list_sessions(days)
    if not files:
        return {"query": query, "matches": [], "hint": "没有找到近期的会话记录"}

    context_records = 2  # records to render before/after the hit
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    all_matches: list[dict] = []

    for f in files:
        records = list(_iter_jsonl_records(f))
        if not records:
            continue
        # Build a one-line-per-record "haystack" view, plus the line index
        # for context snippets.
        haystacks = [_record_to_search_text(r) for r in records]
        for idx, hay in enumerate(haystacks):
            if pattern.search(hay):
                start = max(0, idx - context_records)
                end = min(len(records), idx + context_records + 1)
                snippet = "\n".join(
                    f"  [{records[i].get('ts', '')}] "
                    f"{records[i].get('type', '')}: "
                    f"{_record_to_search_text(records[i])[:200]}"
                    for i in range(start, end)
                )
                all_matches.append({
                    "line": idx + 1,
                    "match": hay[:300],
                    "context": snippet,
                    "session": f.stem,
                    "date": f.parent.name,
                    "type": records[idx].get("type", ""),
                })
                if len(all_matches) >= max_results:
                    break
        if len(all_matches) >= max_results:
            break

    return {
        "query": query,
        "searched_days": days,
        "total_matches": len(all_matches),
        "matches": all_matches,
    }


@router.get("/memory/recall")
async def recall_recent(lines: int = Query(60, ge=10, le=200)):
    """Return the tail of the most recent session file.

    Reads the most-recently-modified ``.jsonl`` and renders the last
    ``lines`` records (not raw lines) for human display.
    """
    files = _list_sessions(since_days=30)
    if not files:
        return {"content": "", "hint": "没有找到近期的会话记录"}

    latest = files[0]
    records = list(_iter_jsonl_records(latest))
    if not records:
        return {"content": "", "hint": f"无法读取 {latest.name}"}

    tail = records[-lines:] if len(records) > lines else records

    rendered: list[str] = []
    for rec in tail:
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
        else:
            rendered.append(f"[{ts}] {rtype}")
    content = "\n".join(rendered)

    return {
        "session": latest.stem,
        "date": latest.parent.name,
        "total_records": len(records),
        "shown_records": len(tail),
        "content": content,
    }


@router.get("/memory/session/{session_id}")
async def get_session(
    session_id: str,
    date: str | None = Query(
        None,
        description="会话日期 YYYY-MM-DD，传了直接定位 conversations/{date}/{id}.jsonl，不传则跨日期遍历。",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
):
    """Return the parsed JSONL records of a specific session.

    Used by the frontend to *load* a historical conversation into the
    chat panel (vs. just searching it).

    Fast path: when ``date`` is supplied we go straight to
    ``conversations/{date}/{session_id}.jsonl`` — no directory walk.
    Slow path: when ``date`` is missing we cross date folders newest-first
    so the caller only needs the id.

    Returns the raw parsed records list (not a flattened transcript)
    so the frontend can render user/agent/tool messages with its own
    tool-call collapsing UI.  ``session_start`` / ``session_end`` records
    are filtered out — they're bookkeeping, not conversation content.
    """
    if not session_id or not session_id.strip():
        return {"ok": False, "error": "session_id 不能为空"}

    path: Path | None = None
    if date:
        # Fast path: caller told us the date, skip the scan.
        root = get_conversations_dir()
        candidate = root / date / f"{session_id}.jsonl"
        if candidate.is_file():
            path = candidate

    if path is None:
        # Slow path / fallback: cross-date scan.  This also covers the
        # case where the caller passed a date but the file wasn't in
        # that folder (rare; e.g. the file was moved between days).
        path = _find_session_file(session_id)

    if path is None:
        return {
            "ok": False,
            "error": f"找不到 session_id={session_id} 的历史会话"
            + (f"（date={date}）" if date else ""),
            "hint": "session_id 是 16 位 hex，可在历史会话查询中查看",
        }

    records = list(_iter_jsonl_records(path))
    # Drop bookkeeping lines — they have no rendering value.
    payload = [
        r for r in records
        if r.get("type") not in ("session_start", "session_resume", "session_end")
    ]

    return {
        "ok": True,
        "session": session_id,
        "date": path.parent.name,
        "total_records": len(records),
        "records": payload,
    }


__all__ = ["router"]
