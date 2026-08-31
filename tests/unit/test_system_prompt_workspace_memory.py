"""Workspace AGENTS.md is the single source of truth.

Two contracts are pinned here:

1. ``compose_system_prompt`` injects ``<workspace>/AGENTS.md`` into the
   SystemMessage at agent build time, with stable BEGIN/END markers so
   the splice is grep-able in cached transcriptions.
2. ``get_filesystem_backend`` roots the deepagents ``FilesystemBackend``
   at the workspace, so deepagents' ``/AGENTS.md`` virtual path
   resolves to the same file the agent just got injected via
   ``compose_system_prompt``. The two paths must agree or the agent
   can read fresh edits via tools but the prompt shows the stale copy
   (or vice versa).

Missing files are tolerated (the orchestrator must still boot — see
T-001), and ``KG_AGENT_WORKSPACE_MEM_DISABLED=1`` is an unconditional
opt-out. Both knobs are exercised below.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from src.agent.memory import (
    get_filesystem_backend,
    reset_memory_subsystem,
    get_workspace_agents_md_path,
)
from src.agent.system_prompt import (
    SYSTEM_PROMPT,
    compose_system_prompt,
    load_workspace_agents_md,
)


@pytest.fixture
def temp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``KG_WORKSPACE_DIR`` at *tmp_path* for the duration of one test.

    The settings module is cached by ``lru_cache`` so we must call
    ``reload_settings()`` — otherwise the cached ``Settings`` from the
    previous test session leaks workspace_dir pointing at the real
    on-disk layout.
    """
    from src.config import settings as settings_module
    monkeypatch.setenv("KG_WORKSPACE_DIR", str(tmp_path))
    settings_module.reload_settings()
    reset_memory_subsystem()
    yield tmp_path
    reset_memory_subsystem()


# ----- compose_system_prompt ---------------------------------------------------


class TestComposeSystemPrompt:
    """The injection contract for the workspace-curated AGENTS.md."""

    def test_static_prompt_unchanged_when_file_missing(
        self, temp_workspace: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No AGENTS.md on disk → no injection, no crash, one warning.

        This is the cold-start path (fresh checkout with no AGENTS.md).
        The agent must still build; it just loses the curated memory
        that turn. ``MemoryMiddleware`` exposes ``/AGENTS.md`` so the
        agent can detect the absence itself on the next tool call.
        """
        import logging

        with caplog.at_level(logging.WARNING, logger="src.agent.system_prompt"):
            out = compose_system_prompt(workspace_dir=temp_workspace)
        assert out == SYSTEM_PROMPT
        assert any("AGENTS.md not found" in r.message for r in caplog.records)

    def test_workspace_file_is_injected_with_markers(
        self, temp_workspace: Path
    ) -> None:
        """When AGENTS.md is present, its content lands between BEGIN/END markers."""
        (temp_workspace / "AGENTS.md").write_text(
            "# curated\nactual user memory\n", encoding="utf-8"
        )
        out = compose_system_prompt(workspace_dir=temp_workspace)
        # Static prefix intact
        assert out.startswith(SYSTEM_PROMPT)
        # Injected section visible
        assert "<!-- BEGIN workspace/AGENTS.md" in out
        assert "<!-- END workspace/AGENTS.md -->" in out
        assert "# curated" in out
        assert "actual user memory" in out
        # No leak of the previous (\n\n) duplication
        section = out.split("<!-- BEGIN workspace/AGENTS.md (injected at build time) -->\n", 1)[1]
        assert section.startswith("# curated")

    def test_disabled_env_skips_read_and_injection(
        self, temp_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``KG_AGENT_WORKSPACE_MEM_DISABLED=1`` is an unconditional opt-out.

        Even when AGENTS.md exists, the file is not read and the prompt
        is not modified. This protects production deployments where the
        workspace is read-only or the operator wants the static
        SYSTEM_PROMPT only.
        """
        (temp_workspace / "AGENTS.md").write_text("MARKER_INJECTED", encoding="utf-8")
        monkeypatch.setenv("KG_AGENT_WORKSPACE_MEM_DISABLED", "1")
        # Reload the module so the os.environ re-read at function-call time
        # picks up the freshly-set var (the helper reads env on every call,
        # so this is belt-and-braces).
        import src.agent.system_prompt as sp_module
        importlib.reload(sp_module)
        try:
            out = sp_module.compose_system_prompt(workspace_dir=temp_workspace)
            assert out == sp_module.SYSTEM_PROMPT
            assert "MARKER_INJECTED" not in out
        finally:
            monkeypatch.delenv("KG_AGENT_WORKSPACE_MEM_DISABLED")
            importlib.reload(sp_module)

    def test_load_returns_empty_string_for_missing_file(
        self, temp_workspace: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        with caplog.at_level(logging.WARNING, logger="src.agent.system_prompt"):
            assert load_workspace_agents_md(workspace_dir=temp_workspace) == ""

    def test_load_appends_trailing_newline(
        self, temp_workspace: Path
    ) -> None:
        path = temp_workspace / "AGENTS.md"
        path.write_text("no-trailing-newline", encoding="utf-8")
        out = load_workspace_agents_md(workspace_dir=temp_workspace)
        assert out == "no-trailing-newline\n"


# ----- get_filesystem_backend -------------------------------------------------


class TestFilesystemBackendRoot:
    """Confirm the FilesystemBackend resolves ``/AGENTS.md`` to the same file.

    Without this, deepagents' MemoryMiddleware would point at a
    different file than the prompt — see the "stale copy" foot-gun
    the architecture change is designed to eliminate.
    """

    def test_backend_root_is_workspace(
        self, temp_workspace: Path
    ) -> None:
        backend = get_filesystem_backend()
        # ``FilesystemBackend`` exposes its root through ``cwd`` (no
        # public ``root_dir`` attribute); we use the canonical accessor
        # instead of poking private state so the test is robust to
        # upstream renames within ``deepagents``.
        assert Path(str(backend.cwd)).resolve() == temp_workspace.resolve()

    def test_virtual_agents_md_reads_same_workspace_file(
        self, temp_workspace: Path
    ) -> None:
        """End-to-end: writing ``<workspace>/AGENTS.md`` is observable
        via the backend's ``/AGENTS.md`` virtual path with no extra
        wiring. If this fails, MemoryMiddleware would silently point
        at a stale copy."""
        target = temp_workspace / "AGENTS.md"
        target.write_text("DEEPAGENTS_VIRTUAL_PATH_PROBE", encoding="utf-8")

        backend = get_filesystem_backend()
        # ``read`` accepts the virtual path with the leading slash in
        # virtual mode (which is the default we pass to the constructor).
        # The return value is a ``ReadResult`` dataclass; the file bytes
        # live under ``file_data['content']`` per deepagents >= the
        # version vendored in ``pyproject.toml``.
        result = backend.read("/AGENTS.md")
        assert result.error is None, result.error
        assert result.file_data["content"] == "DEEPAGENTS_VIRTUAL_PATH_PROBE"

    def test_workspace_agents_md_path_matches_virtual_root(
        self, temp_workspace: Path
    ) -> None:
        """``/AGENTS.md`` (deepagents' virtual path) and
        ``compose_system_prompt``'s read site must hit the same file."""
        agents_path = get_workspace_agents_md_path()
        backend = get_filesystem_backend()
        assert agents_path == Path(str(backend.cwd)) / "AGENTS.md"
