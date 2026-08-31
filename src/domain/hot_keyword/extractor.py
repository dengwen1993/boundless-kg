"""LLM-based keyword extractor over search results."""

from __future__ import annotations

import json
from typing import Any

from src.domain.protocols import LLMClientProtocol, SearchResult


SYSTEM_PROMPT = """你是热点词提取助手。
从给出的搜索结果里抽取「与主题最相关、最具学习价值」的关键词，每个关键词配 0-1 的相关度分数。
只返回 JSON 数组：[{ "keyword": "...", "score": 0.95, "evidence": "..." }, ...]
不要其它文字。"""


async def extract_keywords_async(
    topic: str,
    results: list[SearchResult],
    llm_client: LLMClientProtocol,
    *,
    top_k: int = 30,
) -> list[dict[str, Any]]:
    """Run the LLM keyword extractor over the search results."""
    if not results:
        return []
    user_msg = (
        f"主题：{topic}\n\n"
        "搜索结果：\n"
        + "\n".join(
            f"- {r.title}: {r.snippet[:200]} ({r.link})"
            for r in results[:30]
        )
        + f"\n\n输出 top-{top_k} 关键词。"
    )
    result = await llm_client.chat(
        SYSTEM_PROMPT, user_msg, temperature=0.4, max_tokens=2000, json_mode=True
    )
    items = _safe_list(result)
    items.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return items[:top_k]


def _safe_list(text: str) -> list[dict[str, Any]]:
    from src.utils.json_repair import try_parse_json

    data = try_parse_json(text)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "keywords" in data:
        return list(data["keywords"])
    return []


__all__ = ["extract_keywords_async"]