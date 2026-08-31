"""Tests for :meth:`DualSearchClient.search_one` and :meth:`has_backend`.

These are the single-backend (no-fallback) hooks used by
``kg_search_resources`` when the user passes ``channel=`` and by
``kg_set_search_channel`` to reject unconfigured channels BEFORE
persisting the preference.

Contract under test
-------------------

* ``has_backend(source)`` returns True iff the corresponding client is
  injected (bocha needs API key, ddg needs proxy, mmx is always on).
* ``search_one(source, query, num_results=...)`` calls exactly ONE
  backend, never loops the chain, and never touches the preference
  store (no promotion, no quarantine).
* Failure modes (unknown source, unconfigured backend, raise,
  timeout, empty result) all collapse to ``[]`` — no fallback.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.infrastructure.search.base import SearchResult
from src.infrastructure.search.mmx import DualSearchClient
from src.infrastructure.search.preference import (
    BACKEND_BOCHA,
    BACKEND_DDG,
    BACKEND_MMX,
    SearchPreferenceStore,
)


# ---------------------------------------------------------------------------
# Stub backend — same as test_search_preference.py but kept inline so this
# file is self-contained.
# ---------------------------------------------------------------------------


class _StubBackend:
    """Minimal AsyncSearchClient for testing."""

    def __init__(
        self,
        source: str,
        *,
        returns: list[SearchResult] | None = None,
        raises: Exception | None = None,
        sleep_for: float = 0.0,
    ) -> None:
        self.source = source
        self.returns = returns or []
        self.raises = raises
        self.sleep_for = sleep_for
        self.call_count = 0

    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        self.call_count += 1
        if self.sleep_for:
            await asyncio.sleep(self.sleep_for)
        if self.raises is not None:
            raise self.raises
        return self.returns


def _make(
    *,
    ddg: _StubBackend | None = None,
    mmx: _StubBackend | None = None,
    bocha: _StubBackend | None = None,
    proxy: str = "",
    adaptive: bool = True,
    pref: SearchPreferenceStore | None = None,
) -> DualSearchClient:
    """Build a DualSearchClient with stubbed backends."""
    if ddg is None and mmx is None and bocha is None:
        mmx = _StubBackend(BACKEND_MMX, returns=[SearchResult("m", "https://m", "", "mmx")])
    return DualSearchClient(
        ddg=ddg,
        mmx=mmx,
        bocha=bocha,
        proxy=proxy,
        adaptive=adaptive,
        preference_store=pref,
    )


# ---------------------------------------------------------------------------
# has_backend()
# ---------------------------------------------------------------------------


async def test_has_backend_mmx_when_injected() -> None:
    mmx = _StubBackend(BACKEND_MMX)
    client = _make(mmx=mmx)
    assert client.has_backend(BACKEND_MMX) is True


async def test_has_backend_ddg_requires_proxy() -> None:
    ddg = _StubBackend(BACKEND_DDG)
    client_no_proxy = _make(ddg=ddg, proxy="")  # explicit no-proxy
    assert client_no_proxy.has_backend(BACKEND_DDG) is False

    client_with_proxy = _make(ddg=ddg, proxy="http://127.0.0.1:7890")
    assert client_with_proxy.has_backend(BACKEND_DDG) is True


async def test_has_backend_bocha_requires_injection() -> None:
    mmx = _StubBackend(BACKEND_MMX)
    client_no_bocha = _make(mmx=mmx, bocha=None)
    assert client_no_bocha.has_backend(BACKEND_BOCHA) is False

    bocha = _StubBackend(BACKEND_BOCHA)
    client_with_bocha = _make(mmx=mmx, bocha=bocha)
    assert client_with_bocha.has_backend(BACKEND_BOCHA) is True


async def test_has_backend_rejects_unknown_name() -> None:
    mmx = _StubBackend(BACKEND_MMX)
    client = _make(mmx=mmx)
    assert client.has_backend("bogus") is False
    assert client.has_backend("") is False


# ---------------------------------------------------------------------------
# search_one() — happy path
# ---------------------------------------------------------------------------


async def test_search_one_returns_results_from_chosen_backend() -> None:
    """A configured backend returns its results verbatim."""
    payload = [
        SearchResult("a", "https://a", "snippet a", "bocha"),
        SearchResult("b", "https://b", "snippet b", "bocha"),
    ]
    bocha = _StubBackend(BACKEND_BOCHA, returns=payload)
    mmx = _StubBackend(BACKEND_MMX, returns=[SearchResult("wrong", "x", "", "mmx")])
    client = _make(bocha=bocha, mmx=mmx)

    out = await client.search_one(BACKEND_BOCHA, "q", num_results=5)
    assert out == payload
    assert bocha.call_count == 1
    # mmx was NOT called — the chain was bypassed.
    assert mmx.call_count == 0


# ---------------------------------------------------------------------------
# search_one() — failure modes collapse to []
# ---------------------------------------------------------------------------


async def test_search_one_returns_empty_for_unknown_source() -> None:
    mmx = _StubBackend(BACKEND_MMX, returns=[SearchResult("m", "https://m", "", "mmx")])
    client = _make(mmx=mmx)

    out = await client.search_one("not-a-backend", "q")
    assert out == []
    assert mmx.call_count == 0


async def test_search_one_returns_empty_when_backend_unconfigured() -> None:
    """bocha=None means 'not injected' → search_one returns []."""
    mmx = _StubBackend(BACKEND_MMX, returns=[SearchResult("m", "https://m", "", "mmx")])
    client = _make(mmx=mmx, bocha=None)

    out = await client.search_one(BACKEND_BOCHA, "q")
    assert out == []
    assert mmx.call_count == 0  # didn't fall back


async def test_search_one_returns_empty_when_ddg_has_no_proxy() -> None:
    """DDG without proxy is 'unconfigured' (proxy=="" disables it)."""
    ddg = _StubBackend(BACKEND_DDG, returns=[SearchResult("d", "https://d", "", "duckduck")])
    mmx = _StubBackend(BACKEND_MMX)
    client = _make(ddg=ddg, mmx=mmx, proxy="")

    out = await client.search_one(BACKEND_DDG, "q")
    assert out == []
    assert ddg.call_count == 0
    assert mmx.call_count == 0


async def test_search_one_returns_empty_when_backend_raises(
    tmp_path: Path,
) -> None:
    pref = SearchPreferenceStore(tmp_path)
    pref.load()
    mmx = _StubBackend(BACKEND_MMX, raises=ConnectionError("mmx down"))
    client = _make(mmx=mmx, pref=pref)

    out = await client.search_one(BACKEND_MMX, "q")
    assert out == []
    # CRITICAL: failure did NOT trigger quarantine.
    assert pref.is_quarantined(BACKEND_MMX) is False
    assert pref.preferred_source() == ""
    # CRITICAL: failure did NOT promote anything either.
    assert pref.summary()["backends"][BACKEND_MMX]["failure_count"] == 0


async def test_search_one_returns_empty_when_backend_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend hangs longer than its per-backend timeout → []. No quarantine."""
    pref = SearchPreferenceStore(tmp_path)
    pref.load()
    # Use a sleep longer than MMX_TIMEOUT_SEC (12s default).
    mmx = _StubBackend(BACKEND_MMX, sleep_for=20.0)
    client = _make(mmx=mmx, pref=pref)
    # Shrink the timeout to make the test fast.
    monkeypatch.setattr(DualSearchClient, "MMX_TIMEOUT_SEC", 0.05)

    out = await client.search_one(BACKEND_MMX, "q")
    assert out == []
    assert pref.is_quarantined(BACKEND_MMX) is False
    assert pref.summary()["backends"][BACKEND_MMX]["failure_count"] == 0


