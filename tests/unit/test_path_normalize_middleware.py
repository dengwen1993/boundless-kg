"""Tests for ``src.agent.guard.PathNormalizeMiddleware`` (BUG-2026-08-19-003 L4 gate).

Coverage:
  * Pure-function ``_normalize_path``: prefix stripping, edge cases,
    passthrough for non-matching paths.
  * Middleware end-to-end: ``write_file`` / ``edit_file`` args get
    rewritten before reaching the handler; non-write tools untouched;
    relative paths pass through unchanged; logs the rewrite.
  * Edge cases: nested paths, path-aliases (``path`` / ``target_file``),
    non-string args, missing path arg.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.agent.guard import PathNormalizeMiddleware, _normalize_path


# ─────────────────────────────────────────────────────────────────────
# Pure-function _normalize_path
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # ── prefix stripping ──
        (
            "/home/wend/boundless_kg/workspace/AGENTS.md",
            "AGENTS.md",
        ),
        (
            "/home/wend/boundless_kg/workspace/bugs.md",
            "bugs.md",
        ),
        (
            "/home/wend/boundless_kg/workspace/knowledge_bases/foo.md",
            "knowledge_bases/foo.md",
        ),
        (
            "/home/wend/boundless_kg/workspace/knowledge_bases/AI 应用开发/notes/x.md",
            "knowledge_bases/AI 应用开发/notes/x.md",
        ),
        # ── bare prefix (no trailing slash) ──
        (
            "/home/wend/boundless_kg/workspace",
            "",
        ),
        # ── prefix only with trailing slash ──
        (
            "/home/wend/boundless_kg/workspace/",
            "",
        ),
        # ── passthrough for relative paths ──
        (
            "AGENTS.md",
            "AGENTS.md",
        ),
        (
            "knowledge_bases/foo.md",
            "knowledge_bases/foo.md",
        ),
        # ── passthrough for absolute paths OUTSIDE the prefix ──
        (
            "/tmp/x",
            "/tmp/x",
        ),
        (
            "/etc/hosts",
            "/etc/hosts",
        ),
        (
            "/home/wend/other.txt",
            "/home/wend/other.txt",
        ),
        # ── passthrough for paths that look similar but differ ──
        (
            "/home/wend/boundless_kg/other/AGENTS.md",
            "/home/wend/boundless_kg/other/AGENTS.md",
        ),
        (
            "/home/wend/boundless_kg/workspaceX/AGENTS.md",
            "/home/wend/boundless_kg/workspaceX/AGENTS.md",
        ),
        # ── edge cases ──
        ("", ""),
        (
            "/home/wend/boundless_kg/workspace//nested/foo.md",
            "nested/foo.md",
        ),
    ],
)
def test_normalize_path(raw: str, expected: str) -> None:
    assert _normalize_path(raw) == expected


def test_normalize_path_is_idempotent() -> None:
    """Normalising an already-relative path leaves it alone, so the
    middleware can run repeatedly without state drift."""
    once = _normalize_path("/home/wend/boundless_kg/workspace/foo.md")
    twice = _normalize_path(once)
    assert once == "foo.md"
    assert twice == "foo.md"


# ─────────────────────────────────────────────────────────────────────
# Middleware test helpers
# ─────────────────────────────────────────────────────────────────────


@dataclass
class _FakeAIMessage:
    content: str = ""


@dataclass
class _Req:
    """Stand-in for deepagents' ToolCallRequest-shaped object."""

    tool_call: dict[str, Any] = field(default_factory=dict)


def _build_middleware_request(
    tool_name: str,
    args: dict[str, Any],
) -> Any:
    return _Req(tool_call={"name": tool_name, "args": args})


# ─────────────────────────────────────────────────────────────────────
# Middleware behaviour
# ─────────────────────────────────────────────────────────────────────


