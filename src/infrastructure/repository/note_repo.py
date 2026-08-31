"""Note repository — async CRUD over Markdown files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles

from src.infrastructure.lock import graph_lock

from ._atomic import atomic_write_text


def _infer_material_category(filename: str) -> str:
    """Map a study-material filename to a human-readable category tag.

    The agent (knowledge-digest skill) writes files into
    ``study_materials/`` without a sidecar index.  Frontend wants
    per-file categories for filtering / colour-coding, so we infer
    them from the filename here instead of asking the agent to
    maintain an index.
    """
    name = filename.lower()
    if name.endswith(".pdf") and "notes" in name:
        return "复习笔记"
    if name.endswith(".pdf") and "slide" in name:
        return "讲义幻灯片"
    if name.endswith(".pptx") and "slide" in name:
        return "幻灯片"
    if name.endswith(".html") and "quiz" in name:
        return "测验"
    if name.endswith(".mmd") or (name.endswith(".png") and "mindmap" in name):
        return "思维导图"
    if name.endswith(".pdf"):
        return "PDF"
    if name.endswith(".pptx"):
        return "幻灯片"
    if name.endswith(".html"):
        return "网页"
    if name.endswith(".md"):
        return "文档"
    if name.endswith(".png") or name.endswith(".jpg") or name.endswith(".jpeg"):
        return "图片"
    return "其他"


class NoteRepository:
    """Async CRUD for ``<kb_root>/<domain>/notes/<node>/note.md``."""

    def __init__(self, kb_root: Path) -> None:
        self._kb_root = Path(kb_root)

    @staticmethod
    def _safe_domain(domain: str) -> str:
        """Extract the real domain name from a possible 'domain / node' compound."""
        return domain.split(" / ")[0].split(" \\ ")[0].strip()

    def _note_path(self, domain: str, node_name: str) -> Path:
        return (
            self._kb_root / self._safe_domain(domain) / "notes" / node_name / "note.md"
        )

    async def read_note(self, domain: str, node_name: str) -> str | None:
        path = self._note_path(domain, node_name)
        if not path.exists():
            return None
        async with aiofiles.open(path, encoding="utf-8") as f:
            return await f.read()

    async def write_note(self, domain: str, node_name: str, content: str) -> None:
        async with graph_lock():
            path = self._note_path(domain, node_name)
            await atomic_write_text(path, content)

    async def list_notes(self, domain: str) -> list[str]:
        notes_dir = self._kb_root / self._safe_domain(domain) / "notes"
        if not notes_dir.exists():
            return []
        return sorted(p.name for p in notes_dir.iterdir() if p.is_dir())

    async def delete_note(self, domain: str, node_name: str) -> bool:
        async with graph_lock():
            path = self._note_path(domain, node_name)
            if not path.exists():
                return False
            path.unlink()
            return True

    def node_dir(self, domain: str, node_name: str) -> Path:
        """Return the directory path for a node's notes."""
        return self._note_path(domain, node_name).parent

    async def ensure_node_dir(self, domain: str, node_name: str) -> Path:
        """Create the node directory (and resource sub-dirs) if missing.

        Returns the path to the node directory.
        """
        nd = self.node_dir(domain, node_name)
        nd.mkdir(parents=True, exist_ok=True)
        (nd / "web_resources").mkdir(exist_ok=True)
        (nd / "user_uploads").mkdir(exist_ok=True)
        # Digest skill outputs (knowledge-digest) live alongside the other
        # resource sub-dirs so the layout stays uniform — every node has
        # exactly: note.md + web_resources + user_uploads + study_materials.
        # Knowledge agents find them by listing this directory; the
        # MANIFEST.md gives a human + LLM readable navigation entry point.
        (nd / "study_materials").mkdir(exist_ok=True)
        return nd

    def study_materials_dir(self, domain: str, node_name: str) -> Path:
        """Path to ``notes/<node>/study_materials/`` for digest outputs."""
        return self.node_dir(domain, node_name) / "study_materials"

    async def list_study_materials(
        self, domain: str, node_name: str, path: str = ""
    ) -> list[dict[str, Any]]:
        """Scan ``study_materials/<path>/`` and return file + folder entries.

        Each file entry mirrors the ``UploadResource`` shape consumed by
        the frontend's resource dialog (``file`` / ``size`` / ``mtime`` /
        ``category``) so a single UI can render both ``user_uploads``
        and ``study_materials`` without diverging.

        Folder entries carry ``type='folder'`` and ``children_count``
        (immediate files only — sub-folders are not drilled into here).

        ``file`` is always the path relative to the ``study_materials/``
        root, so a root-level file is ``cordis_quiz.html`` and a nested
        one is ``chapters/chapter-01.md``.  The frontend uses it
        directly for download URLs and splits on ``/`` for display.

        ``path`` is treated as a single forward-slash segment — any
        attempt to traverse (``..``, leading ``/``) returns an empty
        list instead of 500'ing the dialog.
        """
        sm_dir = self.study_materials_dir(domain, node_name)
        if not sm_dir.exists():
            return []
        # Reject path traversal; ``path`` must be a clean relative seg
        if path and (".." in path.split("/") or path.startswith("/")):
            return []
        target_dir = (sm_dir / path).resolve() if path else sm_dir.resolve()
        try:
            target_dir.relative_to(sm_dir.resolve())
        except ValueError:
            return []
        if not target_dir.exists() or not target_dir.is_dir():
            return []

        items: list[dict[str, Any]] = []
        for p in sorted(target_dir.iterdir()):
            rel = p.relative_to(sm_dir).as_posix()
            try:
                stat = p.stat()
                size = stat.st_size
                mtime = datetime.fromtimestamp(stat.st_mtime).isoformat(
                    timespec="seconds"
                )
            except OSError:
                size, mtime = 0, ""
            if p.is_dir():
                # Children count = immediate files; sub-folders are not
                # drilled into (the UI enters them on click instead).
                child_files = sum(1 for c in p.iterdir() if c.is_file())
                items.append(
                    {
                        "file": rel,
                        "type": "folder",
                        "category": "folder",
                        "note": "",
                        "moved_at": mtime,
                        "size": size,
                        "children_count": child_files,
                        "source": "study_materials",
                    }
                )
            else:
                items.append(
                    {
                        "file": rel,
                        "type": "file",
                        "category": _infer_material_category(p.name),
                        "note": "",
                        "moved_at": mtime,
                        "size": size,
                        "source": "study_materials",
                    }
                )
        return items

    def study_material_path(
        self, domain: str, node_name: str, filename: str
    ) -> Path:
        """Resolve a study-material file path; rejects ``..`` traversal."""
        sm_dir = self.study_materials_dir(domain, node_name)
        # Resolve and ensure the result stays inside sm_dir
        candidate = (sm_dir / filename).resolve()
        sm_resolved = sm_dir.resolve()
        try:
            candidate.relative_to(sm_resolved)
        except ValueError as exc:
            raise ValueError(f"非法路径：{filename}") from exc
        return candidate

    def note_path(self, domain: str, node_name: str) -> Path:
        """Return the path to ``note.md`` for a node."""
        return self._note_path(domain, node_name)


__all__ = ["NoteRepository"]