async def test_search_one_returns_empty_when_backend_returns_empty() -> None:
    """Backend reachable but returned [] → search_one returns [] (not an error)."""
    mmx = _StubBackend(BACKEND_MMX, returns=[])
    client = _make(mmx=mmx)

    out = await client.search_one(BACKEND_MMX, "q")
    assert out == []
    assert mmx.call_count == 1


# ---------------------------------------------------------------------------
# search_one() — does NOT mutate preference store
# ---------------------------------------------------------------------------


async def test_search_one_does_not_promote_successful_backend(
    tmp_path: Path,
) -> None:
    """A successful search_one must NOT change preferred."""
    pref = SearchPreferenceStore(tmp_path)
    pref.load()
    await pref.record_success(BACKEND_BOCHA)  # set prior preference
    assert pref.preferred_source() == BACKEND_BOCHA

    mmx = _StubBackend(BACKEND_MMX, returns=[SearchResult("m", "https://m", "", "mmx")])
    client = _make(mmx=mmx, pref=pref)

    out = await client.search_one(BACKEND_MMX, "q")
    assert len(out) == 1
    # The user's explicit override MUST NOT overwrite the auto-learned
    # preference — that's what kg_set_search_channel is for.
    assert pref.preferred_source() == BACKEND_BOCHA


async def test_search_one_does_not_increment_success_counter(
    tmp_path: Path,
) -> None:
    """Success counters live in the auto fallback chain, not in search_one."""
    pref = SearchPreferenceStore(tmp_path)
    pref.load()
    initial = pref.summary()["backends"][BACKEND_MMX]["success_count"]

    mmx = _StubBackend(BACKEND_MMX, returns=[SearchResult("m", "https://m", "", "mmx")])
    client = _make(mmx=mmx, pref=pref)

    await client.search_one(BACKEND_MMX, "q")
    after = pref.summary()["backends"][BACKEND_MMX]["success_count"]
    assert after == initial


# ---------------------------------------------------------------------------
# search_one() — does not call other backends
# ---------------------------------------------------------------------------


async def test_search_one_calls_only_requested_backend() -> None:
    """Even if the requested backend fails, search_one must NOT try the next."""
    ddg = _StubBackend(BACKEND_DDG, returns=[SearchResult("d", "https://d", "", "duckduck")])
    mmx = _StubBackend(BACKEND_MMX, raises=RuntimeError("mmx down"))
    bocha = _StubBackend(BACKEND_BOCHA, returns=[SearchResult("b", "https://b", "", "bocha")])
    client = _make(ddg=ddg, mmx=mmx, bocha=bocha, proxy="http://x")

    out = await client.search_one(BACKEND_MMX, "q")
    assert out == []
    assert mmx.call_count == 1
    # The other two backends MUST NOT have been attempted.
    assert ddg.call_count == 0
    assert bocha.call_count == 0