def test_middleware_rewrites_write_file_path() -> None:
    """The BUG-2026-08-19-003 incident shape: write_file with an
    absolute ``/home/wend/boundless_kg/workspace/...`` path gets the
    prefix stripped before the handler runs."""
    mw = PathNormalizeMiddleware()
    captured: dict[str, Any] = {}

    def handler(req: Any) -> Any:
        captured["args"] = req.tool_call["args"]
        return "ok"

    request = _build_middleware_request(
        "write_file",
        {"file_path": "/home/wend/boundless_kg/workspace/AGENTS.md", "content": "x"},
    )
    mw.wrap_tool_call(request, handler=handler)
    assert captured["args"]["file_path"] == "AGENTS.md"


def test_middleware_rewrites_edit_file_path() -> None:
    """``edit_file`` uses the same gate."""
    mw = PathNormalizeMiddleware()
    captured: dict[str, Any] = {}

    def handler(req: Any) -> Any:
        captured["args"] = req.tool_call["args"]
        return "ok"

    request = _build_middleware_request(
        "edit_file",
        {
            "file_path": "/home/wend/boundless_kg/workspace/bugs.md",
            "old": "a",
            "new": "b",
        },
    )
    mw.wrap_tool_call(request, handler=handler)
    assert captured["args"]["file_path"] == "bugs.md"


def test_middleware_leaves_relative_path_alone() -> None:
    """Relative paths must not be touched (the most common case)."""
    mw = PathNormalizeMiddleware()
    captured: dict[str, Any] = {}

    def handler(req: Any) -> Any:
        captured["args"] = req.tool_call["args"]
        return "ok"

    request = _build_middleware_request(
        "write_file",
        {"file_path": "AGENTS.md", "content": "x"},
    )
    mw.wrap_tool_call(request, handler=handler)
    assert captured["args"]["file_path"] == "AGENTS.md"


def test_middleware_leaves_non_write_tool_alone() -> None:
    """``read_file`` / ``ls`` etc. must not be touched — the rewrite
    could break tool-specific path semantics."""
    mw = PathNormalizeMiddleware()
    captured: dict[str, Any] = {}

    def handler(req: Any) -> Any:
        captured["args"] = req.tool_call["args"]
        return "ok"

    request = _build_middleware_request(
        "read_file",
        {"file_path": "/home/wend/boundless_kg/workspace/AGENTS.md"},
    )
    mw.wrap_tool_call(request, handler=handler)
    assert captured["args"]["file_path"] == "/home/wend/boundless_kg/workspace/AGENTS.md"


def test_middleware_accepts_path_alias_keys() -> None:
    """Forward-compat: ``path`` / ``target_file`` / ``filepath`` keys
    should also get rewritten (mirrors write_claim's aliases)."""
    mw = PathNormalizeMiddleware()
    for key in ("path", "target_file", "filepath"):
        captured: dict[str, Any] = {}

        def handler(req: Any, _k: str = key) -> Any:
            captured["args"] = req.tool_call["args"]
            return "ok"

        request = _build_middleware_request(
            "write_file",
            {key: "/home/wend/boundless_kg/workspace/foo.md", "content": "x"},
        )
        mw.wrap_tool_call(request, handler=handler)
        assert captured["args"][key] == "foo.md", f"key {key!r} should be rewritten"


def test_middleware_skips_non_string_path_arg() -> None:
    """A non-string ``file_path`` (e.g. dict or None) is left alone —
    we can't safely rewrite it."""
    mw = PathNormalizeMiddleware()
    captured: dict[str, Any] = {}

    def handler(req: Any) -> Any:
        captured["args"] = req.tool_call["args"]
        return "ok"

    request = _build_middleware_request(
        "write_file",
        {"file_path": None, "content": "x"},
    )
    mw.wrap_tool_call(request, handler=handler)
    assert captured["args"]["file_path"] is None


