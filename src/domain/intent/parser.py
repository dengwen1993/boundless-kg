"""Async intent parser — multi-sample LLM vote + conflict repair.

ENGINEERING_PLAN.md §4.4 / §3.5 — three samples taken concurrently
via ``asyncio.gather``, then a simple majority vote resolves the
final ``IntentMeta``. Conflict rules snap enum mismatches to a
canonical value.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from typing import Any

from src.domain.protocols import LLMClientProtocol

from .models import ALL_DIMENSION_ENUMS, IntentDimension, IntentMeta


SYSTEM_PROMPT = """你是一个知识图谱意图识别助手。
对用户输入的主题，返回严格的 JSON：
{
  "topic": "...",
  "angle": "<从 angle 枚举中选择>",
  "audience": "<从 audience 枚举中选择>",
  "depth": "<从 depth 枚举中选择>",
  "knowledge_type": "<从 knowledge_type 枚举中选择>",
  "learning_goal": "<从 learning_goal 枚举中选择>",
  "graph_type": "learning | person_relationship | competitor_analysis | event_timeline | other_non_learning",
  "summary": "一句话总结用户的学习诉求"
}

枚举：
- angle:        """ + " / ".join(ALL_DIMENSION_ENUMS["angle"]) + """
- audience:     """ + " / ".join(ALL_DIMENSION_ENUMS["audience"]) + """
- depth:        """ + " / ".join(ALL_DIMENSION_ENUMS["depth"]) + """
- knowledge_type:""" + " / ".join(ALL_DIMENSION_ENUMS["knowledge_type"]) + """
- learning_goal:""" + " / ".join(ALL_DIMENSION_ENUMS["learning_goal"]) + """

如果主题不是学习类（如人物关系 / 竞品分析 / 时间线 / 其它），把 graph_type 设成对应值。
只返回 JSON，不要任何额外解释。"""


class IntentParser:
    """Async intent-understanding wrapper around an LLM client."""

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        *,
        sample_temperatures: tuple[float, ...] = (0.3, 0.5, 0.7),
    ) -> None:
        self._llm = llm_client
        self._temps = sample_temperatures

    async def parse(self, topic: str, direction_hint: str = "") -> IntentMeta:
        user_msg = f"主题：{topic}\n附加方向提示：{direction_hint or '（无）'}"
        samples = await asyncio.gather(
            *(
                self._sample(user_msg, t)
                for t in self._temps
            ),
            return_exceptions=True,
        )
        valid = [s for s in samples if isinstance(s, dict) and s]
        if not valid:
            return IntentMeta(topic=topic, direction_hint=direction_hint)
        merged = self._vote(valid)
        return self._to_meta(topic, direction_hint, merged)

    async def _sample(self, user_msg: str, temperature: float) -> dict[str, Any]:
        result = await self._llm.chat_with_reasoning(
            SYSTEM_PROMPT,
            user_msg,
            temperature=temperature,
            max_tokens=800,
            json_mode=True,
        )
        return _safe_json(result.text)

    @staticmethod
    def _vote(samples: list[dict[str, Any]]) -> dict[str, Any]:
        """Majority vote across three samples, per dimension."""
        keys = ["angle", "audience", "depth", "knowledge_type", "learning_goal", "graph_type"]
        merged: dict[str, Any] = {}
        for k in keys:
            counter: Counter[str] = Counter(str(s.get(k, "")) for s in samples)
            merged[k] = counter.most_common(1)[0][0] if counter else ""
        merged["summary"] = samples[0].get("summary", "")
        return merged

    @staticmethod
    def _to_meta(topic: str, direction_hint: str, data: dict[str, Any]) -> IntentMeta:
        dims: list[IntentDimension] = []
        for k in ("angle", "audience", "depth", "knowledge_type", "learning_goal"):
            v = data.get(k, "")
            allowed = ALL_DIMENSION_ENUMS.get(k, [])
            if allowed and v not in allowed:
                v = allowed[0]
            dims.append(IntentDimension(key=k, value=v))
        return IntentMeta(
            topic=topic,
            dimensions=dims,
            graph_type=data.get("graph_type", "learning") or "learning",
            direction_hint=direction_hint,
            summary=data.get("summary", ""),
        )


async def parse_intent_async(
    topic: str,
    direction_hint: str,
    llm_client: LLMClientProtocol,
) -> IntentMeta:
    """Top-level convenience."""
    return await IntentParser(llm_client).parse(topic, direction_hint)


def _safe_json(text: str) -> dict[str, Any]:
    """Best-effort JSON parse; tolerates trailing prose + markdown fences."""
    from src.utils.json_repair import try_parse_json

    if not text:
        return {}
    data = try_parse_json(text)
    if isinstance(data, dict):
        return data
    # Try to locate the first {...} block (legacy fallback).
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return {}


__all__ = ["IntentParser", "parse_intent_async", "_safe_json"]