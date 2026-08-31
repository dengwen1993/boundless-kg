"""GraphSyncService — sync truth-source files to FalkorDB + Embedding.

Replaces the old GraphSyncOrchestrator that wrote to associations.json.
This service reads knowledge_graph.json + notes/{node}/* and syncs
the derived data into FalkorDB (graph nodes + semantic edges) and
the embedding store (vectors + BM25 corpus).

Triggered by DerivationSubscriber on ActivityBus events, or manually
via CLI / API.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any

from src.domain.graph.models import Graph, Node
from src.infrastructure.embedding.bm25 import BM25Index, _tokenize
from src.infrastructure.embedding.client import EmbeddingClient
from src.infrastructure.graph_store.client import GraphStoreClient
from src.infrastructure.repository.association_repo import AssociationRepository
from src.infrastructure.repository.dossier_repo import DossierRepository
from src.infrastructure.repository.graph_repo import GraphRepository
from src.infrastructure.repository.note_repo import NoteRepository
from src.infrastructure.repository.plan_repo import PlanRepository
from src.infrastructure.repository.resource_repo import ResourceRepository

logger = logging.getLogger(__name__)

#: @节点名 pattern in notes — for REFERENCES edges.
NODE_NAME_PATTERN = re.compile(r"@([\w\u4e00-\u9fff][\w\u4e00-\u9fff\-_·]{0,40})")


def _infer_level(node: Node, graph: Graph) -> int:
    """BFS from roots to compute node level."""
    if not graph.nodes:
        return 0
    all_names = {n.name for n in graph.nodes}
    referenced: set[str] = set()
    for n in graph.nodes:
        for child in n.links:
            if child in all_names:
                referenced.add(child)
    roots = [n.name for n in graph.nodes if n.name not in referenced]
    if not roots:
        return 0

    parent: dict[str, str | None] = {}
    queue: list[tuple[str, str | None]] = [(r, None) for r in roots]
    visited: set[str] = set()
    while queue:
        cur, par = queue.pop(0)
        if cur in visited:
            continue
        visited.add(cur)
        parent[cur] = par
        node_obj = graph.find_node(cur)
        if not node_obj:
            continue
        for child in node_obj.links:
            if child not in visited:
                queue.append((child, cur))

    if node.name not in parent:
        return 0
    depth = 0
    cur: str | None = node.name
    while cur is not None and cur in parent:
        cur = parent[cur]
        depth += 1
    return max(0, depth - 1)


def _sha8(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]


class GraphSyncService:
    """Sync truth-source → FalkorDB + Embedding.

    Thread model: all methods are async, can be called concurrently.
    FalkorDB operations are wrapped in asyncio.to_thread.
    """

    def __init__(
        self,
        domain: str,
        graph_repo: GraphRepository,
        note_repo: NoteRepository,
        resource_repo: ResourceRepository,
        plan_repo: PlanRepository,
        graph_store: GraphStoreClient,
        embedding_client: EmbeddingClient,
        bm25_index: BM25Index,
        association_repo: AssociationRepository | None = None,
        dossier_repo: DossierRepository | None = None,
    ) -> None:
        self._domain = domain
        self._graph_repo = graph_repo
        self._note_repo = note_repo
        self._resource_repo = resource_repo
        self._plan_repo = plan_repo
        self._store = graph_store
        self._embed = embedding_client
        self._bm25 = bm25_index
        # Optional — when None, manual association sync is skipped.
        self._assoc_repo = association_repo
        self._dossier_repo = dossier_repo

    # ============================================================
    # Full sync (CLI / initialization)
    # ============================================================

    async def sync_full(self) -> dict[str, int]:
        """Full sync: read all truth-source → write to FalkorDB + BM25."""
        graph = await self._graph_repo.read_graph(self._domain)

        # Ensure vector index exists before inserting embeddings
        await asyncio.to_thread(self._store.ensure_vector_index, self._domain)

        # Sync concepts + PART_OF edges
        await self._sync_concepts(graph)
        await self._sync_part_of_edges(graph)
        await self._sync_notes(graph)
        await self._sync_resources(graph)
        await self._sync_plans(graph)

        # NEW: 同步手动添加的语义边 (RELATED_TO / PREREQUISITE_OF / SIMILAR_TO / …)
        # 之前 sync 只派生结构性 PART_OF + references,
        # 导致用户在 UI 上手动加的边不会进 FalkorDB,搜索时完全找不到。
        await self._sync_all_associations(graph)

        # NEW: 同步节点的档案条目 (DossierEntry) — 让 Graph-aware prompt 能搜
        await self._sync_all_dossiers(graph)

        # Rebuild BM25 index (含 1 跳邻居扩域)
        await self._rebuild_bm25(graph)

        return {
            "concepts": len(graph.nodes),
            "synced": True,
        }

    # ============================================================
    # Incremental sync (per-event, called by DerivationSubscriber)
    # ============================================================

    async def sync_for_node(self, node: str, event_id: str = "") -> dict[str, int]:
        """Incremental sync for a single node (NODE_CREATED / RENAMED / RELINKED)."""
        graph = await self._graph_repo.read_graph(self._domain)
        node_obj = graph.find_node(node)
        if node_obj is None:
            return {"error": f"node {node!r} not found"}

        # Sync this concept
        await self._sync_single_concept(node_obj, graph)
        # Rebuild PART_OF for this node's subtree
        await self._sync_part_of_edges(graph)
        # Sync note if exists
        await self._sync_note_for_node(node)
        # NEW: 同步该节点相关的语义边 (RELATED_TO / PREREQUISITE_OF / …)
        # 这样 UI 加一条边 → activity bus → sync_for_node → FalkorDB
        await self._sync_associations_for_node(node, graph)
        # NEW: 同步该节点的档案条目
        await self._sync_dossier_for_node(node)
        # Update BM25
        await self._rebuild_bm23_safe(graph)

        return {"synced": True}

    async def delete_node(self, node: str, event_id: str = "") -> dict[str, int]:
        """Delete a node from FalkorDB (NODE_DELETED)."""
        self._store.delete_concept_by_name(self._domain, node)
        self._store.delete_note_by_node(self._domain, node)

        graph = await self._graph_repo.read_graph(self._domain)
        await self._sync_part_of_edges(graph)
        await self._rebuild_bm23_safe(graph)

        return {"deleted": True}

    async def sync_note(self, node: str, event_id: str = "") -> dict[str, int]:
        """Sync a note (NOTE_GENERATED / REBUILT / UPDATED)."""
        await self._sync_note_for_node(node)
        graph = await self._graph_repo.read_graph(self._domain)
        await self._rebuild_bm23_safe(graph)
        return {"synced": True}

    async def sync_resources(self, node: str, event_id: str = "") -> dict[str, int]:
        """Sync web resources (WEB_RESOURCE_ADDED / UPLOAD_ADDED)."""
        graph = await self._graph_repo.read_graph(self._domain)
        await self._sync_resources_for_node(node)
        await self._rebuild_bm23_safe(graph)
        return {"synced": True}

    async def sync_plans(self, node: str, event_id: str = "") -> dict[str, int]:
        """Sync plans (PLAN_CREATED / PLAN_*)."""
        await self._sync_plans_for_node(node)
        return {"synced": True}

    # ============================================================
    # Internal sync implementations
    # ============================================================

    async def _sync_concepts(self, graph: Graph) -> None:
        """Sync all Concept nodes to FalkorDB."""
        for n in graph.nodes:
            await self._sync_single_concept(n, graph)

    async def _sync_single_concept(self, node: Node, graph: Graph) -> None:
        """Sync a single Concept node with embedding."""
        level = _infer_level(node, graph)

        # Read note for description
        note_text = await self._note_repo.read_note(self._domain, node.name)
        description = note_text[:500] if note_text else ""

        # Generate embedding (concept type prefix + name + description + child names).
        # The type prefix lets vector retrieval group results by node kind
        # (semantically, queries like "节点笔记" rank Note-type vectors higher).
        embed_text = f"概念 {node.name} {description} {' '.join(node.links)}"
        embedding = None
        if self._embed.is_available:
            try:
                embedding = await self._embed.embed_one(embed_text)
            except Exception as e:
                logger.warning("Embedding failed for %s: %s", node.name, e)

        await asyncio.to_thread(
            self._store.upsert_concept,
            self._domain,
            name=node.name,
            level=level,
            is_root=(level == 0),
            description=description,
            embedding=embedding,
        )

    async def _sync_part_of_edges(self, graph: Graph) -> None:
        """Sync PART_OF edges from knowledge_graph.json.links."""
        for parent in graph.nodes:
            for child in parent.links:
                if child == parent.name:
                    continue
                if not graph.find_node(child):
                    continue
                await asyncio.to_thread(
                    self._store.add_edge,
                    self._domain,
                    source=f"concept:{parent.name}",
                    target=f"concept:{child}",
                    relation="part_of",
                    weight=1.0,
                    intensity="STRUCTURAL",
                    evidence="从 knowledge_graph.links 派生",
                    created_by="system",
                )

    async def _sync_notes(self, graph: Graph) -> None:
        """Sync all Note nodes + REFERENCES edges."""
        for n in graph.nodes:
            await self._sync_note_for_node(n.name)

    async def _sync_note_for_node(self, node_name: str) -> None:
        """Sync a single note + its @references."""
        note_text = await self._note_repo.read_note(self._domain, node_name)
        if not note_text:
            return

        # Generate embedding for the note. The "节点笔记" prefix makes
        # vector search type-aware: a query like "笔记" will rank Note-type
        # vectors higher than Concept/Resource/Plan vectors.
        embedding = None
        if self._embed.is_available:
            try:
                embedding = await self._embed.embed_one(f"节点笔记 {note_text[:1000]}")
            except Exception as e:
                logger.warning("Note embedding failed for %s: %s", node_name, e)

        await asyncio.to_thread(
            self._store.upsert_note,
            self._domain,
            node=node_name,
            word_count=len(note_text),
            summary=note_text[:500],
            embedding=embedding,
        )
        await asyncio.to_thread(
            self._store.add_edge_any,
            self._domain,
            source=f"concept:{node_name}",
            target=f"note:{node_name}",
            relation="has_note",
            weight=1.0,
            intensity="STRUCTURAL",
            evidence="节点 ↔ 笔记资源",
            created_by="system",
        )

        # Parse @节点名 references → REFERENCES edges
        all_node_names = {n.name for n in (await self._graph_repo.read_graph(self._domain)).nodes}
        for match in NODE_NAME_PATTERN.finditer(note_text):
            ref = match.group(1).strip().rstrip(".,;:!?。，；：！？）」』] ")
            if ref and ref != node_name and ref in all_node_names:
                await asyncio.to_thread(
                    self._store.add_edge,
                    self._domain,
                    source=f"note:{node_name}",
                    target=f"concept:{ref}",
                    relation="references",
                    weight=0.9,
                    intensity="SOFT",
                    evidence=f"笔记 @{ref}",
                    created_by="system",
                )

    async def _sync_resources(self, graph: Graph) -> None:
        """Sync all Resource nodes."""
        for n in graph.nodes:
            await self._sync_resources_for_node(n.name)

    async def _sync_resources_for_node(self, node_name: str) -> None:
        """Sync web resources for a node."""
        resources = await self._resource_repo.list_node_resources(
            self._domain, node_name
        )
        for r in resources or []:
            url = r.get("url") or r.get("link", "")
            if not url:
                continue
            title = r.get("title", "")
            summary = r.get("summary") or r.get("title", "")

            # Embedding text carries the "学习资料" type prefix so a query like
            # "学习资料" or "资料" ranks Resource-type vectors higher.
            embedding = None
            if self._embed.is_available:
                try:
                    embedding = await self._embed.embed_one(f"学习资料 {title} {summary}")
                except Exception:
                    pass

            await asyncio.to_thread(
                self._store.upsert_resource,
                self._domain,
                node=node_name,
                url=url,
                title=title,
                summary=summary[:500],
                embedding=embedding,
            )
            # has_resource edge：concept → resource
            sha = _sha8(url)
            await asyncio.to_thread(
                self._store.add_edge_any,
                self._domain,
                source=f"concept:{node_name}",
                target=f"resource:{sha}",
                relation="has_resource",
                weight=1.0,
                intensity="STRUCTURAL",
                evidence="节点 ↔ 学习资料",
                created_by="system",
            )

    async def _sync_plans(self, graph: Graph) -> None:
        """Sync all Plan nodes."""
        for n in graph.nodes:
            await self._sync_plans_for_node(n.name)

    async def _sync_plans_for_node(self, node_name: str) -> None:
        """Sync plans for a node."""
        plans = await self._plan_repo.list_plans(self._domain, node_name)
        for p in plans or []:
            pid = p.get("id", "")
            if not pid:
                continue
            actions = p.get("actions") or []
            completed = sum(
                1 for a in actions
                if isinstance(a, dict) and a.get("status") == "done"
            )
            await asyncio.to_thread(
                self._store.upsert_plan,
                self._domain,
                node=node_name,
                plan_id=pid,
                goal=p.get("goal", ""),
                action_count=len(actions),
                completed=completed,
            )
            # has_plan edge：concept → plan
            await asyncio.to_thread(
                self._store.add_edge_any,
                self._domain,
                source=f"concept:{node_name}",
                target=f"plan:{node_name}:{pid}",
                relation="has_plan",
                weight=1.0,
                intensity="STRUCTURAL",
                evidence="节点 ↔ 学习计划",
                created_by="system",
            )

    async def _rebuild_bm25(self, graph: Graph) -> None:
        """Rebuild the BM25 index from all nodes + notes.

        阶段2 增量:把节点的 1 跳 FalkorDB 邻居名(来自 PART_OF +
        手动语义边)并入索引文本。这样搜 "循环控制" 时,索引里含
        "任务并发",即使 BM25 完全独立于向量检索,也能命中关联。
        """
        documents: list[dict[str, Any]] = []
        # 预计算每个节点的邻居名列表(从 PART_OF + associations 派生)。
        neighbor_index = await self._build_neighbor_index(graph)

        for n in graph.nodes:
            note_text = await self._note_repo.read_note(self._domain, n.name)
            text = f"概念 {n.name}"
            if note_text:
                text += " " + note_text[:500]
            # 结构性子节点
            text += " " + " ".join(n.links)
            # 邻居扩域:把 FalkorDB 1 跳关联的名字并入索引文本
            neighbors = neighbor_index.get(n.name, [])
            if neighbors:
                text += " 关联: " + " ".join(neighbors)
            documents.append({
                "id": f"concept:{n.name}",
                "name": n.name,
                "type": "concept",
                "text": text,
            })

        self._bm25.rebuild(self._domain, documents)

    async def _build_neighbor_index(
        self, graph: Graph,
    ) -> dict[str, list[str]]:
        """Build {node_name: [neighbor_names]} from PART_OF + associations.

        用于 BM25 扩域,使关键词检索也能命中"通过手动边连接的邻居"。

        注意:这一步只从 JSON 派生,不依赖 FalkorDB — FalkorDB 不可用
        时 BM25 仍可享受扩域。
        """
        all_node_names = {n.name for n in graph.nodes}
        result: dict[str, set[str]] = {n.name: set() for n in graph.nodes}

        # 1) PART_OF — 从 knowledge_graph.links 派生(双向)
        for n in graph.nodes:
            for child in n.links:
                if child in all_node_names:
                    result[n.name].add(child)
                    result[child].add(n.name)

        # 2) 手动语义边 — 从 associations.json 派生(双向)
        if self._assoc_repo is not None:
            try:
                assoc_graph = await self._assoc_repo.read(self._domain)
            except Exception as e:
                logger.warning("_build_neighbor_index: read associations failed: %s", e)
                assoc_graph = None
            if assoc_graph is not None:
                for edge in assoc_graph.associations:
                    if edge.source in all_node_names and edge.target in all_node_names:
                        result[edge.source].add(edge.target)
                        result[edge.target].add(edge.source)

        return {k: sorted(v) for k, v in result.items()}

    async def _sync_associations_for_node(
        self, node: str, graph: Graph,
    ) -> int:
        """同步某个节点相关的所有手动语义边到 FalkorDB。

        只处理 ``relation`` 在 FalkorDB SEMANTIC_RELATIONS 集合中的边
        (RELATED_TO / PREREQUISITE_OF / SIMILAR_TO / ENABLES / …),
        跳过结构性 PART_OF 和资源归属 HAS_NOTE / HAS_RESOURCE — 后两者
        已在 _sync_part_of_edges / _sync_notes 等方法里写过。

        Returns:
            写入成功的边数
        """
        if self._assoc_repo is None:
            return 0

        try:
            assoc_graph = await self._assoc_repo.read(self._domain)
        except Exception as e:
            logger.warning(
                "_sync_associations_for_node: read failed for %s: %s",
                node, e,
            )
            return 0

        all_node_names = {n.name for n in graph.nodes}
        written = 0
        for edge in assoc_graph.associations:
            # 只处理涉及该节点的边
            if edge.source != node and edge.target != node:
                continue
            # 只处理 FalkorDB 支持的语义边;结构性边由其他方法负责
            rel = edge.relation.value if hasattr(edge.relation, "value") else str(edge.relation)
            if rel in (
                "part_of", "has_note", "has_resource", "has_plan",
                "references", "cites",
            ):
                continue
            # 端点必须在 graph 中(避免指向已删除节点)
            if edge.source not in all_node_names or edge.target not in all_node_names:
                continue
            try:
                intensity = (
                    edge.intensity.value
                    if hasattr(edge.intensity, "value")
                    else str(edge.intensity)
                )
                ok = await asyncio.to_thread(
                    self._store.add_edge_any,
                    self._domain,
                    source=f"concept:{edge.source}",
                    target=f"concept:{edge.target}",
                    relation=rel,
                    weight=float(edge.weight or 1.0),
                    intensity=intensity,
                    evidence=(edge.evidence or "")[:200],
                    created_by=(edge.created_by or "system"),
                )
                if ok:
                    written += 1
            except Exception as e:
                logger.warning(
                    "_sync_associations_for_node: write edge %s->%s failed: %s",
                    edge.source, edge.target, e,
                )
        if written:
            logger.info(
                "_sync_associations_for_node: %s → %d semantic edges synced",
                node, written,
            )
        return written

    async def _sync_all_associations(self, graph: Graph) -> int:
        """全量同步所有手动语义边到 FalkorDB。

        由 ``sync_full`` 调用,作为"快速补全"链路的一部分。
        """
        if self._assoc_repo is None:
            return 0

        try:
            assoc_graph = await self._assoc_repo.read(self._domain)
        except Exception as e:
            logger.warning(
                "_sync_all_associations: read failed: %s", e,
            )
            return 0

        all_node_names = {n.name for n in graph.nodes}
        written = 0
        for edge in assoc_graph.associations:
            rel = edge.relation.value if hasattr(edge.relation, "value") else str(edge.relation)
            if rel in (
                "part_of", "has_note", "has_resource", "has_plan",
                "references", "cites",
            ):
                continue
            if edge.source not in all_node_names or edge.target not in all_node_names:
                continue
            try:
                intensity = (
                    edge.intensity.value
                    if hasattr(edge.intensity, "value")
                    else str(edge.intensity)
                )
                ok = await asyncio.to_thread(
                    self._store.add_edge_any,
                    self._domain,
                    source=f"concept:{edge.source}",
                    target=f"concept:{edge.target}",
                    relation=rel,
                    weight=float(edge.weight or 1.0),
                    intensity=intensity,
                    evidence=(edge.evidence or "")[:200],
                    created_by=(edge.created_by or "system"),
                )
                if ok:
                    written += 1
            except Exception as e:
                logger.warning(
                    "_sync_all_associations: write edge %s->%s failed: %s",
                    edge.source, edge.target, e,
                )
        if written:
            logger.info(
                "_sync_all_associations: %d semantic edges synced",
                written,
            )
        return written

    async def _rebuild_bm23_safe(self, graph: Graph) -> None:
        """Rebuild BM25, catching errors."""
        try:
            await self._rebuild_bm25(graph)
        except Exception as e:
            logger.warning("BM25 rebuild failed: %s", e)

    # ============================================================
    # Dossier sync (经验档案)
    # ============================================================

    async def _sync_dossier_for_node(self, node: str) -> int:
        """同步单个节点的档案条目到 FalkorDB。

        Returns:
            写入 FalkorDB 的条目数
        """
        if self._dossier_repo is None:
            return 0
        try:
            dossier = await self._dossier_repo.read(self._domain, node)
        except Exception as e:
            logger.warning(
                "_sync_dossier_for_node: read %s failed: %s", node, e,
            )
            return 0

        written = 0
        for entry in dossier.entries:
            try:
                # 生成 embedding — 文本拼接:类型前缀 + 标题 + 正文 + tags
                embed_text = (
                    f"经验 {entry.type.value} {entry.title} "
                    f"{entry.body} {' '.join(entry.tags or [])}"
                )
                embedding = None
                if self._embed.is_available:
                    try:
                        embedding = await self._embed.embed_one(
                            embed_text[:1000]
                        )
                    except Exception:
                        pass

                ok = await asyncio.to_thread(
                    self._store.upsert_dossier_entry,
                    self._domain,
                    entry_id=entry.id,
                    node=node,
                    entry_type=entry.type.value,
                    title=entry.title,
                    body=entry.body,
                    tags=entry.tags,
                    score=entry.score,
                    use_count=entry.use_count,
                    created_at=entry.created_at.isoformat(),
                    embedding=embedding,
                )
                if ok:
                    await asyncio.to_thread(
                        self._store.add_has_dossier_edge,
                        self._domain,
                        node=node,
                        entry_id=entry.id,
                    )
                    written += 1
            except Exception as e:
                logger.warning(
                    "_sync_dossier_for_node: write entry %s failed: %s",
                    entry.id, e,
                )
        if written:
            logger.info(
                "_sync_dossier_for_node: %s → %d dossier entries synced",
                node, written,
            )
        return written

    async def _sync_all_dossiers(self, graph: Graph) -> int:
        """全量同步所有节点的档案条目。"""
        if self._dossier_repo is None:
            return 0
        total = 0
        for n in graph.nodes:
            total += await self._sync_dossier_for_node(n.name)
        if total:
            logger.info("_sync_all_dossiers: %d entries total", total)
        return total


__all__ = ["GraphSyncService", "NODE_NAME_PATTERN", "_infer_level"]
