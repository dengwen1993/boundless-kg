"""OpenAI-compatible async client (DeepSeek, OpenAI, MiniMax via /chat/completions)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from ._retry import llm_retrying
from .base import AsyncLLMClient, LLMResult


class OpenAICompatClient(AsyncLLMClient):
    """Async client for OpenAI-compatible APIs.

    Supports both regular chat completion (``chat``) and reasoning
    models that expose a ``reasoning_content`` field alongside the
    final ``content`` (DeepSeek-V4 family).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        timeout_sec: float = 300.0,
        reasoning_effort: str = "",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = httpx.Timeout(timeout_sec)
        self._reasoning_effort = (reasoning_effort or "").lower().strip()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _payload(
        self,
        system: str,
        user: str,
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self._reasoning_effort:
            # Maps to ``low|medium|high|max|xhigh`` on reasoning-capable
            # providers (e.g. deepseek-v4-*). Setting this does NOT fully
            # disable reasoning — those models always emit a reasoning
            # trace; this only adjusts its depth.
            payload["reasoning_effort"] = self._reasoning_effort
        return payload

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.5,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        result = await self.chat_with_reasoning(
            system,
            user,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        return result.text

    async def chat_with_reasoning(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 16000,
        json_mode: bool = False,
    ) -> LLMResult:
        client = await self._get_client()
        payload = self._payload(
            system,
            user,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        # Transient transport failures (ReadError on long reasoning
        # requests, 429/5xx under load) are retried with backoff; 4xx
        # and parse errors propagate on the first attempt.
        async for attempt in llm_retrying(label=f"openai_compat:{self._model}"):
            with attempt:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
        body = resp.json()
        msg = body["choices"][0]["message"]
        text = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content", "") or ""
        return LLMResult(text=text, reasoning=reasoning, raw=body)


def _safe_json_loads(text: str) -> dict[str, Any]:
    """Best-effort JSON parse — used by callers when ``json_mode`` is set."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


__all__ = ["OpenAICompatClient", "_safe_json_loads"]