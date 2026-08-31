"""Factory that selects the right ``AsyncLLMClient`` from settings.

Two scopes share this factory:

* ``create_llm_client()`` — used by the **agent chat layer**
  (deepagents orchestration). Reads ``KG_LLM_PROVIDER``.
* ``create_generation_llm_client()`` — used by **business flows**
  (graph-generation pipeline, note service, resource classifier).
  Reads ``KG_GENERATION_LLM_PROVIDER`` and falls back to
  ``KG_LLM_PROVIDER`` so existing setups keep working.

Provider dispatch:

  * ``mock``     → :class:`MockLLMClient` (tests + offline dev)
  * ``minimax``  → :class:`AnthropicCompatClient` (Anthropic-protocol)
  * ``deepseek`` / ``deepseek-chat`` → :class:`OpenAICompatClient`
  * ``openai``   → :class:`OpenAICompatClient`
"""

from __future__ import annotations

from src.config import (
    get_deepseek_api_key,
    get_deepseek_base_url,
    get_deepseek_chat_model,
    get_deepseek_v4_model,
    get_generation_llm_provider,
    get_llm_reasoning_effort,
    get_llm_timeout_sec,
    get_minimax_api_key,
    get_minimax_base_url,
    get_minimax_model,
    get_note_llm_provider,
)

from .anthropic_compat import AnthropicCompatClient
from .base import AsyncLLMClient
from .mock import MockLLMClient
from .openai_compat import OpenAICompatClient


def _build_client(provider: str, *, timeout_sec: float | None = None) -> AsyncLLMClient:
    """Internal provider dispatch (no env lookup)."""
    t = timeout_sec if timeout_sec is not None else get_llm_timeout_sec()
    # Only consumed by reasoning-capable OpenAI-compatible providers
    # (deepseek-v4-*). AnthropicCompatClient has its own thinking param
    # semantics and ignores this — left untouched here.
    effort = get_llm_reasoning_effort()
    if provider == "mock":
        return MockLLMClient()
    if provider == "minimax":
        return AnthropicCompatClient(
            api_key=get_minimax_api_key(),
            base_url=get_minimax_base_url(),
            model=get_minimax_model(),
            timeout_sec=t,
        )
    if provider == "deepseek":
        return OpenAICompatClient(
            api_key=get_deepseek_api_key(),
            base_url=get_deepseek_base_url(),
            model=get_deepseek_v4_model(),
            timeout_sec=t,
            reasoning_effort=effort,
        )
    if provider == "deepseek-chat":
        return OpenAICompatClient(
            api_key=get_deepseek_api_key(),
            base_url=get_deepseek_base_url(),
            model=get_deepseek_chat_model(),
            timeout_sec=t,
            reasoning_effort=effort,
        )
    if provider == "openai":
        import os

        return OpenAICompatClient(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            timeout_sec=t,
            reasoning_effort=effort,
        )

    raise ValueError(f"Unknown LLM provider: {provider!r}")


def create_llm_client(provider: str | None = None) -> AsyncLLMClient:
    """Build an ``AsyncLLMClient`` for the agent chat layer.

    ``provider=None`` reads ``KG_LLM_PROVIDER`` from settings (which
    loads from ``.env``); defaults to ``mock`` so missing config can't
    accidentally hit the network. Pass an explicit ``provider`` to
    override the env for one call site.
    """
    from src.config import get_llm_provider

    provider = (provider or get_llm_provider() or "mock").lower()
    return _build_client(provider)


def create_generation_llm_client(provider: str | None = None) -> AsyncLLMClient:
    """Build an ``AsyncLLMClient`` for business flows (graph / notes /
    resources). Reads ``KG_GENERATION_LLM_PROVIDER`` (falling back to
    ``KG_LLM_PROVIDER``) so the model used for background generation
    can differ from the one driving agent chat.

    Example: ``KG_LLM_PROVIDER=minimax`` + ``KG_GENERATION_LLM_PROVIDER=deepseek``
    keeps agent chat on ``MiniMax-M3-512k`` while graph generation
    uses ``deepseek-v4-pro``.
    """
    provider = (provider or get_generation_llm_provider() or "mock").lower()
    return _build_client(provider)


def create_note_llm_client(provider: str | None = None) -> AsyncLLMClient:
    """Build an ``AsyncLLMClient`` for note generation specifically.

    Reads ``KG_NOTE_LLM_PROVIDER`` (falling back through
    ``KG_GENERATION_LLM_PROVIDER`` → ``KG_LLM_PROVIDER``). Use this to
    pin notes to a faster/cheaper model without changing the
    graph-generation provider.

    Example: ``KG_GENERATION_LLM_PROVIDER=deepseek`` (graph → v4-pro) +
    ``KG_NOTE_LLM_PROVIDER=deepseek-chat`` (notes → v4-flash).
    """
    provider = (provider or get_note_llm_provider() or "mock").lower()
    return _build_client(provider)


__all__ = ["create_llm_client", "create_generation_llm_client", "create_note_llm_client"]
