"""NoteService — generate-or-fetch note for a node."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.domain.protocols import (
    LLMClientProtocol,
    SearchClientProtocol,
    SearchResult,
    WikiClientProtocol,
)
from src.domain.graph.models import Graph
from src.domain.note import NoteGenerator
from src.infrastructure.build_log import BuildLogger
from src.infrastructure.repository.graph_repo import GraphRepository
from src.infrastructure.repository.note_repo import NoteRepository


@dataclass
class NoteResult:
    domain: str
    node: str
    content: str
    created: bool


class NoteService:
    """Async note service: read existing or generate fresh."""

    def __init__(
        self,
        llm: LLMClientProtocol,
        graph_repo: GraphRepository,
        note_repo: NoteRepository,
        wiki_client: WikiClientProtocol | None = None,
        search_client: SearchClientProtocol | None = None,
        build_logger: BuildLogger | None = None,
    ) -> None:
        self._llm = llm
        self._graph_repo = graph_repo
        self._note_repo = note_repo
        self._wiki = wiki_client
        self._search = search_client
        self._build_log = build_logger
        self._generator = NoteGenerator(llm, wiki_client)

    async def get_or_generate(
        self, domain: str, node_name: str, *, force: bool = False
    ) -> NoteResult:
        existing = await self._note_repo.read_note(domain, node_name)
        if existing and not force:
            if self._build_log:
                await self._build_log.log_domain(
                    domain, "note-reused",
                    f"节点「{node_name}」笔记已存在，复用（{len(existing)} 字）",
                )
            return NoteResult(domain=domain, node=node_name, content=existing, created=False)

        if self._build_log:
            await self._build_log.log_domain(
                domain, "note-generate-start",
                f"开始生成节点「{node_name}」的笔记（搜索 + Wiki + LLM）",
            )

        ctx = await self._build_context(domain, node_name)
        wiki_def = ""
        search_results: list[SearchResult] = []
        if self._wiki and ctx.get("hierarchy_path"):
            wiki_def = await self._wiki.lookup(ctx["hierarchy_path"][-1])
        if self._search:
            try:
                search_results = await self._search.search(node_name, num_results=5)
            except Exception:
                search_results = []

        body = await self._generator.generate(
            node_name,
            domain,
            graph_ctx=ctx,
            wiki_def=wiki_def,
            search_results=search_results,
        )
        await self._note_repo.write_note(domain, node_name, body)

        if self._build_log:
            await self._build_log.log_domain(
                domain, "note-generated",
                f"节点「{node_name}」笔记生成完成（{len(body)} 字）",
            )

        return NoteResult(domain=domain, node=node_name, content=body, created=True)

    async def _build_context(self, domain: str, node_name: str) -> dict[str, Any]:
        """Build rich graph context for the note generator.

        Mirrors ``_helpers.gather_graph_context`` but works with the
        ``Graph`` domain model instead of raw dicts.  Provides parents,
        children, siblings, hierarchy path, and domain direction so the
        LLM can tailor note depth and emphasis.
        """
        graph = await self._graph_repo.read_graph(domain)
        target = graph.find_node(node_name)

        ctx: dict[str, Any] = {"domain": domain}

        # Domain direction
        direction = graph.direction
        parts = []
        for k in ("angle", "audience", "depth", "summary"):
            v = getattr(direction, k, "")
            if v:
                parts.append(f"{k}={v}")
        if parts:
            ctx["direction_summary"] = "；".join(parts)
        ctx["graph_summary"] = direction.summary

        ctx["total_nodes"] = len(graph.nodes)

        if target is None:
            ctx["parents"] = []
            ctx["children"] = []
            ctx["siblings"] = []
            ctx["hierarchy_path"] = f"{domain} - {node_name}"
            return ctx

        # Build parent / child maps from the graph
        parents_of: dict[str, list[str]] = {}
        children_of: dict[str, list[str]] = {}
        for n in graph.nodes:
            for child in n.links:
                parents_of.setdefault(child, []).append(n.name)
                children_of.setdefault(n.name, []).append(child)

        parents = parents_of.get(node_name, [])
        children = children_of.get(node_name, [])
        siblings: list[str] = []
        if parents:
            siblings = [
                x for x in children_of.get(parents[0], [])
                if x != node_name
            ]

        ctx["parents"] = parents
        ctx["children"] = children
        ctx["siblings"] = siblings

        # Hierarchy chain (walk up to root)
        chain: list[str] = []
        cur, seen = node_name, set()
        while cur and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            ps = parents_of.get(cur, [])
            cur = ps[0] if ps else ""
        chain.reverse()
        if not any(domain in c for c in chain):
            chain.insert(0, domain)
        ctx["hierarchy_path"] = (
            " - ".join(chain) if chain else f"{domain} - {node_name}"
        )

        return ctx


__all__ = ["NoteService", "NoteResult"]