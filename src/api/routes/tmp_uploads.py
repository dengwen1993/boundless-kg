"""Transient-file upload route — drops files into ``.agent_memory/tmp/``.

API contract:
  POST   /api/tmp/upload              → upload a file to the workspace tmp dir
  GET    /api/tmp/list                → list files currently in the tmp dir
  DELETE /api/tmp/{filename}          → delete a file by name
  GET    /api/tmp/download/{filename} → download / preview a file
  GET    /api/tmp/parse/{filename}    → extract the file's text content for the LLM
  POST   /api/tmp/auto-place          → parse + LLM-classify + save into the right node

This route is intentionally separate from the per-node ``/api/resources/.../upload``
endpoint — those files are *curated knowledge* tied to a knowledge-graph node
and survive long-term; these are *scratch files* the user drops into the
chat so the agent can read them in the current session, and they get
auto-deleted after :data:`TMP_MAX_AGE_DAYS` by the periodic background
cleanup loop in :mod:`src.api.server`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.agent.memory import get_tmp_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tmp", tags=["tmp-uploads"])

#: Hard cap on a single upload. ``minimax-docx`` and other skills may
#: blow up on multi-hundred-MB inputs, and ``aiofiles`` buffering
#: double the resident RAM, so 200 MB is a sensible upper bound for a
#: chat scratch file.
MAX_UPLOAD_BYTES: int = 200 * 1024 * 1024  # 200 MiB


def _safe_name(name: str) -> str:
    """Strip path components from an uploaded filename.

    Browsers happily forward ``../../etc/passwd`` in ``filename`` if a
    user (or attacker) crafts one; rejecting anything that contains a
    path separator keeps the file inside the tmp dir.
    """
    cleaned = Path(name).name
    if not cleaned or cleaned in {".", ".."}:
        raise HTTPException(400, f"非法的文件名：{name!r}")
    return cleaned


@router.post("/upload")
async def upload_tmp_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a file into the agent-memory tmp directory.

    The destination name is the original filename.  A numeric suffix
    (``_2``, ``_3``, …) is appended on collision so two uploads of the
    same name don't clobber each other.
    """
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"文件过大（{len(content)} bytes），单文件上限 {MAX_UPLOAD_BYTES} bytes",
        )

    safe = _safe_name(file.filename or "unnamed")
    tmp = get_tmp_dir()
    target = tmp / safe
    if target.exists():
        stem, suffix = Path(safe).stem, Path(safe).suffix
        i = 2
        while True:
            cand = tmp / f"{stem}_{i}{suffix}"
            if not cand.exists():
                target = cand
                break
            i += 1
    target.write_bytes(content)

    logger.info(
        "tmp upload: saved %s (%d bytes) — container path %s",
        target.name,
        len(content),
        target,
    )
    return {
        "ok": True,
        "item": {
            "file": target.name,
            "size": len(content),
            "path": str(target),
        },
    }


@router.get("/list")
async def list_tmp_files() -> dict[str, Any]:
    """List files currently sitting in the tmp directory.

    Sorted newest-first so freshly uploaded files (the ones the user is
    most likely thinking about) appear at the top.
    """
    tmp = get_tmp_dir()
    if not tmp.exists():
        return {"items": [], "total": 0}

    items: list[dict[str, Any]] = []
    for path in tmp.iterdir():
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append(
            {
                "file": path.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "path": str(path),
            }
        )
    items.sort(key=lambda it: it["mtime"], reverse=True)
    return {"items": items, "total": len(items)}


@router.delete("/{filename}")
async def delete_tmp_file(filename: str) -> dict[str, Any]:
    """Delete a single file from the tmp directory."""
    safe = _safe_name(filename)
    tmp = get_tmp_dir()
    target = tmp / safe
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"文件不存在：{safe}")
    target.unlink()
    return {"ok": True}


@router.get("/download/{filename}")
async def download_tmp_file(filename: str) -> FileResponse:
    """Download / preview a single file from the tmp directory."""
    safe = _safe_name(filename)
    target = get_tmp_dir() / safe
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"文件不存在：{safe}")
    return FileResponse(str(target), filename=safe)


@router.get("/parse/{filename}")
async def parse_tmp_file(
    filename: str,
    max_chars: int = Query(20000, ge=1000, le=200000),
) -> dict[str, Any]:
    """Extract text from an uploaded file (best-effort, format-aware).

    Returns a JSON ``{"text": "...", "truncated": bool, "format": "..."}``
    so the frontend (or the ``kg_parse_uploaded_file`` agent tool) can
    show the result inline without having to handle format-specific
    routing on its own.

    Format dispatch is intentionally *try-and-fall-back* — we always
    return *some* text (or a clear error), never raise on an unknown
    extension, so the agent can recover gracefully.
    """
    from src.application.tmp_parser import parse_file_to_text

    safe = _safe_name(filename)
    target = get_tmp_dir() / safe
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"文件不存在：{safe}")

    try:
        text = await parse_file_to_text(target, max_chars=max_chars)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("parse failed for %s", target.name)
        raise HTTPException(500, f"解析失败：{type(exc).__name__}: {exc}")

    return text


# ------------------------------------------------------------------
# Auto-place: parse + LLM-classify + save into the right node
# ------------------------------------------------------------------


class AutoPlaceReq(BaseModel):
    """Body for ``POST /api/tmp/auto-place``.

    ``filename`` is the file currently sitting in ``.agent_memory/tmp/``
    (upload via ``POST /api/tmp/upload`` first). ``domain`` is the
    knowledge-base domain to attach the file to.
    """

    filename: str
    domain: str
    create_new_node: bool = True
    max_chars: int = 8000


@router.post("/auto-place")
async def auto_place_tmp_file(req: AutoPlaceReq = Body(...)) -> dict[str, Any]:
    """One-shot auto-placement endpoint.

    Workflow:

    1. Parse the file via :func:`src.application.tmp_parser.parse_file_to_text`.
    2. Load the domain's ``knowledge_graph.json`` (the outline).
    3. Ask the LLM which existing node the file belongs to.
    4. Copy the file into the node's ``user_uploads/`` directory and
       append to its ``user_uploads/index.json``.

    Designed for the frontend "smart upload" button — after the user
    drops a file, the UI calls this endpoint instead of forcing them
    to pick a node manually.

    Returns ``{ok, node, path, rationale, new_node_created, decision, ...}``.
    """
    from src.agent.dependencies import get_resource_service
    from src.application.tmp_parser import parse_file_to_text

    safe = _safe_name(req.filename)
    target = get_tmp_dir() / safe
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"文件不存在：{safe}")

    max_chars = max(500, min(32000, req.max_chars))

    try:
        parsed = await parse_file_to_text(target, max_chars=max_chars)
    except Exception as exc:
        logger.exception("parse failed for %s", target.name)
        raise HTTPException(500, f"解析失败：{type(exc).__name__}: {exc}")

    svc = get_resource_service()
    try:
        result = await svc.auto_place_upload(
            domain=req.domain,
            tmp_path=target,
            filename=safe,
            parsed=parsed,
            create_new_node=req.create_new_node,
        )
    except Exception as exc:
        logger.exception("auto_place_upload failed for %s/%s", req.domain, safe)
        raise HTTPException(
            500,
            f"自动归类失败：{type(exc).__name__}: {exc}",
        )

    return {
        "ok": True,
        "filename": safe,
        "format": parsed.get("format"),
        "size": parsed.get("size"),
        **result,
    }


__all__ = ["router", "MAX_UPLOAD_BYTES"]