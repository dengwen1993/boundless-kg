"""Tests for the user-facing search-channel preference tools.

Covers:

* :func:`kg_set_search_channel` — validates the requested channel,
  rejects unconfigured backends before touching the preference file,
  writes ``_preferred`` + clears any active quarantine.
* :func:`kg_clear_search_channel` — wipes ``_preferred`` so the
  adaptive fallback chain takes over again.
* :func:`kg_search_resources` ``channel=`` arg — single-backend
  dispatch with no fallback, no preference update, no quarantine.

The tools are exercised through ``.ainvoke({...})`` to match the
production path used by deepagents.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
# Stub backend (kept local — same shape as test_search_one.py / test_search_preference.py)
# ---------------------------------------------------------------------------


class _StubBackend:
    def __init__(
        self,
        source: str,
        *,
        returns: list[SearchResult] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.source = source
        self.returns = returns or []
        self.raises = raises
        self.call_count = 0

    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        self.call_count += 1
        if self.raises is not None:
            raise self.raises
        return self.returns


def _make_dual(
    *,
    ddg: _StubBackend | None = None,
    mmx: _StubBackend | None = None,
    bocha: _StubBackend | None = None,
    proxy: str = "",
    pref: SearchPreferenceStore | None = None,
) -> DualSearchClient:
    return DualSearchClient(
        ddg=ddg,
        mmx=mmx,
        bocha=bocha,
        proxy=proxy,
        adaptive=True,
        preference_store=pref,
    )


@pytest.fixture
def fresh_store(tmp_path: Path) -> SearchPreferenceStore:
    """A fresh SearchPreferenceStore rooted at ``tmp_path``."""
    s = SearchPreferenceStore(tmp_path)
    s.load()
    return s


@pytest.fixture
def inject_dependencies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """
    Patch the dependency accessors used by the three tools so we can
    inject a stub ``DualSearchClient`` and a fresh ``SearchPreferenceStore``
    without touching global singletons.

    Yields ``(client_getter, store_getter, store_ref, backends)`` so
    individual tests can pull handles to the stub backends.
    """
    from src.agent import dependencies

    store = SearchPreferenceStore(tmp_path)
    store.load()

    ddg = _StubBackend(BACKEND_DDG, returns=[SearchResult("d", "https://d", "", "duckduck")])
    mmx = _StubBackend(BACKEND_MMX, returns=[SearchResult("m", "https://m", "", "mmx")])
    bocha = _StubBackend(
        BACKEND_BOCHA, returns=[SearchResult("b", "https://b", "", "bocha")]
    )

    # Default: mmx + bocha wired, no proxy (ddg not configured).
    client = _make_dual(mmx=mmx, bocha=bocha, pref=store)

    def _client() -> DualSearchClient:
        return client

    def _store() -> SearchPreferenceStore:
        return store

    monkeypatch.setattr(dependencies, "get_search_client", _client)
    monkeypatch.setattr(dependencies, "get_search_preference_store", _store)
    # The two new tools import the same names from src.agent.tools.search_channel_tools,
    # so patch those too (they're separate symbols after import).
    from src.agent.tools import search_channel_tools
    monkeypatch.setattr(search_channel_tools, "get_search_client", _client)
    monkeypatch.setattr(search_channel_tools, "get_search_preference_store", _store)
    # resource_tools imports get_search_client too.
    from src.agent.tools import resource_tools
    monkeypatch.setattr(resource_tools, "get_search_client", _client)

    yield {
        "client": client,
        "store": store,
        "ddg": ddg,
        "mmx": mmx,
        "bocha": bocha,
    }


# ---------------------------------------------------------------------------
# kg_set_search_channel
# ---------------------------------------------------------------------------


async def test_set_channel_valid_persists_preference(
    inject_dependencies: dict[str, Any],
) -> None:
    from src.agent.tools import kg_set_search_channel

    store: SearchPreferenceStore = inject_dependencies["store"]
    assert store.preferred_source() == ""

    out = await kg_set_search_channel.ainvoke({"channel": "bocha"})

    assert isinstance(out, str)
    assert "✅" in out
    assert "'bocha'" in out
    assert store.preferred_source() == BACKEND_BOCHA


async def test_set_channel_rejects_unknown_name(
    inject_dependencies: dict[str, Any],
) -> None:
    from src.agent.tools import kg_set_search_channel

    store: SearchPreferenceStore = inject_dependencies["store"]

    out = await kg_set_search_channel.ainvoke({"channel": "google"})

    assert isinstance(out, str)
    assert "❌" in out
    assert "google" in out
    assert "duckduck" in out
    assert "mmx" in out
    assert "bocha" in out
    # Critical: bad name MUST NOT touch the preference.
    assert store.preferred_source() == ""


async def test_set_channel_rejects_empty_string(
    inject_dependencies: dict[str, Any],
) -> None:
    from src.agent.tools import kg_set_search_channel

    store: SearchPreferenceStore = inject_dependencies["store"]

    out = await kg_set_search_channel.ainvoke({"channel": ""})

    assert "❌" in out
    assert store.preferred_source() == ""


async def test_set_channel_rejects_unconfigured_backend(
    inject_dependencies: dict[str, Any],
) -> None:
    """DDG is unconfigured in the default fixture (no proxy) → must be rejected."""
    from src.agent.tools import kg_set_search_channel

    store: SearchPreferenceStore = inject_dependencies["store"]

    out = await kg_set_search_channel.ainvoke({"channel": "duckduck"})

    assert "❌" in out
    assert "duckduck" in out
    assert "BOCHA_API_KEY" not in out  # different hint
    assert "未修改" in out or "未" in out  # confirms it didn't write
    assert store.preferred_source() == ""


async def test_set_channel_clears_existing_quarantine(
    inject_dependencies: dict[str, Any],
) -> None:
    """User opted-in: any prior auto-quarantine on that backend is cleared."""
    from src.agent.tools import kg_set_search_channel

    store: SearchPreferenceStore = inject_dependencies["store"]
    # Pretend mmx was quarantined by an earlier auto-failure.
    await store.record_failure(BACKEND_MMX, "synthetic test failure")
    assert store.is_quarantined(BACKEND_MMX) is True

    out = await kg_set_search_channel.ainvoke({"channel": "mmx"})

    assert "✅" in out
    assert store.preferred_source() == BACKEND_MMX
    assert store.is_quarantined(BACKEND_MMX) is False


async def test_set_channel_normalises_case(
    inject_dependencies: dict[str, Any],
) -> None:
    """Capitalisation / whitespace don't trip validation."""
    from src.agent.tools import kg_set_search_channel

    store: SearchPreferenceStore = inject_dependencies["store"]

    out = await kg_set_search_channel.ainvoke({"channel": "  BOCHA  "})

    assert "✅" in out
    assert store.preferred_source() == BACKEND_BOCHA


