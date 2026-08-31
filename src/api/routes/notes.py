"""Note CRUD + generation routes.

API contract:
  GET   /api/notes/{domain}?node=...            → {content, created, needs_generation?}
  PUT   /api/notes/{domain}?node=...            → save note
  POST  /api/notes/{domain}/generate?node=...   → LLM-generate note
  GET   /api/notes-index/{domain}?node=...      → child note summaries

``node`` is a query parameter (not a path segment) so node names
containing ``/`` (e.g. GitHub ``owner/repo``) are accepted verbatim.

All file IO goes through ``NoteRepository`` / ``NoteService`` — the
route layer never touches ``aiofiles`` / ``atomic_write_text`` /
``Path(get_kb_root())`` directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.agent import dependencies as agent_deps
from src.domain.graph.decorator import decorate_graph, node_tier
from src.domain.note.generator import PROMPT_VERSION, NoteGenerator
from src.domain.note.text_utils import (
    count_words,
    extract_first_definition_summary,
    extract_source,
)
from src.observability.activity_bus import ActivityKind, get_activity_bus

router = APIRouter(prefix="/api", tags=["notes"])


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------


class SaveNoteReq(BaseModel):
    content: str


# ------------------------------------------------------------------
# Note template
# ------------------------------------------------------------------

_NOTE_TPL = """# {node_name}

<!--
所属领域：{domain}
层级路径：{hierarchy_path}
父节点：{parent}
同级节点：{siblings}
生成方式：api（{version_label}）
生成时间：{today}
-->

