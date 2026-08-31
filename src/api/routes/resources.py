"""Resource CRUD routes — web resources + file uploads per node.

API contract:
  GET    /api/resources/{domain}?node=...                                 → {web_resources, user_uploads, study_materials}
  POST   /api/resources/{domain}/web?node=...                             → add web resource
  DELETE /api/resources/{domain}/web?node=...&url=...                     → delete web resource
  PUT    /api/resources/{domain}/web?node=...&original_url=...            → edit web resource
  POST   /api/resources/{domain}/upload?node=...                          → upload file (multipart)
  DELETE /api/resources/{domain}/upload/{filename}?node=...               → delete upload
  PUT    /api/resources/{domain}/upload/{filename}?node=...               → edit upload metadata
  GET    /api/resources/{domain}/download/{filename}?node=...             → download user-uploaded file
  GET    /api/resources/{domain}/study-materials?node=...                 → list study-materials (knowledge-digest outputs)
  GET    /api/resources/{domain}/study-materials?node=...&path=...         → list a sub-directory (drill-down navigation)
  GET    /api/resources/{domain}/study-materials/{filename:path}?node=... → download/preview a study-material file (incl. nested)

``node`` is a query parameter (not a path segment) so node names
containing ``/`` (e.g. GitHub ``owner/repo``) are accepted verbatim.

Activity timeline
-----------------

Adding a web resource or uploading a file emits an event on the bus
so the timeline shows 「搜索资料」 / 「整理文件」 activity.  Edits and
deletes intentionally do NOT emit — they are noise and would dilute
the timeline.

All file IO goes through ``ResourceRepository`` / ``NoteRepository`` —
the route layer never touches ``aiofiles`` / ``atomic_write_text`` /
``graph_lock`` directly.

study_materials/
-----------------

Files here are produced by the ``knowledge-digest`` skill (PDF / PPTX /
HTML / Mermaid / PNG).  They are READ-ONLY from the user's perspective
— managed by the agent, surfaced alongside user uploads so the
学习资料 dialog can preview them.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.agent import dependencies as agent_deps
from src.observability.activity_bus import ActivityKind, get_activity_bus

router = APIRouter(prefix="/api", tags=["resources"])


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------


class AddWebResourceReq(BaseModel):
    title: str = ""
    url: str
    summary: str = ""
    category: str = "网页"


class EditWebResourceReq(BaseModel):
    title: str = ""
    url: str
    summary: str = ""
    category: str = "网页"


class EditUploadReq(BaseModel):
    category: str = "其他"
    note: str = ""


# ------------------------------------------------------------------
# Routes — web resources
# ------------------------------------------------------------------


@router.get("/resources/{domain}")
async def get_resources(domain: str, node: str = Query(...)):
    """Get all resources (web + uploads + study materials) for a node.

    ``node`` is a query parameter so names with ``/`` are matched verbatim.

    ``study_materials`` is scanned live from the filesystem (read-only
    catalog of knowledge-digest outputs).  Reading is best-effort:
    if the directory is missing / a stat fails, an empty list is
    returned instead of 500'ing the dialog.
    """
    repo = agent_deps.get_resource_repo()
    note_repo = agent_deps.get_note_repo()
    web_idx = repo.web_index_path(domain, node)
    up_idx = repo.uploads_index_path(domain, node)
    try:
        study_materials = await note_repo.list_study_materials(domain, node)
    except Exception:
        study_materials = []
    return {
        "web_resources": await repo.read_json_index(web_idx),
        "user_uploads": await repo.read_json_index(up_idx),
        "study_materials": study_materials,
    }


@router.post("/resources/{domain}/web")
async def add_web_resource(
    domain: str, node: str = Query(...), req: AddWebResourceReq = ...
):
    """Add a web resource (URL) to the node's index."""
    note_repo = agent_deps.get_note_repo()
    await note_repo.ensure_node_dir(domain, node)
    repo = agent_deps.get_resource_repo()
    idx_path = repo.web_index_path(domain, node)
    items = await repo.read_json_index(idx_path)

    if any(it.get("url") == req.url for it in items):
        raise HTTPException(409, f"URL 已存在：{req.url}")

    item: dict[str, Any] = {
        "title": req.title or req.url,
        "url": req.url,
        "summary": req.summary,
        "category": req.category,
        "added_at": datetime.now().isoformat(timespec="seconds"),
    }
    items.append(item)
    await repo.write_json_index(idx_path, items)

    # Activity timeline
    await get_activity_bus().emit(
        ActivityKind.WEB_RESOURCE_ADDED,
        domain=domain,
        node=node,
        title=f"添加了资料「{item['title']}」",
        source="manual",
        ref=f"web_resources#{item['url']}",
        extra={"url": item["url"], "category": item["category"]},
    )

    return {"ok": True, "item": item, "total": len(items)}


@router.delete("/resources/{domain}/web")
async def delete_web_resource(
    domain: str, node: str = Query(...), url: str = Query(...)
):
    """Delete a web resource by URL."""
    repo = agent_deps.get_resource_repo()
    idx_path = repo.web_index_path(domain, node)
    items = await repo.read_json_index(idx_path)
    new_items = [it for it in items if it.get("url") != url]
    if len(new_items) == len(items):
        raise HTTPException(404, f"未找到 URL：{url}")
    await repo.write_json_index(idx_path, new_items)
    return {"ok": True, "total": len(new_items)}


