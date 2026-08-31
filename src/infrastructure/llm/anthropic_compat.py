"""Anthropic-compatible async client (MiniMax Anthropic-protocol endpoint).

Supports tool calling via the Anthropic Messages API tool-use flow:
  1. Send messages + tools → API returns ``tool_use`` content blocks
  2. Execute tools locally
  3. Send back ``tool_result`` blocks as a user message
  4. API continues generating (may call more tools or produce final text)
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ._retry import llm_retrying
from .base import AsyncLLMClient, ChatTurn, LLMResult, ToolCall, ToolChatResult


class AnthropicCompatClient(AsyncLLMClient):
    """Async client for Anthropic-compatible APIs.

    Talks to ``/v1/messages``. Defaults align with MiniMax's
    ``https://api.minimaxi.com/anthropic`` endpoint.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        timeout_sec: float = 300.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = httpx.Timeout(timeout_sec)
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
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> tuple[str, str]:
        text = ""
        for block in body.get("content", []) or []:
            if block.get("type") == "text":
                text += block.get("text", "") or ""
        return text, ""

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
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        async for attempt in llm_retrying(label=f"anthropic_compat:{self._model}"):
            with attempt:
                resp = await client.post(
                    f"{self._base_url}/v1/messages",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
        body = resp.json()
        text, reasoning = self._extract_text(body)
        return LLMResult(text=text, reasoning=reasoning, raw=body)

    # ------------------------------------------------------------------
    # Tool-calling support (Anthropic Messages API)
    # ------------------------------------------------------------------

    def _messages_to_anthropic(
        self, messages: list[ChatTurn]
    ) -> list[dict[str, Any]]:
        """Convert ``ChatTurn`` list to Anthropic message format."""
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "user" and m.tool_results:
                # User turn carrying tool results
                content: list[dict[str, Any]] = []
                for tr in m.tool_results:
                    content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tr["tool_use_id"],
                            "content": tr["content"],
                        }
                    )
                if m.text:
                    content.insert(
                        0, {"type": "text", "text": m.text}
                    )
                out.append({"role": "user", "content": content})
            elif m.role == "assistant" and m.tool_calls:
                # Assistant turn with tool calls
                content: list[dict[str, Any]] = []
                if m.text:
                    content.append({"type": "text", "text": m.text})
                for tc in m.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                out.append({"role": "assistant", "content": content})
            elif m.role == "assistant":
                out.append({"role": "assistant", "content": m.text})
            else:
                out.append({"role": "user", "content": m.text})
        return out

    async def chat_with_tools(
        self,
        system: str,
        messages: list[ChatTurn],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ToolChatResult:
        """Call the Anthropic Messages API with tools enabled."""
        client = await self._get_client()
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": self._messages_to_anthropic(messages),
            "tools": tools,
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        async for attempt in llm_retrying(label=f"anthropic_tools:{self._model}"):
            with attempt:
                resp = await client.post(
                    f"{self._base_url}/v1/messages",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
        body = resp.json()

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in body.get("content", []) or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block["id"],
                        name=block["name"],
                        arguments=block.get("input", {}),
                    )
                )

        return ToolChatResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            raw=body,
            stop_reason=body.get("stop_reason", ""),
        )


def _safe_json_loads(text: str) -> dict[str, Any]:
    """Best-effort JSON parse — used by callers when ``json_mode`` is set."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


__all__ = ["AnthropicCompatClient", "_safe_json_loads"]