# ---------------------------------------------------------------------------
# kg_clear_search_channel
# ---------------------------------------------------------------------------


async def test_clear_channel_resets_preference(
    inject_dependencies: dict[str, Any],
) -> None:
    from src.agent.tools import (
        kg_clear_search_channel,
        kg_set_search_channel,
    )

    store: SearchPreferenceStore = inject_dependencies["store"]
    await kg_set_search_channel.ainvoke({"channel": "bocha"})
    assert store.preferred_source() == BACKEND_BOCHA

    out = await kg_clear_search_channel.ainvoke({})

    assert "✅" in out
    assert "'bocha'" in out  # reports what was cleared
    assert store.preferred_source() == ""


async def test_clear_channel_when_already_empty(
    inject_dependencies: dict[str, Any],
) -> None:
    """No prior preference → return an info message, don't crash."""
    from src.agent.tools import kg_clear_search_channel

    store: SearchPreferenceStore = inject_dependencies["store"]
    assert store.preferred_source() == ""

    out = await kg_clear_search_channel.ainvoke({})

    assert "ℹ️" in out or "无需清除" in out
    assert store.preferred_source() == ""


# ---------------------------------------------------------------------------
# kg_search_resources(channel=...)
# ---------------------------------------------------------------------------


async def test_search_resources_with_channel_calls_only_that_backend(
    inject_dependencies: dict[str, Any],
) -> None:
    """channel=bocha → only bocha called; mmx untouched; no fallback."""
    from src.agent.tools import kg_search_resources

    bocha = inject_dependencies["bocha"]
    mmx = inject_dependencies["mmx"]
    bocha.call_count = mmx.call_count = 0

    out = await kg_search_resources.ainvoke({
        "domain": "test",
        "query": "hello",
        "channel": "bocha",
    })

    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "b"
    assert parsed[0]["url"] == "https://b"
    assert parsed[0]["domain"] == "test"
    # CRITICAL: mmx was NOT consulted.
    assert bocha.call_count == 1
    assert mmx.call_count == 0


