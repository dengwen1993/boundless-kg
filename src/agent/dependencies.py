"""Agent dependencies — lazily-built service singletons."""

from __future__ import annotations

from functools import lru_cache

from src.application.association_service import AssociationService
from src.application.card_service import CardService
from src.application.dossier_service import DossierService
from src.application.generation_pipeline import GenerationPipeline
from src.application.graph_service import GraphService
from src.application.graph_sync_service import GraphSyncService
from src.application.note_service import NoteService
from src.application.plan_service import PlanService
from src.application.resource_service import ResourceService
from src.application.search_service import SearchService
from src.application.timeline_service import TimelineService
from src.config import (
    get_kb_root,
    get_settings,
    get_workspace_dir,
)
from src.infrastructure.llm import (
    create_generation_llm_client,
    create_llm_client,
    create_note_llm_client,
)
from src.infrastructure.build_log import BuildLogger
from src.infrastructure.embedding.bm25 import BM25Index
from src.infrastructure.embedding.client import EmbeddingClient
from src.infrastructure.graph_store.client import GraphStoreClient
from src.infrastructure.pipeline_state_store import PipelineStateStore
from src.infrastructure.repository.association_repo import AssociationRepository
from src.infrastructure.repository.dossier_repo import DossierRepository
from src.infrastructure.repository.graph_repo import GraphRepository
from src.infrastructure.repository.note_repo import NoteRepository
from src.infrastructure.repository.plan_repo import PlanRepository
from src.infrastructure.repository.resource_repo import ResourceRepository
from src.infrastructure.repository.timeline_repo import TimelineRepository
from src.infrastructure.search import (
    BochaSearchClient,
    DualSearchClient,
    MmxSearchClient,
    detect_proxy,
)
from src.infrastructure.search.mmx import DuckDuckGoClient
from src.infrastructure.search.preference import SearchPreferenceStore
from src.agent.cards.library import CardLibrary
from src.infrastructure.wiki import AsyncWikiClient


@lru_cache(maxsize=1)
def get_llm():
    """LLM client for business flows (graph / notes / resources).

    Reads ``KG_GENERATION_LLM_PROVIDER`` (falling back to
    ``KG_LLM_PROVIDER``) via :func:`create_generation_llm_client`.
    The agent chat layer is built separately by
    ``src.agent.orchestrator._build_model`` and reads
    ``KG_LLM_PROVIDER`` directly, so this pair can target two
    different providers (e.g. deepseek for generation,
    MiniMax for chat).
    """
    return create_generation_llm_client()


@lru_cache(maxsize=1)
def get_note_llm():
    """LLM client for note generation only.

    Reads ``KG_NOTE_LLM_PROVIDER`` (falling back to
    ``KG_GENERATION_LLM_PROVIDER`` → ``KG_LLM_PROVIDER``). Useful when
    notes want a faster/cheaper model than the rest of the generation
    pipeline (e.g. deepseek-v4-flash for notes while graph generation
    stays on deepseek-v4-pro).
    """
    return create_note_llm_client()


@lru_cache(maxsize=1)
def get_graph_repo() -> GraphRepository:
    return GraphRepository(get_kb_root())


@lru_cache(maxsize=1)
def get_note_repo() -> NoteRepository:
    return NoteRepository(get_kb_root())


@lru_cache(maxsize=1)
def get_resource_repo() -> ResourceRepository:
    return ResourceRepository(get_kb_root(), workspace_dir=get_workspace_dir())


@lru_cache(maxsize=1)
def get_plan_repo() -> PlanRepository:
    return PlanRepository(get_kb_root())


@lru_cache(maxsize=1)
def get_timeline_repo() -> TimelineRepository:
    return TimelineRepository(get_kb_root())


@lru_cache(maxsize=1)
def get_association_repo() -> AssociationRepository:
    return AssociationRepository(get_kb_root())


@lru_cache(maxsize=1)
def get_dossier_repo() -> DossierRepository:
    """节点档案 repository — 读 notes/{node}/dossier.json。"""
    return DossierRepository(get_kb_root())


@lru_cache(maxsize=1)
def get_dossier_service() -> DossierService:
    """节点档案业务服务。

    依赖 graph_store + embedding_client 做向量召回和派生同步;
    失败时 (deps=None) 也能跑(只 BM25)。
    """
    return DossierService(
        dossier_repo=get_dossier_repo(),
        embedding_client=get_embedding_client(),
        graph_store=get_graph_store(),
    )


