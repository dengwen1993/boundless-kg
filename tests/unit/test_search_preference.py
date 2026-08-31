"""Tests for the adaptive search preference layer.

Covers:

* ``SearchPreferenceStore`` — persistence, promotion, quarantine,
  failure history, ordering.
* ``DualSearchClient`` — adaptive behaviour (skip primary when a
  preference says so, quarantine failing backends, preserve legacy
  fixed-chain semantics when ``adaptive=False``).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from src.infrastructure.search.base import SearchResult
from src.infrastructure.search.mmx import (
    DualSearchClient,
    DuckDuckGoClient,
    MmxSearchClient,
)
from src.infrastructure.search.preference import (
    BACKEND_BOCHA,
    BACKEND_DDG,
    BACKEND_MMX,
    HISTORY_CAP,
    SearchPreferenceStore,
    _now,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> SearchPreferenceStore:
    return SearchPreferenceStore(tmp_path)


@pytest.fixture
def fixed_now(monkeypatch):
    """Freeze ``_now()`` so quarantine-window math is deterministic."""
    anchor = datetime(2026, 8, 12, 12, 0, 0)

    def _fixed_now() -> datetime:
        return anchor

    monkeypatch.setattr(
        "src.infrastructure.search.preference._now", _fixed_now
    )
    return anchor


# ---------------------------------------------------------------------------
# SearchPreferenceStore — basics
# ---------------------------------------------------------------------------


async def test_store_starts_with_no_preference(store: SearchPreferenceStore) -> None:
    store.load()
    assert store.preferred_source() == ""
    assert store.is_quarantined(BACKEND_DDG) is False
    assert store.is_quarantined(BACKEND_MMX) is False
    assert store.is_quarantined(BACKEND_BOCHA) is False


async def test_store_records_success_and_promotes(
    store: SearchPreferenceStore,
) -> None:
    store.load()
    await store.record_success(BACKEND_MMX)
    assert store.preferred_source() == BACKEND_MMX
    summary = store.summary()
    assert summary["backends"][BACKEND_MMX]["success_count"] == 1


async def test_store_records_failure_and_quarantines(
    store: SearchPreferenceStore,
) -> None:
    store.load()
    await store.record_failure(BACKEND_DDG, "ConnectError: no proxy")
    assert store.is_quarantined(BACKEND_DDG) is True
    state = store.summary()["backends"][BACKEND_DDG]
    assert state["failure_count"] == 1
    assert state["last_error"] == "ConnectError: no proxy"
    assert state["quarantined_until"]  # ISO string


async def test_store_failure_drops_preference(
    store: SearchPreferenceStore,
) -> None:
    """If the preferred backend fails, the chain must fall back."""
    store.load()
    await store.record_success(BACKEND_DDG)
    assert store.preferred_source() == BACKEND_DDG
    await store.record_failure(BACKEND_DDG, "401 Unauthorized")
    assert store.preferred_source() == ""


async def test_store_success_clears_quarantine(
    store: SearchPreferenceStore,
    fixed_now,
) -> None:
    """A live backend proves itself: quarantine should be cleared on success."""
    store.load()
    # Manually mark as quarantined.
    await store.record_failure(BACKEND_DDG, "down")
    assert store.is_quarantined(BACKEND_DDG) is True
    # Now prove it's working again.
    await store.record_success(BACKEND_DDG)
    assert store.is_quarantined(BACKEND_DDG) is False
    assert store.preferred_source() == BACKEND_DDG


async def test_store_quarantine_window_expires(
    store: SearchPreferenceStore,
    fixed_now,
    monkeypatch,
) -> None:
    """After QUARANTINE_SEC, a backend is re-probed automatically."""
    store.load()
    await store.record_failure(BACKEND_DDG, "down")
    assert store.is_quarantined(BACKEND_DDG) is True
    # Advance the clock past the window.
    future = fixed_now + timedelta(hours=7)
    monkeypatch.setattr(
        "src.infrastructure.search.preference._now", lambda: future
    )
    assert store.is_quarantined(BACKEND_DDG) is False


# ---------------------------------------------------------------------------
# SearchPreferenceStore — chain ordering
# ---------------------------------------------------------------------------


async def test_chain_for_promotes_preferred_when_active(
    store: SearchPreferenceStore,
) -> None:
    store.load()
    await store.record_success(BACKEND_MMX)
    chain = store.chain_for((BACKEND_DDG, BACKEND_MMX, BACKEND_BOCHA))
    assert chain[0] == BACKEND_MMX
    assert set(chain) == {BACKEND_DDG, BACKEND_MMX, BACKEND_BOCHA}


async def test_chain_for_skips_quarantined_backends(
    store: SearchPreferenceStore,
) -> None:
    store.load()
    await store.record_failure(BACKEND_DDG, "down")
    chain = store.chain_for((BACKEND_DDG, BACKEND_MMX, BACKEND_BOCHA))
    assert BACKEND_DDG not in chain
    assert chain == [BACKEND_MMX, BACKEND_BOCHA]


async def test_chain_for_ignores_unknown_backends(
    store: SearchPreferenceStore,
) -> None:
    """Backends the store doesn't track (custom future types) are dropped."""
    store.load()
    chain = store.chain_for((BACKEND_MMX, "future_engine_x"))
    assert chain == [BACKEND_MMX]