async def test_search_resources_with_channel_does_not_persist_preference(
    inject_dependencies: dict[str, Any],
) -> None:
    """A successful inline channel call MUST NOT write to the preference file."""
    from src.agent.tools import kg_search_resources

    store: SearchPreferenceStore = inject_dependencies["store"]
    initial = pref_before = store.preferred_source()

    await kg_search_resources.ainvoke({
        "domain": "test",
        "query": "x",
        "channel": "bocha",
    })

    # Inline `channel=` is transient — only kg_set_search_channel persists.
    assert store.preferred_source() == initial


async def test_search_resources_with_unknown_channel_returns_empty(
    inject_dependencies: dict[str, Any],
) -> None:
    from src.agent.tools import kg_search_resources

    out = await kg_search_resources.ainvoke({
        "domain": "test",
        "query": "x",
        "channel": "google",
    })
    assert out == "[]"


async def test_search_resources_with_unconfigured_channel_returns_empty(
    inject_dependencies: dict[str, Any],
) -> None:
    """channel=duckduck is rejected (no proxy in fixture) — empty, no fallback."""
    from src.agent.tools import kg_search_resources

    bocha = inject_dependencies["bocha"]
    mmx = inject_dependencies["mmx"]
    bocha.call_count = mmx.call_count = 0

    out = await kg_search_resources.ainvoke({
        "domain": "test",
        "query": "x",
        "channel": "duckduck",
    })
    assert out == "[]"
    # CRITICAL: bocha was NOT tried as fallback.
    assert bocha.call_count == 0
    assert mmx.call_count == 0


async def test_search_resources_with_channel_propagates_node_and_domain(
    inject_dependencies: dict[str, Any],
) -> None:
    """Inline channel path must produce the same item shape as the default path."""
    from src.agent.tools import kg_search_resources

    out = await kg_search_resources.ainvoke({
        "domain": "ai",
        "query": "x",
        "node": "transformer",
        "channel": "bocha",
    })
    parsed = json.loads(out)
    assert parsed[0]["domain"] == "ai"
    assert parsed[0]["node"] == "transformer"
    assert "added_at" in parsed[0]
    assert "category" in parsed[0]


async def test_search_resources_default_path_unchanged(
    inject_dependencies: dict[str, Any],
) -> None:
    """No channel= → adaptive fallback path; resource service still consulted."""
    from src.agent.tools import kg_search_resources

    # The default path goes via ResourceService. We don't fully exercise
    # it here (covered by other tests) — only assert it still returns
    # valid JSON and doesn't blow up.
    out = await kg_search_resources.ainvoke({
        "domain": "test",
        "query": "x",
    })
    # ResourceService may return [] depending on fixture state; either
    # way the result must be a JSON array.
    json.loads(out)  # must not raise


async def test_search_resources_empty_channel_means_default_path(
    inject_dependencies: dict[str, Any],
) -> None:
    """channel="" is treated the same as no channel at all."""
    from src.agent.tools import kg_search_resources

    # If empty-channel went through search_one, bocha.call_count would be 1.
    # We can't easily assert that without inspecting calls to ResourceService,
    # so just confirm the call doesn't crash and returns valid JSON.
    out = await kg_search_resources.ainvoke({
        "domain": "test",
        "query": "x",
        "channel": "",
    })
    json.loads(out)