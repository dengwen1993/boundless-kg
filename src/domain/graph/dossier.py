"""Dossier — per-node experience archive.

每节点一份档案,记录可复用经验(SOP / 陷阱 / 术语 / 技巧等)。
物理存储在 ``notes/{node}/dossier.json``,FalkorDB 是派生态。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _new_entry_id() -> str:
    """Generate a short 8-char id for a dossier entry."""
    import os
    import time
    # Windows time.time_ns() can repeat within a single batch; mix in
    # entropy (os.urandom + a counter) so back-to-back calls in the
    # same nanosecond still get distinct ids.
    seed = "{}-{}{}-{}".format(
        time.time_ns(),
        os.getpid(),
        hashlib.sha1(os.urandom(8)).hexdigest()[:6],
        _new_entry_id._counter(),
    )
    return f"de_{hashlib.sha1(seed.encode()).hexdigest()[:8]}"


def _new_entry_id_counter() -> int:
    """Monotonic counter to disambiguate ids minted in the same nanosecond."""
    n = getattr(_new_entry_id, "_n", 0) + 1
    _new_entry_id._n = n  # type: ignore[attr-defined]
    return n


_new_entry_id._counter = staticmethod(lambda: _new_entry_id_counter())  # type: ignore[attr-defined]


class DossierEntryType(str, Enum):
    """条目类型 — 决定 prompt 注入的章节标题和检索优先级。"""

    SOP = "sop"               # 标准操作流程(最高)
    TIP = "tip"               # 小技巧
    PITFALL = "pitfall"       # 陷阱 / 反面教材
    TERM = "term"             # 术语解释
    PATTERN = "pattern"       # 设计模式 / 抽象
    LINK = "link"             # 外部链接 / 资源索引
    NOTE = "note"             # 通用备忘


class DossierEntry(BaseModel):
    """一条档案条目。

    Attributes:
        id: 8 字符短 ID,前缀 ``de_``
        type: 条目类型(决定检索权重和 UI 标签)
        title: 一句话标题
        body: 正文(支持 markdown,建议 < 500 字)
        tags: 关键词,跨节点检索时用
        trigger_keywords: 高优先级关键词,匹配时强制注入 prompt
        evidence: 归档依据(用户原话 / Agent 反思)
        score: 重要性 0~1,LLM 自评
        use_count: 被引用次数(给老兵加成)
        created_by: ``agent`` / ``user``
        created_at: 创建时间
        last_used_at: 最近一次被检索 / 注入的时间
        supersedes: 被本条目替代的旧条目 ID 列表(合并时用)
    """

    id: str = Field(default_factory=_new_entry_id)
    type: DossierEntryType = DossierEntryType.NOTE
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)
    trigger_keywords: list[str] = Field(default_factory=list)
    evidence: str = ""
    score: float = Field(default=0.5, ge=0.0, le=1.0)
    use_count: int = 0
    created_by: str = "agent"
    created_at: datetime = Field(default_factory=_utcnow)
    last_used_at: datetime | None = None
    supersedes: list[str] = Field(default_factory=list)


class DossierMetadata(BaseModel):
    last_modified: datetime = Field(default_factory=_utcnow)
    schema_version: str = "1.0"
    entry_count: int = 0


class Dossier(BaseModel):
    """节点档案 — ``notes/{node}/dossier.json`` 的 schema。"""

    node: str
    domain: str
    entries: list[DossierEntry] = Field(default_factory=list)
    metadata: DossierMetadata = Field(default_factory=DossierMetadata)

    def add_entry(self, entry: DossierEntry) -> None:
        """Append a new entry, dedup by id."""
        if any(e.id == entry.id for e in self.entries):
            return
        self.entries.append(entry)
        self.metadata.entry_count = len(self.entries)
        self.metadata.last_modified = _utcnow()

    def update_entry(self, entry_id: str, **updates: Any) -> bool:
        """Patch fields of an existing entry."""
        for e in self.entries:
            if e.id == entry_id:
                for k, v in updates.items():
                    if hasattr(e, k):
                        setattr(e, k, v)
                self.metadata.last_modified = _utcnow()
                return True
        return False

    def remove_entry(self, entry_id: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.id != entry_id]
        if len(self.entries) < before:
            self.metadata.entry_count = len(self.entries)
            self.metadata.last_modified = _utcnow()
            return True
        return False

    def find_by_id(self, entry_id: str) -> DossierEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def find_similar(self, *, title: str = "", body: str = "",
                     threshold: float = 0.8) -> list[DossierEntry]:
        """Lightweight dedup: same title or large body overlap → candidate duplicates."""
        candidates: list[DossierEntry] = []
        norm_title = title.strip().lower()
        for e in self.entries:
            if norm_title and e.title.strip().lower() == norm_title:
                candidates.append(e)
                continue
            if body and e.body and (
                body in e.body or e.body in body
            ):
                candidates.append(e)
        return candidates


__all__ = [
    "DossierEntry",
    "Dossier",
    "DossierEntryType",
    "DossierMetadata",
    "_new_entry_id",
]