def test_middleware_skips_write_call_without_path() -> None:
    """If there's no path-bearing arg, the middleware is a no-op."""
    mw = PathNormalizeMiddleware()
    captured: dict[str, Any] = {}

    def handler(req: Any) -> Any:
        captured["args"] = req.tool_call["args"]
        return "ok"

    request = _build_middleware_request(
        "write_file",
        {"content": "x"},
    )
    mw.wrap_tool_call(request, handler=handler)
    assert captured["args"] == {"content": "x"}


def test_middleware_logs_rewrite_at_info_level(caplog: pytest.LogCaptureFixture) -> None:
    """Each rewrite should leave a breadcrumb so future BUG-2026-08-19
    -style incidents can be traced from logs."""
    mw = PathNormalizeMiddleware()

    def handler(_req: Any) -> Any:
        return "ok"

    request = _build_middleware_request(
        "write_file",
        {"file_path": "/home/wend/boundless_kg/workspace/AGENTS.md", "content": "x"},
    )
    with caplog.at_level(logging.INFO, logger="src.agent.guard.path_normalize"):
        mw.wrap_tool_call(request, handler=handler)
    assert any(
        "PathNormalizeMiddleware" in rec.message and "AGENTS.md" in rec.message
        for rec in caplog.records
    )


def test_middleware_disabled_is_passthrough() -> None:
    """``enabled=False`` short-circuits — useful for golden tests
    where the model is allowed to pass absolute paths."""
    mw = PathNormalizeMiddleware(enabled=False)
    assert mw.enabled is False
    mw.set_enabled(True)
    assert mw.enabled is True

    captured: dict[str, Any] = {}

    def handler(req: Any) -> Any:
        captured["args"] = req.tool_call["args"]
        return "ok"

    mw2 = PathNormalizeMiddleware(enabled=False)
    request = _build_middleware_request(
        "write_file",
        {"file_path": "/home/wend/boundless_kg/workspace/AGENTS.md", "content": "x"},
    )
    mw2.wrap_tool_call(request, handler=handler)
    assert (
        captured["args"]["file_path"]
        == "/home/wend/boundless_kg/workspace/AGENTS.md"
    )


def test_middleware_async_hook_also_rewrites() -> None:
    """The async hook is what LangGraph actually uses."""
    mw = PathNormalizeMiddleware()
    captured: dict[str, Any] = {}

    async def handler(req: Any) -> Any:
        captured["args"] = req.tool_call["args"]
        return "ok"

    request = _build_middleware_request(
        "edit_file",
        {
            "file_path": "/home/wend/boundless_kg/workspace/foo.md",
            "old": "a",
            "new": "b",
        },
    )
    asyncio.run(mw.awrap_tool_call(request, handler=handler))
    assert captured["args"]["file_path"] == "foo.md"


def test_middleware_handles_request_without_tool_call_attr() -> None:
    """Defensive: an unexpected request shape (no ``tool_call`` attr)
    must not crash the middleware — it just no-ops."""

    mw = PathNormalizeMiddleware()

    def handler(_req: Any) -> Any:
        return "ok"

    class _Bad:
        pass

    # Must not raise.
    mw.wrap_tool_call(_Bad(), handler=handler)


def test_middleware_rewrites_nested_workspace_path() -> None:
    """The BUG-2026-08-19-003 actual symptom: deep paths inside the
    workspace tree get rewritten to the correct relative form."""
    mw = PathNormalizeMiddleware()
    captured: dict[str, Any] = {}

    def handler(req: Any) -> Any:
        captured["args"] = req.tool_call["args"]
        return "ok"

    request = _build_middleware_request(
        "write_file",
        {
            "file_path": (
                "/home/wend/boundless_kg/workspace/knowledge_bases/"
                "DeepSeek Harness/notes/Cordis框架/study_materials/"
                "cordis_quiz.html"
            ),
            "content": "x",
        },
    )
    mw.wrap_tool_call(request, handler=handler)
    assert captured["args"]["file_path"] == (
        "knowledge_bases/DeepSeek Harness/notes/Cordis框架/"
        "study_materials/cordis_quiz.html"
    )