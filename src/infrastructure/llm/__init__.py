"""LLM infrastructure — async clients for both OpenAI- and Anthropic-compatible APIs."""

from .base import AsyncLLMClient, LLMResult, ToolCall, ChatTurn, ToolChatResult
from .openai_compat import OpenAICompatClient
from .anthropic_compat import AnthropicCompatClient
from .mock import MockLLMClient
from .factory import (
    create_generation_llm_client,
    create_llm_client,
    create_note_llm_client,
)

__all__ = [
    "AsyncLLMClient",
    "LLMResult",
    "ToolCall",
    "ChatTurn",
    "ToolChatResult",
    "OpenAICompatClient",
    "AnthropicCompatClient",
    "MockLLMClient",
    "create_llm_client",
    "create_generation_llm_client",
    "create_note_llm_client",
]
