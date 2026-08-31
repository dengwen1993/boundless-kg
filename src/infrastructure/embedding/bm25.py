"""BM25 in-memory keyword index.

Per-domain BM25Okapi index, rebuilt when the domain's corpus changes.
Used alongside vector search for hybrid retrieval.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer for BM25.

    Splits on non-alphanumeric (including CJK characters as individual tokens).
    Lowercases everything for case-insensitive matching.
    """
    # Split CJK characters individually, split Latin words on boundaries
    tokens: list[str] = []
    # Match sequences of Latin letters/digits, or individual CJK chars
    for match in re.finditer(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower()):
        tokens.append(match.group(0))
    return tokens


class BM25Index:
    """Per-domain BM25 keyword index.

    Stores a corpus of documents (node name + description + summary)
    and provides keyword search via BM25 scoring.
    """

    def __init__(self) -> None:
        self._indexes: dict[str, BM25Okapi] = {}
        self._corpora: dict[str, list[dict[str, Any]]] = {}
        self._tokenized: dict[str, list[list[str]]] = {}

    def rebuild(
        self, domain: str, documents: list[dict[str, Any]]
    ) -> None:
        """Rebuild the BM25 index for a domain.

        Each document should have:
            - id: str (e.g. "concept:ReAct推理")
            - text: str (name + description + summary concatenated)
            - type: str ("concept" | "note" | "resource")
            - name: str (display name)
        """
        if not documents:
            self._indexes.pop(domain, None)
            self._corpora.pop(domain, None)
            self._tokenized.pop(domain, None)
            return

        tokenized = [_tokenize(doc.get("text", "")) for doc in documents]
        self._tokenized[domain] = tokenized
        self._corpora[domain] = documents
        try:
            self._indexes[domain] = BM25Okapi(tokenized)
        except Exception as e:
            logger.warning("BM25 index rebuild failed for %s: %s", domain, e)
            self._indexes.pop(domain, None)

    def search(
        self, domain: str, query: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        """Search the BM25 index for a domain.

        Returns list of {id, name, type, score} sorted by BM25 score desc.
        """
        idx = self._indexes.get(domain)
        if idx is None:
            return []

        tokens = _tokenize(query)
        if not tokens:
            return []

        try:
            scores = idx.get_scores(tokens)
        except Exception as e:
            logger.warning("BM25 search failed for %s: %s", domain, e)
            return []

        corpus = self._corpora.get(domain, [])
        results: list[dict[str, Any]] = []
        for i, score in enumerate(scores):
            # rank_bm25 的 BM25Okapi 在小语料库(N 很小)下 IDF 可能
            # 算成负数,导致分数为负。扩域引入的"邻居名"在文档里只
            # 出现 1 次时,这种负分很常见。这里用 == 0 过滤,
            # 负分文档也参与排序 — 比"score <= 0 直接过滤"更稳健。
            if score == 0:
                continue
            doc = corpus[i] if i < len(corpus) else {}
            results.append({
                "id": doc.get("id", ""),
                "name": doc.get("name", ""),
                "type": doc.get("type", "concept"),
                "domain": domain,
                "bm25_score": abs(float(score)),
                "snippet": doc.get("text", "")[:200],
            })

        results.sort(key=lambda x: x["bm25_score"], reverse=True)
        return results[:top_k]

    def is_indexed(self, domain: str) -> bool:
        """Whether a BM25 index exists for the domain."""
        return domain in self._indexes

    def clear(self, domain: str) -> None:
        """Remove the BM25 index for a domain."""
        self._indexes.pop(domain, None)
        self._corpora.pop(domain, None)
        self._tokenized.pop(domain, None)

    def clear_all(self) -> None:
        """Remove all BM25 indexes."""
        self._indexes.clear()
        self._corpora.clear()
        self._tokenized.clear()


__all__ = ["BM25Index", "_tokenize"]