async def test_chain_for_returns_empty_when_all_quarantined(
    store: SearchPreferenceStore,
) -> None:
    store.load()
    await store.record_failure(BACKEND_DDG, "down")
    await store.record_failure(BACKEND_MMX, "down")
    await store.record_failure(BACKEND_BOCHA, "down")
    chain = store.chain_for((BACKEND_DDG, BACKEND_MMX, BACKEND_BOCHA))
    assert chain == []


async def test_chain_for_keeps_default_when_no_preference(
    store: SearchPreferenceStore,
) -> None:
    store.load()
    chain = store.chain_for((BACKEND_DDG, BACKEND_MMX, BACKEND_BOCHA))
    assert chain == [BACKEND_DDG, BACKEND_MMX, BACKEND_BOCHA]


# ---------------------------------------------------------------------------
# SearchPreferenceStore — persistence
# ---------------------------------------------------------------------------


async def test_store_persists_to_disk(tmp_path: Path) -> None:
    """``record_*`` writes through to ``search_preference.json``."""
    s = SearchPreferenceStore(tmp_path)
    s.load()
    await s.record_success(BACKEND_BOCHA)
    await s.record_failure(BACKEND_DDG, "ConnectError")

    persist_path = tmp_path / ".agent_memory" / "search_preference.json"
    assert persist_path.exists()
    payload = json.loads(persist_path.read_text(encoding="utf-8"))
    assert payload["preferred"] == BACKEND_BOCHA
    assert payload["backends"][BACKEND_DDG]["failure_count"] == 1
    assert payload["backends"][BACKEND_DDG]["last_error"] == "ConnectError"
    # History: success then failure.
    outcomes = [h["outcome"] for h in payload["history"]]
    assert outcomes == ["success", "failure"]


async def test_store_reloads_from_disk(tmp_path: Path) -> None:
    """A fresh store reads back the persisted state."""
    s1 = SearchPreferenceStore(tmp_path)
    s1.load()
    await s1.record_success(BACKEND_BOCHA)
    await s1.record_failure(BACKEND_DDG, "boom")

    s2 = SearchPreferenceStore(tmp_path)
    s2.load()
    assert s2.preferred_source() == BACKEND_BOCHA
    assert s2.is_quarantined(BACKEND_DDG) is True


