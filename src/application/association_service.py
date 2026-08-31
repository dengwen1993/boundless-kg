"""AssociationService — LLM-extracted semantic relations between nodes.

策略
----

1. 从 GraphRepository 读 ``knowledge_graph.json`` 拿到所有节点名
2. 从 NoteRepository 读每个节点的 ``note.md`` 摘要（前 300 字）
3. 按 level 分桶（每桶 12 节点），避免 prompt 过长
4. 对每桶调用 LLM（带 schema 约束 prompt）抽取关联三元组
5. Pydantic 校验 → 过滤跨度过大 / 节点不存在 / intensity 非法
6. 写入 AssociationRepository

降级
----

LLM 不可用时（chat 抛异常），记录 warning 并返回空列表——**派生失败不能
阻塞 Node 同步写入**。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Iterable

from pydantic import BaseModel, Field, ValidationError

from src.domain.graph.association import (
    Association,
    AssociationGraph,
    DEFAULT_INTENSITY_BY_RELATION,
    EdgeIntensity,
    RelationType,
)
from src.domain.graph.models import Graph
from src.infrastructure.graph_store.client import GraphStoreClient
from src.infrastructure.llm import AsyncLLMClient
from src.infrastructure.repository.association_repo import AssociationRepository

logger = logging.getLogger(__name__)


#: LLM 抽取时的最大跨层级差（避免"哲学 → 自注意力机制"）
DEFAULT_MAX_LEVEL_SPAN: int = 2

#: LLM 每批节点数
DEFAULT_BATCH_SIZE: int = 12


class RelationTriplet(BaseModel):
    """LLM 输出的单条三元组 schema。"""

    source: str
    target: str
    relation: str
    weight: float = Field(default=0.8, ge=0.0, le=1.0)
    intensity: str = "SOFT"
    evidence: str = ""


class AssociationService:
    """LLM 抽取节点间语义关系。

    本服务**仅**通过 ``AssociationRepository.add_associations_batch`` 写入
    派生态。**不**触碰真相源文件。
    """

    def __init__(
        self,
        llm: AsyncLLMClient | None,
        assoc_repo: AssociationRepository,
        *,
        graph_store: GraphStoreClient | None = None,
        max_level_span: int = DEFAULT_MAX_LEVEL_SPAN,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._llm = llm
        self._repo = assoc_repo
        self._graph_store = graph_store
        self._max_level_span = max_level_span
        self._batch_size = batch_size

    # ---------- 对外 API ----------

    async def build_associations(
        self,
        domain: str,
        graph: Graph,
        node_cards: dict[str, str],
        *,
        target_nodes: list[str] | None = None,
        level_map: dict[str, int] | None = None,
        dry_run: bool = False,
    ) -> list[Association]:
        """为节点抽取关联边。

        Args:
            domain: 领域名
            graph: 完整 Graph（用于校验节点存在）
            node_cards: 节点名 → 卡片文本（前 300 字）
            target_nodes: 仅抽取涉及这些节点的关联；None = 全量
            level_map: 节点名 → level（用于跨度约束）；None = 不约束
            dry_run: True 只返回，不写入

        Returns:
            通过校验的 Association 列表
        """
        valid_node_names = {n.name for n in graph.nodes}
        if target_nodes is None:
            target_nodes = sorted(valid_node_names)
        else:
            target_nodes = [n for n in target_nodes if n in valid_node_names]

        if len(target_nodes) < 2:
            return []

        # 按 level 分桶
        buckets = self._bucket_by_level(target_nodes, level_map)

        all_assocs: list[Association] = []
        for bucket in buckets:
            try:
                assocs = await self._extract_bucket(domain, bucket, node_cards)
            except Exception as e:
                logger.warning(
                    "AssociationService: LLM extract failed for bucket: %s", e,
                    exc_info=True,
                )
                continue
            # 过滤掉 source/target 不在节点白名单中的三元组（LLM 幻觉）
            assocs = [
                a for a in assocs
                if a.source in valid_node_names and a.target in valid_node_names
            ]
            # 过滤自环（source == target）
            assocs = [a for a in assocs if a.source != a.target]
            # 过滤跨度过大
            assocs = self._filter_level_span(assocs, level_map)
            all_assocs.extend(assocs)

        if not dry_run and all_assocs:
            await self._repo.add_associations_batch(domain, all_assocs)
            await self._mirror_to_falkordb(domain, all_assocs)

        return all_assocs

    # ---------- 内部 ----------

    def _bucket_by_level(
        self, nodes: list[str], level_map: dict[str, int] | None
    ) -> list[list[str]]:
        if not nodes:
            return []
        if level_map is None:
            return [
                nodes[i : i + self._batch_size]
                for i in range(0, len(nodes), self._batch_size)
            ]
        sorted_nodes = sorted(
            nodes,
            key=lambda n: (level_map.get(n, 0), n),
        )
        return [
            sorted_nodes[i : i + self._batch_size]
            for i in range(0, len(sorted_nodes), self._batch_size)
        ]

    def _filter_level_span(
        self,
        assocs: Iterable[Association],
        level_map: dict[str, int] | None,
    ) -> list[Association]:
        if level_map is None:
            return list(assocs)
        out: list[Association] = []
        for a in assocs:
            ls = level_map.get(a.source, 0)
            lt = level_map.get(a.target, 0)
            if abs(ls - lt) <= self._max_level_span:
                out.append(a)
        return out

    async def _extract_bucket(
        self,
        domain: str,
        bucket: list[str],
        node_cards: dict[str, str],
    ) -> list[Association]:
        if self._llm is None:
            logger.warning("AssociationService: no LLM client; skip extraction")
            return []

        card_json = json.dumps(
            {n: node_cards.get(n, "")[:300] for n in bucket},
            ensure_ascii=False,
            indent=2,
        )

        system = (
            "你是知识图谱关系标注专家。"
            "只输出 JSON 数组，不要任何额外解释。"
        )
        user = (
            f"领域：{domain}\n\n"
            f"以下节点可能存在关联，请判断关系类型：\n{card_json}\n\n"
            "可选关系类型（source → target）：\n"
            "- prerequisite_of: A 是 B 的前置知识（学 B 必须先学 A）\n"
            "- enables: A 是 B 的反向前置（学 A 自然能 B）\n"
            "- similar_to: A 与 B 相似\n"
            "- contrasts_with: A 与 B 对比\n"
            "- applies_to: A 应用于 B\n"
            "- derived_from: A 衍生自 B\n"
            "- related_to: 通用关联\n\n"
            "约束：\n"
            "1. 不要输出 part_of（结构性包含由系统自动派生）\n"
            "2. source 是前置，target 是后续学习对象\n"
            "3. prerequisite_of 的 intensity 必须是 HARD（死磕级）\n"
            "4. 其他关系的 intensity 默认为 SOFT\n"
            "5. 只输出 4~8 条，不要更多\n\n"
            "返回 JSON 数组（每条格式）：\n"
            '[{"source": "...", "target": "...", "relation": "...", '
            '"weight": 0.0~1.0, "intensity": "HARD|SOFT", "evidence": "≤30字"}]'
        )

        raw = await self._llm.chat(system, user, temperature=0.3, json_mode=True)
        triplets = self._parse_triplets(raw)
        return [self._triplet_to_association(t) for t in triplets]

    @staticmethod
    def _parse_triplets(raw: str) -> list[RelationTriplet]:
        """容错解析 LLM 输出。"""
        if not raw:
            return []
        # 尝试直接解析
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return _validate_triplets(data)
            if isinstance(data, dict) and "items" in data:
                return _validate_triplets(data["items"])
        except (json.JSONDecodeError, ValidationError):
            pass
        # 容错：尝试找到 JSON 数组片段
        try:
            from src.utils.json_repair import try_parse_json
            data = try_parse_json(raw)
            if isinstance(data, list):
                return _validate_triplets(data)
        except Exception:
            pass
        return []

    @staticmethod
    def _triplet_to_association(t: RelationTriplet) -> Association:
        try:
            relation = RelationType(t.relation)
        except ValueError:
            relation = RelationType.RELATED_TO
        try:
            intensity = EdgeIntensity(t.intensity)
        except ValueError:
            intensity = DEFAULT_INTENSITY_BY_RELATION.get(
                relation, EdgeIntensity.SOFT
            )
        return Association(
            source=t.source.strip(),
            target=t.target.strip(),
            relation=relation,
            weight=t.weight,
            intensity=intensity,
            evidence=t.evidence[:200] if t.evidence else "",
            created_by="llm",
        )

    # ---------- Buffer 接口 ----------

    async def flush_buffer(
        self,
        domain: str,
        nodes: list[str],
        *,
        event_ids: list[str] | None = None,
    ) -> dict[str, int | list[str]]:
        """Buffer flush 入口：读 graph + cards → build_associations → mark_events_derived。

        Args:
            domain: 领域名
            nodes: 待抽取节点名列表
            event_ids: 对应来源 event id（用于 mark derived）

        Returns:
            {"flushed": N, "added": K, "errors": 0, "event_ids": [...]}
        """
        # 读 Graph + node cards（通过 dependencies 工厂避免循环依赖）
        graph_obj = await self._read_graph(domain)
        node_cards = await self._read_node_cards(domain, nodes)

        # level_map —— 一次 BFS 算所有节点的 level（O(V+E)），比对每个节点单独 BFS
        # 的 O(N²) 节省一个数量级
        from src.application.graph_sync_orchestrator import _compute_all_levels
        level_map = _compute_all_levels(graph_obj)

        assocs = await self.build_associations(
            domain,
            graph_obj,
            node_cards,
            target_nodes=nodes,
            level_map=level_map,
            dry_run=False,
        )

        # mark derived
        if event_ids:
            await self._repo.mark_events_derived(
                domain, [eid for eid in event_ids if eid]
            )

        return {
            "flushed": len(nodes),
            "added": len(assocs),
            "errors": 0,
            "event_ids": event_ids or [],
        }

    async def _read_graph(self, domain: str) -> "Graph":
        """读 Graph；通过 dependencies 工厂拿 repo（避免循环依赖）。"""
        from src.agent import dependencies as agent_deps
        from src.domain.graph.models import Graph

        repo = agent_deps.get_graph_repo()
        return await repo.read_graph(domain)

    async def _read_node_cards(
        self, domain: str, nodes: list[str]
    ) -> dict[str, str]:
        """读每个节点 note.md 的前 300 字作为卡片。"""
        from src.agent import dependencies as agent_deps

        note_repo = agent_deps.get_note_repo()
        out: dict[str, str] = {}
        for n in nodes:
            text = await note_repo.read_note(domain, n)
            if text:
                out[n] = text[:300]
        return out

    async def _mirror_to_falkordb(
        self,
        domain: str,
        edges: list[Association],
    ) -> None:
        """Mirror LLM-derived edges into FalkorDB so the graph-store read
        path (used by the associations view) sees them too.

        No-ops when:
          * ``graph_store`` was not wired in (older caller),
          * FalkorDB is disabled / unreachable (``is_available`` is False),
          * the edge set is empty.
        """
        if self._graph_store is None or not edges:
            return
        try:
            if not await self._graph_store.ensure_available():  # type: ignore[attr-defined]
                return
        except Exception:
            return

        async def _push_one(e: Association) -> None:
            await asyncio.to_thread(
                self._graph_store.add_edge_any,  # type: ignore[union-attr]
                domain,
                source=f"concept:{e.source}",
                target=f"concept:{e.target}",
                relation=e.relation.value,
                weight=float(e.weight),
                intensity=e.intensity.value,
                evidence=e.evidence or "",
                created_by=e.created_by or "llm",
            )

        results = await asyncio.gather(
            *[_push_one(e) for e in edges],
            return_exceptions=True,
        )
        fails = sum(1 for r in results if isinstance(r, Exception))
        if fails:
            logger.warning(
                "AssociationService: mirrored %d/%d LLM edges to FalkorDB (failures=%d)",
                len(edges) - fails, len(edges), fails,
            )


def _validate_triplets(items: list[Any]) -> list[RelationTriplet]:
    out: list[RelationTriplet] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            out.append(RelationTriplet(**item))
        except ValidationError:
            continue
    return out


__all__ = [
    "AssociationService",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_MAX_LEVEL_SPAN",
    "RelationTriplet",
]