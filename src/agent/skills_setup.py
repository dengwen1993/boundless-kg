"""Skills subsystem — wires deepagents SkillsMiddleware to the orchestrator.

Responsibilities
----------------
1. Discover every skill under ``<skills_dir>/<skill-name>/SKILL.md``.
2. Build a :class:`CompositeBackend` that exposes both the agent memory root
   (for AGENTS.md / conversation history) and the skills root (for skill
   SKILL.md + helper scripts / references), so a single ``FilesystemBackend``
   argument can serve both subsystems.
3. Return the list of skill source paths that ``create_deep_agent(skills=...)``
   consumes (each prefixed with ``/skills/<skill-name>/`` so the agent reads
   helpers via the same backend).

Backend layout
--------------
``CompositeBackend`` mounts the skills tree at ``/skills/``::

    /<anything-else>          →  agent memory backend (AGENTS.md etc.)
    /skills/<skill-name>/...  →  skills backend (read-only in practice)

This way:
  * SkillsMiddleware scans ``/skills/knowledge-digest/`` etc. as configured.
  * Agent uses ``read_file("/skills/knowledge-digest/SKILL.md")`` to fetch
    the full SKILL.md on demand (progressive disclosure).
  * Agent uses ``read_file("/skills/knowledge-digest/scripts/foo.py")`` to
    fetch helper scripts / references bundled with the skill.

Resolution rules
----------------
* ``KG_SKILLS_DIR`` env var overrides the default location.
* Missing directories are skipped (no crash).  An empty skill set is
  logged at INFO so operators see it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from deepagents.backends import CompositeBackend, FilesystemBackend

from src.agent.memory import get_filesystem_backend
from src.config.settings import get_skills_dir

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol

logger = logging.getLogger(__name__)


# ── Skill discovery ────────────────────────────────────────────────


def discover_skills(skills_dir: Path | None = None) -> list[Path]:
    """Return the list of skill directories containing a SKILL.md.

    A valid skill has shape ``<skills_dir>/<skill-name>/SKILL.md``.
    Anything else (loose files, nested dirs without SKILL.md) is ignored.
    """
    base = (skills_dir or get_skills_dir()).resolve()
    if not base.exists():
        logger.info("[skills] skills directory %s does not exist; no skills loaded", base)
        return []
    found: list[Path] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / "SKILL.md").exists():
            found.append(entry.resolve())
    if found:
        logger.info(
            "[skills] discovered %d skill(s) under %s: %s",
            len(found), base, ", ".join(p.name for p in found),
        )
    else:
        logger.info("[skills] no skills found under %s", base)
    return found


# ── Backend composition ────────────────────────────────────────────


def get_skills_backend(skills_dir: Path | None = None) -> "BackendProtocol":
    """Build the :class:`CompositeBackend` that backs both memory + skills.

    Returns a fresh :class:`FilesystemBackend` rooted at the skills dir,
    mounted at the ``/skills/`` virtual prefix.  Other paths delegate to
    the existing agent-memory backend (which owns AGENTS.md, conversation
    logs, summarization offload).
    """
    base = (skills_dir or get_skills_dir()).resolve()
    # ``virtual_mode=True`` so paths starting with ``/skills/`` resolve
    # cleanly under ``base`` without exposing the absolute prefix.
    skills_fs = FilesystemBackend(root_dir=str(base), virtual_mode=True)
    return CompositeBackend(
        default=get_filesystem_backend(),
        routes={"/skills/": skills_fs},
    )


# ── Skill source paths (for create_deep_agent(skills=[...])) ───────


def get_skill_sources(skills_dir: Path | None = None) -> list[str]:
    """Return the list of skill source paths consumed by ``create_deep_agent``.

    Each entry is a virtual path under the skills backend:

        /skills/knowledge-digest/
        /skills/minimax-pdf/
        /skills/pptx-generator/
        /skills/minimax-docx/

    DeepAgents' SkillsMiddleware reads these paths via the
    ``CompositeBackend`` we build in :func:`get_skills_backend`.
    """
    skills = discover_skills(skills_dir)
    return [f"/skills/{p.name}/" for p in skills]


__all__ = [
    "discover_skills",
    "get_skill_sources",
    "get_skills_backend",
]