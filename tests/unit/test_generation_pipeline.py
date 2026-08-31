"""GenerationPipeline — three-stage orchestration, registry, errors."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.application.generation_pipeline import (
    GenerationPipeline,
    PipelineProgress,
)
from src.application.graph_service import GraphService
from src.domain.intent.models import IntentMeta
from src.infrastructure.llm import MockLLMClient
from src.infrastructure.repository.graph_repo import GraphRepository
from src.infrastructure.search.base import AsyncSearchClient, SearchResult


class _FakeSearch(AsyncSearchClient):
    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results
        self.calls: list[str] = []

    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        self.calls.append(query)
        return self._results


def _mk_pipeline(tmp_kb_root: Path, llm=None, search=None) -> GenerationPipeline:
    return GenerationPipeline(
        llm or MockLLMClient(latency_sec=0),
        GraphService(GraphRepository(tmp_kb_root)),
        search_client=search,
    )


async def test_start_returns_task_id_and_eventually_completes(tmp_kb_root: Path) -> None:
    pipe = _mk_pipeline(tmp_kb_root)
    task_id = await pipe.start("topic")
    assert isinstance(task_id, str)
    # Poll until done.
    for _ in range(50):
        p = await pipe.status(task_id)
        if p is not None and p.finished_at is not None:
            break
        await asyncio.sleep(0.05)
    p = await pipe.status(task_id)
    assert p.finished_at is not None
    assert p.stage in {"done", "error"}


async def test_start_writes_graph_to_repo(tmp_kb_root: Path) -> None:
    pipe = _mk_pipeline(tmp_kb_root)
    task_id = await pipe.start("RAG")
    for _ in range(50):
        p = await pipe.status(task_id)
        if p is not None and p.finished_at is not None:
            break
        await asyncio.sleep(0.05)
    saved = await GraphRepository(tmp_kb_root).read_graph("RAG")
    assert saved.domain == "RAG"


async def test_pipeline_handles_search_failure_gracefully(tmp_kb_root: Path) -> None:
    class BoomSearch(AsyncSearchClient):
        async def search(self, query, *, num_results=10):
            raise RuntimeError("search is down")

    pipe = _mk_pipeline(tmp_kb_root, search=BoomSearch())
    task_id = await pipe.start("topic")
    for _ in range(50):
        p = await pipe.status(task_id)
        if p is not None and p.finished_at is not None:
            break
        await asyncio.sleep(0.05)
    # Should still complete (stage "done"), not "error".
    p = await pipe.status(task_id)
    assert p.stage == "done"


async def test_pipeline_uses_search_results(tmp_kb_root: Path) -> None:
    fake = _FakeSearch([SearchResult(title="x", link="l", snippet="s")])
    pipe = _mk_pipeline(tmp_kb_root, search=fake)
    task_id = await pipe.start("topic")
    for _ in range(50):
        p = await pipe.status(task_id)
        if p is not None and p.finished_at is not None:
            break
        await asyncio.sleep(0.05)
    assert fake.calls  # pipeline made at least one search call


async def test_status_returns_none_for_unknown_task(tmp_kb_root: Path) -> None:
    pipe = _mk_pipeline(tmp_kb_root)
    assert await pipe.status("does-not-exist") is None


async def test_pipeline_progress_defaults() -> None:
    p = PipelineProgress(task_id="t", domain="d")
    assert p.stage == "init"
    assert p.progress == 0.0
    assert p.started_at is not None
    assert p.finished_at is None
    assert p.error is None