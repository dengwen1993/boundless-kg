"""Top-level conftest — fixtures shared by unit + integration tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.infrastructure.lock import _reset_locks_for_tests


def pytest_configure(config: pytest.Config) -> None:
    """Redirect the project logger away from the real ``logs/`` directory.

    This MUST happen in ``pytest_configure`` rather than a fixture:
    ``src/api/server.py`` calls ``create_app()`` at module scope, which
    calls ``setup_logging()``.  That fires during *collection* — before
    any fixture runs — so a session fixture sets ``KG_LOG_DIR`` too late
    and the file handlers are already bound to the real ``logs/``.

    Without this, every deliberately-failing test fixture (e.g.
    ``kg_add_subtree`` with ``domain='d'``) wrote a full traceback into
    the real ``logs/error.log``, drowning genuine production errors in
    test noise.
    """
    if not os.environ.get("KG_LOG_DIR"):
        os.environ["KG_LOG_DIR"] = tempfile.mkdtemp(prefix="kg_test_logs_")


@pytest.fixture
def tmp_kb_root(tmp_path: Path) -> Path:
    """Per-test scratch directory to back all repositories."""
    root = tmp_path / "kb_root"
    root.mkdir()
    return root


@pytest.fixture
def tmp_workspace_dir(tmp_path: Path) -> Path:
    """Per-test scratch workspace — sibling of ``tmp_kb_root`` so layout
    matches the real directory tree (workspace/ contains both
    knowledge_bases/ and the operational artefacts)."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "knowledge_bases").mkdir()
    return ws


@pytest.fixture(autouse=True)
def _reset_graph_lock():
    """Per-loop lock must reset between tests so each pytest-asyncio
    loop gets a fresh canonical instance."""
    _reset_locks_for_tests()
    yield
    _reset_locks_for_tests()


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wipe KG_* env vars so settings don't leak between tests.

    Also chdir into the temp directory so pydantic-settings does not
    pick up the developer's ``.env`` file from the project root.
    """
    for var in list(os.environ.keys()):
        if var == "KG_LOG_DIR":
            # Owned by ``_isolate_test_logs`` — wiping it would let a
            # later ``setup_logging()`` re-attach handlers to the real
            # project ``logs/`` directory.
            continue
        if var.startswith("KG_") or var in {
            "ANTHROPIC_API_KEY",
            "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY",
            "MMX_API_KEY",
            "BOCHA_API_KEY",
            "BOCHA_ENDPOINT",
            "BOCHA_PROXY",
            "BOCHA_TIMEOUT_SEC",
            "BOCHA_COUNT",
        }:
            monkeypatch.delenv(var, raising=False)
    # Point KG_KB_ROOT at the test scratch.
    monkeypatch.setenv("KG_KB_ROOT", str(tmp_path / "kb_root"))
    (tmp_path / "kb_root").mkdir(exist_ok=True)
    # KG_WORKSPACE_DIR defaults to ``./workspace`` (relative to CWD),
    # which under tests would resolve into the project tree. Pin it to
    # a sibling of ``kb_root`` so each test is fully isolated.
    monkeypatch.setenv("KG_WORKSPACE_DIR", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir(exist_ok=True)
    # Isolate from the project-root .env so LLMSettings() doesn't
    # pick up real API keys during tests.
    monkeypatch.chdir(tmp_path)
    # Clear the lru_cache on get_settings so the next call re-reads
    # the (now clean) environment.
    from src.config import reload_settings
    reload_settings()