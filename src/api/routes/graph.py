"""Graph CRUD routes — domains list, decorated graph view, node CRUD.

API contract (matches the Vue frontend's ``api/index.ts``):
  GET    /api/domains                 → {domains, details}
  GET    /api/graph/{domain}          → decorated graph (levels/tiers/L0 root)
  POST   /api/nodes                   → add node
  PATCH  /api/nodes/{domain}?name=... → rename / relink
  DELETE /api/nodes/{domain}?name=... → remove + clean references

``name`` is a query parameter (not a path segment) so node names
containing ``/`` (e.g. GitHub ``owner/repo``) are accepted verbatim.

Activity timeline
-----------------

Every mutating endpoint (``POST`` / ``PATCH`` / ``DELETE``) emits an
activity event on the bus so the JSONL timeline log captures it.
This is the **only** way node CRUD shows up in the activity timeline
(per the single-track decision: we no longer scan the graph to
back-fill events).

All graph IO goes through ``GraphService`` → ``GraphRepository`` — the
route layer never touches ``aiofiles`` / ``atomic_write_text`` /
``graph_lock`` directly.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.agent import dependencies as agent_deps
from src.domain.graph.decorator import decorate_graph
from src.observability.activity_bus import ActivityKind, get_activity_bus

router = APIRouter(prefix="/api", tags=["graph"])


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class AddNodeReq(BaseModel):
    domain: str
    name: str
    parent: str = ""
    links: list[str] = []


class UpdateNodeReq(BaseModel):
    newName: Optional[str] = None
    newLinks: Optional[list[str]] = None


# ------------------------------------------------------------------
# Domains
# ------------------------------------------------------------------


@router.get("/domains")
async def list_domains():
    repo = agent_deps.get_graph_repo()
    names = await repo.list_domains()
    details: list[dict] = []
    for name in names:
        try:
            g = await repo.read_graph(name)
            details.append({"name": name, "node_count": len(g.nodes)})
        except Exception:
            details.append({"name": name, "node_count": 0})
    return {
        "domains": [d["name"] for d in details],
        "details": details,
    }


# ------------------------------------------------------------------
# Graph view
# ------------------------------------------------------------------


@router.get("/graph/{domain}")
async def get_graph(domain: str, decorated: bool = True):
    repo = agent_deps.get_graph_repo()
    raw = await repo.read_raw(domain)
    if not raw.get("nodes") and not (repo._kb_root / domain / "knowledge_graph.json").exists():
        raise HTTPException(404, f"领域「{domain}」不存在")
    if decorated:
        return decorate_graph(raw)
    return raw


@router.post("/graph/{domain}/fix-links")
async def fix_links_route(domain: str):
    """Normalise the graph to forward-only tree links."""
    from src.application.graph_service import GraphService

    repo = agent_deps.get_graph_repo()
    svc = GraphService(repo)
    removed, scanned = await svc.fix_links(domain)

    # Activity timeline — manual UI click on "修复孤链" / "fix-links".
    await get_activity_bus().emit(
        ActivityKind.FIX_LINKS,
        domain=domain,
        node="",
        title=f"修复了孤链（扫描 {scanned} 节点，清理 {removed} 条）",
        source="manual",
        ref=f"domain:{domain}",
        extra={"removed": removed, "scanned": scanned},
    )
    return {"removed": removed, "scanned": scanned}


@router.get("/graph/{domain}/export-zip")
async def export_zip(domain: str):
    """Stream the whole ``<kb_root>/<domain>`` folder as a ``<domain>.zip``.

    The archive preserves the on-disk layout (``knowledge_graph.json``,
    ``notes/``, ``activity/``, ``build.log``, …) so the recipient gets a
    self-contained snapshot of the domain — no round-tripping through JSON.

    Streamed via ``StreamingResponse`` so we never materialise the full
    archive in memory; works for arbitrarily large knowledge bases.
    """
    repo = agent_deps.get_graph_repo()
    domain_dir = repo._domain_dir(domain)
    if not domain_dir.exists() or not domain_dir.is_dir():
        raise HTTPException(404, f"领域「{domain}」不存在")

    buf = io.BytesIO()
    base = domain_dir.resolve()
    # ZipFile lets us stream entries straight into the buffer; we wrap the
    # final position back to 0 so the StreamingResponse reads from the start.
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(base):
            root_path = Path(root)
            rel_root = root_path.relative_to(base)
            for name in files:
                full = root_path / name
                arcname = (
                    str(rel_root / name) if str(rel_root) != "." else name
                )
                # Prefix every entry with the domain folder name so the
                # zip root matches the source directory layout exactly.
                zf.write(full, arcname=str(Path(domain) / arcname))
    buf.seek(0)

    # Build the Content-Disposition header.
    #
    # Starlette serialises every header value through ``.encode("latin-1")``
    # (see starlette/responses.py:init_headers), so the plain ``filename=``
    # attribute MUST be latin-1-clean.  When the domain contains CJK (or any
    # non-Latin-1 character) we have to drop the plain ``filename=`` and rely
    # solely on RFC 5987 ``filename*=UTF-8''…`` — modern browsers honour the
    # ``filename*`` attribute on its own.  The URL-quoted ``safe_name`` is
    # always ASCII, so the final header string is guaranteed latin-1-safe.
    safe_name = quote(f"{domain}.zip")
    if domain.isascii():
        disposition = f"attachment; filename={domain}.zip; filename*=UTF-8''{safe_name}"
    else:
        disposition = f"attachment; filename*=UTF-8''{safe_name}"
    headers = {"Content-Disposition": disposition}

    # Activity timeline — record the export so the timeline shows the
    # user actually pulled a snapshot of this domain today.  Emit
    # BEFORE building the StreamingResponse (cheap), so a slow client
    # download does not delay the log write.
    file_count = sum(len(files) for _, _, files in os.walk(base))
    await get_activity_bus().emit(
        ActivityKind.GRAPH_EXPORTED,
        domain=domain,
        node="",
        title=f"导出了领域「{domain}」的快照（{file_count} 个文件）",
        source="manual",
        ref=f"domain:{domain}",
        extra={"file_count": file_count, "filename": f"{domain}.zip"},
    )

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers=headers,
    )


# ------------------------------------------------------------------
# Node CRUD
# ------------------------------------------------------------------


@router.post("/nodes")
async def add_node(req: AddNodeReq):
    """Add a node. If ``parent`` is given, auto-appends to parent's links."""
    repo = agent_deps.get_graph_repo()

    name = req.name.strip()
    if not name:
        raise HTTPException(400, "节点名不能为空")

    # Check domain exists
    graph = await repo.read_graph(req.domain)
    if not (repo._kb_root / req.domain / "knowledge_graph.json").exists():
        raise HTTPException(404, f"领域「{req.domain}」不存在")

    if any(n.name == name for n in graph.nodes):
        raise HTTPException(409, f"节点「{name}」已存在")

    existing = set(graph.node_names())
    existing.add(graph.domain)

    if req.parent and req.parent not in existing:
        raise HTTPException(400, f"父节点「{req.parent}」不存在")

    invalid = [l for l in req.links if l not in existing and l != req.parent]
    if invalid:
        raise HTTPException(400, f"links 引用了不存在的节点：{invalid}")

    links = [l for l in req.links if l != req.parent]
    await repo.add_node(
        req.domain, name,
        links=links,
        parent=req.parent or None,
    )

    # Ensure note directory exists
    note_repo = agent_deps.get_note_repo()
    await note_repo.ensure_node_dir(req.domain, name)

    # Activity timeline — emit only after the graph is durably written,
    # so the JSONL log never references a node that doesn't exist.
    await get_activity_bus().emit(
        ActivityKind.NODE_CREATED,
        domain=req.domain,
        node=name,
        title=f"新建了节点「{name}」",
        source="manual",
        ref=f"node:{name}",
        extra={"parent": req.parent} if req.parent else None,
    )

    return {"ok": True, "message": f"已加节点「{name}」到「{req.parent or '根'}」下"}


