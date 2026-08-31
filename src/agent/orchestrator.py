"""KGCurationAgent — built with deepagents, streaming via astream_events.

Replaces the stub implementation.  Uses ``deepagents.create_deep_agent``
to compose the LangChain @tool definitions + system prompt into a
LangGraph CompiledStateGraph that supports:
  - streaming text chunks (``on_chat_model_stream``)
  - tool call / result events (``on_tool_start`` / ``on_tool_end``)
  - multi-turn tool-calling loop (handled internally by deepagents)

Supports multiple LLM providers via the ``KG_LLM_PROVIDER`` setting:
  - ``minimax``  → ChatAnthropic (Anthropic-protocol endpoint)
  - ``deepseek`` → ChatOpenAI (OpenAI-compatible endpoint)
  - ``openai``   → ChatOpenAI (standard OpenAI API)
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents import MemoryMiddleware, create_deep_agent

logger = logging.getLogger(__name__)
from langchain_core.language_models import BaseChatModel

from src.agent.memory import (
    get_checkpointer,
    get_filesystem_backend,
)
from src.agent.cards import CardsMiddleware
from src.agent.date_prompt import DateContextMiddleware
from src.agent.guard import PathNormalizeMiddleware, WriteClaimMiddleware
from src.agent.session_context import SessionContextMiddleware, build_session_context
from src.agent.skills_setup import get_skill_sources, get_skills_backend
from src.agent.system_prompt import SYSTEM_PROMPT, compose_system_prompt
from src.config.settings import (
    get_agent_cards_enabled,
)
from src.agent.tools import (
    kg_add_card,
    kg_add_dossier_entry,
    kg_add_edge,
    kg_add_learning_resources,
    kg_add_node,
    kg_add_plan,
    kg_add_subtree,
    kg_bocha_web_search,
    kg_check_status,
    kg_classify_pending,
    kg_clear_search_channel,
    kg_create_node_with_resource,
    kg_delete_card,
    kg_delete_edge,
    kg_delete_node,
    kg_delete_plan,
    kg_fix_links,
    kg_generate_note,
    kg_list_cards,
    kg_list_domains,
    kg_list_notes,
    kg_list_plans,
    kg_open_node,
    kg_query_neighbors,
    kg_repair_json,
    kg_read_note,
    kg_remove_dossier_entry,
    kg_run_skill,
    kg_search_dossier,
    kg_search_resources,
    kg_set_search_channel,
    kg_stage_file,
    kg_sync_associations,
    kg_sync_node_associations,
    kg_update_dossier_entry,
    kg_update_node,
    kg_update_plan_status,
    kg_validate_graph,
    kg_view_associations,
    kg_view_card,
    kg_view_dossier,
    kg_view_graph,
    kg_view_resources,
    kg_view_timeline,
    kg_search_memory,
    kg_recall_recent,
    kg_recall_session,
    kg_global_search,
    kg_graph_neighbors,
    kg_list_uploaded_files,
    kg_parse_uploaded_file,
    kg_delete_uploaded_file,
    kg_auto_place_uploaded_file,
)

# Shell-exec tool — only imported if enabled.  Lazy import keeps the
# heavy ``deepagents.backends`` surface out of the cold-start path for
# deployments that disable the feature.
try:
    from src.agent.shell_sandbox import kg_shell_exec
    _shell_exec_tool = kg_shell_exec
except ImportError:  # pragma: no cover - only triggered if module is renamed
    _shell_exec_tool = None
from src.config import (
    get_deepseek_api_key,
    get_deepseek_base_url,
    get_deepseek_v4_model,
    get_llm_provider,
    get_minimax_api_key,
    get_minimax_base_url,
    get_minimax_model,
)

ALL_TOOLS: list[Any] = [
    # Graph management
    kg_list_domains,
    kg_view_graph,
    kg_add_node,
    kg_add_subtree,
    kg_fix_links,
    kg_delete_node,
    kg_update_node,
    kg_validate_graph,
    kg_open_node,
    # Notes
    kg_generate_note,
    kg_read_note,
    kg_list_notes,
    # Resources
    kg_search_resources,
    kg_view_resources,
    kg_add_learning_resources,
    kg_bocha_web_search,
    kg_set_search_channel,
    kg_clear_search_channel,
    # Staging
    kg_stage_file,
    kg_classify_pending,
    kg_create_node_with_resource,
    # Pipeline
    kg_run_skill,
    kg_check_status,
    # Plans
    kg_add_plan,
    kg_list_plans,
    kg_update_plan_status,
    kg_delete_plan,
    # Timeline
    kg_view_timeline,
    # Cards
    kg_add_card,
    kg_list_cards,
    kg_view_card,
    kg_delete_card,
    # JSON repair (generic utility — LLM can call it when an upstream
    # tool returned malformed JSON it cannot use directly)
    kg_repair_json,
    # Memory / session search
    kg_search_memory,
    kg_recall_recent,
    kg_recall_session,
    # Associations (L1/L2/L3 derivation layer)
    kg_view_associations,
    kg_query_neighbors,
    kg_sync_associations,
    kg_sync_node_associations,
    kg_add_edge,
    kg_delete_edge,
    # Global search (FalkorDB + Embedding)
    kg_global_search,
    kg_graph_neighbors,
    # Dossier (节点经验档案)
    kg_add_dossier_entry,
    kg_view_dossier,
    kg_search_dossier,
    kg_update_dossier_entry,
    kg_remove_dossier_entry,
    # Transient file tools (chat attachments — paperclip uploads)
    kg_list_uploaded_files,
    kg_parse_uploaded_file,
    kg_delete_uploaded_file,
    kg_auto_place_uploaded_file,
]


def _build_tool_list() -> list[Any]:
    """Compose the final tool list, conditioned on config flags.

    Centralising this in one place lets ``ALL_TOOLS`` stay a flat
    constant for tests / type-checkers while the runtime list adapts to
    ``KG_AGENT_SHELL_ENABLED``.
    """
    tools: list[Any] = list(ALL_TOOLS)
    # Shell exec — opt-out via env so audits / prod deployments can
    # disable it without code changes.  See ``shell_sandbox.py`` for
    # the sandbox + timeout semantics.
    if _shell_exec_tool is not None:
        from src.config.settings import get_agent_shell_enabled
        if get_agent_shell_enabled():
            tools.append(_shell_exec_tool)
    return tools


def _build_model() -> BaseChatModel:
    """Build a LangChain chat model configured for the active provider.

    Provider is selected by ``KG_LLM_PROVIDER`` (default: ``mock``).
    Only providers that support tool-calling are accepted here —
    ``mock`` raises a clear error because deepagents needs real
    function-calling capability.
    """
    provider = (get_llm_provider() or "mock").lower()

    if provider == "minimax":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=get_minimax_model(),
            api_key=get_minimax_api_key(),
            base_url=get_minimax_base_url(),
            max_tokens=4096,
            temperature=0.7,
            timeout=300,
            max_retries=2,
        )

    if provider in ("deepseek", "deepseek-chat", "deepseek-v4"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=get_deepseek_v4_model(),
            api_key=get_deepseek_api_key(),
            base_url=get_deepseek_base_url(),
            max_tokens=4096,
            temperature=0.7,
            timeout=300,
            max_retries=2,
        )

    if provider == "openai":
        import os

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            max_tokens=4096,
            temperature=0.7,
            timeout=300,
            max_retries=2,
        )

    raise ValueError(
        f"LLM provider {provider!r} does not support deepagents tool-calling. "
        "Set KG_LLM_PROVIDER to 'minimax', 'deepseek', or 'openai' for agent functionality."
    )


def _try_build_agent() -> tuple[Any | None, str | None]:
    """Build the deepagents agent, capturing any build-time error.

    Returns ``(agent, error_message)``: ``agent`` is ``None`` when the
    build fails (e.g. ``mock`` provider, missing API key, network
    unreachable), and ``error_message`` is the stringified exception
    or ``None`` when everything succeeded.
    """
    try:
        model = _build_model()

        # FilesystemBackend: persistent storage for AGENTS.md +
        # conversation history offloading (SummarizationMiddleware).
        # The backend root is now the workspace itself — see
        # ``src/agent/memory.py:get_filesystem_backend`` — so the
        # virtual path ``/AGENTS.md`` resolves to ``<workspace>/AGENTS.md``
        # (the single curated memory source).
        #
        # CompositeBackend mounts the bundled skills tree at ``/skills/``
        # so SkillsMiddleware (built automatically by ``create_deep_agent``
        # when ``skills=[...]`` is non-empty) can scan each skill directory
        # without losing access to the agent-memory root.
        backend = get_skills_backend()
        skill_sources = get_skill_sources()

        # MemoryMiddleware — exposes the workspace AGENTS.md as the
        # virtual path ``/AGENTS.md`` so the agent can read / amend it
        # on demand.  The matching ``system_prompt`` injection is now
        # done unconditionally by ``compose_system_prompt()`` below, so
        # we leave MemoryMiddleware's own ``system_prompt`` at the default
        # to avoid double-splicing the same file.
        memory_mw = MemoryMiddleware(
            backend=backend,
            sources=["/AGENTS.md"],
            add_cache_control=True,
        )

        # Intent-driven prompt cards. Always registered (so the agent's
        # middleware chain is deterministic across deploys) but is a no-op
        # until ``KG_AGENT_CARDS_ENABLED`` flips to ``true``.
        #
        # Uses the shared ``get_card_library()`` singleton so that the
        # ``kg_add_card`` / ``kg_delete_card`` tools (backed by
        # ``CardService``) can hot-reload the same library instance by
        # calling ``reload()`` — no restart needed for new cards to
        # take effect.
        from src.agent.dependencies import get_card_library

        cards_mw = CardsMiddleware(
            library=get_card_library(),
            enabled=get_agent_cards_enabled(),
        )

        # BUG-2026-08-19-003 L4 hard gate: strip the
        # ``/home/wend/boundless_kg/workspace/`` prefix from
        # ``write_file`` / ``edit_file`` calls so deepagents'
        # FilesystemMiddleware doesn't misroute writes into a nested
        # ``workspace/home/wend/boundless_kg/workspace/...`` tree.
        # Placed BEFORE ``write_claim_mw`` so the claim tracker
        # records the canonical (relative) path the file actually
        # landed at, not the buggy absolute one.
        path_normalize_mw = PathNormalizeMiddleware(enabled=True)

        # BUG-005 L4 hard gate: scan the model's 「已写入 X.md」
        # claims and rewrite any path that wasn't actually written
        # this turn via deepagents' write_file / edit_file.  Placed
        # last so it sees the post-Cards SystemMessage and the
        # post-Memory system_prompt that the agent will eventually
        # read — but ordering doesn't actually matter for correctness
        # here because the middleware only mutates AIMessage content.
        write_claim_mw = WriteClaimMiddleware(enabled=True)

        # 「今天是几月几号」— appended per model call rather than baked
        # into SYSTEM_PROMPT, because the agent is cached for the whole
        # process and a frozen date goes stale over midnight.
        date_mw = DateContextMiddleware()

        # Recent session context — pre-compute so disk reads only happen
        # once at build time, not on every model call.
        session_ctx = build_session_context()
        session_mw = SessionContextMiddleware(session_context=session_ctx)
        if session_ctx:
            logger.info("Injected recent session context (%d chars)", len(session_ctx))

        agent = create_deep_agent(
            model=model,
            tools=_build_tool_list(),
            system_prompt=compose_system_prompt(),
            middleware=[memory_mw, cards_mw, path_normalize_mw, write_claim_mw, date_mw, session_mw],
            backend=backend,
            skills=skill_sources,
            checkpointer=get_checkpointer(),
        )
        return agent, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _agent_holder() -> dict[str, Any]:
    """Return a mutable dict so we can lazily populate it on first use."""
    if not hasattr(_agent_holder, "_cache"):
        _agent_holder._cache = {"built": False, "agent": None, "error": None}
    return _agent_holder._cache


def get_agent():
    """Build (and cache) the deepagents agent, tolerating failures.

    If the build fails (e.g. a required provider is not configured),
    subsequent calls return a cached ``None`` and ``get_agent_status()``
    reflects the failure. The SSE route ``/api/agent/invoke`` translates
    that to HTTP 503 instead of crashing the request.

    .. warning::
        This function is **synchronous** — ``_try_build_agent()`` imports
        langchain modules and compiles the LangGraph, which can take
        several seconds.  Calling it from an async route handler blocks
        the entire event loop, preventing the ASGI server from sending
        SSE response headers and causing the first chat message after a
        backend restart to be silently dropped.

        Use :func:`ensure_agent_built` (async, non-blocking) from any
        ``async def`` context, or call :func:`prebuild_agent` during
        server startup.
    """
    holder = _agent_holder()
    if not holder["built"]:
        agent, error = _try_build_agent()
        holder["agent"] = agent
        holder["error"] = error
        holder["built"] = True
    return holder["agent"]


async def ensure_agent_built() -> None:
    """Build the agent in a thread pool (non-blocking).

    Unlike :func:`get_agent`, this runs ``_try_build_agent()`` via
    ``anyio.to_thread.run_sync`` so the event loop stays responsive.
    Use this from async route handlers instead of the synchronous
    ``get_agent()`` / ``get_agent_status()``.
    """
    holder = _agent_holder()
    if not holder["built"]:
        import anyio
        agent, error = await anyio.to_thread.run_sync(_try_build_agent)
        holder["agent"] = agent
        holder["error"] = error
        holder["built"] = True


def prebuild_agent() -> None:
    """Synchronously build the agent (for server startup).

    Safe to call from the FastAPI lifespan handler — blocks startup
    until the agent is ready, so the first request never triggers a
    lazy build.
    """
    get_agent()


def get_agent_status() -> dict[str, Any]:
    """Return the agent's last-known build status (non-blocking).

    Shape::

        {
            "agent_available": bool,
            "agent_error": str | None,
        }

    Returns the **cached** status without triggering a build.  If the
    agent hasn't been built yet, ``agent_available`` is ``False``.
    Use :func:`ensure_agent_built` to trigger a non-blocking build from
    async code, or :func:`prebuild_agent` during startup.
    """
    holder = _agent_holder()
    return {
        "agent_available": holder["agent"] is not None,
        "agent_error": holder["error"],
    }


def reset_agent_status() -> None:
    """Clear the cached agent build. Tests use this between cases."""
    if hasattr(_agent_holder, "_cache"):
        _agent_holder._cache = {"built": False, "agent": None, "error": None}


__all__ = [
    "ensure_agent_built",
    "get_agent",
    "get_agent_status",
    "prebuild_agent",
    "reset_agent_status",
    "ALL_TOOLS",
    "SYSTEM_PROMPT",
]
