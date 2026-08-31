"""Async Wikipedia client — proxy-aware, graceful failure.

These tests use ``httpx.MockTransport`` and ``patch`` to avoid real
network calls.  Run them with::

    python tests/manual/_test_wiki_unit.py   # standalone, no package imports
    pytest tests/unit/test_infrastructure_wiki.py  # if venv is active
"""

from __future__ import annotations

from unittest.mock import patch

import httpx

from src.infrastructure.wiki import AsyncWikiClient


def _transport(*, status: int = 200, json_body: dict | None = None) -> httpx.MockTransport:
    """One-shot transport that returns *status* / *json_body* for the first request."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if json_body is not None and status < 400:
            return httpx.Response(status, json=json_body)
        return httpx.Response(status, text="oops")

    return httpx.MockTransport(handler)


async def test_wiki_lookup_returns_extract() -> None:
    t = _transport(json_body={"extract": "深度学习是机器学习的分支。"})
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t)):
        out = await AsyncWikiClient().lookup("深度学习")
        assert out == "深度学习是机器学习的分支。"


async def test_wiki_lookup_returns_empty_on_500() -> None:
    t = _transport(status=500)
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t)):
        out = await AsyncWikiClient().lookup("Foo")
        assert out == ""


async def test_wiki_lookup_returns_empty_on_missing_extract() -> None:
    t = _transport(json_body={"title": "x"})
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t)):
        out = await AsyncWikiClient().lookup("x")
        assert out == ""


async def test_wiki_lookup_returns_empty_on_empty_title() -> None:
    out = await AsyncWikiClient().lookup("")
    assert out == ""


async def test_wiki_lookup_uses_explicit_proxy() -> None:
    t = _transport(json_body={"extract": "ok"})
    side_effect = lambda **kw: httpx.AsyncClient(**kw, transport=t)  # noqa: E731
    with patch("httpx.AsyncClient", side_effect=side_effect) as mock:
        await AsyncWikiClient(proxy="http://proxy:7897").lookup("test")
        assert mock.call_args[1].get("proxy") == "http://proxy:7897"


async def test_wiki_lookup_trust_env_enabled() -> None:
    t = _transport(json_body={"extract": "ok"})
    # Build the real httpx client OUTSIDE the patch context so the
    # transport instance isn't itself intercepted by the mock (which
    # would turn resp.json / resp.raise_for_status into AsyncMock and
    # break the lookup).  Inject it via side_effect — see also
    # ``test_wiki_lookup_uses_explicit_proxy`` for the same pattern.
    real_client = httpx.AsyncClient(transport=t)
    side_effect = lambda **kw: httpx.AsyncClient(**kw, transport=t)  # noqa: E731
    with patch("httpx.AsyncClient", side_effect=side_effect) as mock:
        await AsyncWikiClient().lookup("test")
        assert mock.call_args[1].get("trust_env") is True
    # Cleanup the unbound client.
    await real_client.aclose()


async def test_wiki_lookup_handles_connect_error() -> None:
    async def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated")

    t = httpx.MockTransport(boom)
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t)):
        out = await AsyncWikiClient().lookup("test")
        assert out == ""


async def test_wiki_zh_url_construction() -> None:
    called: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={"extract": "x"})

    t = httpx.MockTransport(handler)
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t)):
        await AsyncWikiClient(language="zh").lookup("深度学习")
    assert "zh.wikipedia.org" in (called[0] if called else "")


async def test_wiki_en_url_construction() -> None:
    called: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={"extract": "x"})

    t = httpx.MockTransport(handler)
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t)):
        await AsyncWikiClient(language="en").lookup("Deep learning")
    assert "en.wikipedia.org" in (called[0] if called else "")
