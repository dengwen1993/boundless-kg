"""Skills subsystem wiring — discover_skills + CompositeBackend mount."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.skills_setup import (
    discover_skills,
    get_skill_sources,
    get_skills_backend,
)


def test_discover_skills_finds_bundled(tmp_path: Path) -> None:
    """A directory with one valid skill should be picked up."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: x\n---\n", encoding="utf-8"
    )
    (tmp_path / "no_skill").mkdir()  # missing SKILL.md → ignored
    found = discover_skills(tmp_path)
    assert [p.name for p in found] == ["alpha"]


def test_discover_skills_missing_dir(tmp_path: Path) -> None:
    """Missing directory returns empty list, no exception."""
    assert discover_skills(tmp_path / "does_not_exist") == []


def test_skill_sources_use_skills_prefix(tmp_path: Path) -> None:
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "SKILL.md").write_text("---\nname: beta\n---\n", encoding="utf-8")
    sources = get_skill_sources(tmp_path)
    assert sources == ["/skills/beta/"]


def test_get_skills_backend_returns_composite(tmp_path: Path) -> None:
    """CompositeBackend should expose /skills/ routes."""
    backend = get_skills_backend(tmp_path)
    # CompositeBackend has a .routes dict-like attribute.
    assert hasattr(backend, "routes")
    assert "/skills/" in backend.routes


def test_get_skills_backend_finds_real_bundled() -> None:
    """Sanity check: the bundled src/skills/ tree is recognised."""
    sources = get_skill_sources()
    # Order is alphabetical (sorted).
    assert "/skills/knowledge-digest/" in sources
    assert "/skills/minimax-docx/" in sources
    assert "/skills/minimax-pdf/" in sources
    assert "/skills/pptx-generator/" in sources