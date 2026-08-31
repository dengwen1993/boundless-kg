"""LLM transport retry policy.

Bug we're guarding against
--------------------------

``logs/error.log`` (2026-08-02 23:42:03) — two consecutive
``kg_generate_note`` calls died on ``httpx.ReadError`` raised while
streaming the response body.  The connection to the provider dropped
mid-response on a long reasoning request; there was no retry, so a
transient blip surfaced to the user as a failed note.
"""

from __future__ import annotations

import httpx
import pytest

from src.infrastructure.llm._retry import is_transient, llm_retrying


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://example.com/v1/messages")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError("boom", request=req, response=resp)


# ── classification ──


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadError("conn dropped"),
        httpx.ReadTimeout("too slow"),
        httpx.ConnectError("no route"),
        httpx.RemoteProtocolError("bad framing"),
    ],
)
def test_transport_failures_are_transient(exc) -> None:
    assert is_transient(exc) is True


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_server_side_status_codes_are_transient(code: int) -> None:
    assert is_transient(_status_error(code)) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(code: int) -> None:
    """Retrying a bad key or bad payload just burns quota."""
    assert is_transient(_status_error(code)) is False


def test_unrelated_exceptions_are_not_retried() -> None:
    assert is_transient(ValueError("logic bug")) is False
    assert is_transient(KeyError("choices")) is False


# ── retry loop behaviour ──


async def test_retries_then_succeeds() -> None:
    calls = 0

    async for attempt in llm_retrying(attempts=3, label="test"):
        with attempt:
            calls += 1
            if calls < 3:
                raise httpx.ReadError("conn dropped")
            result = "ok"

    assert calls == 3
    assert result == "ok"


async def test_reraises_after_exhausting_attempts() -> None:
    calls = 0

    with pytest.raises(httpx.ReadError):
        async for attempt in llm_retrying(attempts=2, label="test"):
            with attempt:
                calls += 1
                raise httpx.ReadError("always down")

    assert calls == 2


async def test_non_transient_error_fails_on_first_attempt() -> None:
    calls = 0

    with pytest.raises(httpx.HTTPStatusError):
        async for attempt in llm_retrying(attempts=3, label="test"):
            with attempt:
                calls += 1
                raise _status_error(401)

    assert calls == 1, "a 401 must not be retried"