@router.patch("/nodes/{domain}")
async def update_node(domain: str, name: str = Query(...), req: UpdateNodeReq = ...):
    """Rename a node and/or update its links. Patches all incoming references.

    ``name`` is a query parameter so names with ``/`` (e.g. ``owner/repo``)
    are accepted verbatim.
    """
    repo = agent_deps.get_graph_repo()

    graph = await repo.read_graph(domain)
    target = graph.find_node(name)
    if target is None:
        raise HTTPException(404, f"节点「{name}」不存在")

    final_name = req.newName.strip() if req.newName else name
    if not final_name:
        raise HTTPException(400, "节点名不能为空")

    if final_name != name:
        if any(n.name == final_name for n in graph.nodes):
            raise HTTPException(409, f"节点「{final_name}」已存在")
        if name == graph.domain:
            raise HTTPException(400, "不能重命名领域根节点（请直接改 domain 字段）")

    if req.newLinks is not None:
        existing = set(graph.node_names())
        existing.add(graph.domain)
        invalid = [l for l in req.newLinks if l not in existing]
        if invalid:
            raise HTTPException(400, f"links 引用了不存在的节点：{invalid}")

    old_name = target.name
    await repo.update_node(
        domain, old_name,
        new_name=final_name if final_name != old_name else "",
        new_links=req.newLinks,
    )

    # Migrate on-disk assets (notes, resources, plans, uploads).
    if old_name != final_name:
        from src.application.node_migration import migrate_node_assets

        await migrate_node_assets(repo.kb_root, domain, old_name, final_name)

    # Activity timeline
    bus = get_activity_bus()
    if old_name != final_name:
        await bus.emit(
            ActivityKind.NODE_RENAMED,
            domain=domain,
            node=final_name,
            title=f"重命名节点「{old_name}」→「{final_name}」",
            source="manual",
            ref=f"node:{final_name}",
            extra={"old_name": old_name, "new_name": final_name},
        )
    if req.newLinks is not None:
        await bus.emit(
            ActivityKind.NODE_RELINKED,
            domain=domain,
            node=final_name,
            title=f"更新了节点「{final_name}」的链接",
            source="manual",
            ref=f"node:{final_name}",
            extra={"new_links": req.newLinks},
        )

    return {"ok": True, "message": f"已更新节点「{old_name}」→「{final_name}」"}


@router.delete("/nodes/{domain}")
async def delete_node(domain: str, name: str = Query(...)):
    """Delete a node and remove all incoming references.

    ``name`` is a query parameter so names with ``/`` are accepted verbatim.
    """
    repo = agent_deps.get_graph_repo()

    graph = await repo.read_graph(domain)
    if name == graph.domain:
        raise HTTPException(400, "不能删除领域根节点")

    if graph.find_node(name) is None:
        raise HTTPException(404, f"节点「{name}」不存在")

    await repo.delete_node(domain, name)

    # Clean up on-disk assets (notes, resources, plans, uploads).
    from src.application.node_migration import delete_node_assets

    await delete_node_assets(repo.kb_root, domain, name)

    # Activity timeline
    await get_activity_bus().emit(
        ActivityKind.NODE_DELETED,
        domain=domain,
        node=name,
        title=f"删除了节点「{name}」",
        source="manual",
        ref=f"node:{name}",
    )

    return {"ok": True, "message": f"已删除节点「{name}」"}


__all__ = ["router"]
