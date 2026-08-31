"""Tests for ``src.agent.guard.WriteClaimMiddleware`` (BUG-005 L4 gate).

Coverage:
  * Pure-function scanner: claims / non-claims / authorizations.
  * Middleware end-to-end: tool-call records paths, model-call rewrites
    unauthorised claims, tracker resets between turns.
  * Edge cases: nested paths, absolute vs relative, negation, sentence
    boundaries, backtick-wrapped paths, multi-claim dedup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.agent.guard import (
    WriteClaimMiddleware,
    WriteClaimTracker,
    current_turn_writes,
    get_tracker,
    reset_tracker,
    scan_for_unauthorized_claim,
)


# ─────────────────────────────────────────────────────────────────────
# Pure-function scanner
# ─────────────────────────────────────────────────────────────────────


def test_scanner_returns_empty_for_empty_text() -> None:
    assert scan_for_unauthorized_claim("", authorized=set()) == []


def test_scanner_ignores_text_with_no_completion_claim() -> None:
    """Reading / inspecting a file is not a write claim."""
    assert scan_for_unauthorized_claim(
        "请阅读 bugs.md 以确认内容", authorized=set()
    ) == []


def test_scanner_flags_bare_path_with_claim_phrase() -> None:
    """The BUG-004 incident shape: 「已写入 bugs.md」 with no actual write."""
    assert scan_for_unauthorized_claim(
        "已写入 bugs.md", authorized=set()
    ) == [("已写入", "bugs.md")]


def test_scanner_does_not_flag_authorised_path() -> None:
    assert scan_for_unauthorized_claim(
        "已写入 AGENTS.md", authorized={"AGENTS.md"}
    ) == []


def test_scanner_normalises_absolute_vs_relative() -> None:
    """deepagents writes to ``/AGENTS.md``; users say ``AGENTS.md``."""
    assert scan_for_unauthorized_claim(
        "已写入 /AGENTS.md", authorized={"AGENTS.md"}
    ) == []
    assert scan_for_unauthorized_claim(
        "已写入 AGENTS.md", authorized={"/AGENTS.md"}
    ) == []


def test_scanner_unwraps_backtick_wrapped_paths() -> None:
    r"""``\`AGENTS.md\``` should match without the backticks ending up
    in the surfaced path (which would confuse the user / log)."""
    claims = scan_for_unauthorized_claim(
        "已写入 `AGENTS.md`", authorized=set()
    )
    assert ("已写入", "AGENTS.md") in claims  # backticks stripped


def test_scanner_handles_nested_paths() -> None:
    assert scan_for_unauthorized_claim(
        "已写入 knowledge_bases/.agent_memory/bugs.md",
        authorized={"knowledge_bases/.agent_memory/bugs.md"},
    ) == []
    assert scan_for_unauthorized_claim(
        "已写入 knowledge_bases/.agent_memory/bugs.md",
        authorized=set(),
    ) == [("已写入", "knowledge_bases/.agent_memory/bugs.md")]


def test_scanner_handles_multiple_paths_in_one_claim() -> None:
    """「已写入 foo.md 和 bar.md」 — both checked; foo authorised, bar not."""
    claims = scan_for_unauthorized_claim(
        "已写入 foo.md 和 bar.md", authorized={"foo.md"}
    )
    assert claims == [("已写入", "bar.md")]


def test_scanner_dedups_across_multiple_claims() -> None:
    """「已写入 a.md，已写入 b.md」 — b is unauthorised and only reported once."""
    claims = scan_for_unauthorized_claim(
        "已写入 a.md，已写入 b.md", authorized={"a.md"}
    )
    assert claims == [("已写入", "b.md")]


def test_scanner_stops_at_sentence_boundary() -> None:
    """A second mention past a full stop should NOT be attributed to the
    earlier claim — it's a separate statement."""
    claims = scan_for_unauthorized_claim(
        "已写入 bugs.md。后续我们会在 README.md 里说明更多细节。",
        authorized=set(),
    )
    assert claims == [("已写入", "bugs.md")]


def test_scanner_respects_negation_tokens() -> None:
    """「已写入 bugs.md，AGENTS.md 还没动」 — AGENTS.md is a negation, not a claim."""
    claims = scan_for_unauthorized_claim(
        "已写入 bugs.md，AGENTS.md 还没动", authorized=set()
    )
    assert claims == [("已写入", "bugs.md")]


def test_scanner_supports_english_claim_phrases() -> None:
    assert scan_for_unauthorized_claim(
        "wrote bugs.md", authorized=set()
    ) == [("wrote ", "bugs.md")]


def test_scanner_no_path_after_claim_phrase_does_not_trigger() -> None:
    assert scan_for_unauthorized_claim(
        "已写入完成！", authorized=set()
    ) == []


# ─────────────────────────────────────────────────────────────────────
# Middleware: tracker lifecycle + content rewriting
# ─────────────────────────────────────────────────────────────────────


@dataclass
class _FakeAIMessage:
    """Stand-in for langchain_core AIMessage — only ``.content`` matters here."""
    content: str = ""


@dataclass
class _FakeModelResponse:
    """Stand-in for langchain_core ModelResponse — only ``.result`` matters."""
    result: list[_FakeAIMessage] = field(default_factory=list)
    structured_response: Any = None


def _build_middleware_request(
    tool_name: str,
    args: dict[str, Any],
) -> Any:
    """Build a minimal ToolCallRequest-shaped object for unit tests."""
    @dataclass
    class _Req:
        tool_call: dict[str, Any] = field(
            default_factory=lambda: {"name": tool_name, "args": args}
        )

    return _Req()


def _build_middleware_response(text: str) -> Any:
    return _FakeModelResponse(result=[_FakeAIMessage(content=text)])


@pytest.fixture(autouse=True)
def _reset_tracker_between_tests() -> None:
    """Each test starts with a clean tracker so per-turn state doesn't leak."""
    reset_tracker()
    yield
    reset_tracker()


def test_tool_call_records_path_on_successful_write() -> None:
    """Successful ``write_file`` records the path in the tracker.

    BUG-2026-08-19-003 修复后：``verify_on_disk=True`` 会要求磁盘上
    真的有文件才授权；这里用 ``verify_on_disk=False`` 显式绕开，因为
    这个测试只关心 BUG-005 的写声明拦截主路径。专门校验见
    ``test_post_write_verify_*`` 系列。
    """
    mw = WriteClaimMiddleware(verify_on_disk=False)
    request = _build_middleware_request(
        "write_file", {"file_path": "AGENTS.md", "content": "x"}
    )

    @dataclass
    class _ToolOK:
        content: str = "ok"

    mw._wrap_tool_call_sync(
        request,
        handler=lambda _req: _ToolOK(),
    )
    assert "agents.md" in current_turn_writes()


def test_tool_call_skips_failed_writes() -> None:
    """A write that returned an error-shaped payload should NOT count."""
    mw = WriteClaimMiddleware()
    request = _build_middleware_request(
        "write_file", {"file_path": "AGENTS.md", "content": "x"}
    )

    @dataclass
    class _ToolErr:
        content: str = '{"error": "permission_denied"}'

    mw._wrap_tool_call_sync(
        request,
        handler=lambda _req: _ToolErr(),
    )
    assert "agents.md" not in current_turn_writes()


def test_non_write_tool_call_does_not_record_path() -> None:
    """Only ``write_file`` / ``edit_file`` feed the tracker."""
    mw = WriteClaimMiddleware()
    request = _build_middleware_request(
        "read_file", {"file_path": "AGENTS.md"}
    )

    @dataclass
    class _ToolOK:
        content: str = "ok"

    mw._wrap_tool_call_sync(
        request,
        handler=lambda _req: _ToolOK(),
    )
    assert current_turn_writes() == set()


def test_model_call_with_unauthorised_claim_rewrites_content() -> None:
    """End-to-end: claim about a path that was never written triggers
    a warning appended to the AIMessage."""
    mw = WriteClaimMiddleware()
    response = _build_middleware_response("已写入 bugs.md")
    out = mw._gate_response(response)
    content = out.result[0].content
    assert content.startswith("已写入 bugs.md")
    assert "WriteClaim gate" in content
    assert "bugs.md" in content


def test_model_call_with_authorised_claim_passes_through() -> None:
    """If the model wrote the path earlier in the same turn, the claim
    is left untouched."""
    reset_tracker()
    get_tracker().written_paths.add("bugs.md")
    mw = WriteClaimMiddleware()
    response = _build_middleware_response("已写入 bugs.md")
    out = mw._gate_response(response)
    assert out.result[0].content == "已写入 bugs.md"


def test_model_call_resets_tracker_when_no_claim_present() -> None:
    """Without a claim, the tracker is cleared so a stale write from
    an earlier turn doesn't leak in."""
    reset_tracker()
    get_tracker().written_paths.add("AGENTS.md")
    mw = WriteClaimMiddleware()
    mw._gate_response(_build_middleware_response("我已完成任务"))
    # The next turn starts with an empty tracker.
    assert current_turn_writes() == set()


def test_model_call_disabled_middleware_is_passthrough() -> None:
    """``enabled=False`` short-circuits the public hook (used by golden
    tests that want the model free to claim anything).  The internal
    :meth:`_gate_response` is intentionally NOT gated by ``enabled``
    — disabling happens one layer up in ``awrap_model_call`` so that
    the helper can be unit-tested in isolation.
    """
    mw = WriteClaimMiddleware(enabled=False)
    assert mw.enabled is False
    mw.set_enabled(True)
    assert mw.enabled is True


def test_disabled_middleware_short_circuits_in_real_hook() -> None:
    """The actual ``awrap_model_call`` short-circuits before touching
    the AIMessage when ``enabled`` is False."""
    mw = WriteClaimMiddleware(enabled=False)

    async def handler(_req: Any) -> Any:
        return _build_middleware_response("已写入 bugs.md")

    import asyncio
    request = object()
    out = asyncio.run(mw.awrap_model_call(request, handler))
    assert "WriteClaim gate" not in out.result[0].content


def test_async_tool_call_records_path() -> None:
    """``awrap_tool_call`` (the path LangGraph actually uses) records
    paths the same way the sync wrapper does.

    BUG-2026-08-19-003 修复后：默认 verify_on_disk=True 会拒绝把
    不存在于磁盘上的路径加进 tracker；这里用 ``verify_on_disk=False``
    显式绕开（因为这个测试只关心 BUG-005 的写声明拦截链路），专用
    验证逻辑见 ``test_post_write_verify_*``。
    """
    import asyncio
    mw = WriteClaimMiddleware(verify_on_disk=False)
    request = _build_middleware_request(
        "edit_file", {"file_path": "knowledge_bases/foo.md", "old": "a", "new": "b"}
    )

    @dataclass
    class _ToolOK:
        content: str = "ok"

    async def handler(_req: Any) -> Any:
        return _ToolOK()

    asyncio.run(mw.awrap_tool_call(request, handler))
    assert "knowledge_bases/foo.md" in current_turn_writes()


def test_full_turn_flow_with_tracker_reset() -> None:
    """Two turns; each turn's write only authorises its own claim.

    BUG-2026-08-19-003：同上，显式 ``verify_on_disk=False`` 以避免
    ``a.md`` 在磁盘上不存在触发新校验。
    """
    import asyncio
    mw = WriteClaimMiddleware(verify_on_disk=False)

    # Turn 1: model writes a.md, then claims a.md → authorised.
    reset_tracker()
    t1_request = _build_middleware_request("write_file", {"file_path": "a.md"})

    @dataclass
    class _OK:
        content: str = "ok"

    async def write_handler(_req: Any) -> Any:
        return _OK()

    asyncio.run(mw.awrap_tool_call(t1_request, handler=write_handler))

    async def model_handler_1(_req: Any) -> Any:
        return _build_middleware_response("已写入 a.md")

    out_1 = asyncio.run(mw.awrap_model_call(object(), handler=model_handler_1))
    assert out_1.result[0].content == "已写入 a.md"

    # Turn 2: model didn't write anything but claims b.md → blocked.
    async def model_handler_2(_req: Any) -> Any:
        return _build_middleware_response("已写入 b.md")

    out_2 = asyncio.run(mw.awrap_model_call(object(), handler=model_handler_2))
    assert "WriteClaim gate" in out_2.result[0].content


# ─────────────────────────────────────────────────────────────────────
# BUG-2026-08-19-003: write_file returns success but file is not on disk
# ─────────────────────────────────────────────────────────────────────


class _StubBackend:
    """Test stub mimicking deepagents ``FilesystemBackend._resolve_path``."""

    def __init__(self, root: str, *, exists: bool) -> None:
        self.cwd = Path(root)
        self._exists = exists

    def _resolve_path(self, key: str) -> Path:
        vpath = key if key.startswith("/") else "/" + key
        return (self.cwd / vpath.lstrip("/")).resolve()

    # The middleware doesn't read this — the helper is here so tests can
    # decide between "exists on disk" and "phantom write" cases without
    # touching the real FilesystemBackend singleton.
    def file_should_exist(self) -> bool:
        return self._exists


def test_post_write_verify_blocks_phantom_writes(tmp_path: Path) -> None:
    """``write_file`` returns success but ``Path.exists()`` is False →
    path must NOT be added to tracker (otherwise「已写入」would be
    treated as authorised and bypass the L4 gate)."""
    import asyncio

    backend = _StubBackend(str(tmp_path), exists=False)
    mw = WriteClaimMiddleware(verify_on_disk=True, backend=backend)
    reset_tracker()

    request = _build_middleware_request(
        "write_file", {"file_path": "phantom.md"}
    )

    @dataclass
    class _ToolOK:
        content: str = "Updated file phantom.md"

    async def handler(_req: Any) -> Any:
        return _ToolOK()

    asyncio.run(mw.awrap_tool_call(request, handler))
    # 关键断言：磁盘无文件 → tracker 不收
    assert "phantom.md" not in current_turn_writes()


def test_post_write_verify_passes_real_writes(tmp_path: Path) -> None:
    """正向路径：磁盘上真的有文件 → tracker 正常收录。"""
    import asyncio

    real = tmp_path / "real.md"
    real.write_text("hello", encoding="utf-8")

    backend = _StubBackend(str(tmp_path), exists=True)
    mw = WriteClaimMiddleware(verify_on_disk=True, backend=backend)
    reset_tracker()

    request = _build_middleware_request(
        "write_file", {"file_path": "real.md"}
    )

    @dataclass
    class _ToolOK:
        content: str = "Updated file real.md"

    async def handler(_req: Any) -> Any:
        return _ToolOK()

    asyncio.run(mw.awrap_tool_call(request, handler))
    assert "real.md" in current_turn_writes()


def test_post_write_verify_disabled() -> None:
    """``verify_on_disk=False`` 时，即使磁盘无文件也照常授权（向后兼容）。"""
    import asyncio

    mw = WriteClaimMiddleware(verify_on_disk=False)
    reset_tracker()
    request = _build_middleware_request(
        "write_file", {"file_path": "does-not-matter.md"}
    )

    @dataclass
    class _ToolOK:
        content: str = "ok"

    async def handler(_req: Any) -> Any:
        return _ToolOK()

    asyncio.run(mw.awrap_tool_call(request, handler))
    assert "does-not-matter.md" in current_turn_writes()