{body}
"""


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _generate_note_content(domain: str, node_name: str) -> tuple[str, str]:
    """Generate note.md body via LLM. Returns (content, version_label)."""
    from src.domain.graph.decorator import gather_graph_context

    repo = agent_deps.get_graph_repo()
    try:
        graph = await repo.read_graph(domain)
        raw = graph.model_dump()
    except Exception:
        raw = {"domain": domain, "nodes": []}

    graph_ctx = gather_graph_context(raw, domain, node_name)
    hierarchy_path = graph_ctx.get("hierarchy_path", f"{domain} - {node_name}")
    parents = graph_ctx.get("parents", [])
    siblings = graph_ctx.get("siblings", [])
    parent_str = "、".join(parents) if parents else "（无 / 顶层节点）"
    siblings_str = "、".join(siblings[:10]) if siblings else "（无）"

    llm = agent_deps.get_note_llm()
    generator = NoteGenerator(llm)
    try:
        body_text = await generator.generate(
            node_name,
            domain,
            graph_ctx=graph_ctx,
        )
        version_label = f"api-{PROMPT_VERSION}"
    except Exception as e:
        body_text = (
            f"## 定义\n\n> （生成失败：{type(e).__name__}: {e}，请手动补全）\n\n"
            f"## 重要概念与知识点\n\n> （待补全）\n\n"
            f"## 如何开启快速学习\n\n> （待补全）"
        )
        version_label = f"ERROR: {type(e).__name__}"

    body = _NOTE_TPL.format(
        node_name=node_name,
        domain=domain,
        hierarchy_path=hierarchy_path,
        parent=parent_str,
        siblings=siblings_str,
        version_label=version_label,
        today=datetime.now().strftime("%Y-%m-%d"),
        body=body_text.strip(),
    )
    return body, f"version={version_label}"


def _note_summary(domain: str, node_name: str, decorated: dict) -> dict[str, Any]:
    note_repo = agent_deps.get_note_repo()
    note_path = note_repo.note_path(domain, node_name)
    if not note_path.exists():
        return {
            "name": node_name,
            "tier": node_tier(decorated, node_name),
            "has_note": False,
            "summary": "",
            "words": 0,
            "mtime": None,
            "source": None,
        }
    try:
        text = note_path.read_text(encoding="utf-8")
    except Exception as e:
        return {
            "name": node_name,
            "tier": node_tier(decorated, node_name),
            "has_note": True,
            "summary": f"（读取失败：{e}）",
            "words": 0,
            "mtime": None,
            "source": None,
        }
    try:
        mtime = datetime.fromtimestamp(
            note_path.stat().st_mtime
        ).isoformat(timespec="seconds")
    except Exception:
        mtime = None
    return {
        "name": node_name,
        "tier": node_tier(decorated, node_name),
        "has_note": True,
        "summary": extract_first_definition_summary(text),
        "words": count_words(text),
        "mtime": mtime,
        "source": extract_source(text),
    }


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------


@router.get("/notes/{domain}")
async def get_note(domain: str, node: str = Query(...)):
    """Get note.md content. Returns ``needs_generation`` when missing
    (no synchronous LLM call to avoid blocking).

    ``node`` is a query parameter (not a path segment) so node names
    containing ``/`` (e.g. GitHub ``owner/repo``) are accepted verbatim.
    """
    note_repo = agent_deps.get_note_repo()
    await note_repo.ensure_node_dir(domain, node)
    content = await note_repo.read_note(domain, node)
    if content is None:
        return {"content": "", "created": False, "needs_generation": True}
    return {"content": content, "created": False}


@router.put("/notes/{domain}")
async def save_note(domain: str, node: str = Query(...), req: SaveNoteReq = ...):
    """Save note.md content."""
    note_repo = agent_deps.get_note_repo()
    await note_repo.ensure_node_dir(domain, node)
    await note_repo.write_note(domain, node, req.content)

    # Activity timeline — manual save via UI editor.
    await get_activity_bus().emit(
        ActivityKind.NOTE_UPDATED,
        domain=domain,
        node=node,
        title=f"更新了笔记「{node}」",
        source="manual",
        ref=f"note.md#{node}",
    )

    return {"ok": True, "message": "已保存 note.md"}


@router.post("/notes/{domain}/generate")
async def generate_note(domain: str, node: str = Query(...)):
    """Generate note.md via LLM (manual trigger)."""
    note_repo = agent_deps.get_note_repo()
    await note_repo.ensure_node_dir(domain, node)
    content, source_info = await _generate_note_content(domain, node)
    await note_repo.write_note(domain, node, content)

    # Activity timeline — LLM-generated via the UI button.
    await get_activity_bus().emit(
        ActivityKind.NOTE_GENERATED,
        domain=domain,
        node=node,
        title=f"生成了笔记「{node}」",
        source="agent",
        ref=f"note.md#{node}",
        extra={"prompt_version": source_info},
    )

    return {"content": content, "created": True, "source": source_info}


@router.get("/notes-index/{domain}")
async def get_notes_index(domain: str, node: str = Query(...)):
    """Enumerate child (and self) note status summaries for a node.

    ``node`` is a query parameter so names with ``/`` are matched verbatim.
    """
    repo = agent_deps.get_graph_repo()
    try:
        raw = await repo.read_raw(domain)
        decorated = decorate_graph(raw)
        node_meta = None
        for n in decorated.get("nodes", []):
            if n.get("name") == node:
                node_meta = n
                break
    except Exception:
        decorated = {"nodes": []}
        node_meta = None

    if not node_meta:
        note_repo = agent_deps.get_note_repo()
        nd = note_repo.node_dir(domain, node)
        if not nd.exists():
            raise HTTPException(404, f"节点「{node}」不存在（图谱与笔记目录均无）")
        tier = "leaf"
        child_names: list[str] = []
        is_leaf = True
    else:
        tier = node_meta.get("tier", "leaf")
        child_names = list(node_meta.get("links", []) or [])
        is_leaf = len(child_names) == 0 or tier == "leaf"

    return {
        "node": node,
        "tier": tier,
        "is_leaf": is_leaf,
        "children": [
            _note_summary(domain, c, decorated) for c in child_names
        ],
        "self": None if is_leaf else _note_summary(domain, node, decorated),
    }


__all__ = ["router"]
