"""Unit tests for the today's-date prompt injection."""

from __future__ import annotations

from datetime import date

import pytest
from langchain_core.messages import SystemMessage

from src.agent.date_prompt import DateContextMiddleware, today_fragment


# ── today_fragment ───────────────────────────────────────────────────────


def test_fragment_has_year_month_day() -> None:
    frag = today_fragment(date(2026, 8, 3))
    assert "2026 年 8 月 3 日" in frag
    assert "2026-08-03" in frag


def test_fragment_weekday_is_monday_indexed() -> None:
    # 2026-08-03 is a Monday.
    assert "星期一" in today_fragment(date(2026, 8, 3))
    assert "星期日" in today_fragment(date(2026, 8, 9))


def test_fragment_defaults_to_local_today() -> None:
    assert date.today().isoformat() in today_fragment()


# ── DateContextMiddleware dispatch ───────────────────────────────────────


class _StubRequest:
    """Minimal stand-in for langchain's ModelRequest."""

    def __init__(self, system_message) -> None:
        self.state = {"messages": []}
        self.system_message = system_message
        self.override_calls = []

    def override(self, *, system_message):
        self.override_calls.append(system_message)
        return _StubRequest(system_message)


def _text_of(msg: SystemMessage) -> str:
    return "".join(
        b.get("text", "") for b in msg.content_blocks if b.get("type") == "text"
    )


def test_middleware_appends_date() -> None:
    req = _StubRequest(SystemMessage(content="你是 BoundlessKG。"))
    mw = DateContextMiddleware()

    mw.wrap_model_call(req, lambda r: "ok")

    assert len(req.override_calls) == 1
    text = _text_of(req.override_calls[0])
    # Original prompt preserved, date appended — not clobbered.
    assert "你是 BoundlessKG。" in text
    assert "## 当前日期" in text
    assert date.today().isoformat() in text


def test_middleware_handles_no_system_message() -> None:
    req = _StubRequest(None)
    mw = DateContextMiddleware()

    mw.wrap_model_call(req, lambda r: "ok")

    assert "## 当前日期" in _text_of(req.override_calls[0])


def test_middleware_passes_augmented_request_to_handler() -> None:
    req = _StubRequest(SystemMessage(content="base"))
    mw = DateContextMiddleware()

    seen = []
    mw.wrap_model_call(req, lambda r: seen.append(r))

    # The handler must receive the *new* request, not the original.
    assert seen and seen[0] is not req
    assert "## 当前日期" in _text_of(seen[0].system_message)


@pytest.mark.asyncio
async def test_async_middleware_appends_date() -> None:
    req = _StubRequest(SystemMessage(content="base"))
    mw = DateContextMiddleware()

    async def handler(r):
        return "ok"

    out = await mw.awrap_model_call(req, handler)

    assert out == "ok"
    assert "## 当前日期" in _text_of(req.override_calls[0])
