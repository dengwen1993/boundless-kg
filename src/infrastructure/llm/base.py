"""LLM client abstract base class.

ENGINEERING_PLAN.md §3.2 — every provider-specific implementation
fulfils this contract. No synchronous escape hatches.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class LLMResult:
    """Result envelope returned by an ``AsyncLLMClient`` call.

    Attributes:
        text: Final answer text (always present, may be empty).
        reasoning: Optional chain-of-thought / reasoning trace. Some
            reasoning models expose this separately from the final
            text. Empty string when the provider doesn't surface it.
        raw: Provider-specific raw body, retained for debugging.
    """

    text: str
    reasoning: str = ""
    raw: dict | None = None


@dataclass
class ToolCall:
    """A single tool-call request from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatTurn:
    """One turn in a multi-turn conversation (may contain text + tool calls)."""

    role: str  # "user" | "assistant"
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    """For user turns that carry tool results: [{tool_use_id, content}]"""


@dataclass
class ToolChatResult:
    """Result of a ``chat_with_tools`` call.

    Either the LLM produced text (``text`` is non-empty, ``tool_calls``
    is empty) or it requested tool execution (``tool_calls`` non-empty).
    """

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict | None = None
    stop_reason: str = ""


class AsyncLLMClient(ABC):
    """Abstract async LLM client.

    Concrete subclasses MUST be safe to ``await`` from inside any
    ``async def`` call site without blocking the event loop. The
    baseline implementation violated this by using ``requests`` /
    ``time.sleep`` — see ENGINEERING_PLAN.md §1.3 / §3.2.
    """

    @abstractmethod
    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.5,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Single-turn chat; returns the assistant text."""

    @abstractmethod
    async def chat_with_reasoning(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 16000,
        json_mode: bool = False,
    ) -> LLMResult:
        """Single-turn chat with reasoning trace; returns LLMResult."""

    async def chat_with_tools(
        self,
        system: str,
        messages: list[ChatTurn],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ToolChatResult:
        """Multi-turn chat with tool-calling support.

        Default implementation falls back to plain ``chat()`` (no tools).
        Subclasses override to add real function-calling support.
        """
        # Fallback: concatenate all message texts and do a plain chat
        parts = []
        for m in messages:
            if m.text:
                parts.append(m.text)
        reply = await self.chat(system, "\n\n".join(parts), temperature=temperature, max_tokens=max_tokens)
        return ToolChatResult(text=reply, raw=None)

    async def aclose(self) -> None:
        """Release any underlying HTTP resources. Default: no-op."""
        return None


__all__ = ["AsyncLLMClient", "LLMResult", "ToolCall", "ChatTurn", "ToolChatResult"]
