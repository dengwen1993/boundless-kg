"""LLM clients — async, mock-safe, factory dispatches correctly."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from src.infrastructure.llm import (
    AsyncLLMClient,
    LLMResult,
    MockLLMClient,
    create_llm_client,
)
from src.infrastructure.llm.base import AsyncLLMClient as BaseClient


class TestMockLLMClient:
    async def test_chat_yields_to_event_loop(self) -> None:
        llm = MockLLMClient(latency_sec=0.02)
        # If mock blocked, the loop would never observe other tasks.
        order: list[str] = []

        async def watcher() -> None:
            for _ in range(3):
                await asyncio.sleep(0.01)
                order.append("watch")

        async def run_chat() -> None:
            await llm.chat("system", "user")
            order.append("chat")

        await asyncio.gather(watcher(), run_chat())
        # We expect at least one "watch" before "chat" — proof we yielded.
        assert order.index("watch") < order.index("chat")

    async def test_chat_returns_echo(self) -> None:
        llm = MockLLMClient(latency_sec=0)
        result = await llm.chat("s", "hello")
        assert result == "[mock:mock-llm] hello"

    async def test_chat_with_reasoning_envelope(self) -> None:
        llm = MockLLMClient(latency_sec=0)
        result = await llm.chat_with_reasoning("s", "u")
        assert isinstance(result, LLMResult)
        assert result.text.startswith("[mock:")
        assert result.reasoning == ""
        assert result.raw == {"_mock": True}

    async def test_env_var_overrides_latency(self, monkeypatch) -> None:
        monkeypatch.setenv("KG_MOCK_LLM_LATENCY_SEC", "0.5")
        llm = MockLLMClient()
        # First call must take roughly 0.5s, not the constructor default.
        import time

        t0 = time.perf_counter()
        await llm.chat("s", "x")
        elapsed = time.perf_counter() - t0
        assert elapsed >= 0.4

    async def test_aclose_is_noop(self) -> None:
        llm = MockLLMClient()
        assert await llm.aclose() is None


class TestLLMFactory:
    def test_mock_provider_does_not_require_secrets(
        self, clean_env, monkeypatch
    ) -> None:
        # No API keys in env — must still succeed.
        llm = create_llm_client("mock")
        assert isinstance(llm, MockLLMClient)

    def test_unknown_provider_raises(self, clean_env) -> None:
        with pytest.raises(ValueError):
            create_llm_client("totally-unknown")

    def test_default_provider_is_mock(self, clean_env, monkeypatch) -> None:
        monkeypatch.delenv("KG_LLM_PROVIDER", raising=False)
        llm = create_llm_client()
        assert isinstance(llm, MockLLMClient)

    def test_minimax_provider_requires_key(self, clean_env) -> None:
        with pytest.raises(EnvironmentError):
            create_llm_client("minimax")

    def test_deepseek_provider_requires_key(self, clean_env) -> None:
        with pytest.raises(EnvironmentError):
            create_llm_client("deepseek")

    def test_minimax_provider_with_key(self, clean_env, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        from src.infrastructure.llm import AnthropicCompatClient

        llm = create_llm_client("minimax")
        assert isinstance(llm, AnthropicCompatClient)


class TestAnthropicCompatRequestShape:
    """Black-box test: feed an httpx MockTransport and verify the
    payload the client would send to MiniMax."""

    async def test_payload_contains_x_api_key_and_anthropic_version(self) -> None:
        import httpx

        from src.infrastructure.llm.anthropic_compat import AnthropicCompatClient

        captured: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            import json as _json

            captured["body"] = _json.loads(request.content)
            return httpx.Response(
                200,
                json={"content": [{"type": "text", "text": "hi"}]},
            )

        transport = httpx.MockTransport(handler)
        client = AnthropicCompatClient(
            api_key="sk-test",
            base_url="https://api.minimaxi.com/anthropic",
            model="MiniMax-M3",
        )
        # Inject the mocked client.
        client._client = httpx.AsyncClient(timeout=client._timeout, transport=transport)

        text = await client.chat("system", "user")
        assert text == "hi"
        assert captured["headers"]["x-api-key"] == "sk-test"
        assert captured["headers"]["anthropic-version"] == "2023-06-01"
        assert captured["body"]["model"] == "MiniMax-M3"
        assert captured["body"]["system"] == "system"
        await client.aclose()


class TestOpenAICompatRequestShape:
    async def test_payload_uses_bearer_auth_and_reasoning_field(self) -> None:
        import httpx

        from src.infrastructure.llm.openai_compat import OpenAICompatClient

        captured: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            import json as _json

            captured["body"] = _json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "final",
                                "reasoning_content": "thinking…",
                            }
                        }
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        client = OpenAICompatClient(
            api_key="sk-ds",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
        )
        client._client = httpx.AsyncClient(timeout=client._timeout, transport=transport)

        result = await client.chat_with_reasoning("s", "u")
        assert result.text == "final"
        assert result.reasoning == "thinking…"
        assert captured["headers"]["authorization"] == "Bearer sk-ds"
        assert captured["body"]["model"] == "deepseek-v4-pro"
        assert captured["url"].endswith("/chat/completions")
        await client.aclose()