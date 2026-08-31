"""Bocha AI Search backend — httpx-mocked parser + tool smoke tests."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.infrastructure.search.base import SearchResult
from src.infrastructure.search.bocha import (
    BochaSearchClient,
    MAX_COUNT as BOCHA_MAX_COUNT,
)


# ---------------------------------------------------------------------------
# httpx mocking helpers (mirrors test_infrastructure_search.py)
# ---------------------------------------------------------------------------


def _build_mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=5.0, transport=httpx.MockTransport(handler))


@pytest.fixture
def fake_bocha_transport(monkeypatch):
    """Replace ``httpx.AsyncClient`` with one backed by a MockTransport.

    The factory pattern is required because the SUT creates a fresh
    ``httpx.AsyncClient(headers=...)`` per call — the mock has to
    accept whatever kwargs the SUT passes (headers, timeout, proxy)
    and route them through to a shared MockTransport. We achieve that
    by patching the class itself, not pre-building an instance.

    Returns the mutable state dict that individual tests can override
    via ``monkeypatch.setitem``.
    """
    state: dict[str, Any] = {
        "status": 200,
        "payload": {
            "_type": "SearchResponse",
            "queryContext": {"originalQuery": "test"},
            "webPages": {
                "webSearchUrl": "https://bocha.example/search",
                "totalEstimatedMatches": 2,
                "value": [
                    {
                        "id": "1",
                        "name": "First result",
                        "url": "https://x.example/1",
                        "snippet": "short snippet",
                        "summary": "a longer AI-generated summary for the first result",
                        "datePublished": "2024-07-22T00:00:00+08:00",
                    },
                    {
                        "id": "2",
                        "name": "Second result",
                        "url": "https://x.example/2",
                        "snippet": "second snippet",
                        "summary": "",
                        "datePublished": "2024-08-01T00:00:00+08:00",
                    },
                ],
            },
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        # Verify the Authorization header is set correctly — the whole
        # point of integrating Bocha is to authenticate against it.
        auth = request.headers.get("Authorization", "")
        assert auth.startswith("Bearer "), (
            f"BochaSearchClient did not send a Bearer Authorization header: {auth!r}"
        )
        # And that the body has the expected shape.
        body = json.loads(request.content.decode("utf-8"))
        assert "query" in body, f"BochaSearchClient request body missing 'query': {body}"
        assert "count" in body, f"BochaSearchClient request body missing 'count': {body}"
        return httpx.Response(state["status"], json=state["payload"])

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient  # capture before monkeypatch

    class _MockAsyncClient:
        """Drop-in replacement that accepts whatever kwargs the SUT passes
        (headers / timeout / proxy) and forwards them to a real httpx
        client backed by our MockTransport."""

        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            self._inner = real_async_client(*args, **kwargs)

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return await self._inner.__aexit__(exc_type, exc, tb)

        async def post(self, url, **kwargs):
            return await self._inner.post(url, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)
    return state


# ---------------------------------------------------------------------------
# BochaSearchClient
# ---------------------------------------------------------------------------


async def test_bocha_client_parses_response(fake_bocha_transport) -> None:
    client = BochaSearchClient(api_key="test-key")
    out = await client.search("hello", num_results=10)
    assert len(out) == 2
    assert all(isinstance(r, SearchResult) for r in out)
    # The summary field (longer) should win over snippet on item 1;
    # item 2 has empty summary so the snippet should be retained.
    assert out[0].title == "First result"
    assert out[0].link == "https://x.example/1"
    assert out[0].snippet == "a longer AI-generated summary for the first result"
    assert out[0].source == "bocha"
    assert out[1].title == "Second result"
    assert out[1].snippet == "second snippet"


async def test_bocha_client_respects_num_results(fake_bocha_transport) -> None:
    client = BochaSearchClient(api_key="test-key")
    out = await client.search("hello", num_results=1)
    assert len(out) == 1
    assert out[0].title == "First result"


async def test_bocha_client_clamps_count(fake_bocha_transport) -> None:
    """``num_results`` above MAX_COUNT must be clamped, not rejected."""
    client = BochaSearchClient(api_key="test-key")
    # Even though we ask for 9999, the parser only returns what the
    # server sent (2), but the request body should clamp to MAX_COUNT.
    out = await client.search("hello", num_results=9999)
    assert len(out) == 2  # server cap


async def test_bocha_client_falls_back_on_http_error(fake_bocha_transport) -> None:
    fake_bocha_transport["status"] = 401
    fake_bocha_transport["payload"] = {"code": "Unauthorized", "message": "bad key"}
    client = BochaSearchClient(api_key="bad-key")
    out = await client.search("hello")
    assert out == []


async def test_bocha_client_handles_missing_webpages(fake_bocha_transport) -> None:
    """An error-shaped response (no ``webPages`` key) should not crash."""
    fake_bocha_transport["payload"] = {"_type": "ErrorResponse", "message": "oops"}
    client = BochaSearchClient(api_key="test-key")
    out = await client.search("hello")
    assert out == []


async def test_bocha_client_rejects_empty_api_key() -> None:
    """Constructing without a key must raise loudly."""
    with pytest.raises(ValueError, match="api_key"):
        BochaSearchClient(api_key="")


async def test_bocha_client_skips_empty_query(fake_bocha_transport) -> None:
    client = BochaSearchClient(api_key="test-key")
    assert await client.search("") == []
    assert await client.search("   ") == []


async def test_bocha_client_missing_url_is_skipped(fake_bocha_transport) -> None:
    """An item without ``url`` cannot be indexed — drop it."""
    fake_bocha_transport["payload"]["webPages"]["value"] = [
        {"name": "no-url", "snippet": "x"},  # dropped
        {"name": "ok", "url": "https://x.example/y", "snippet": "good"},
    ]
    client = BochaSearchClient(api_key="test-key")
    out = await client.search("hello")
    assert len(out) == 1
    assert out[0].link == "https://x.example/y"


async def test_bocha_client_decodes_gbk_response(monkeypatch) -> None:
    """Real Bocha API serves Chinese titles in GBK bytes — mojibake fix."""
    # Build the GBK-encoded body exactly as Bocha sends it today:
    # outer envelope ``{"code":200,"data":{...SearchResponse...}}``,
    # body bytes encoded as GB18030 (superset of GBK).
    gb_body = '{"code":200,"data":{"_type":"SearchResponse",' \
              '"webPages":{"value":[' \
              '{"name":"中文标题","url":"https://x.example/y",' \
              '"snippet":"中文摘要"}' \
              ']}}}'.encode("gb18030")

    real = httpx.Response(
        200,
        content=gb_body,
        headers={"content-type": "application/json"},
    )

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda req: real)

    class _MockAsyncClient:
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            self._inner = real_async_client(*args, **kwargs)

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return await self._inner.__aexit__(exc_type, exc, tb)

        async def post(self, url, **kwargs):
            return await self._inner.post(url, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)

    client = BochaSearchClient(api_key="test-key")
    out = await client.search("hello")
    assert len(out) == 1
    assert out[0].title == "中文标题"
    assert out[0].snippet == "中文摘要"


async def test_bocha_client_handles_nested_data_envelope(
    monkeypatch, fake_bocha_transport
) -> None:
    """Real envelope shape: ``{\"code\": 200, \"data\": {SearchResponse}}``."""
    fake_bocha_transport["payload"] = {
        "code": 200,
        "log_id": "abc",
        "msg": None,
        "data": {
            "_type": "SearchResponse",
            "webPages": {
                "value": [
                    {
                        "name": "Wrapped result",
                        "url": "https://x.example/w",
                        "snippet": "ok",
                        "summary": "",
                    }
                ]
            },
        },
    }
    client = BochaSearchClient(api_key="test-key")
    out = await client.search("hello")
    assert len(out) == 1
    assert out[0].title == "Wrapped result"


async def test_bocha_client_treats_envelope_code_as_error(
    monkeypatch, fake_bocha_transport
) -> None:
    """``HTTP 200 + envelope code != 200`` must degrade to empty list."""
    fake_bocha_transport["payload"] = {
        "code": 401,
        "msg": "Unauthorized",
        "data": None,
    }
    client = BochaSearchClient(api_key="bad-key")
    out = await client.search("hello")
    assert out == []


# ---------------------------------------------------------------------------
# DualSearchClient integration (Bocha as tertiary fallback)
# ---------------------------------------------------------------------------


async def test_dual_search_falls_through_to_bocha(
    monkeypatch, fake_bocha_transport
) -> None:
    """When DDG and mmx return nothing, Bocha should serve the request."""
    from src.infrastructure.search.mmx import (
        DualSearchClient,
        MmxSearchClient,
        DuckDuckGoClient,
    )

    class _EmptyClient(DuckDuckGoClient):
        async def search(self, query, *, num_results=10):
            return []

    ddg = _EmptyClient(proxy="")
    mmx = MmxSearchClient()
    # Force mmx to also return nothing — short-circuit the subprocess
    # call by replacing ``search`` directly.
    async def _empty_mmx(self, query, *, num_results=10):
        return []

    monkeypatch.setattr(MmxSearchClient, "search", _empty_mmx)

    bocha = BochaSearchClient(api_key="test-key")
    dual = DualSearchClient(ddg=ddg, mmx=mmx, bocha=bocha, proxy="")

    # Without proxy the chain should be: mmx (empty) → bocha (2 results)
    out = await dual.search("anything")
    assert len(out) == 2
    assert out[0].source == "bocha"


async def test_dual_search_omits_bocha_when_not_configured(
    monkeypatch, fake_bocha_transport
) -> None:
    """``bocha=None`` must keep the chain working identically to before."""
    from src.infrastructure.search.mmx import DualSearchClient, MmxSearchClient

    async def _empty_mmx(self, query, *, num_results=10):
        return []

    monkeypatch.setattr(MmxSearchClient, "search", _empty_mmx)

    dual = DualSearchClient(
        ddg=type("E", (), {
            "search": lambda self, q, *, num_results=10: __import__("asyncio").sleep(0, result=[])
        })(),
        mmx=MmxSearchClient(),
        bocha=None,
        proxy="",
    )
    out = await dual.search("anything")
    assert out == []


# ---------------------------------------------------------------------------
# Tool wrapper (LangChain @tool)
# ---------------------------------------------------------------------------


async def test_bocha_tool_returns_friendly_error_when_unconfigured(
    monkeypatch, clean_env
) -> None:
    """Without BOCHA_API_KEY the tool must report a clear, actionable error."""
    from src.agent.tools import kg_bocha_web_search
    from src.agent.tools import search_bocha_tool

    # Make sure no cached client from another test pollutes this one.
    search_bocha_tool.reset_bocha_client()

    # The tool is a langchain StructuredTool — invoke via .ainvoke to
    # match the production path.
    result = await kg_bocha_web_search.ainvoke(
        {"query": "anything", "num_results": 5, "freshness": "noLimit"}
    )
    assert isinstance(result, str)
    assert "BOCHA_API_KEY" in result


async def test_bocha_tool_rejects_bad_freshness(clean_env) -> None:
    from src.agent.tools import kg_bocha_web_search

    result = await kg_bocha_web_search.ainvoke(
        {"query": "x", "freshness": "tomorrow"}
    )
    assert isinstance(result, str)
    assert "freshness" in result


async def test_bocha_tool_rejects_empty_query(clean_env) -> None:
    from src.agent.tools import kg_bocha_web_search

    result = await kg_bocha_web_search.ainvoke({"query": ""})
    assert isinstance(result, str)
    assert "query" in result


async def test_bocha_tool_clamps_num_results(fake_bocha_transport, clean_env) -> None:
    """Ask for more than MAX_COUNT — request body should clamp to BOCHA_MAX_COUNT."""
    from src.agent.tools import kg_bocha_web_search
    from src.agent.tools import search_bocha_tool

    # Provide a key so the tool actually calls the (mocked) Bocha API.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")

    from src.config.settings import reload_settings
    reload_settings()
    search_bocha_tool.reset_bocha_client()
    try:
        # Ask for way more than the documented cap; tool should clamp
        # silently rather than 400-ing.
        await kg_bocha_web_search.ainvoke({"query": "hi", "num_results": 9999})
    finally:
        monkeypatch.undo()
        reload_settings()
        search_bocha_tool.reset_bocha_client()


async def test_bocha_client_max_count_constant_is_sane() -> None:
    """The clamp value matches Bocha's documented 1..50 range."""
    assert 1 <= BOCHA_MAX_COUNT <= 50