"""GraphSyncOrchestrator — derive associations.json from knowledge_bases.

职责
----

根据 ``knowledge_graph.json`` 和 ``notes/{node}/*`` 自动派生
``associations.json`` 中的：

  - ConceptNode（L1）—— 节点名 → 缩进层级 → 描述
  - ResourceNode（L2）—— Note / WebResource / Plan
  - PART_OF 边（从 knowledge_graph.links 派生）
  - RELATED_TO 边（笔记中 ``@节点名`` 引用）

LLM 抽取的 PREREQUISITE_OF / SIMILAR_TO / CONTRASTS_WITH **不在此**派生，
由 :class:`AssociationService` 在 PendingNodesBuffer flush 时执行。

触发模型
--------

本类**只由 DerivationSubscriber 调用**，不直接挂在 Service 层。每次调用
处理一个具体事件类型（kind），做最小化的增量派生：

  - NODE_CREATED / NODE_RENAMED / NODE_RELINKED → sync_for_node
  - NODE_DELETED → delete_node_derived
  - NOTE_GENERATED / NOTE_REBUILT / NOTE_UPDATED → sync_note_assets
  - WEB_RESOURCE_ADDED / UPLOAD_ADDED → sync_resource_assets
  - PLAN_CREATED / PLAN_ACTION_DONE / PLAN_ACTION_SKIPPED / PLAN_DELETED → sync_plan_assets

派生方向永远从真相源流向 associations.json，**不可逆**。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime
from typing import Any

from src.application.pending_nodes_buffer import PendingNodesBuffer
from src.domain.graph.association import (
    Association,
    AssociationGraph,
    ConceptNode,
    EdgeIntensity,
    RelationType,
    ResourceNode,
    ResourceType,
    _utcnow,
    make_concept_id,
    make_resource_id,
)
from src.domain.graph.models import Graph, Node
from src.infrastructure.repository.association_repo import AssociationRepository
from src.infrastructure.repository.graph_repo import GraphRepository
from src.infrastructure.repository.note_repo import NoteRepository
from src.infrastructure.repository.plan_repo import PlanRepository
from src.infrastructure.repository.resource_repo import ResourceRepository

logger = logging.getLogger(__name__)


#: 笔记中 @节点名 的正则
#:
# 允许的字符：字母 / 数字 / 中文 / 下划线 / 中点 / 短横线。
# 显式**排除空白**——否则 `@Deep Learning is` 会被匹配成整段
# "Deep Learning is"，污染 RELATED_TO 边。
# 节点名长度上限 40（与 knowledge_graph 校验一致）。
NODE_NAME_PATTERN = re.compile(r"@([\w一-鿿][\w一-鿿\-_·]{0,40})")


def _infer_level_from_node(node: Node, graph: Graph) -> int:
    """从 Graph 推算节点的层级（根=0，子=父+1）。

    ``knowledge_graph.json`` 的 Node.links 仅含子节点，不携带缩进信息，
    所以采用 BFS：从所有根节点出发，每个子节点的 level = 父节点的 level + 1。

    根节点定义：没有被任何其他节点的 links 引用的节点。
    如果所有节点都被引用（循环引用），说明拓扑无根——此时所有节点视为
    level=0（扁平图），避免错误地把某个节点提升为"伪根"。
    """
    if not graph.nodes:
        return 0

    all_names = {n.name for n in graph.nodes}
    referenced: set[str] = set()
    for n in graph.nodes:
        for child in n.links:
            if child in all_names:
                referenced.add(child)

    # 根节点 = 没有被任何节点 links 引用的节点
    roots = [n.name for n in graph.nodes if n.name not in referenced]
    if not roots:
        # 所有节点都被引用（环/自环）—— 视为扁平图，所有节点 level=0
        # 这样 parent dict 保持为空，下面的 while 循环直接返回 depth=0
        # 注意：不能选 graph.nodes[0] 当伪根，否则其被引用的"父"会得到 level=1
        # 而真正的祖先 level=0，导致父子层级颠倒
        return 0

    parent: dict[str, str | None] = {}      # name -> parent_name
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
        # 节点存在但未被 BFS 到达（如数据不一致），保守返回 0
        return 0
    depth = 0
    cur: str | None = node.name
    while cur is not None and cur in parent:
        cur = parent[cur]
        depth += 1
    return max(0, depth - 1)


def _sha8(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]


# ----------------------------------------------------------------------


class GraphSyncOrchestrator:
    """knowledge_bases → associations.json 的派生编排。

    线程模型：所有方法都是 ``async``，可在 asyncio 事件循环中并发调用。
    Repository 内部持有 :func:`graph_lock`，并发安全。
    """

    def __init__(
        self,
        domain: str,
        graph_repo: GraphRepository,
        note_repo: NoteRepository,
        resource_repo: ResourceRepository,
        plan_repo: PlanRepository,
        assoc_repo: AssociationRepository,
        *,
        buffer: PendingNodesBuffer | None = None,
    ) -> None:
        self._domain = domain
        self._graph_repo = graph_repo
        self._note_repo = note_repo
        self._resource_repo = resource_repo
        self._plan_repo = plan_repo
        self._assoc_repo = assoc_repo
        self._buffer = buffer

    # ============================================================
    # 全量派生（CLI / 初始化时使用）
    # ============================================================

    async def sync_full(self) -> dict[str, int]:
        """全量派生整个领域。

        步骤：
          1. 读 Graph
          2. 派生所有 ConceptNode
          3. 派生 PART_OF 边（结构性父→子）
          4. 派生所有 Note ResourceNode + @引用 边
          5. 派生所有 Web ResourceNode
          6. 派生所有 Plan ResourceNode
          7. 不在本方法调 LLM（避免阻塞 CLI）—— LLM 由 buffer flush 触发
        """
        graph = await self._graph_repo.read_graph(self._domain)

        assoc_graph = await self._assoc_repo.read(self._domain)
        assoc_graph = await self._ensure_concepts_from_graph(assoc_graph, graph)
        assoc_graph = self._ensure_part_of_edges(assoc_graph, graph)
        assoc_graph = await self._ensure_note_resources(assoc_graph, graph)
        assoc_graph = await self._ensure_web_resources(assoc_graph, graph)
        assoc_graph = await self._ensure_plan_resources(assoc_graph, graph)

        assoc_graph = assoc_graph.model_copy(update={"generated_at": _utcnow()})
        await self._assoc_repo.write(self._domain, assoc_graph)

        return {
            "concepts": len(assoc_graph.concepts),
            "resources": len(assoc_graph.resources),
            "associations": len(assoc_graph.associations),
        }

    # ============================================================
    # 增量派生（被订阅者调用，每个事件触发一次）
    # ============================================================

    async def sync_for_node(
        self,
        node: str,
        event_id: str = "",
        *,
        enqueue_llm: bool = True,
    ) -> dict[str, int]:
        """单节点增量派生（NODE_CREATED / NODE_RENAMED / NODE_RELINKED）。

        派生内容：
          - 该节点 ConceptNode（含 level / is_root / description）
          - 重建整个树的 PART_OF（便于一并处理父节点关系变化）
          - 该节点 Note ResourceNode + @引用 边
          - 可选：入 LLM buffer
        """
        graph = await self._graph_repo.read_graph(self._domain)
        node_obj = graph.find_node(node)
        if node_obj is None:
            logger.warning(
                "sync_for_node: node %r not found in domain %r",
                node, self._domain,
            )
            return {"error": f"node {node!r} not found"}

        assoc_graph = await self._assoc_repo.read(self._domain)
        # 修复 BUG-2026-08-17-001：
        # 增量派生必须把 node 的 immediate children 也纳入 ConceptNode 派生，
        # 否则 PART_OF 边会指向不存在的 ConceptNode，前端关联视图丢节点。
        child_names = {
            link for link in node_obj.links if graph.find_node(link)
        }
        target_concept_nodes = {node} | child_names
        assoc_graph = await self._ensure_concepts_from_graph(
            assoc_graph, graph, only_nodes=target_concept_nodes
        )
        assoc_graph = self._ensure_part_of_edges(assoc_graph, graph)
        assoc_graph = await self._ensure_note_resources(
            assoc_graph, graph, node=node
        )
        await self._assoc_repo.write(self._domain, assoc_graph)

        stats: dict[str, int] = {
            "concepts": len(assoc_graph.concepts),
            "associations": len(assoc_graph.associations),
        }

        if enqueue_llm and self._buffer is not None:
            await self._buffer.add(self._domain, node, event_id=event_id)

        return stats

    async def delete_node_derived(
        self, node: str, event_id: str = ""
    ) -> dict[str, int]:
        """删除节点时联动清理关联图。

        一次读 + 内存修改 + 一次写，避免多次 IO。
        """
        # 1. 读取当前关联图
        assoc_graph = await self._assoc_repo.read(self._domain)

        # 2. 删 Concept 节点
        new_concepts = {
            k: v for k, v in assoc_graph.concepts.items() if k != node
        }

        # 3. 删涉及该节点的 ResourceNode
        new_resources = {
            k: v for k, v in assoc_graph.resources.items() if v.node != node
        }

        # 4. 删涉及该节点的所有边
        new_ass = [
            a for a in assoc_graph.associations
            if a.source != node and a.target != node
        ]

        # 5. 重新派生 PART_OF（删除节点后树的形状变了）
        graph = await self._graph_repo.read_graph(self._domain)
        valid_nodes = {n.name for n in graph.nodes}
        existing_keys = {
            (a.source, a.target, a.relation) for a in new_ass
        }
        now = _utcnow()
        for parent_node in graph.nodes:
            for child in parent_node.links:
                if child == parent_node.name:
                    continue
                if not graph.find_node(child):
                    continue
                key = (parent_node.name, child, RelationType.PART_OF)
                if key in existing_keys:
                    continue
                new_ass.append(
                    Association(
                        source=parent_node.name,
                        target=child,
                        relation=RelationType.PART_OF,
                        weight=1.0,
                        intensity=EdgeIntensity.STRUCTURAL,
                        evidence="从 knowledge_graph.json.links 自动派生",
                        created_by="system",
                        created_at=now,
                    )
                )
                existing_keys.add(key)
        # 清理孤儿 PART_OF
        new_ass = [
            a for a in new_ass
            if a.relation != RelationType.PART_OF
            or (a.source in valid_nodes and a.target in valid_nodes)
        ]

        # 6. 一次写入
        new_graph = assoc_graph.model_copy(
            update={
                "concepts": new_concepts,
                "resources": new_resources,
                "associations": new_ass,
            }
        )
        await self._assoc_repo.write(self._domain, new_graph)

        return {
            "concepts_remaining": len(new_graph.concepts),
            "associations_remaining": len(new_graph.associations),
        }

    async def sync_note_assets(
        self,
        node: str,
        event_id: str = "",
    ) -> dict[str, int]:
        """派生 / 更新某节点的笔记 ResourceNode + @引用 边。"""
        graph = await self._graph_repo.read_graph(self._domain)
        assoc_graph = await self._assoc_repo.read(self._domain)
        assoc_graph = await self._ensure_note_resources(
            assoc_graph, graph, node=node
        )
        await self._assoc_repo.write(self._domain, assoc_graph)
        return {
            "resources": len(assoc_graph.resources),
            "associations": len(assoc_graph.associations),
        }

    async def sync_resource_assets(
        self,
        node: str,
        event_id: str = "",
    ) -> dict[str, int]:
        """派生某节点的 web_resources ResourceNode。"""
        graph = await self._graph_repo.read_graph(self._domain)
        assoc_graph = await self._assoc_repo.read(self._domain)
        assoc_graph = await self._ensure_web_resources(
            assoc_graph, graph, node=node
        )
        await self._assoc_repo.write(self._domain, assoc_graph)
        return {
            "resources": len(assoc_graph.resources),
            "associations": len(assoc_graph.associations),
        }

    async def sync_plan_assets(
        self,
        node: str,
        event_id: str = "",
    ) -> dict[str, int]:
        """派生某节点的 plan ResourceNode。"""
        graph = await self._graph_repo.read_graph(self._domain)
        assoc_graph = await self._assoc_repo.read(self._domain)
        assoc_graph = await self._ensure_plan_resources(
            assoc_graph, graph, node=node
        )
        await self._assoc_repo.write(self._domain, assoc_graph)
        return {
            "resources": len(assoc_graph.resources),
            "associations": len(assoc_graph.associations),
        }

    # ============================================================
    # buffer 接入
    # ============================================================

    async def enqueue_dependency_extraction(
        self, node: str, event_id: str = ""
    ) -> None:
        """入 PendingNodesBuffer 等待 LLM 抽取。"""
        if self._buffer is None:
            logger.warning(
                "enqueue_dependency_extraction called without buffer; skipping"
            )
            return
        await self._buffer.add(self._domain, node, event_id=event_id)

    # ============================================================
    # mark derived（join 时间线 API）
    # ============================================================

    async def mark_events_derived(self, event_ids: list[str]) -> None:
        if not event_ids:
            return
        await self._assoc_repo.mark_events_derived(self._domain, event_ids)

    # ============================================================
    # 派生规则实现
    # ============================================================

    async def _ensure_concepts_from_graph(
        self,
        assoc_graph: AssociationGraph,
        graph: Graph,
        *,
        only_nodes: set[str] | None = None,
    ) -> AssociationGraph:
        """从 Graph 派生所有 ConceptNode（含 level / description）。"""
        new_concepts: dict[str, ConceptNode] = dict(assoc_graph.concepts)
        now = _utcnow()

        target = (
            [n for n in graph.nodes if n.name in only_nodes]
            if only_nodes
            else list(graph.nodes)
        )

        for n in target:
            level = _infer_level_from_node(n, graph)
            existing = new_concepts.get(n.name)
            description = existing.description if existing else ""

            # 从 note.md 取摘要
            note_text = await self._note_repo.read_note(self._domain, n.name)
            if note_text:
                description = note_text[:500]

            new_concepts[n.name] = ConceptNode(
                id=make_concept_id(n.name),
                name=n.name,
                domain=self._domain,
                level=level,
                is_root=(level == 0),
                description=description,
                part_of_count=sum(
                    1 for other in graph.nodes if n.name in other.links
                ),
                updated_at=now,
            )
        return assoc_graph.model_copy(update={"concepts": new_concepts})

    @staticmethod
    def _ensure_part_of_edges(
        assoc_graph: AssociationGraph,
        graph: Graph,
    ) -> AssociationGraph:
        """从 Graph.links 派生 PART_OF 边（父 → 子，STRUCTURAL）。

        修复 BUG-2026-08-17-001：在写入 PART_OF 边之前，先确保所有
        source/target 端的 ConceptNode 实体存在；否则增量模式下边会指向
        不存在的 concept key，前端关联视图丢失节点。
        """
        # 1. 兜底：确保 PART_OF 涉及的概念都有 ConceptNode 实体
        valid_nodes = {n.name for n in graph.nodes}
        new_concepts: dict[str, ConceptNode] = dict(assoc_graph.concepts)
        now = _utcnow()
        for name in valid_nodes:
            if name in new_concepts:
                continue
            node_obj = graph.find_node(name)
            if node_obj is None:
                continue
            new_concepts[name] = ConceptNode(
                id=make_concept_id(name),
                name=name,
                domain=graph.domain,
                level=_infer_level_from_node(node_obj, graph),
                is_root=False,
                description="",
                part_of_count=sum(
                    1 for other in graph.nodes if name in other.links
                ),
                updated_at=now,
            )

        # 2. 派生 PART_OF 边
        existing = {
            (a.source, a.target, a.relation) for a in assoc_graph.associations
        }
        new_ass = list(assoc_graph.associations)
        for parent_node in graph.nodes:
            for child in parent_node.links:
                if child == parent_node.name:
                    continue
                if not graph.find_node(child):
                    continue
                key = (parent_node.name, child, RelationType.PART_OF)
                if key in existing:
                    continue
                new_ass.append(
                    Association(
                        source=parent_node.name,
                        target=child,
                        relation=RelationType.PART_OF,
                        weight=1.0,
                        intensity=EdgeIntensity.STRUCTURAL,
                        evidence="从 knowledge_graph.json.links 自动派生",
                        created_by="system",
                        created_at=now,
                    )
                )
                existing.add(key)
        # 3. 清理孤儿 PART_OF（节点已删除）
        new_ass = [
            a for a in new_ass
            if a.relation != RelationType.PART_OF
            or (a.source in valid_nodes and a.target in valid_nodes)
        ]
        return assoc_graph.model_copy(
            update={"concepts": new_concepts, "associations": new_ass}
        )

    async def _ensure_note_resources(
        self,
        assoc_graph: AssociationGraph,
        graph: Graph,
        *,
        node: str | None = None,
    ) -> AssociationGraph:
        """派生 Note ResourceNode + 笔记中 @节点名 → REFERENCES 边 + HAS_NOTE 边。

        增量模式 (``node`` 非空) 下：先清除该节点对应的旧 ResourceNode 与
        所有以该 node 为源/汇的边，再重建。否则用户编辑笔记后删除某个 @ 引用，
        旧的 REFERENCES 边会变成孤儿。
        """
        new_resources: dict[str, ResourceNode] = dict(assoc_graph.resources)
        new_ass: list[Association] = list(assoc_graph.associations)

        if node:
            # 增量模式：清除该 node 的旧 note 资源及其相关边
            note_id_to_clear = make_resource_id(ResourceType.NOTE, node)
            new_resources, new_ass = _purge_node_artifacts(
                new_resources, new_ass, node, note_id_to_clear
            )

        existing_ass: set[tuple[str, str, RelationType]] = {
            (a.source, a.target, a.relation) for a in new_ass
        }
        now = _utcnow()

        target_nodes = (
            [graph.find_node(node)] if node else list(graph.nodes)
        )
        target_nodes = [n for n in target_nodes if n is not None]
        all_node_names = {n.name for n in graph.nodes}

        for n in target_nodes:
            note_text = await self._note_repo.read_note(self._domain, n.name)
            if note_text is None:
                continue
            note_id = make_resource_id(ResourceType.NOTE, n.name)
            new_resources[note_id] = ResourceNode(
                id=note_id,
                type=ResourceType.NOTE,
                node=n.name,
                domain=self._domain,
                payload={
                    "path": f"notes/{n.name}/note.md",
                    "word_count": len(note_text),
                },
                summary=note_text[:500],
                updated_at=now,
            )

            # HAS_NOTE 边：Concept → Note（结构性归属）
            concept_id = make_concept_id(n.name)
            _add_assoc(
                new_ass, existing_ass, now,
                source=concept_id,
                target=note_id,
                relation=RelationType.HAS_NOTE,
                weight=1.0,
                intensity=EdgeIntensity.STRUCTURAL,
                evidence="concept 拥有 note 资源",
                created_by="system",
            )

            # 笔记中 @节点名 → REFERENCES 边（Note → Concept）
            # 旧版本用 RELATED_TO，这里升级为 REFERENCES 以区分 @ 引用与一般关联
            for ref_match in NODE_NAME_PATTERN.finditer(note_text):
                ref = ref_match.group(1).strip().rstrip(
                    ".,;:!?。，；：！？）」』] "
                )
                if ref and ref != n.name and ref in all_node_names:
                    _add_assoc(
                        new_ass, existing_ass, now,
                        source=note_id,
                        target=ref,
                        relation=RelationType.REFERENCES,
                        weight=0.9,
                        intensity=EdgeIntensity.SOFT,
                        evidence=f"笔记 @{ref}",
                        created_by="system",
                    )

        return assoc_graph.model_copy(
            update={"resources": new_resources, "associations": new_ass}
        )

    async def _ensure_web_resources(
        self,
        assoc_graph: AssociationGraph,
        graph: Graph,
        *,
        node: str | None = None,
    ) -> AssociationGraph:
        """派生 web_resources ResourceNode + HAS_RESOURCE 边。

        增量模式下先清除该 node 的旧 web 资源（id 形如 ``resource:*``，但
        通过 :attr:`ResourceNode.node` 字段匹配，避免误删其他节点的同 sha
        URL）。
        """
        new_resources: dict[str, ResourceNode] = dict(assoc_graph.resources)
        new_ass: list[Association] = list(assoc_graph.associations)

        if node:
            # 增量模式：清除该 node 的所有 web 资源及其 HAS_RESOURCE 边
            keep_resources: dict[str, ResourceNode] = {}
            for rid, res in new_resources.items():
                if res.type == ResourceType.RESOURCE and res.node == node:
                    continue
                keep_resources[rid] = res
            new_resources = keep_resources
            new_ass = [
                a for a in new_ass
                if not (
                    a.relation == RelationType.HAS_RESOURCE
                    and a.source == make_concept_id(node)
                )
            ]

        existing_ass: set[tuple[str, str, RelationType]] = {
            (a.source, a.target, a.relation) for a in new_ass
        }
        now = _utcnow()

        target_nodes = (
            [n for n in graph.nodes if n.name == node] if node else list(graph.nodes)
        )
        for n in target_nodes:
            resources = await self._resource_repo.list_node_resources(
                self._domain, n.name
            )
            for r in resources or []:
                url = r.get("url") or r.get("link", "")
                if not url:
                    continue
                sha = _sha8(url)
                rid = make_resource_id(ResourceType.RESOURCE, sha)
                new_resources[rid] = ResourceNode(
                    id=rid,
                    type=ResourceType.RESOURCE,
                    node=n.name,
                    domain=self._domain,
                    payload={"url": url, "title": r.get("title", "")},
                    summary=(r.get("summary") or r.get("title", ""))[:500],
                    updated_at=now,
                )
                # HAS_RESOURCE 边
                _add_assoc(
                    new_ass, existing_ass, now,
                    source=make_concept_id(n.name),
                    target=rid,
                    relation=RelationType.HAS_RESOURCE,
                    weight=1.0,
                    intensity=EdgeIntensity.STRUCTURAL,
                    evidence="concept 拥有 web/upload 资源",
                    created_by="system",
                )
        return assoc_graph.model_copy(
            update={"resources": new_resources, "associations": new_ass}
        )

    async def _ensure_plan_resources(
        self,
        assoc_graph: AssociationGraph,
        graph: Graph,
        *,
        node: str | None = None,
    ) -> AssociationGraph:
        """派生 plan ResourceNode + HAS_PLAN 边。

        增量模式下先清除该 node 的旧 plan 资源及其 HAS_PLAN 边。
        """
        new_resources: dict[str, ResourceNode] = dict(assoc_graph.resources)
        new_ass: list[Association] = list(assoc_graph.associations)

        if node:
            keep_resources = {
                rid: res for rid, res in new_resources.items()
                if not (res.type == ResourceType.PLAN and res.node == node)
            }
            new_resources = keep_resources
            new_ass = [
                a for a in new_ass
                if not (
                    a.relation == RelationType.HAS_PLAN
                    and a.source == make_concept_id(node)
                )
            ]

        existing_ass: set[tuple[str, str, RelationType]] = {
            (a.source, a.target, a.relation) for a in new_ass
        }
        now = _utcnow()

        target_nodes = (
            [n for n in graph.nodes if n.name == node] if node else list(graph.nodes)
        )
        for n in target_nodes:
            plans = await self._plan_repo.list_plans(self._domain, n.name)
            for p in plans or []:
                pid = p.get("id", "")
                if not pid:
                    continue
                rid = make_resource_id(ResourceType.PLAN, f"{n.name}:{pid}")
                actions = p.get("actions") or []
                completed = sum(
                    1 for a in actions
                    if isinstance(a, dict) and a.get("status") == "done"
                )
                new_resources[rid] = ResourceNode(
                    id=rid,
                    type=ResourceType.PLAN,
                    node=n.name,
                    domain=self._domain,
                    payload={
                        "plan_id": pid,
                        "goal": p.get("goal", ""),
                        "action_count": len(actions),
                        "completed": completed,
                    },
                    summary=(p.get("goal", ""))[:500],
                    updated_at=now,
                )
                # HAS_PLAN 边
                _add_assoc(
                    new_ass, existing_ass, now,
                    source=make_concept_id(n.name),
                    target=rid,
                    relation=RelationType.HAS_PLAN,
                    weight=1.0,
                    intensity=EdgeIntensity.STRUCTURAL,
                    evidence="concept 关联 plan 资源",
                    created_by="system",
                )
        return assoc_graph.model_copy(
            update={"resources": new_resources, "associations": new_ass}
        )


# ----------------------------------------------------------------------
# 模块级辅助函数（提取到顶层以便测试 & 复用）
# ----------------------------------------------------------------------


def _add_assoc(
    existing_list: list[Association],
    existing_set: set[tuple[str, str, RelationType]],
    now: datetime,
    *,
    source: str,
    target: str,
    relation: RelationType,
    weight: float,
    intensity: EdgeIntensity,
    evidence: str,
    created_by: str = "system",
) -> None:
    """Append an :class:`Association` if (source,target,relation) not yet present.

    用于派生阶段批量构造边——避免各处重复写入样板代码。
    """
    key = (source, target, relation)
    if key in existing_set:
        return
    existing_list.append(
        Association(
            source=source,
            target=target,
            relation=relation,
            weight=weight,
            intensity=intensity,
            evidence=evidence,
            created_by=created_by,
            created_at=now,
        )
    )
    existing_set.add(key)


def _purge_node_artifacts(
    resources: dict[str, ResourceNode],
    associations: list[Association],
    node: str,
    note_id_to_clear: str | None = None,
) -> tuple[dict[str, ResourceNode], list[Association]]:
    """增量模式：清除某 node 相关的旧资源节点与边。

    清理策略：

      - 若指定 ``note_id_to_clear``，移除该 ResourceNode 以及所有以它为
        source/target 的边。
      - 移除所有 ``source == concept:{node}`` 的结构性 HAS_* 边。
      - 移除所有 ``relation == REFERENCES`` 且 ``source`` 是 ``note:{node}``
        的边（清理旧 @ 引用）。
      - 移除所有 ``ResourceNode.node == node`` 的剩余资源（web / plan
        在 _ensure_*_resources 内部处理；这里兜底）。
    """
    concept_id = make_concept_id(node)
    keep_resources: dict[str, ResourceNode] = {}
    for rid, res in resources.items():
        if note_id_to_clear and rid == note_id_to_clear:
            continue
        if res.node == node and res.type in (
            ResourceType.RESOURCE,
            ResourceType.PLAN,
        ):
            continue
        keep_resources[rid] = res

    keep_ass: list[Association] = []
    for a in associations:
        if note_id_to_clear and (a.source == note_id_to_clear or a.target == note_id_to_clear):
            continue
        if a.relation in (
            RelationType.HAS_NOTE,
            RelationType.HAS_RESOURCE,
            RelationType.HAS_PLAN,
        ) and a.source == concept_id:
            continue
        if (
            a.relation == RelationType.REFERENCES
            and a.source == note_id_to_clear
        ):
            continue
        keep_ass.append(a)
    return keep_resources, keep_ass


def _compute_all_levels(graph: Graph) -> dict[str, int]:
    """一次 BFS 计算所有节点的 level（O(V+E)）。

    比对每个节点单独跑 :func:`_infer_level_from_node`（O(N²)）节省一个数量级。
    """
    if not graph.nodes:
        return {}

    all_names = {n.name for n in graph.nodes}
    referenced: set[str] = set()
    for n in graph.nodes:
        for child in n.links:
            if child in all_names:
                referenced.add(child)

    # 根节点 = 没被任何 links 引用的节点
    roots = [n.name for n in graph.nodes if n.name not in referenced]

    levels: dict[str, int] = {n.name: 0 for n in graph.nodes}

    if not roots:
        # 全环：所有节点都视为 level=0（扁平图）
        return levels

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

    for name in all_names:
        depth = 0
        cur: str | None = name
        while cur is not None and cur in parent:
            cur = parent[cur]
            depth += 1
        levels[name] = max(0, depth - 1)
    return levels


__all__ = [
    "GraphSyncOrchestrator",
    "NODE_NAME_PATTERN",
    "_compute_all_levels",
    "_infer_level_from_node",
    "_purge_node_artifacts",
    "_sha8",
]