@router.put("/resources/{domain}/web")
async def edit_web_resource(
    domain: str,
    node: str = Query(...),
    original_url: str = Query(...),
    req: EditWebResourceReq = ...,
):
    """Edit a web resource (located by ``original_url``)."""
    repo = agent_deps.get_resource_repo()
    idx_path = repo.web_index_path(domain, node)
    items = await repo.read_json_index(idx_path)
    found = False
    for it in items:
        if it.get("url") == original_url:
            it["title"] = req.title or req.url
            it["url"] = req.url
            it["summary"] = req.summary
            it["category"] = req.category
            found = True
            break
    if not found:
        raise HTTPException(404, f"未找到 URL：{original_url}")
    await repo.write_json_index(idx_path, items)
    return {"ok": True, "total": len(items)}


# ------------------------------------------------------------------
# Routes — file uploads
# ------------------------------------------------------------------


@router.post("/resources/{domain}/upload")
async def upload_file(
    domain: str,
    node: str = Query(...),
    file: UploadFile = File(...),
    category: str = Form("其他"),
    note: str = Form(""),
):
    """Upload a file to the node's ``user_uploads/`` directory."""
    note_repo = agent_deps.get_note_repo()
    await note_repo.ensure_node_dir(domain, node)
    repo = agent_deps.get_resource_repo()

    content = await file.read()
    target = await repo.save_upload(domain, node, file.filename, content)

    idx_path = repo.uploads_index_path(domain, node)
    items = await repo.read_json_index(idx_path)
    item: dict[str, Any] = {
        "file": target.name,
        "category": category,
        "note": note,
        "moved_at": datetime.now().isoformat(timespec="seconds"),
        "original_source": file.filename,
        "size": len(content),
    }
    items.append(item)
    await repo.write_json_index(idx_path, items)

    # Activity timeline
    await get_activity_bus().emit(
        ActivityKind.UPLOAD_ADDED,
        domain=domain,
        node=node,
        title=f"上传了文件「{item['file']}」",
        source="manual",
        ref=f"user_uploads#{item['file']}",
        extra={"category": item["category"], "size": item.get("size")},
    )

    return {"ok": True, "item": item, "total": len(items)}


@router.delete("/resources/{domain}/upload/{filename}")
async def delete_upload(domain: str, filename: str, node: str = Query(...)):
    """Delete an uploaded file."""
    repo = agent_deps.get_resource_repo()
    deleted = await repo.delete_upload(domain, node, filename)
    if not deleted:
        raise HTTPException(404, f"文件不存在：{filename}")

    idx_path = repo.uploads_index_path(domain, node)
    items = await repo.read_json_index(idx_path)
    new_items = [it for it in items if it.get("file") != filename]
    await repo.write_json_index(idx_path, new_items)
    return {"ok": True, "total": len(new_items)}


@router.put("/resources/{domain}/upload/{filename}")
async def edit_upload(
    domain: str,
    filename: str,
    node: str = Query(...),
    req: EditUploadReq = ...,
):
    """Edit upload metadata (category / note) without touching the file."""
    repo = agent_deps.get_resource_repo()
    idx_path = repo.uploads_index_path(domain, node)
    items = await repo.read_json_index(idx_path)
    found = False
    for it in items:
        if it.get("file") == filename:
            it["category"] = req.category
            it["note"] = req.note
            found = True
            break
    if not found:
        raise HTTPException(404, f"文件不存在：{filename}")
    await repo.write_json_index(idx_path, items)
    return {"ok": True, "total": len(items)}


@router.get("/resources/{domain}/download/{filename}")
async def download_upload(domain: str, filename: str, node: str = Query(...)):
    """Download / preview an uploaded file."""
    repo = agent_deps.get_resource_repo()
    target = repo.upload_path(domain, node, filename)
    if not target.exists():
        raise HTTPException(404, f"文件不存在：{filename}")
    return FileResponse(str(target), filename=filename)


# ------------------------------------------------------------------
# Routes — study materials (read-only, knowledge-digest outputs)
# ------------------------------------------------------------------


@router.get("/resources/{domain}/study-materials")
async def list_study_materials(
    domain: str,
    node: str = Query(...),
    path: str = Query("", description="Sub-directory relative to study_materials/"),
):
    """List the node's ``study_materials/<path>/`` directory contents.

    Returns a flat list mixing files and folders.  Folder entries have
    ``type='folder'`` and ``children_count``; file entries have
    ``type='file'``.  The frontend drills into folders by re-calling
    this endpoint with ``path=<folder>``.
    """
    note_repo = agent_deps.get_note_repo()
    items = await note_repo.list_study_materials(domain, node, path=path)
    return {"items": items, "total": len(items)}


@router.get("/resources/{domain}/study-materials/{filename:path}")
async def download_study_material(
    domain: str, filename: str, node: str = Query(...)
):
    """Download / preview a study-material file (PDF / PPTX / HTML / …).

    ``filename`` may contain ``/`` so the URL can point at a nested
    file like ``chapters/chapter-01.md``.  The path is resolved safely
    inside the node's ``study_materials/`` directory — ``..``
    traversal is rejected.
    """
    note_repo = agent_deps.get_note_repo()
    try:
        target = note_repo.study_material_path(domain, node, filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"学习资料不存在：{filename}")
    return FileResponse(str(target), filename=filename)


__all__ = ["router"]
