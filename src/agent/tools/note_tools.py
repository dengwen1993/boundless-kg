"""Note-generation + reading tools.

Activity timeline
-----------------

``kg_generate_note`` emits ``note_generated`` (LLM-driven creation),
``note_rebuilt`` (force-regenerate overwrites an existing note),
or ``note_updated`` (created + then updated).
``kg_read_note`` / ``kg_list_notes`` are read-only and do not emit.
"""

from __future__ import annotations

from langchain_core.tools import tool

from src.agent.dependencies import get_note_repo, get_note_service
from src.observability.activity_bus import ActivityKind, get_activity_bus
from src.observability.logged_tool import logged_tool


@tool
@logged_tool
async def kg_generate_note(domain: str, node_name: str, force: bool = False) -> str:
    """为指定节点生成 Markdown 笔记；已存在则复用（除非 force=True）。"""
    svc = get_note_service()
    res = await svc.get_or_generate(domain, node_name, force=force)

    # Activity timeline — only emit on the LLM write path (created or
    # force-regenerated).  A cached read returns ``created=False`` and
    # is intentionally silent.
    if res.created or force:
        kind = (
            ActivityKind.NOTE_REBUILT
            if force and not res.created
            else ActivityKind.NOTE_GENERATED
        )
        title_action = "重建了" if force and not res.created else "生成了"
        await get_activity_bus().emit(
            kind,
            domain=domain,
            node=node_name,
            title=f"{title_action}笔记「{node_name}」",
            source="agent",
            ref=f"note.md#{node_name}",
            extra={"force": force, "created": res.created},
        )

    status = "新建" if res.created else "复用"
    n_chars = len(res.content)
    return f"✅ {status}笔记（{domain} / {node_name}, {n_chars} 字）：\n\n{res.content}"


@tool
async def kg_read_note(domain: str, node_name: str) -> str:
    """读取节点已有的笔记完整内容；不存在则返回提示。不会触发 LLM 生成。"""
    repo = get_note_repo()
    content = await repo.read_note(domain, node_name)
    if content is None:
        return f"📝 节点 {node_name!r}（{domain}）暂无笔记，可用 kg_generate_note 生成。"
    n_chars = len(content)
    return f"📝 笔记已存在（{domain} / {node_name}, {n_chars} 字）：\n\n{content}"


@tool
async def kg_list_notes(domain: str) -> str:
    """列出某领域下所有已有笔记的节点名。"""
    repo = get_note_repo()
    names = await repo.list_notes(domain)
    import json
    return json.dumps({"domain": domain, "notes": names, "count": len(names)}, ensure_ascii=False)


__all__ = ["kg_generate_note", "kg_read_note", "kg_list_notes"]