def get_dossier_reflector():
    """异步归档反射器(单例)。

    持有 LLM + DossierService 实例,用于在 Agent 主响应后异步判定
    并归档可复用经验。
    """
    from src.agent.reflection.dossier_reflector import DossierReflector
    return DossierReflector(
        llm=get_llm(),
        dossier_service=get_dossier_service(),
    )


@lru_cache(maxsize=1)
def get_association_service() -> AssociationService:
    return AssociationService(
        llm=get_llm(),
        assoc_repo=get_association_repo(),
        graph_store=get_graph_store(),
    )


@lru_cache(maxsize=1)
def get_graph_store() -> GraphStoreClient:
    """FalkorDB graph store client singleton."""
    return GraphStoreClient()


@lru_cache(maxsize=1)
def get_embedding_client() -> EmbeddingClient:
    """Embedding API client singleton."""
    return EmbeddingClient()


@lru_cache(maxsize=1)
def get_bm25_index() -> BM25Index:
    """BM25 keyword index singleton."""
    return BM25Index()


@lru_cache(maxsize=1)
def get_search_service() -> SearchService:
    """Hybrid search service singleton."""
    return SearchService(
        graph_store=get_graph_store(),
        embedding_client=get_embedding_client(),
        bm25_index=get_bm25_index(),
    )


def get_graph_sync_service(domain: str) -> GraphSyncService:
    """Create a GraphSyncService for a specific domain."""
    return GraphSyncService(
        domain,
        graph_repo=get_graph_repo(),
        note_repo=get_note_repo(),
        resource_repo=get_resource_repo(),
        plan_repo=get_plan_repo(),
        graph_store=get_graph_store(),
        embedding_client=get_embedding_client(),
        bm25_index=get_bm25_index(),
        association_repo=get_association_repo(),
        dossier_repo=get_dossier_repo(),
    )


@lru_cache(maxsize=1)
def get_wiki_client() -> AsyncWikiClient:
    """Proxy-aware wiki client.

    Proxy is auto-detected on every ``lookup()`` call (env vars, port
    probes) so the client adapts when the proxy appears or disappears.
    """
    return AsyncWikiClient()


@lru_cache(maxsize=1)
def get_search_preference_store() -> SearchPreferenceStore:
    """Build the singleton :class:`SearchPreferenceStore`.

    Persists to ``<KG_AGENT_MEMORY_DIR>/search_preference.json`` —
    resolved by passing :func:`src.config.get_workspace_dir` to
    :class:`SearchPreferenceStore`, which appends ``.agent_memory``
    internally (default ``./workspace/.agent_memory``). Keeping this
    file next to the conversation logs + ``tmp/`` transient files
    means the agent's runtime state is co-located in one place,
    away from the curated ``knowledge_bases/`` domain tree.

    Tests call :func:`reset_dependencies` to wipe this singleton.
    """
    store = SearchPreferenceStore(get_workspace_dir())
    store.load()
    return store


@lru_cache(maxsize=1)
def get_search_client() -> DualSearchClient:
    """Build the proxy-aware multi-backend search client.

    * Proxy detected (env var / Windows registry / port probe) →
      DuckDuckGo (primary, via proxy) → mmx → Bocha (fallback).
    * No proxy → mmx → Bocha.

    Bocha is only injected when ``BOCHA_API_KEY`` is set; otherwise
    the chain silently omits it so deployments without a Bocha key
    keep working unchanged.

    When ``KG_SEARCH_ADAPTIVE=true`` (default) the client also takes
    a :class:`SearchPreferenceStore` so it can promote the last
    successful backend and quarantine failures — see
    ``src/infrastructure/search/preference.py``. Set the env var to
    ``false`` for audits / reproducible demos.
    """
    s = get_settings().search

    # Resolve proxy: explicit DDG_PROXY setting wins, else auto-detect.
    proxy = s.ddg_proxy or detect_proxy()

    ddg = DuckDuckGoClient(proxy=proxy)
    mmx = MmxSearchClient(
        api_key=s.mmx_api_key.get_secret_value() if s.mmx_api_key else None,
        region=s.mmx_region,
        proxy=s.mmx_proxy,
    )

    bocha: BochaSearchClient | None = None
    if s.bocha_api_key is not None:
        # BochaSearchClient raises if api_key is empty, but we already
        # guarded against that via ``is not None`` — the SecretStr
        # wrapper will throw before reaching an empty-string check.
        bocha = BochaSearchClient(
            api_key=s.bocha_api_key.get_secret_value(),
            endpoint=s.bocha_endpoint,
            proxy=s.bocha_proxy,
            timeout_sec=float(s.bocha_timeout_sec or 15.0),
            default_count=int(s.bocha_count or 10),
        )

    pref = get_search_preference_store() if s.adaptive else None
    return DualSearchClient(
        ddg=ddg,
        mmx=mmx,
        bocha=bocha,
        proxy=proxy,
        adaptive=bool(s.adaptive),
        preference_store=pref,
    )


