"""Node asset migration — shared by Agent tools and API routes.

When a node is renamed, the on-disk directory ``notes/{old_name}/``
must be moved to ``notes/{new_name}/`` so that notes, web resources,
plan files, and uploads all follow the rename.  When a node is deleted,
the directory should be cleaned up.

Historically the Agent @tool ``kg_update_node`` only patched
``knowledge_graph.json`` and left the notes directory behind, causing
silent data loss (see the 2026-08-01 session log).  This module
centralises the migration logic so both the Agent tool layer and the
API route layer call the same function.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


async def migrate_node_assets(
    kb_root: Path, domain: str, old_name: str, new_name: str
) -> dict[str, Any]:
    """Rename ``notes/{old_name}/`` → ``notes/{new_name}/`` in place.

    Returns a report dict::

        {
            "note_migrated": bool,
            "resources_migrated": bool,
            "plans_migrated": bool,
            "uploads_migrated": bool,
            "errors": list[str],
        }

    If the source directory does not exist (node had no assets), all
    fields are ``False`` and ``errors`` is empty — this is not an error.
    If the target directory already exists, files are merged one-by-one
    without overwriting existing entries.
    """
    report: dict[str, Any] = {
        "note_migrated": False,
        "resources_migrated": False,
        "plans_migrated": False,
        "uploads_migrated": False,
        "errors": [],
    }
    if old_name == new_name:
        return report

    old_dir = kb_root / domain / "notes" / old_name
    new_dir = kb_root / domain / "notes" / new_name

    if not old_dir.exists():
        return report

    new_dir.parent.mkdir(parents=True, exist_ok=True)

    if new_dir.exists():
        # Target exists — merge file-by-file without overwriting.
        for item in old_dir.rglob("*"):
            if item.is_file():
                rel = item.relative_to(old_dir)
                dest = new_dir / rel
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.move(str(item), str(dest))
                    except Exception as e:
                        report["errors"].append(f"{rel}: {e}")
        try:
            shutil.rmtree(str(old_dir), ignore_errors=True)
        except Exception:
            pass
    else:
        try:
            old_dir.rename(new_dir)
        except Exception as e:
            report["errors"].append(f"rename {old_dir} → {new_dir}: {e}")
            return report

    # Fill in the report based on what now exists under new_dir.
    report["note_migrated"] = (new_dir / "note.md").exists()
    report["resources_migrated"] = (new_dir / "web_resources" / "index.json").exists()
    report["plans_migrated"] = (new_dir / "plan.json").exists()
    report["uploads_migrated"] = (new_dir / "user_uploads").exists()

    return report


async def delete_node_assets(
    kb_root: Path, domain: str, node_name: str
) -> bool:
    """Remove ``notes/{node_name}/`` if it exists.

    Returns ``True`` if the directory was removed, ``False`` if it did
    not exist.
    """
    node_dir = kb_root / domain / "notes" / node_name
    if not node_dir.exists():
        return False
    try:
        shutil.rmtree(str(node_dir), ignore_errors=True)
    except Exception:
        return False
    return True


__all__ = ["migrate_node_assets", "delete_node_assets"]
