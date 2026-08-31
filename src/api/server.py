"""FastAPI app factory + health check + static frontend serving."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.agent.orchestrator import get_agent_status, prebuild_agent
from src.agent.memory import TMP_MAX_AGE_DAYS, cleanup_tmp
from src.api.middleware import DomainError, domain_error_handler, install_middlewares
from src.api.routes import agent, graph, notes, plans, resources, timeline, memory, associations, search, tmp_uploads
from src.config import get_kb_root
from src.observability.activity_log import start_activity_log, stop_activity_log

logger = logging.getLogger(__name__)

#: How often (seconds) the background tmp-cleanup task sweeps the
#: agent-memory tmp directory. 24h strikes a balance between not
#: waking up too often and not letting stale files linger past the
#: 7-day retention window in :data:`TMP_MAX_AGE_DAYS`.
_TMP_CLEANUP_INTERVAL_SEC: float = 24 * 60 * 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-build the deepagents agent on startup.

    Without this, the agent is lazily built on the first ``/api/agent/invoke``
    request.  The build is synchronous (imports langchain, compiles the
    LangGraph) and blocks the event loop for several seconds, which delays
    SSE response headers and causes the Vite dev-server proxy to drop the
    connection — the user sees "first message after restart is lost".

    Pre-building on startup ensures the agent is ready before any request
    is accepted.  If the build fails (e.g. missing API key), the server
    still starts; the health endpoint reports the error and chat returns 503.
    """
    # Initialise logging on server startup so importing this module
    # (e.g. in tests) does NOT trigger file-handler side effects.
    from src.observability.logging_config import setup_logging
    setup_logging()

    # Activity-timeline observer: register the JSONL subscriber so any
    # event emitted by write-points is captured even if the request that
    # triggered it returned 5xx.  Registration must succeed before the
    # first request is served.
    await start_activity_log()

    # Derivation subscribers: register a per-domain subscriber so
    # associations.json / FalkorDB is kept in sync with knowledge_bases state.
    # The subscriber reacts to ActivityBus events (NODE_CREATED, NOTE_GENERATED,
    # ...) and runs GraphSyncService for the affected domain.
    from src.agent.dependencies import (
        get_graph_repo,
        get_note_repo,
        get_resource_repo,
        get_plan_repo,
        get_graph_store,
        get_embedding_client,
        get_bm25_index,
        get_graph_sync_service,
    )
    from src.observability.derivation_subscriber import (
        get_or_create_derivation_subscriber,
        MultiDomainDerivationDispatcher,
    )
    try:
        domains = await get_graph_repo().list_domains()
        for domain in domains:
            sync_svc = get_graph_sync_service(domain)
            sub = await get_or_create_derivation_subscriber(domain, sync_service=sync_svc)
            await sub.start()
        logger.info(
            "DerivationSubscribers registered for %d domain(s)", len(domains)
        )
        # 注册全局 dispatcher — 自动为新 domain 创建 subscriber
        dispatcher = MultiDomainDerivationDispatcher()
        await dispatcher.start()
    except Exception:
        logger.warning(
            "Failed to register DerivationSubscribers", exc_info=True
        )

    # Load persisted pipeline state so kg_check_status can return
    # task progress from before a restart.
    from src.agent.dependencies import get_pipeline_state_store
    await get_pipeline_state_store().load()

    logger.info("Pre-building deepagents agent …")
    prebuild_agent()
    status = get_agent_status()
    if status["agent_available"]:
        logger.info("Agent ready ✓")
    else:
        logger.warning("Agent build failed: %s — chat will return 503", status["agent_error"])

    # Background tmp-cleanup task: sweeps
    # ``.agent_memory/tmp/`` every ``_TMP_CLEANUP_INTERVAL_SEC`` seconds
    # and deletes files older than :data:`TMP_MAX_AGE_DAYS`. Cancellable
    # via ``app.state._tmp_cleanup_task`` on shutdown so we don't leak a
    # pending sleep on FastAPI's lifespan exit.
    async def _tmp_cleanup_loop() -> None:
        try:
            while True:
                try:
                    removed = await asyncio.to_thread(
                        cleanup_tmp, TMP_MAX_AGE_DAYS
                    )
                    if removed:
                        logger.info(
                            "[tmp-cleanup] removed %d stale file(s) older "
                            "than %d days",
                            removed,
                            TMP_MAX_AGE_DAYS,
                        )
                except Exception:
                    logger.warning(
                        "[tmp-cleanup] iteration failed", exc_info=True
                    )
                await asyncio.sleep(_TMP_CLEANUP_INTERVAL_SEC)
        except asyncio.CancelledError:
            logger.info("[tmp-cleanup] background task cancelled")
            raise

    cleanup_task = asyncio.create_task(_tmp_cleanup_loop(), name="tmp-cleanup")
    app.state._tmp_cleanup_task = cleanup_task

    yield

    # Shutdown — unsubscribe so a long-running handler isn't left holding
    # onto a stale bus reference.
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    await stop_activity_log()

    # Stop derivation subscribers + flush LLM buffers
    try:
        from src.observability.derivation_subscriber import reset_derivation_subscribers
        from src.api.routes._buffer_singleton import reset_buffers
        # 最后一次 flush buffer，然后关闭
        await reset_buffers()
        await reset_derivation_subscribers()
    except Exception:
        logger.warning("shutdown derivation cleanup failed", exc_info=True)


