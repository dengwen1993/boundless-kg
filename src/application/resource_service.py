"""ResourceService — search, stage, classify, persist."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.domain.graph.models import Graph
from src.domain.protocols import (
    LLMClientProtocol,
    SearchClientProtocol,
    SearchResult,
    WikiClientProtocol,
)
from src.domain.resource import (
    AutoClassifyDecision,
    auto_classify_async,
    classify_pending_async,
    stage_to_node,
)
from src.infrastructure.repository.graph_repo import GraphRepository
from src.infrastructure.repository.resource_repo import ResourceRepository
from src.observability.activity_bus import ActivityKind, get_activity_bus


@dataclass
class ResourceItem:
    """Normalised resource item — field names match the on-disk JSON shape.

    Uses ``url`` / ``summary`` (not ``link`` / ``snippet``) so the
    domain model, the repository layer, and the API response all share
    one vocabulary.
    """
    domain: str
    node: str
    title: str
    url: str
    summary: str
    added_at: str
    # 资料类型（论文/视频/课程/代码/文档/教程/书籍/网页/其他）。
    category: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class ResourceService:
    """High-level resource orchestration."""

    def __init__(
        self,
        llm: LLMClientProtocol,
        graph_repo: GraphRepository,
        resource_repo: ResourceRepository,
        search_client: SearchClientProtocol | None = None,
    ) -> None:
        self._llm = llm
        self._graph_repo = graph_repo
        self._resource_repo = resource_repo
        self._search = search_client

    async def search(
        self,
        domain: str,
        query: str,
        node: str | None = None,
        *,
        num_results: int = 10,
    ) -> list[ResourceItem]:
        if self._search is None:
            return []
        results = await self._search.search(query, num_results=num_results)
        return [
            ResourceItem(
                domain=domain,
                node=node or "",
                title=r.title,
                url=r.link,
                summary=r.snippet,
                added_at=datetime.utcnow().isoformat(),
                category=r.category or "",
            )
            for r in results
        ]

    async def list(self, domain: str, node: str | None = None) -> list[dict[str, Any]]:
        return await self._resource_repo.list_resources(domain, node)

    async def stage_upload(
        self, src: Path, suggested_name: str
    ) -> Path:
        return await self._resource_repo.stage_upload(src, suggested_name)

    async def classify_and_promote(
        self,
        domain: str,
        staged_path: Path,
        file_meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Classify a staged upload and promote it into the right node."""
        graph = await self._graph_repo.read_graph(domain)
        decision = await classify_pending_async(file_meta, graph, self._llm)
        if decision.is_new:
            target_node = decision.new_node_name or "新节点"
            await self._graph_repo.add_node(domain, target_node)
            # Activity timeline — agent created a brand-new node during
            # classify-and-promote; emit so the timeline sees it.
            await get_activity_bus().emit(
                ActivityKind.NODE_CREATED,
                domain=domain,
                node=target_node,
                title=f"新建了节点「{target_node}」",
                source="agent",
                ref=f"node:{target_node}",
                extra={"created_by": "classify_and_promote"},
            )
        else:
            target_node = decision.node
        promoted = await self._resource_repo.promote_staged(staged_path, domain, target_node)
        return {
            "node": target_node,
            "path": str(promoted),
            "rationale": decision.rationale,
        }

    async def auto_place_upload(
        self,
        domain: str,
        tmp_path: Path,
        filename: str,
        parsed: dict[str, Any],
        *,
        create_new_node: bool = True,
    ) -> dict[str, Any]:
        """Classify a parsed tmp upload into the right node and persist it.

        ``parsed`` is the dict returned by
        :func:`src.application.tmp_parser.parse_file_to_text` —
        carries the text preview plus structured hints (pages, slides,
        author …).

        Pipeline (see :mod:`src.domain.resource.auto_classify` for the
        layered classifier):

        1. Read ``<kb>/<domain>/knowledge_graph.json`` (the outline).
        2. Run the layered classifier (deterministic → LLM fallback).
        3. If decision is ``matched`` and references an existing node →
           copy file into that node's ``user_uploads/``.
        4. If decision is ``matched`` and references a new node name →
           create the node first (when ``create_new_node=True``).
        5. If decision status is ``needs_review`` / ``llm_failed`` /
           ``no_graph`` → DO NOT create any node, leave the tmp file
           in place, return the candidate list so the caller can show
           it to the user. This is the safeguard added for
           `BUG-2026-08-26-001` — we never auto-create placeholder
           nodes like ``未命名资料``.

        Returns ``{node, path, rationale, new_node_created,
        needs_review, candidates, ...}``. The ``needs_review=True`` branch
        also sets ``skipped`` so callers can distinguish "did nothing"
        from "succeeded".
        """
        from src.application.tmp_parser import parse_file_to_text  # noqa: F401

        graph = await self._graph_repo.read_graph(domain)

        # ``Graph`` is a domain object; the classifier wants a plain
        # dict that mirrors ``knowledge_graph.json`` on disk. We try
        # Pydantic's ``model_dump()`` first, then fall back to a
        # hand-rolled mapping for older non-Pydantic implementations.
        graph_dict: dict[str, Any] | None
        if hasattr(graph, "model_dump"):
            try:
                graph_dict = graph.model_dump()
            except Exception:
                graph_dict = None
        elif hasattr(graph, "to_dict"):
            graph_dict = graph.to_dict()
        else:
            try:
                graph_dict = {
                    "domain": getattr(graph, "domain", domain),
                    "nodes": [
                        {"name": n.name, "links": list(n.links or [])}
                        for n in (getattr(graph, "nodes", []) or [])
                    ],
                }
            except Exception:
                graph_dict = None

        decision: AutoClassifyDecision = await auto_classify_async(
            filename=filename,
            parsed=parsed,
            graph=graph_dict,
            llm_client=self._llm,
            extra_hints={
                "format": parsed.get("format"),
                "size": parsed.get("size"),
                **{
                    k: v
                    for k, v in (parsed.get("hints") or {}).items()
                    if k in {"pages", "slides", "sheets", "rows"}
                },
            },
        )

        # --- Safeguard: any non-matched status means "ask the user" ---
        # Per BUG-2026-08-26-001 we MUST NOT auto-create a node when the
        # classifier is uncertain. Tmp file is left in place so the user
        # can retry / pick from candidates.
        if decision.status in {"needs_review", "llm_failed", "no_graph"}:
            return self._auto_place_needs_review(
                domain=domain,
                tmp_path=tmp_path,
                filename=filename,
                parsed=parsed,
                decision=decision,
            )

        new_node_created = False
        target_node: str = ""
        if decision.is_new:
            if not create_new_node:
                return {
                    "node": "",
                    "path": "",
                    "rationale": decision.rationale
                    or "LLM 建议新建节点，但 create_new_node=false",
                    "new_node_created": False,
                    "status": "needs_review",
                    "decision": {
                        "node": decision.node,
                        "new_node_name": decision.new_node_name,
                        "rationale": decision.rationale,
                        "is_new": decision.is_new,
                        "status": decision.status,
                    },
                    "candidates": [
                        {"node": c.node, "confidence": c.confidence}
                        for c in decision.candidates
                    ],
                    "skipped": "create_new_node_disabled",
                }
            target_node = (decision.new_node_name or "").strip()
            if not target_node:
                # Defensive: is_new requires a name — if missing, fall
                # through to needs_review rather than creating a blank
                # or placeholder node.
                return self._auto_place_needs_review(
                    domain=domain,
                    tmp_path=tmp_path,
                    filename=filename,
                    parsed=parsed,
                    decision=AutoClassifyDecision(
                        status="needs_review",
                        rationale="LLM 返回了 is_new 但缺少 new_node_name",
                        candidates=decision.candidates,
                    ),
                )
            await self._graph_repo.add_node(domain, target_node)
            new_node_created = True
            await get_activity_bus().emit(
                ActivityKind.NODE_CREATED,
                domain=domain,
                node=target_node,
                title=f"新建了节点「{target_node}」",
                source="auto_place",
                ref=f"node:{target_node}",
                extra={"created_by": "auto_place_upload"},
            )
        else:
            target_node = decision.node

        # Persist bytes (not the parsed preview) — the file on disk is
        # the source of truth. ``save_upload`` already handles
        # collision suffixes and creates ``user_uploads/`` if missing.
        content = tmp_path.read_bytes()
        repo_path = await self._resource_repo.save_upload(
            domain, target_node, filename, content
        )

        # Append to ``user_uploads/index.json`` so the resources dialog
        # surfaces the new file. Mirrors ``/api/resources/.../upload``.
        idx_path = self._resource_repo.uploads_index_path(domain, target_node)
        items = await self._resource_repo.read_json_index(idx_path)
        item: dict[str, Any] = {
            "file": repo_path.name,
            "category": "其他",
            "note": decision.rationale or "",
            "moved_at": datetime.now().isoformat(timespec="seconds"),
            "original_source": filename,
            "size": len(content),
            "auto_placed": True,
            "auto_classify_decision": {
                "status": decision.status,
                "node": decision.node,
                "new_node_name": decision.new_node_name,
                "rationale": decision.rationale,
                "confidence": decision.confidence,
                "is_new": decision.is_new,
            },
        }
        items.append(item)
        await self._resource_repo.write_json_index(idx_path, items)

        await get_activity_bus().emit(
            ActivityKind.UPLOAD_ADDED,
            domain=domain,
            node=target_node,
            title=f"自动归类并上传了文件「{repo_path.name}」",
            source="auto_place",
            ref=f"user_uploads#{repo_path.name}",
            extra={
                "category": "其他",
                "size": len(content),
                "auto_placed": True,
                "new_node_created": new_node_created,
                "rationale": decision.rationale,
                "status": decision.status,
                "confidence": decision.confidence,
            },
        )

        # Best-effort cleanup of the tmp file — don't fail the call if
        # the unlink fails (e.g. another reader has it open). The
        # background cleanup loop in ``src.api.server`` will sweep it
        # within 24h anyway.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

        return {
            "node": target_node,
            "path": str(repo_path),
            "rationale": decision.rationale,
            "status": decision.status,
            "new_node_created": new_node_created,
            "confidence": decision.confidence,
            "decision": {
                "status": decision.status,
                "node": decision.node,
                "new_node_name": decision.new_node_name,
                "rationale": decision.rationale,
                "is_new": decision.is_new,
            },
            "candidates": [
                {"node": c.node, "confidence": c.confidence}
                for c in decision.candidates
            ],
            "bytes_copied": len(content),
        }

    def _auto_place_needs_review(
        self,
        *,
        domain: str,
        tmp_path: Path,
        filename: str,
        parsed: dict[str, Any],
        decision: AutoClassifyDecision,
    ) -> dict[str, Any]:
        """Build the 'needs review' response when classification is uncertain.

        Tmp file is left in place; ``skipped`` carries the reason so
        the caller (or its UI) can surface "请手动选择节点" to the user.
        See BUG-2026-08-26-001 — this is the path that prevents the
        '未命名资料' garbage node from being created.
        """
        return {
            "node": "",
            "path": "",
            "rationale": decision.rationale
            or "归类器不确定，请手动选择节点。",
            "status": decision.status,
            "new_node_created": False,
            "needs_review": True,
            "error": decision.error,
            "decision": {
                "status": decision.status,
                "node": decision.node,
                "new_node_name": decision.new_node_name,
                "rationale": decision.rationale,
                "is_new": decision.is_new,
                "error": decision.error,
            },
            "candidates": [
                {"node": c.node, "confidence": c.confidence, "matched_tokens": c.matched_tokens}
                for c in decision.candidates
            ],
            "skipped": f"classifier_status={decision.status}",
            "file_kept_in_tmp": str(tmp_path),
            "filename": filename,
            "size": parsed.get("size"),
            "format": parsed.get("format"),
        }


__all__ = ["ResourceService", "ResourceItem"]