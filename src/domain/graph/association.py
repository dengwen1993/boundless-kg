"""Association graph — domain layer models.

知识图谱关联层的数据契约。与 ``knowledge_graph.json`` 平级，存储于
``associations.json``。派生方向：领域 → 节点 → 资源（L3 反馈层本版本不启用）。

主要类型
--------

- :class:`RelationType` — 边关系类型枚举（8 种）
- :class:`EdgeIntensity` — 边强度枚举（HARD / SOFT / STRUCTURAL）
- :class:`Association` — 一条关联边（source, target, relation, weight, ...）
- :class:`ConceptNode` — L1 派生节点（对应一个思维导图节点）
- :class:`ResourceNode` — L2 派生节点（笔记/资料/计划）
- :class:`AssociationGraph` — 一个领域的完整关联图（含节点 + 边 + 派生状态）

派生方向唯一性
--------------

本模块定义的所有对象都是**派生态**，由真相源（``knowledge_graph.json``、
``notes/{node}/*``）自动计算生成。**禁止**任何反向写入真相源的入口。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator

from pydantic import BaseModel, Field, field_validator, model_validator


# ----------------------------------------------------------------------
# 枚举
# ----------------------------------------------------------------------


class RelationType(str, Enum):
    """节点间语义关系类型。

    命名采用 snake_case（与 ActivityKind 风格一致）；存储为小写字符串。
    """

    PART_OF = "part_of"                       # 父子包含（结构性）
    PREREQUISITE_OF = "prerequisite_of"        # 学习前置（语义性）
    ENABLES = "enables"                        # 反向前置
    SIMILAR_TO = "similar_to"                  # 相似
    CONTRASTS_WITH = "contrasts_with"          # 对比
    APPLIES_TO = "applies_to"                  # 应用
    DERIVED_FROM = "derived_from"              # 衍生
    RELATED_TO = "related_to"                  # 通用关联
    # L2 资源 → L1 概念 的"归属"边（结构性，由 GraphSyncOrchestrator 派生）
    HAS_NOTE = "has_note"                      # 概念节点 → Note 资源
    HAS_RESOURCE = "has_resource"              # 概念节点 → Web/Upload 资源
    HAS_PLAN = "has_plan"                      # 概念节点 → Plan 资源
    # L2 资源之间的引用边（笔记 @web_resource 等）
    CITES = "cites"                            # Note → Resource（笔记引用外部资源）
    REFERENCES = "references"                  # Note/Plan → Concept（笔记/计划引用概念）


class EdgeIntensity(str, Enum):
    """边强度 — 决定 UI 渲染颜色与学习路径生成权重。"""

    HARD = "HARD"               # 死磕级前置（学习路径必走）
    SOFT = "SOFT"               # 了解级（可跳过）
    STRUCTURAL = "STRUCTURAL"   # 结构性（非学习依赖，例如 PART_OF）


class ResourceType(str, Enum):
    """L2 资源节点的类型。"""

    NOTE = "note"
    RESOURCE = "resource"
    PLAN = "plan"
    # QUIZ / GAP 本版本不启用，但保留枚举便于未来扩展
    QUIZ = "quiz"
    GAP = "gap"


# 每种关系类型的默认强度（参考文档 §6.1 EdgeIntensity 默认值）
DEFAULT_INTENSITY_BY_RELATION: dict[RelationType, EdgeIntensity] = {
    RelationType.PART_OF: EdgeIntensity.STRUCTURAL,
    RelationType.PREREQUISITE_OF: EdgeIntensity.HARD,
    RelationType.ENABLES: EdgeIntensity.HARD,
    RelationType.SIMILAR_TO: EdgeIntensity.SOFT,
    RelationType.CONTRASTS_WITH: EdgeIntensity.SOFT,
    RelationType.APPLIES_TO: EdgeIntensity.SOFT,
    RelationType.DERIVED_FROM: EdgeIntensity.SOFT,
    RelationType.RELATED_TO: EdgeIntensity.SOFT,
    # 结构性"归属"边 — 由 GraphSyncOrchestrator 派生，UI 用作资源树状导航
    RelationType.HAS_NOTE: EdgeIntensity.STRUCTURAL,
    RelationType.HAS_RESOURCE: EdgeIntensity.STRUCTURAL,
    RelationType.HAS_PLAN: EdgeIntensity.STRUCTURAL,
    RelationType.CITES: EdgeIntensity.SOFT,
    RelationType.REFERENCES: EdgeIntensity.SOFT,
}


# ----------------------------------------------------------------------
# 节点模型
# ----------------------------------------------------------------------


def _utcnow() -> datetime:
    """ISO 秒级 UTC 时间。"""
    return datetime.now(timezone.utc).replace(microsecond=0)


class ConceptNode(BaseModel):
    """L1 派生节点 — 对应思维导图中的一个概念节点。

    Attributes:
        id: 形如 ``"concept:{name}"``
        name: 节点名（与 ``knowledge_graph.json`` 中 node.name 完全一致）
        domain: 所属领域
        level: 层级（根=0，主干=1，叶子=2…），从思维导图缩进自动计算
        is_root: 是否为根节点（``level == 0``，自动同步）
        description: 笔记前 500 字摘要
        in_degree: PREREQUISITE_OF 入度（被多少节点依赖）
        out_degree: PREREQUISITE_OF 出度（依赖多少节点）
        part_of_count: 直接父节点数（一般=1，根=0）
        updated_at: 最后更新时间（ISO 8601，UTC）
    """

    id: str
    name: str
    domain: str
    level: int = 0
    is_root: bool = False
    description: str = ""
    in_degree: int = 0
    out_degree: int = 0
    part_of_count: int = 0
    updated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _sync_root_flag(self):
        """is_root 与 level 自动同步：level==0 ⇒ is_root=True。"""
        # 注意：调用方显式给 is_root=False + level=0 时也会被覆盖为 True。
        # 这是设计选择——概念层只有 level 能定义"根"，is_root 始终等于 (level==0)。
        if self.level == 0 and not self.is_root:
            object.__setattr__(self, "is_root", True)
        return self

    @field_validator("level")
    @classmethod
    def _validate_level(cls, v: int) -> int:
        if v < 0:
            return 0
        return v


class ResourceNode(BaseModel):
    """L2 派生节点 — 笔记 / 资料 / 计划。

    Attributes:
        id: 形如 ``"note:{name}"`` / ``"resource:{sha8}"`` / ``"plan:{node}:{plan_id}"``
        type: 资源类型（``note`` / ``resource`` / ``plan``）
        node: 所属概念节点
        domain: 所属领域
        payload: 资源路径、URL、action_count 等额外字段
        summary: 前 500 字摘要或资源标题
        updated_at: 最后更新时间
    """

    id: str
    type: ResourceType
    node: str
    domain: str
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    updated_at: datetime = Field(default_factory=_utcnow)


# ----------------------------------------------------------------------
# 边模型
# ----------------------------------------------------------------------


class Association(BaseModel):
    """一条关联边。

    Attributes:
        source: 源节点名
        target: 目标节点名
        relation: 关系类型
        weight: 0~1，关系强度（与 intensity 配合使用）
        intensity: 边强度（HARD / SOFT / STRUCTURAL）
        evidence: LLM 抽取时的依据文本（用户可查看以判断是否同意）
        created_by: ``llm`` / ``vector`` / ``system`` —— 边的来源
        created_at: 创建时间
    """

    source: str
    target: str
    relation: RelationType
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    intensity: EdgeIntensity = EdgeIntensity.SOFT
    evidence: str = ""
    created_by: str = "llm"
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("source", "target")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("source/target 不能为空")
        return v

    @field_validator("created_by")
    @classmethod
    def _validate_created_by(cls, v: str) -> str:
        allowed = {"llm", "vector", "system"}
        if v not in allowed:
            return "llm"
        return v


def make_association_id(source: str, target: str, relation: RelationType) -> str:
    """生成关联边的稳定标识（用于去重）。"""
    return f"assoc:{relation.value}:{source}→{target}"


# ----------------------------------------------------------------------
# 顶层聚合
# ----------------------------------------------------------------------


class AssociationMetadata(BaseModel):
    """``associations.json`` 的元数据段。

    关键字段：
        derived_events: event_id → 派生完成时间；用于时间线 API join
    """

    derived_events: dict[str, str] = Field(default_factory=dict)
    last_full_sync: str | None = None
    schema_version: str = "1.0"

    def mark_derived(self, event_ids: list[str], when: datetime | None = None) -> None:
        """记录一批 event 已派生完成。"""
        ts = (when or _utcnow()).isoformat()
        for eid in event_ids:
            if eid:
                self.derived_events[eid] = ts

    def is_derived(self, event_id: str) -> bool:
        return event_id in self.derived_events


class AssociationGraph(BaseModel):
    """一个领域的完整关联图。

    存储于 ``<kb_root>/<domain>/associations.json``。与 ``knowledge_graph.json``
    平级，**派生方向永远从真相源流向本文件**，不允许反向。

    Attributes:
        domain: 所属领域
        concepts: 节点名 → ConceptNode
        resources: 资源 id → ResourceNode
        associations: 所有关联边（列表形式，便于 JSON 序列化）
        metadata: 元数据（含 derived_events 等）
        generated_at: 最近一次全量更新时间
    """

    domain: str
    concepts: dict[str, ConceptNode] = Field(default_factory=dict)
    resources: dict[str, ResourceNode] = Field(default_factory=dict)
    associations: list[Association] = Field(default_factory=list)
    metadata: AssociationMetadata = Field(default_factory=AssociationMetadata)
    generated_at: datetime | None = None

    # ---------- 查询助手 ----------

    def neighbors(
        self,
        node: str,
        *,
        relation: RelationType | None = None,
        max_hops: int = 1,
        direction: str = "any",
    ) -> list[tuple[str, Association, int]]:
        """BFS 查询节点的 N 跳邻居。

        Args:
            node: 起始节点名
            relation: 仅返回指定关系类型；None = 所有
            max_hops: 跳数上限（1 = 直接邻居）
            direction: ``any`` / ``out`` / ``in`` —— 出向/入向/双向

        Returns:
            ``(neighbor_name, association, hops)`` 列表，按 hops 升序

        性能
        ----

        用 :attr:`_index_by_source` / :attr:`_index_by_target` 内存索引做
        出/入邻接查找，避免每层 O(E) 线性扫描整个 associations 列表。
        """
        visited: set[str] = {node}
        queue: deque[tuple[str, int, Association | None]] = deque([(node, 0, None)])
        results: list[tuple[str, Association, int]] = []

        # 懒重建索引（每次读 associations.json 后第一次调用 neighbors 才花代价）
        idx_src = self._index_by_source()
        idx_tgt = self._index_by_target()

        while queue:
            cur, hops, _via = queue.popleft()
            if hops >= max_hops:
                continue

            if direction in ("out", "any"):
                for ass in idx_src.get(cur, ()):
                    if relation and ass.relation is not relation:
                        continue
                    if ass.target == cur or ass.target in visited:
                        continue
                    visited.add(ass.target)
                    results.append((ass.target, ass, hops + 1))
                    queue.append((ass.target, hops + 1, ass))

            if direction in ("in", "any"):
                for ass in idx_tgt.get(cur, ()):
                    if relation and ass.relation is not relation:
                        continue
                    if ass.source == cur or ass.source in visited:
                        continue
                    visited.add(ass.source)
                    results.append((ass.source, ass, hops + 1))
                    queue.append((ass.source, hops + 1, ass))
        return results

    # ---------- 内存索引（懒构建） ----------

    def _index_stale_check(self) -> bool:
        """索引是否仍然与 :attr:`associations` 一致？"""
        idx_ver = getattr(self, "__index_version__", None)
        cur_ver = id(self.associations)
        if idx_ver != cur_ver:
            return True
        return False

    def _index_by_source(self) -> dict[str, list[Association]]:
        """``source 节点名 -> 出向边列表``。"""
        if self._index_stale_check() or not hasattr(self, "_idx_src"):
            self._rebuild_index()
        return getattr(self, "_idx_src", {})

    def _index_by_target(self) -> dict[str, list[Association]]:
        """``target 节点名 -> 入向边列表``。"""
        if self._index_stale_check() or not hasattr(self, "_idx_tgt"):
            self._rebuild_index()
        return getattr(self, "_idx_tgt", {})

    def _rebuild_index(self) -> None:
        """重新构建 by_source / by_target 索引。"""
        idx_src: dict[str, list[Association]] = {}
        idx_tgt: dict[str, list[Association]] = {}
        for ass in self.associations:
            idx_src.setdefault(ass.source, []).append(ass)
            idx_tgt.setdefault(ass.target, []).append(ass)
        object.__setattr__(self, "_idx_src", idx_src)
        object.__setattr__(self, "_idx_tgt", idx_tgt)
        object.__setattr__(self, "__index_version__", id(self.associations))

    def edges_for_node(self, node: str) -> Iterator[Association]:
        """返回与某节点相关的所有边（出 + 入）。"""
        for ass in self.associations:
            if ass.source == node or ass.target == node:
                yield ass

    def statistics(self) -> dict[str, int]:
        """返回关联图的统计信息。"""
        return {
            "concepts": len(self.concepts),
            "resources": len(self.resources),
            "associations": len(self.associations),
            "derived_events": len(self.metadata.derived_events),
        }


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------


def make_concept_id(name: str) -> str:
    return f"concept:{name}"


def make_resource_id(kind: ResourceType, key: str) -> str:
    return f"{kind.value}:{key}"


def concept_id_to_name(concept_id: str) -> str:
    return concept_id.removeprefix("concept:")


def _safe_utcnow_iso() -> str:
    """稳定返回当前 UTC ISO 秒字符串，便于单测断言。"""
    return _utcnow().isoformat()


__all__ = [
    "Association",
    "AssociationGraph",
    "AssociationMetadata",
    "ConceptNode",
    "DEFAULT_INTENSITY_BY_RELATION",
    "EdgeIntensity",
    "RelationType",
    "ResourceNode",
    "ResourceType",
    "_safe_utcnow_iso",
    "_utcnow",
    "concept_id_to_name",
    "make_association_id",
    "make_concept_id",
    "make_resource_id",
]