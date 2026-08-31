"""DossierRepository — async IO over ``notes/{node}/dossier.json``.

每节点一份档案,真相源。FalkorDB 是派生态。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import aiofiles

from src.domain.graph.dossier import Dossier, DossierEntry
from src.infrastructure.lock import graph_lock

from ._atomic import atomic_write_text

logger = logging.getLogger(__name__)


class DossierRepository:
    """Async CRUD over per-node ``dossier.json`` files.

    所有 mutation 自动持有 ``graph_lock()``,与 GraphRepository /
    AssociationRepository 共享同一锁。
    """

    def __init__(self, kb_root: Path) -> None:
        self._kb_root = Path(kb_root)

    @property
    def kb_root(self) -> Path:
        return self._kb_root

    @staticmethod
    def _safe_domain(domain: str) -> str:
        return domain.split(" / ")[0].split(" \\ ")[0].strip()

    def _path(self, domain: str, node: str) -> Path:
        safe_node = node.replace("/", "_").replace("\\", "_")
        return (
            self._kb_root
            / self._safe_domain(domain)
            / "notes"
            / safe_node
            / "dossier.json"
        )

    def _node_dir(self, domain: str, node: str) -> Path:
        safe_node = node.replace("/", "_").replace("\\", "_")
        return (
            self._kb_root
            / self._safe_domain(domain)
            / "notes"
            / safe_node
        )

    def exists(self, domain: str, node: str) -> bool:
        return self._path(domain, node).exists()

    async def read(self, domain: str, node: str) -> Dossier:
        """读取节点档案;不存在或损坏时返回空对象。

        与 AssociationRepository 风格一致:损坏 JSON 只 warning,不抛异常。
        """
        async with graph_lock():
            path = self._path(domain, node)
            if not path.exists():
                return Dossier(node=node, domain=self._safe_domain(domain))
            try:
                async with aiofiles.open(path, encoding="utf-8") as f:
                    raw = await f.read()
                return Dossier.model_validate_json(raw)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    "dossier_repo: %s corrupted (%s); returning empty",
                    path, e,
                )
                return Dossier(node=node, domain=self._safe_domain(domain))

    async def write(self, domain: str, dossier: Dossier) -> None:
        """整体覆盖写入。"""
        async with graph_lock():
            if dossier.domain != self._safe_domain(domain):
                dossier = dossier.model_copy(
                    update={"domain": self._safe_domain(domain)}
                )
            path = self._path(domain, dossier.node)
            path.parent.mkdir(parents=True, exist_ok=True)
            text = dossier.model_dump_json(indent=2)
            await atomic_write_text(path, text)

    async def add_entry(
        self,
        domain: str,
        node: str,
        entry: DossierEntry,
        *,
        dedupe: bool = True,
    ) -> Dossier:
        """增量添加一条档案条目。"""
        dossier = await self.read(domain, node)

        if dedupe:
            # 同 ID 直接返回;标题完全相同也跳过
            if any(e.id == entry.id for e in dossier.entries):
                return dossier
            similar = dossier.find_similar(title=entry.title, body=entry.body)
            if similar:
                logger.info(
                    "dossier_repo: similar entry exists for '%s' (matches=%d), skip",
                    entry.title, len(similar),
                )
                return dossier

        dossier.add_entry(entry)
        await self.write(domain, dossier)
        return dossier

    async def remove_entry(
        self, domain: str, node: str, entry_id: str
    ) -> Dossier:
        dossier = await self.read(domain, node)
        dossier.remove_entry(entry_id)
        await self.write(domain, dossier)
        return dossier

    async def update_entry(
        self, domain: str, node: str, entry_id: str, **updates
    ) -> Dossier:
        dossier = await self.read(domain, node)
        dossier.update_entry(entry_id, **updates)
        await self.write(domain, dossier)
        return dossier

    async def increment_use_count(
        self, domain: str, node: str, entry_id: str
    ) -> None:
        """记录条目被检索 / 注入 prompt 的次数(老兵加成)。"""
        from datetime import datetime, timezone
        dossier = await self.read(domain, node)
        entry = dossier.find_by_id(entry_id)
        if entry is None:
            return
        entry.use_count += 1
        entry.last_used_at = datetime.now(timezone.utc).replace(microsecond=0)
        await self.write(domain, dossier)

    def list_nodes_with_dossier(self, domain: str) -> list[str]:
        """列出本领域内所有已建档案的节点名。

        用于 DossierService.search 的 fallback(FalkorDB 不可达时)。
        只扫 ``notes/<node>/dossier.json`` 文件,不去碰 FalkorDB。
        """
        notes_dir = self._kb_root / self._safe_domain(domain) / "notes"
        if not notes_dir.exists():
            return []
        names: list[str] = []
        for child in notes_dir.iterdir():
            if not child.is_dir():
                continue
            if (child / "dossier.json").exists():
                names.append(child.name)
        return sorted(names)


__all__ = ["DossierRepository"]