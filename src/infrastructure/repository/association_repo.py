"""AssociationRepository — async IO over ``associations.json``.

与 :class:`GraphRepository` 平级，但职责完全不同：

- GraphRepository：读写**真相源** ``knowledge_graph.json``（用户直接编辑）
- AssociationRepository：读写**派生态** ``associations.json``（自动派生）

所有写入方法都返回**新对象**（不可变 Pydantic），不直接修改入参。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiofiles

from src.domain.graph.association import (
    Association,
    AssociationGraph,
    ConceptNode,
    ResourceNode,
)
from src.infrastructure.lock import graph_lock

from ._atomic import atomic_write_text

logger = logging.getLogger(__name__)


class AssociationRepository:
    """Async CRUD over per-domain ``associations.json`` files.

    所有 mutation 自动持有 ``graph_lock()``，与 GraphRepository 共享同一锁
    保证并发安全。

    使用：

    >>> repo = AssociationRepository(kb_root=Path("/path/to/kb"))
    >>> graph = await repo.read("AI 应用开发")
    >>> graph.concepts["Transformer"] = ConceptNode(...)
    >>> await repo.write("AI 应用开发", graph)
    """

    def __init__(self, kb_root: Path) -> None:
        self._kb_root = Path(kb_root)

    @property
    def kb_root(self) -> Path:
        return self._kb_root

    # ---------- 路径 ----------

    @staticmethod
    def _safe_domain(domain: str) -> str:
        """Extract the real domain name from a possible 'domain / node' compound."""
        return domain.split(" / ")[0].split(" \\ ")[0].strip()

    def _path(self, domain: str) -> Path:
        return self._kb_root / self._safe_domain(domain) / "associations.json"

    def _domain_dir(self, domain: str) -> Path:
        return self._kb_root / self._safe_domain(domain)

    def exists(self, domain: str) -> bool:
        return self._path(domain).exists()

    # ---------- 读 ----------

    async def read(self, domain: str) -> AssociationGraph:
        """读取关联图；不存在或损坏时返回空对象。

        损坏的 JSON 会记录 warning 而不是抛异常——派生数据可重建。

        注意（BUG-2026-08-20-001 已澄清）：本仓库**无持久缓存**——每次
        ``read()`` 都重新打开磁盘 JSON、重新解析、重新构建
        :class:`AssociationGraph`。也就是说，磁盘被外部写工具
        (包括 ``write_file`` / ``edit_file`` 之外的真实磁盘写入) 修改后，
        下一次 ``read()`` 立刻读到新数据，**无需也不存在 reload 工具**。
        如果发现工具返回旧数据，请优先检查：(a) 调用方是否走的是
        deepagents SDK 的虚拟 FS（不是真实磁盘），(b) FalkorDB 源是否
        走了独立视图（见 :mod:`src.api.routes.associations`）。
        """
        async with graph_lock():
            path = self._path(domain)
            if not path.exists():
                return AssociationGraph(domain=self._safe_domain(domain))
            try:
                async with aiofiles.open(path, encoding="utf-8") as f:
                    raw = await f.read()
                return AssociationGraph.model_validate_json(raw)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    "association_repo: %s corrupted (%s); returning empty",
                    path,
                    e,
                )
                return AssociationGraph(domain=self._safe_domain(domain))

    async def read_raw(self, domain: str) -> dict[str, Any]:
        """读取原始 dict，供 API 端点直接镜像 JSON 文件。"""
        async with graph_lock():
            path = self._path(domain)
            if not path.exists():
                return {
                    "domain": self._safe_domain(domain),
                    "concepts": {},
                    "resources": {},
                    "associations": [],
                    "metadata": {"derived_events": {}, "schema_version": "1.0"},
                    "generated_at": None,
                }
            async with aiofiles.open(path, encoding="utf-8") as f:
                return json.loads(await f.read())

    # ---------- 写 ----------

    async def write(self, domain: str, graph: AssociationGraph) -> None:
        """整体覆盖写入。"""
        if graph.domain != self._safe_domain(domain):
            graph = graph.model_copy(update={"domain": self._safe_domain(domain)})
        async with graph_lock():
            path = self._path(domain)
            path.parent.mkdir(parents=True, exist_ok=True)
            text = graph.model_dump_json(indent=2)
            await atomic_write_text(path, text)

    # ---------- 节点操作 ----------

    async def upsert_concept(self, domain: str, concept: ConceptNode) -> AssociationGraph:
        """插入或更新一个 Concept 节点，返回新图。"""
        graph = await self.read(domain)
        new_concepts = dict(graph.concepts)
        new_concepts[concept.name] = concept
        new_graph = graph.model_copy(update={"concepts": new_concepts})
        await self.write(domain, new_graph)
        return new_graph

    async def upsert_resource(self, domain: str, resource: ResourceNode) -> AssociationGraph:
        """插入或更新一个 Resource 节点，返回新图。"""
        graph = await self.read(domain)
        new_resources = dict(graph.resources)
        new_resources[resource.id] = resource
        new_graph = graph.model_copy(update={"resources": new_resources})
        await self.write(domain, new_graph)
        return new_graph

    async def delete_concept(self, domain: str, name: str) -> AssociationGraph:
        """删除 Concept 节点，同时清理涉及该节点的所有边。"""
        graph = await self.read(domain)
        new_concepts = {k: v for k, v in graph.concepts.items() if k != name}
        new_ass = [
            a for a in graph.associations
            if a.source != name and a.target != name
        ]
        new_graph = graph.model_copy(
            update={"concepts": new_concepts, "associations": new_ass}
        )
        await self.write(domain, new_graph)
        return new_graph

    async def delete_resource(self, domain: str, resource_id: str) -> AssociationGraph:
        """删除 Resource 节点，同时清理以该 resource_id 为 source/target 的所有边。

        否则会留下孤儿边（如 HAS_NOTE 指向已删除的 note_id）。
        """
        graph = await self.read(domain)
        new_resources = {k: v for k, v in graph.resources.items() if k != resource_id}
        new_ass = [
            a for a in graph.associations
            if a.source != resource_id and a.target != resource_id
        ]
        new_graph = graph.model_copy(
            update={"resources": new_resources, "associations": new_ass}
        )
        await self.write(domain, new_graph)
        return new_graph

    # ---------- 边操作 ----------

    async def add_association(
        self, domain: str, assoc: Association, *, dedupe: bool = True
    ) -> AssociationGraph:
        """增量添加一条关联边。

        Args:
            dedupe: True 时按 ``(source, target, relation)`` 去重。
        """
        graph = await self.read(domain)
        existing = list(graph.associations)
        if dedupe:
            if any(
                a.source == assoc.source
                and a.target == assoc.target
                and a.relation == assoc.relation
                for a in existing
            ):
                return graph
        existing.append(assoc)
        new_graph = graph.model_copy(update={"associations": existing})
        await self.write(domain, new_graph)
        return new_graph

    async def add_associations_batch(
        self, domain: str, assocs: list[Association]
    ) -> AssociationGraph:
        """批量添加关联边（按 source+target+relation 去重）。"""
        graph = await self.read(domain)
        existing_set = {
            (a.source, a.target, a.relation) for a in graph.associations
        }
        new_ass = list(graph.associations)
        for a in assocs:
            key = (a.source, a.target, a.relation)
            if key not in existing_set:
                new_ass.append(a)
                existing_set.add(key)
        new_graph = graph.model_copy(update={"associations": new_ass})
        await self.write(domain, new_graph)
        return new_graph

    async def delete_association(
        self, domain: str, source: str, target: str, relation: str
    ) -> AssociationGraph:
        """删除一条关联边。"""
        graph = await self.read(domain)
        new_ass = [
            a for a in graph.associations
            if not (a.source == source and a.target == target and a.relation == relation)
        ]
        new_graph = graph.model_copy(update={"associations": new_ass})
        await self.write(domain, new_graph)
        return new_graph

    # ---------- 派生状态 ----------

    async def mark_events_derived(
        self, domain: str, event_ids: list[str]
    ) -> AssociationGraph:
        """记录一批 event 已派生完成（join 给时间线 API 用）。"""
        if not event_ids:
            return await self.read(domain)
        graph = await self.read(domain)
        meta = graph.metadata.model_copy()
        meta.mark_derived([eid for eid in event_ids if eid])
        new_graph = graph.model_copy(update={"metadata": meta})
        await self.write(domain, new_graph)
        return new_graph

    # ---------- 全清 ----------

    async def clear(self, domain: str) -> None:
        """清空整个关联图（用于 ``--clear`` 全量重建）。"""
        async with graph_lock():
            path = self._path(domain)
            if path.exists():
                path.unlink()


__all__ = ["AssociationRepository"]