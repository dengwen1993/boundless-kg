"""Resource repository — index + uploaded files for a domain."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiofiles

from src.infrastructure.lock import graph_lock

from ._atomic import atomic_write_text


class ResourceRepository:
    """Per-domain resource index + on-disk file storage.

    ``workspace_dir`` is the parent directory for **operational** files
    that aren't part of any specific domain's knowledge — currently just
    ``_staging/`` for file upload hand-off.  ``kb_root`` is still used
    for the per-domain ``<domain>/web_resources/index.json`` and friends.
    """

    def __init__(self, kb_root: Path, workspace_dir: Path | None = None) -> None:
        self._kb_root = Path(kb_root)
        # Default the workspace dir to the parent of kb_root when unset;
        # explicit overrides take precedence so tests can point both at
        # the same temp root.
        if workspace_dir is None:
            self._workspace_dir = self._kb_root.parent
        else:
            self._workspace_dir = Path(workspace_dir)

    def _index_path(self, domain: str) -> Path:
        return self._kb_root / domain / "web_resources" / "index.json"

    def _files_dir(self, domain: str) -> Path:
        return self._kb_root / domain / "web_resources" / "files"

    def _staging_dir(self) -> Path:
        # Operational hand-off area; lives in workspace, NOT in kb_root,
        # so it doesn't show up as a "domain" in the picker.
        return self._workspace_dir / "_staging"

    # ------------------------------------------------------------------
    # Node-level web_resources/index.json (used by agent tools + API)
    # ------------------------------------------------------------------

    def _node_index_path(self, domain: str, node: str) -> Path:
        return self._kb_root / domain / "notes" / node / "web_resources" / "index.json"

    def _notes_root(self, domain: str) -> Path:
        return self._kb_root / domain / "notes"

    async def list_node_resources(self, domain: str, node: str) -> list[dict[str, Any]]:
        """Read a single node's ``web_resources/index.json``.

        Returns ``[]`` when the file is missing or corrupt.
        """
        path = self._node_index_path(domain, node)
        if not path.exists():
            return []
        try:
            async with aiofiles.open(path, encoding="utf-8") as f:
                raw = await f.read()
            items = json.loads(raw)
            return items if isinstance(items, list) else []
        except (json.JSONDecodeError, Exception):
            return []

    async def list_all_node_resources(self, domain: str) -> list[dict[str, Any]]:
        """Scan every node directory and aggregate web_resources.

        Mirrors the old ``kg_view_resources(node="")`` scan logic but
        lives in the Repository so callers never touch the filesystem.
        """
        notes_root = self._notes_root(domain)
        if not notes_root.exists():
            return []
        all_items: list[dict[str, Any]] = []
        for nd in notes_root.iterdir():
            if not nd.is_dir():
                continue
            idx = nd / "web_resources" / "index.json"
            if idx.exists():
                try:
                    async with aiofiles.open(idx, encoding="utf-8") as f:
                        items = json.loads(await f.read())
                    if isinstance(items, list):
                        all_items.extend(items)
                except (json.JSONDecodeError, Exception):
                    pass
        return all_items

    async def add_resources_batch(
        self,
        domain: str,
        node: str,
        items: list[dict[str, Any]],
        *,
        max_batch: int = 5,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Batch-append resources to a node's ``web_resources/index.json``.

        Handles directory creation, URL dedup, atomic write, and locking.

        Returns ``(added_count, newly_added_items)``.
        Truncates input to *max_batch* and signals the caller via the
        difference between ``len(items)`` and ``added_count``.
        """
        from datetime import datetime
        from src.domain.resource.categories import DEFAULT_CATEGORY

        # Truncate to max_batch
        truncated = max(0, len(items) - max_batch)
        if truncated:
            items = items[:max_batch]

        now = datetime.now().isoformat(timespec="seconds")
        index_path = self._node_index_path(domain, node)
        newly_added: list[dict[str, Any]] = []

        async with graph_lock():
            index_path.parent.mkdir(parents=True, exist_ok=True)

            # Read existing
            if index_path.exists():
                async with aiofiles.open(index_path, encoding="utf-8") as f:
                    raw_content = await f.read()
                try:
                    existing_items = json.loads(raw_content)
                    if not isinstance(existing_items, list):
                        existing_items = []
                except json.JSONDecodeError:
                    existing_items = []
            else:
                existing_items = []

            # Add new (dedup by URL)
            added_count = 0
            for it in items:
                cat = (it.get("category") or "").strip() or DEFAULT_CATEGORY
                new_item = {
                    "title": it.get("title", ""),
                    "url": it.get("url", "") or it.get("link", ""),
                    "summary": it.get("summary", "") or it.get("snippet", ""),
                    "category": cat,
                    "added_at": it.get("added_at", now),
                    "node": node,
                }
                if not any(
                    existing.get("url") == new_item["url"]
                    for existing in existing_items
                ):
                    existing_items.append(new_item)
                    newly_added.append(new_item)
                    added_count += 1

            # Atomic write
            await atomic_write_text(
                index_path,
                json.dumps(existing_items, ensure_ascii=False, indent=2),
            )

        return added_count, newly_added

    async def list_resources(self, domain: str, node: str | None = None) -> list[dict[str, Any]]:
        path = self._index_path(domain)
        if not path.exists():
            return []
        async with aiofiles.open(path, encoding="utf-8") as f:
            raw = await f.read()
        index = json.loads(raw)
        items = index.get("resources", [])
        if node:
            items = [it for it in items if it.get("node") == node]
        return items

    async def add_resource(self, domain: str, resource: dict[str, Any]) -> None:
        async with graph_lock():
            path = self._index_path(domain)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                async with aiofiles.open(path, encoding="utf-8") as f:
                    raw = await f.read()
                index = json.loads(raw)
            else:
                index = {"resources": []}
            index["resources"].append(resource)
            await atomic_write_text(path, json.dumps(index, ensure_ascii=False, indent=2))

    async def stage_upload(self, src: Path, suggested_name: str) -> Path:
        """Move *src* into the staging area; return the staged path."""
        async with graph_lock():
            staging = self._staging_dir()
            staging.mkdir(parents=True, exist_ok=True)
            target = staging / suggested_name
            target.write_bytes(src.read_bytes())
            return target

    async def promote_staged(self, staged: Path, domain: str, node: str) -> Path:
        """Move a staged file into the node's resource directory."""
        async with graph_lock():
            target_dir = self._files_dir(domain) / node
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / staged.name
            target.write_bytes(staged.read_bytes())
            staged.unlink(missing_ok=True)
            return target

    # ------------------------------------------------------------------
    # Generic index.json read/write (consolidated from _helpers.py)
    # ------------------------------------------------------------------

    async def read_json_index(self, path: Path) -> list[dict[str, Any]]:
        """Read a JSON array file; return ``[]`` on missing / corrupt."""
        if not path.exists():
            return []
        try:
            async with aiofiles.open(path, encoding="utf-8") as f:
                items = json.loads(await f.read())
            return items if isinstance(items, list) else []
        except Exception:
            return []

    async def write_json_index(self, path: Path, items: list[dict[str, Any]]) -> None:
        """Atomically write a JSON array to *path*."""
        path.parent.mkdir(parents=True, exist_ok=True)
        async with graph_lock():
            await atomic_write_text(
                path, json.dumps(items, ensure_ascii=False, indent=2)
            )

    # ------------------------------------------------------------------
    # File upload / download (used by API resource routes)
    # ------------------------------------------------------------------

    def uploads_dir(self, domain: str, node: str) -> Path:
        """Return the ``user_uploads`` directory for a node."""
        return self._kb_root / domain / "notes" / node / "user_uploads"

    def web_index_path(self, domain: str, node: str) -> Path:
        """Return the ``web_resources/index.json`` path for a node."""
        return self._node_index_path(domain, node)

    def uploads_index_path(self, domain: str, node: str) -> Path:
        """Return the ``user_uploads/index.json`` path for a node."""
        return self.uploads_dir(domain, node) / "index.json"

    async def save_upload(self, domain: str, node: str, filename: str, content: bytes) -> Path:
        """Write an uploaded file to the node's ``user_uploads/`` dir.

        If *filename* already exists, appends a ``_2``, ``_3``, … suffix.
        Returns the final path.
        """
        uploads = self.uploads_dir(domain, node)
        uploads.mkdir(parents=True, exist_ok=True)
        target = uploads / filename
        if target.exists():
            stem, suffix = Path(filename).stem, Path(filename).suffix
            i = 2
            while True:
                cand = uploads / f"{stem}_{i}{suffix}"
                if not cand.exists():
                    target = cand
                    break
                i += 1
        target.write_bytes(content)
        return target

    async def delete_upload(self, domain: str, node: str, filename: str) -> bool:
        """Delete an uploaded file. Returns ``True`` if deleted."""
        target = self.uploads_dir(domain, node) / filename
        if not target.exists():
            return False
        target.unlink()
        return True

    def upload_path(self, domain: str, node: str, filename: str) -> Path:
        """Return the path to an uploaded file."""
        return self.uploads_dir(domain, node) / filename


__all__ = ["ResourceRepository"]