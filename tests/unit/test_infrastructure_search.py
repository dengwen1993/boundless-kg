"""Search backend — DuckDuckGo fallback parses HTML; mocked httpx."""

from __future__ import annotations

import asyncio
import json as _json

import httpx
import pytest

from src.infrastructure.search.base import SearchResult
from src.infrastructure.search.mmx import (
    DuckDuckGoFallbackClient,
    MmxSearchClient,
)


class FakeProc:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass


def _build_mock_async_client(handler):
    """Return an httpx.AsyncClient backed by a MockTransport so the
    client speaks to a fake network instead of duckduckgo.com."""

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(timeout=5.0, transport=transport)


@pytest.fixture
def stub_duckduckgo(monkeypatch):
    """Patch httpx.AsyncClient inside the search module so the fallback
    speaks to our fake transport without making a real request."""
    html = (
        '<html><body>'
        '<a class="result__a" href="https://x.example">first result</a>'
        '<a class="result__a" href="https://y.example">second result</a>'
        "</body></html>"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    fake_client = httpx.AsyncClient(timeout=5.0, transport=transport)

    # The fallback constructs `httpx.AsyncClient(...)` inside its search()
    # method; replace the class so each call returns our pre-built client.
    def factory(*args, **kwargs):
        return fake_client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return html


async def test_mmx_client_falls_back_on_missing_binary(
    monkeypatch, stub_duckduckgo
) -> None:
    """If the mmx CLI doesn't exist, we must fall back to DuckDuckGo."""

    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("no mmx")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    mmx = MmxSearchClient()
    out = await mmx.search("query")
    assert len(out) >= 1
    assert all(isinstance(r, SearchResult) for r in out)


async def test_mmx_client_parses_json_stdout(monkeypatch) -> None:
    """If mmx returns JSON, use it directly without falling back."""
    payload = _json.dumps(
        {"results": [{"title": "t", "link": "l", "snippet": "s"}]}
    )

    async def fake_exec(*args, **kwargs):
        return FakeProc(0, payload.encode("utf-8"))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    mmx = MmxSearchClient()
    out = await mmx.search("q")
    assert len(out) == 1
    assert out[0].title == "t"


async def test_duckduckgo_parses_results(stub_duckduckgo) -> None:
    client = DuckDuckGoFallbackClient()
    out = await client.search("query")
    assert [r.link for r in out] == ["https://x.example", "https://y.example"]
    assert out[0].title == "first result"
    assert out[0].source == "duckduck"