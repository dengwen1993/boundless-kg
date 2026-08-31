"""Integration test fixtures — wire up a FastAPI TestClient against a temp KB root."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.agent import dependencies as agent_deps
from src.api import dependencies as api_deps
from src.api.server import create_app
from src.application.generation_pipeline import GenerationPipeline
from src.application.graph_service import GraphService
from src.application.note_service import NoteService
from src.application.plan_service import PlanService
from src.application.resource_service import ResourceService
from src.application.timeline_service import TimelineService
from src.infrastructure.llm import MockLLMClient
from src.infrastructure.repository.graph_repo import GraphRepository
from src.infrastructure.repository.note_repo import NoteRepository
from src.infrastructure.repository.plan_repo import PlanRepository
from src.infrastructure.repository.resource_repo import ResourceRepository
from src.infrastructure.repository.timeline_repo import TimelineRepository
from src.infrastructure.search.base import AsyncSearchClient, SearchResult
from src.observability.activity_bus import (
    get_activity_bus,
    reset_activity_bus,
)
from src.observability.activity_log import (
    FileActivityLog,
    get_activity_log,
    reset_activity_log,
)
from src.observability.activity_reader import (
    ActivityReader,
    get_activity_reader,
    reset_activity_reader,
)


class _NoopSearch(AsyncSearchClient):
    """Search backend that always returns empty."""

    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        return []


@pytest.fixture
def app_with_overrides(tmp_kb_root: Path, tmp_workspace_dir: Path, monkeypatch):
    """Build a FastAPI app with dependency overrides pointing at tmp_kb_root.

    Several routes (plans / timeline / graph) do file IO through
    ``_helpers.kb_root()`` rather than an injected service, so the KB root
    itself must be redirected as well — otherwise those routes read the
    developer's real ``knowledge_bases/`` directory.

    Activity timeline wiring
    ------------------------

    FastAPI's ``TestClient`` does NOT trigger ``lifespan`` events, so we
    manually wire the activity bus + log subscriber here.  We register a
    fresh ``FileActivityLog`` against the (cleared) singleton bus so any
    event emitted during a request lands in
    ``tmp_kb_root/<domain>/activity/<date>.jsonl``.
    """
    from src.config import settings as settings_mod

    monkeypatch.setenv("KG_KB_ROOT", str(tmp_kb_root))
    # Operational artefacts (_staging/, _pipeline/) live in the
    # workspace, NOT inside kb_root — point KG_WORKSPACE_DIR at a
    # dedicated sibling so files written by the SUT don't leak into
    # the curated domain tree.
    monkeypatch.setenv("KG_WORKSPACE_DIR", str(tmp_workspace_dir))
    settings_mod.get_settings.cache_clear()
    # Disable the 0.05s/keyword sleep in GenerationPipeline._collect_hot_keywords
    # so pipeline tests finish quickly.
    from src.application import generation_pipeline as gp

    _real_sleep = gp.asyncio.sleep

    async def _zero_sleep(*_args, **_kwargs):
        return await _real_sleep(0)

    monkeypatch.setattr(gp.asyncio, "sleep", _zero_sleep)
    llm = MockLLMClient(latency_sec=0)
    graph_repo = GraphRepository(tmp_kb_root)
    note_repo = NoteRepository(tmp_kb_root)
    resource_repo = ResourceRepository(tmp_kb_root, workspace_dir=tmp_workspace_dir)
    plan_repo = PlanRepository(tmp_kb_root)
    timeline_repo = TimelineRepository(tmp_kb_root)

    graph_svc = GraphService(graph_repo)
    note_svc = NoteService(llm, graph_repo, note_repo)
    resource_svc = ResourceService(llm, graph_repo, resource_repo, search_client=None)
    plan_svc = PlanService(plan_repo)
    timeline_svc = TimelineService(timeline_repo)
    pipeline = GenerationPipeline(llm, graph_svc, search_client=_NoopSearch())

    app = create_app()

    app.dependency_overrides[api_deps.graph_service_dep] = lambda: graph_svc
    app.dependency_overrides[api_deps.note_service_dep] = lambda: note_svc
    app.dependency_overrides[api_deps.resource_service_dep] = lambda: resource_svc
    app.dependency_overrides[api_deps.plan_service_dep] = lambda: plan_svc
    app.dependency_overrides[api_deps.timeline_service_dep] = lambda: timeline_svc
    app.dependency_overrides[api_deps.generation_pipeline_dep] = lambda: pipeline

    # /api/notes/{domain} hits get_note_repo() directly — wire that too.
    monkey = pytest.MonkeyPatch()
    monkey.setattr(agent_deps, "get_note_repo", lambda: note_repo)
    monkey.setattr(agent_deps, "get_graph_repo", lambda: graph_repo)
    monkey.setattr(agent_deps, "get_resource_repo", lambda: resource_repo)
    monkey.setattr(agent_deps, "get_plan_repo", lambda: plan_repo)
    monkey.setattr(agent_deps, "get_note_llm", lambda: llm)

    # -- Activity timeline wiring --
    # Reset all observability singletons so each test starts clean.
    reset_activity_bus()
    reset_activity_log()
    reset_activity_reader()
    bus = get_activity_bus()
    log = FileActivityLog(tmp_kb_root, bus=bus)
    # Register synchronously (start() awaits — fine, no other subscribers yet).
    asyncio.run(log.start())
    # Install the log + reader as singletons so route handlers pick them up.
    monkey.setattr(
        "src.observability.activity_log.get_activity_log",
        lambda: log,
    )
    monkey.setattr(
        "src.observability.activity_reader.get_activity_reader",
        lambda: ActivityReader(tmp_kb_root),
    )
    monkey.setattr(
        "src.observability.activity_bus.get_activity_bus",
        lambda: bus,
    )

    yield app, {
        "graph_svc": graph_svc,
        "note_svc": note_svc,
        "resource_svc": resource_svc,
        "plan_svc": plan_svc,
        "timeline_svc": timeline_svc,
        "pipeline": pipeline,
        "graph_repo": graph_repo,
        "activity_log": log,
        "activity_bus": bus,
    }

    # Teardown — unsubscribe + clear overrides + reset singletons.
    try:
        asyncio.run(log.stop())
    except Exception:
        pass
    app.dependency_overrides.clear()
    monkey.undo()
    reset_activity_bus()
    reset_activity_log()
    reset_activity_reader()
    settings_mod.get_settings.cache_clear()


@pytest.fixture
def client(app_with_overrides) -> TestClient:
    app, _ = app_with_overrides
    return TestClient(app)