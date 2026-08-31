"""DossierService — 节点档案业务逻辑。

负责:
- 增删改查档案条目
- 跨节点 / 跨领域 检索条目 (BM25 + 向量 + 时间衰减)
- 生成"prompt 注入片段"(给 Graph-aware prompt 用)
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from src.domain.graph.dossier import (
    Dossier,
    DossierEntry,
    DossierEntryType,
)
from src.infrastructure.embedding.client import EmbeddingClient
from src.infrastructure.graph_store.client import GraphStoreClient
from src.infrastructure.repository.dossier_repo import DossierRepository

logger = logging.getLogger(__name__)

# 时间衰减半衰期(天)。180 天前条目权重降到 ~37%
TIME_DECAY_HALFLIFE_DAYS = 180


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _days_since(dt: datetime) -> float:
    return max(0.0, (_utcnow() - dt).total_seconds() / 86400.0)


def _time_decay(days: float, halflife: float = TIME_DECAY_HALFLIFE_DAYS) -> float:
    """指数衰减:半衰期后权重降到 0.5。"""
    if halflife <= 0:
        return 1.0
    return math.exp(-math.log(2.0) * days / halflife)


def _usage_bonus(use_count: int) -> float:
    """老兵加成:被引用 N 次后权重 × (1 + ln(1 + N))。

    10 次引用 ≈ ×3.5, 100 次引用 ≈ ×6.0。
    """
    return 1.0 + math.log1p(max(0, use_count))


class DossierSearchHit:
    """一条检索命中(已打分)。"""

    __slots__ = (
        "node", "domain", "entry_id", "type", "title", "body",
        "tags", "score", "base_score", "time_decay", "usage_bonus",
        "use_count", "created_at",
    )

    def __init__(
        self,
        *,
        node: str, domain: str, entry_id: str, type: str,
        title: str, body: str, tags: list[str],
        base_score: float, time_decay: float, usage_bonus: float,
        use_count: int, created_at: datetime,
    ) -> None:
        self.node = node
        self.domain = domain
        self.entry_id = entry_id
        self.type = type
        self.title = title
        self.body = body
        self.tags = tags
        self.base_score = base_score
        self.time_decay = time_decay
        self.usage_bonus = usage_bonus
        self.use_count = use_count
        self.created_at = created_at
        self.score = base_score * time_decay * usage_bonus

    def to_prompt_fragment(self) -> str:
        """渲染成可注入 prompt 的 markdown 片段。"""
        type_label = {
            "sop": "SOP",
            "pitfall": "陷阱",
            "tip": "技巧",
            "term": "术语",
            "pattern": "模式",
            "link": "链接",
            "note": "备忘",
        }.get(self.type, self.type)
        tags_str = ", ".join(self.tags[:5]) if self.tags else ""
        body = self.body.strip()
        if len(body) > 300:
            body = body[:300] + "..."
        return (
            f"### {self.node} / {type_label}\n"
            f"> {self.title}\n"
            f"> {body}\n"
            f"> [{tags_str} | use_count={self.use_count} | "
            f"created={self.created_at.strftime('%Y-%m-%d')}]"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "domain": self.domain,
            "entry_id": self.entry_id,
            "type": self.type,
            "title": self.title,
            "body": self.body,
            "tags": self.tags,
            "score": round(self.score, 4),
            "base_score": round(self.base_score, 4),
            "time_decay": round(self.time_decay, 4),
            "usage_bonus": round(self.usage_bonus, 4),
            "use_count": self.use_count,
            "created_at": self.created_at.isoformat(),
        }


class DossierService:
    """节点档案业务逻辑。"""

    def __init__(
        self,
        dossier_repo: DossierRepository,
        embedding_client: EmbeddingClient | None = None,
        graph_store: GraphStoreClient | None = None,
    ) -> None:
        self._repo = dossier_repo
        self._embed = embedding_client
        self._store = graph_store

    # ---------- CRUD ----------

    async def add_entry(
        self,
        domain: str, node: str, *,
        type: str | DossierEntryType = DossierEntryType.NOTE,
        title: str, body: str,
        tags: list[str] | None = None,
        trigger_keywords: list[str] | None = None,
        evidence: str = "",
        score: float = 0.5,
        created_by: str = "agent",
    ) -> tuple[DossierEntry, bool]:
        """添加一条档案条目。

        Returns:
            (entry, created) — entry 是档案里最终落盘的那条;created=False
            表示命中 dedupe,档案里已有同标题 / 同 body 的条目,新 entry
            被丢弃。Reflector 用这个信号判断是否真的归档了。
        """
        entry_type = (
            type if isinstance(type, DossierEntryType) else DossierEntryType(type)
        )
        entry = DossierEntry(
            type=entry_type,
            title=title,
            body=body,
            tags=tags or [],
            trigger_keywords=trigger_keywords or [],
            evidence=evidence,
            score=max(0.0, min(1.0, score)),
            created_by=created_by,
        )
        dossier_obj = await self._repo.add_entry(
            domain, node, entry, dedupe=True,
        )
        # 判断是否真的写入了
        existing = dossier_obj.find_by_id(entry.id)
        if existing is not None:
            return existing, True
        # dedupe 命中 — 找相似条目返回
        similar = dossier_obj.find_similar(title=title, body=body)
        if similar:
            return similar[0], False
        # 异常兜底
        return entry, False

    async def list_entries(
        self, domain: str, node: str,
    ) -> list[DossierEntry]:
        dossier = await self._repo.read(domain, node)
        return list(dossier.entries)

    async def view_dossier(
        self, domain: str, node: str,
    ) -> Dossier:
        return await self._repo.read(domain, node)

    async def remove_entry(
        self, domain: str, node: str, entry_id: str,
    ) -> bool:
        dossier = await self._repo.remove_entry(domain, node, entry_id)
        removed = dossier.find_by_id(entry_id) is None
        # 也删 FalkorDB 节点
        if self._store is not None:
            try:
                self._store.delete_dossier_entry(domain, entry_id)
            except Exception as e:
                logger.warning("delete dossier entry in FalkorDB: %s", e)
        return removed

    async def update_entry(
        self, domain: str, node: str, entry_id: str, **updates,
    ) -> bool:
        dossier = await self._repo.update_entry(
            domain, node, entry_id, **updates
        )
        entry = dossier.find_by_id(entry_id)
        return entry is not None

    # ---------- 检索 + 时间衰减 ----------

    async def search(
        self,
        domain: str, query: str, *,
        top_k: int = 5,
        type_filter: list[str] | None = None,
        node_filter: list[str] | None = None,
    ) -> list[DossierSearchHit]:
        """跨节点搜档案条目。

        策略:
          1. 从 BM25 召回 (关键词 + 上下文)
          2. 从 FalkorDB 向量召回(若有 embedding)
          3. 合并去重 + 应用时间衰减 + 用量加成 → final_score
          4. 顺便增加 use_count(给老兵加成生效)
        """
        # ---- 1. BM25 召回 ----
        # BM25 索引目前只覆盖 concept/note/resource;dossier 不在内。
        # 我们的 dossier search 直接遍历所有节点做关键词匹配 +
        # 在条目文本上计算 TF-IDF-like 分数。轻量,够用。
        bm25_hits: dict[str, float] = {}  # entry_id -> score
        entries_by_id: dict[str, tuple[str, DossierEntry]] = {}  # entry_id -> (node, entry)

        all_node_names = await self._list_all_nodes(domain)
        for node in all_node_names:
            dossier = await self._repo.read(domain, node)
            for entry in dossier.entries:
                if type_filter and entry.type.value not in type_filter:
                    continue
                if node_filter and node not in node_filter:
                    continue
                entries_by_id[entry.id] = (node, entry)
                s = self._keyword_score(query, entry)
                if s > 0:
                    if entry.id in bm25_hits:
                        bm25_hits[entry.id] = max(bm25_hits[entry.id], s)
                    else:
                        bm25_hits[entry.id] = s

        # ---- 2. 向量召回(可选) ----
        vector_hits: dict[str, float] = {}
        if (
            self._embed is not None
            and self._embed.is_available
            and self._store is not None
            and await self._store.ensure_available()
        ):
            try:
                query_vec = await self._embed.embed_one(query)
                vec_results = self._store.vector_search(
                    domain, query_vec, top_k=top_k * 4,
                )
                for r in vec_results:
                    rid = r.get("id", "")
                    if rid.startswith("dossier_entry:"):
                        eid = rid.split(":", 1)[1]
                        vector_hits[eid] = float(r.get("score", 0.0))
            except Exception as e:
                logger.warning("dossier vector search failed: %s", e)

        # ---- 3. 合并 ----
        all_ids = set(bm25_hits.keys()) | set(vector_hits.keys())
        hits: list[DossierSearchHit] = []
        for eid in all_ids:
            if eid not in entries_by_id:
                continue  # FalkorDB 中残留(本地未建档)
            node, entry = entries_by_id[eid]
            bm25_s = bm25_hits.get(eid, 0.0)
            vec_s = vector_hits.get(eid, 0.0)
            # RRF 简化:取 max(bm25, vec)
            base_score = max(bm25_s, vec_s)
            if base_score <= 0:
                continue

            days = _days_since(entry.created_at)
            td = _time_decay(days)
            ub = _usage_bonus(entry.use_count)
            hits.append(DossierSearchHit(
                node=node, domain=domain, entry_id=eid,
                type=entry.type.value, title=entry.title,
                body=entry.body, tags=list(entry.tags),
                base_score=base_score, time_decay=td, usage_bonus=ub,
                use_count=entry.use_count,
                created_at=entry.created_at,
            ))

        hits.sort(key=lambda h: h.score, reverse=True)
        top = hits[:top_k]

        # ---- 4. 给老兵记账 ----
        for h in top:
            try:
                await self._repo.increment_use_count(
                    domain, h.node, h.entry_id,
                )
            except Exception:
                pass  # 记账失败不影响主流程

        return top

    async def build_prompt_context(
        self,
        domain: str, query: str, *,
        top_k: int = 3,
        type_filter: list[str] | None = None,
    ) -> str:
        """生成可注入 prompt 的 markdown 片段。

        给 Graph-aware prompt 用 — 直接拼到 system prompt。
        """
        hits = await self.search(
            domain, query, top_k=top_k, type_filter=type_filter,
        )
        if not hits:
            return ""
        parts = ["## 📚 相关经验档案(从你的知识图谱自动召回)\n"]
        for h in hits:
            parts.append(h.to_prompt_fragment() + "\n")
        return "\n".join(parts)

    # ---------- helpers ----------

    async def _list_all_nodes(self, domain: str) -> list[str]:
        """列出领域内所有节点。

        优先用 FalkorDB;不可用 / 失败时 fallback 到 notes/ 目录扫描
        (因为档案文件就落在这里)。
        """
        if self._store is not None:
            try:
                if await self._store.ensure_available():
                    concepts = self._store.all_concepts(domain)
                    names = [c["name"] for c in concepts if c.get("name")]
                    if names:
                        return names
            except Exception:
                pass
        # Fallback: 扫 notes/ 下所有节点目录
        try:
            return self._repo.list_nodes_with_dossier(domain)
        except Exception:
            return []

    @staticmethod
    def _keyword_score(query: str, entry: DossierEntry) -> float:
        """轻量关键词打分:触发词 + 标题命中加权。"""
        q = query.lower().strip()
        if not q:
            return 0.0
        title_l = entry.title.lower()
        body_l = entry.body.lower()
        tags_l = " ".join(entry.tags).lower()
        triggers_l = " ".join(entry.trigger_keywords).lower()

        score = 0.0
        # 触发词最优先
        for kw in (entry.trigger_keywords or []):
            if kw.lower() in q:
                score += 1.0
        # 标题命中
        for token in title_l.split():
            if len(token) >= 2 and token in q:
                score += 0.5
        # tags 命中
        for tag in entry.tags:
            if tag.lower() in q:
                score += 0.3
        # 正文中包含
        if any(
            len(tok) >= 2 and tok in body_l
            for tok in q.split()
        ):
            score += 0.2
        # 加 score 基础分
        score += float(entry.score) * 0.1
        return score


__all__ = [
    "DossierService",
    "DossierSearchHit",
    "TIME_DECAY_HALFLIFE_DAYS",
]