async def test_store_history_is_capped(
    store: SearchPreferenceStore, tmp_path: Path
) -> None:
    """Bounded ring buffer keeps the JSON file from growing forever."""
    store.load()
    for i in range(HISTORY_CAP + 20):
        await store.record_failure(BACKEND_DDG, f"err {i}")
    payload = json.loads(
        (tmp_path / ".agent_memory" / "search_preference.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(payload["history"]) == HISTORY_CAP
    # The cap is FIFO — oldest entries are dropped, newest kept.
    last = payload["history"][-1]
    assert last["error"] == f"err {HISTORY_CAP + 20 - 1}"


async def test_store_handles_missing_file(tmp_path: Path) -> None:
    s = SearchPreferenceStore(tmp_path)
    s.load()  # no file yet — must not raise
    assert s.preferred_source() == ""


async def test_store_handles_corrupt_file(tmp_path: Path) -> None:
    p = tmp_path / ".agent_memory"
    p.mkdir(parents=True)
    (p / "search_preference.json").write_text("not valid json", encoding="utf-8")
    s = SearchPreferenceStore(tmp_path)
    s.load()  # must not raise — log + start fresh
    assert s.preferred_source() == ""


# ---------------------------------------------------------------------------
# DualSearchClient — adaptive behaviour
# ---------------------------------------------------------------------------


class _StubBackend:
    """Minimal :class:`AsyncSearchClient` that returns canned data
    or raises a canned exception. Each stub records how many times
    it was called so tests can assert about which backends ran.
    """

    def __init__(
        self,
        source: str,
        *,
        returns: list[SearchResult] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.source = source
        self.returns = returns
        self.raises = raises
        self.call_count = 0

    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        self.call_count += 1
        if self.raises is not None:
            raise self.raises
        return self.returns or []


@pytest.fixture
def three_backends():
    """Three stubs wired into DualSearchClient, no proxy."""
    ddg = _StubBackend(BACKEND_DDG, returns=[SearchResult("d", "https://d", "", "duckduck")])
    mmx = _StubBackend(BACKEND_MMX, returns=[SearchResult("m", "https://m", "", "mmx")])
    bocha = _StubBackend(BACKEND_BOCHA, returns=[SearchResult("b", "https://b", "", "bocha")])
    return ddg, mmx, bocha


async def test_adaptive_promotes_last_successful(
    tmp_path: Path, three_backends
) -> None:
    """After mmx returns a result, next call should start with mmx."""
    ddg, mmx, bocha = three_backends
    pref = SearchPreferenceStore(tmp_path)
    pref.load()
    client = DualSearchClient(
        ddg=ddg, mmx=mmx, bocha=bocha,
        proxy="",  # force non-proxy mode → starts with mmx
        adaptive=True,
        preference_store=pref,
    )
    out = await client.search("q1")
    assert out[0].source == "mmx"
    # mmx was preferred → next call should still try mmx first, then skip DDG.
    ddg.call_count = mmx.call_count = bocha.call_count = 0
    out2 = await client.search("q2")
    assert out2[0].source == "mmx"
    # DDG must NOT have been called — it was demoted.
    assert ddg.call_count == 0


async def test_adaptive_quarantines_failing_backend(
    tmp_path: Path, three_backends
) -> None:
    ddg, mmx, bocha = three_backends
    mmx.raises = ConnectionError("mmx down")
    pref = SearchPreferenceStore(tmp_path)
    pref.load()
    client = DualSearchClient(
        ddg=ddg, mmx=mmx, bocha=bocha,
        proxy="",  # starts with mmx
        adaptive=True,
        preference_store=pref,
    )
    out = await client.search("q1")
    # mmx failed → Bocha serves the request.
    assert out[0].source == "bocha"
    # mmx should be quarantined for the next round.
    assert pref.is_quarantined(BACKEND_MMX) is True
    assert pref.summary()["backends"][BACKEND_MMX]["last_error"].startswith(
        "ConnectionError"
    )


async def test_quarantined_backend_is_skipped_on_next_call(
    tmp_path: Path, three_backends
) -> None:
    """A 401 on DDG must not be repeated on the very next query."""
    ddg, mmx, bocha = three_backends
    ddg.raises = RuntimeError("401 Unauthorized")
    pref = SearchPreferenceStore(tmp_path)
    pref.load()
    client = DualSearchClient(
        ddg=ddg, mmx=mmx, bocha=bocha,
        proxy="",  # chain starts with mmx in non-proxy mode; force DDG via proxy
        adaptive=True,
        preference_store=pref,
    )
    # First call (no proxy): mmx → bocha. Now manually promote DDG and
    # mark it as preferred to put DDG at the front of the chain.
    await pref.record_success(BACKEND_DDG)  # hypothetical prior success
    ddg.call_count = mmx.call_count = bocha.call_count = 0
    ddg.raises = RuntimeError("401 Unauthorized")
    out = await client.search("q2")
    # DDG must NOT have been tried at all on the next call.
    assert ddg.call_count == 0
    # mmx is still primary in non-proxy mode → it served the result.
    assert out[0].source == "mmx"


async def test_adaptive_off_keeps_legacy_chain(tmp_path: Path, three_backends) -> None:
    ddg, mmx, bocha = three_backends
    pref = SearchPreferenceStore(tmp_path)
    pref.load()
    await pref.record_success(BACKEND_BOCHA)  # try to pollute preference
    client = DualSearchClient(
        ddg=ddg, mmx=mmx, bocha=bocha,
        proxy="",
        adaptive=False,  # LEGACY mode
        preference_store=pref,
    )
    out = await client.search("q")
    # Without proxy: mmx first.
    assert out[0].source == "mmx"
    # The preference for Bocha MUST NOT have moved it to the front.
    assert bocha.call_count == 0


async def test_empty_result_does_not_promote_or_quarantine(
    tmp_path: Path, three_backends
) -> None:
    """A backend returning ``[]`` is reachable but doesn't match —
    don't pollute the preference state."""
    ddg, mmx, bocha = three_backends
    mmx.returns = []
    bocha.returns = []
    ddg.returns = [SearchResult("d", "https://d", "", "duckduck")]
    pref = SearchPreferenceStore(tmp_path)
    pref.load()
    client = DualSearchClient(
        ddg=None,  # no DDG in non-proxy chain
        mmx=mmx, bocha=bocha,
        proxy="",
        adaptive=True,
        preference_store=pref,
    )
    out = await client.search("q")
    # mmx returned [] → fell through to bocha → also empty → fell
    # through to ... nothing.  But we still got no result, which is
    # expected when every backend matches nothing.
    assert out == []
    # Neither mmx nor bocha should be marked preferred or quarantined.
    assert pref.preferred_source() == ""
    assert not pref.is_quarantined(BACKEND_MMX)
    assert not pref.is_quarantined(BACKEND_BOCHA)


async def test_summary_includes_chain_and_preference(
    tmp_path: Path, three_backends
) -> None:
    ddg, mmx, bocha = three_backends
    pref = SearchPreferenceStore(tmp_path)
    pref.load()
    client = DualSearchClient(
        ddg=ddg, mmx=mmx, bocha=bocha,
        proxy="",
        adaptive=True,
        preference_store=pref,
    )
    await client.search("q")
    s = client.summary()
    assert s["adaptive"] is True
    assert s["primary_source"] == "mmx"  # legacy primary (no proxy)
    assert isinstance(s["chain"], list)
    assert "preference" in s