@lru_cache(maxsize=1)
def get_graph_service() -> GraphService:
    return GraphService(get_graph_repo())


@lru_cache(maxsize=1)
def get_note_service() -> NoteService:
    return NoteService(
        get_llm(),
        get_graph_repo(),
        get_note_repo(),
        wiki_client=get_wiki_client(),
        search_client=get_search_client(),
        build_logger=get_build_logger(),
    )


@lru_cache(maxsize=1)
def get_resource_service() -> ResourceService:
    return ResourceService(get_llm(), get_graph_repo(), get_resource_repo(), get_search_client())


@lru_cache(maxsize=1)
def get_plan_service() -> PlanService:
    return PlanService(get_plan_repo())


@lru_cache(maxsize=1)
def get_timeline_service() -> TimelineService:
    return TimelineService(get_timeline_repo())


@lru_cache(maxsize=1)
def get_build_logger() -> BuildLogger:
    return BuildLogger(get_kb_root())


@lru_cache(maxsize=1)
def get_pipeline_state_store() -> PipelineStateStore:
    """Persistent pipeline state store.

    Survives backend restarts so ``kg_check_status`` can still return
    the last known stage / progress for tasks that were running before
    the restart.  State lives under ``workspace_dir/_pipeline/`` so it
    doesn't pollute the knowledge-base namespace.
    """
    store = PipelineStateStore(get_workspace_dir())
    # ``load()`` is async; we schedule it on the running loop if one
    # exists.  In practice the server lifespan handler calls
    # ``await get_pipeline_state_store().load()`` during startup.
    return store


@lru_cache(maxsize=1)
def get_card_library() -> CardLibrary:
    """The shared, hot-reloadable card library.

    Built from ``KG_AGENT_CARDS_DIR`` (or the package default
    ``src/agent/cards/data/``). The same instance is shared by both
    :class:`CardsMiddleware` and :class:`CardService` so that after the
    agent writes / deletes a card file, calling ``reload()`` on this
    singleton updates the middleware's view immediately.
    """
    from src.config.settings import get_agent_cards_dir
    return CardLibrary.from_directory(get_agent_cards_dir())


@lru_cache(maxsize=1)
def get_card_service() -> CardService:
    """Card CRUD service backed by the shared :func:`get_card_library`."""
    return CardService(get_card_library())


@lru_cache(maxsize=1)
def get_generation_pipeline() -> GenerationPipeline:
    return GenerationPipeline(
        get_llm(),
        get_graph_service(),
        get_search_client(),
        build_logger=get_build_logger(),
        state_store=get_pipeline_state_store(),
    )


def reset_dependencies() -> None:
    """Clear every cached service. Tests use this between cases."""
    for fn in (
        get_llm,
        get_note_llm,
        get_graph_repo,
        get_note_repo,
        get_resource_repo,
        get_plan_repo,
        get_timeline_repo,
        get_association_repo,
        get_association_service,
        get_search_preference_store,
        get_search_client,
        get_wiki_client,
        get_graph_service,
        get_note_service,
        get_resource_service,
        get_plan_service,
        get_timeline_service,
        get_build_logger,
        get_pipeline_state_store,
        get_generation_pipeline,
        get_card_library,
        get_card_service,
        get_graph_store,
        get_embedding_client,
        get_bm25_index,
        get_search_service,
        get_dossier_repo,
        get_dossier_service,
    ):
        fn.cache_clear()


__all__ = [
    "get_llm",
    "get_note_llm",
    "get_graph_repo",
    "get_note_repo",
    "get_resource_repo",
    "get_plan_repo",
    "get_timeline_repo",
    "get_association_repo",
    "get_association_service",
    "get_search_client",
    "get_search_preference_store",
    "get_wiki_client",
    "get_graph_service",
    "get_note_service",
    "get_resource_service",
    "get_plan_service",
    "get_timeline_service",
    "get_build_logger",
    "get_pipeline_state_store",
    "get_generation_pipeline",
    "get_card_library",
    "get_card_service",
    "get_graph_store",
    "get_embedding_client",
    "get_bm25_index",
    "get_search_service",
    "get_graph_sync_service",
    "get_dossier_repo",
    "get_dossier_service",
    "reset_dependencies",
]