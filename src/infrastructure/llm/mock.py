"""Mock LLM client for tests and local development.

Deterministic responses; never hits the network. Cooperative
``asyncio.sleep`` keeps the event loop responsive.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from .base import AsyncLLMClient, LLMResult


class MockLLMClient(AsyncLLMClient):
    """Deterministic mock — yields to the loop instead of blocking.

    Default behaviour:

    * ``chat`` returns ``"[mock:{model}] {user}"``.
    * ``chat_with_reasoning`` adds an empty reasoning trace.
    * A small ``asyncio.sleep`` simulates latency. Length is
      controlled via env var ``KG_MOCK_LLM_LATENCY_SEC`` (default 0.01s)
      so unit tests stay fast.
    """

    def __init__(
        self,
        *,
        model: str = "mock-llm",
        latency_sec: float | None = None,
    ) -> None:
        self._model = model
        env_val = os.environ.get("KG_MOCK_LLM_LATENCY_SEC")
        self._latency_sec = (
            float(env_val) if env_val is not None else (latency_sec or 0.01)
        )

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.5,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        if self._latency_sec > 0:
            await asyncio.sleep(self._latency_sec)
        return f"[mock:{self._model}] {user}"

    async def chat_with_reasoning(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 16000,
        json_mode: bool = False,
    ) -> LLMResult:
        if self._latency_sec > 0:
            await asyncio.sleep(self._latency_sec)
        text = f"[mock:{self._model}] {user}"
        return LLMResult(text=text, reasoning="", raw={"_mock": True})


__all__ = ["MockLLMClient"]