def create_app() -> FastAPI:
    app = FastAPI(
        title="BoundlessKG API",
        version="2.0.0",
        description="Knowledge-graph curation engine — Vue frontend backend",
        lifespan=lifespan,
    )
    install_middlewares(app)
    app.add_exception_handler(DomainError, domain_error_handler)

    # ---- Routers ----
    app.include_router(graph.router)
    app.include_router(notes.router)
    app.include_router(resources.router)
    app.include_router(plans.router)
    app.include_router(timeline.router)
    app.include_router(agent.router)
    app.include_router(memory.router)
    app.include_router(associations.router)
    app.include_router(search.router)
    app.include_router(tmp_uploads.router)

    # ---- Health + meta ----
    @app.get("/api/health")
    async def health() -> dict:
        # Reports real deepagents build status so the frontend can show
        # an explanatory banner when chat is unavailable (e.g. the LLM
        # API key is missing or the chosen provider doesn't support
        # tool calling).
        status = get_agent_status()
        return {
            "ok": True,
            "agent_available": status["agent_available"],
            "agent_error": status["agent_error"],
            "kb_root": str(get_kb_root()),
        }

    @app.get("/api/categories")
    async def categories() -> dict:
        """Return the canonical resource category list.

        The frontend reads this to populate dropdowns and filter chips
        instead of hard-coding the list in Vue.
        """
        from src.domain.resource.categories import RESOURCE_CATEGORIES

        return {"categories": list(RESOURCE_CATEGORIES)}

    @app.get("/api/domain-pack")
    async def domain_pack() -> dict:
        """Return the active domain-pack metadata.

        Phase-1 stub — currently only reports the default pack name.
        Phase-2 will return pack-specific config (prompt templates,
        tool sets, category overrides).
        """
        return {"pack": "default", "version": "1.0"}

    # ---- Static frontend (production — serve built Vue app) ----
    dist_dir = Path(__file__).resolve().parents[2] / "frontend-vue" / "dist"
    if (dist_dir / "index.html").exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount(
                "/assets",
                StaticFiles(directory=str(assets_dir)),
                name="assets",
            )
        index_html = dist_dir / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str, request: Request):
            """SPA catch-all — non-/api paths return index.html."""
            if full_path.startswith("api/"):
                return FileResponse(str(index_html), status_code=404)
            return FileResponse(str(index_html))
    else:

        @app.get("/", include_in_schema=False)
        async def _placeholder() -> dict:
            return {
                "message": "BoundlessKG API. Frontend not built — run `npm run dev` in frontend-vue/.",
                "docs": "/docs",
                "health": "/api/health",
            }

    return app


app = create_app()


__all__ = ["create_app", "app"]
