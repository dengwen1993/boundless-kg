"""Shared retry policy for LLM HTTP calls.

Why
---

``httpx.ReadError`` / ``ReadTimeout`` / ``RemoteProtocolError`` are
*transient* — the TCP connection to the provider dropped mid-response,
usually because a long-running reasoning request idled past a proxy or
keep-alive timeout.  Before this module, a single such blip aborted the
whole call and surfaced to the user as a failed note generation
(``logs/error.log``: ``kg_generate_note failed … httpx.ReadError``).

Retrying is safe here: these errors occur *before* any response body is
parsed, and chat-completion calls are read-only with respect to our own
state, so re-issuing the request cannot double-write anything.

What is NOT retried
-------------------

* ``4xx`` responses — bad key, bad payload, content filter.  Retrying
  gets the identical failure and burns quota.
* ``429`` / ``5xx`` are retried, since they are load-related.
"""

from __future__ import annotations

import logging

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

#: Transport-level failures that mean "the connection broke", not
#: "the request was wrong".
_TRANSIENT_TRANSPORT = (
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)

#: Server-side status codes worth a second attempt.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

DEFAULT_ATTEMPTS = 3


def is_transient(exc: BaseException) -> bool:
    """True when ``exc`` is worth retrying."""
    if isinstance(exc, _TRANSIENT_TRANSPORT):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


def llm_retrying(
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    label: str = "llm",
) -> AsyncRetrying:
    """Build an ``AsyncRetrying`` for one LLM call site.

    Usage::

        async for attempt in llm_retrying(label="chat"):
            with attempt:
                resp = await client.post(...)
                resp.raise_for_status()

    Backoff is exponential (1s → 2s → 4s, capped at 10s) so a provider
    under load gets room to recover instead of being hammered.
    """

    def _before_sleep(state) -> None:
        exc = state.outcome.exception() if state.outcome else None
        logger.warning(
            "%s: transient LLM failure (%s: %s) — retry %d/%d in %.1fs",
            label,
            type(exc).__name__,
            exc,
            state.attempt_number,
            attempts,
            getattr(state.next_action, "sleep", 0.0),
        )

    return AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(is_transient),
        before_sleep=_before_sleep,
        reraise=True,
    )


__all__ = ["DEFAULT_ATTEMPTS", "is_transient", "llm_retrying"]
