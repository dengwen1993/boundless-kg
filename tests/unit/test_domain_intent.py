"""Domain intent layer — multi-sample voting, enum snapping."""

from __future__ import annotations

from typing import Any

import pytest

from src.domain.intent import IntentParser, parse_intent_async
from src.domain.intent.models import (
    ALL_DIMENSION_ENUMS,
    ANGLE_ENUM,
    IntentMeta,
)
from src.infrastructure.llm import LLMResult, MockLLMClient


class _ScriptedLLM:
    """Returns a queue of canned LLMResult objects; raises after exhaustion."""

    def __init__(self, scripts: list[dict[str, Any]]) -> None:
        self._scripts = list(scripts)
        self.calls = 0

    async def chat_with_reasoning(self, system, user, **kwargs) -> LLMResult:
        if not self._scripts:
            raise RuntimeError("no more scripted responses")
        self.calls += 1
        import json as _json

        text = _json.dumps(self._scripts.pop(0))
        return LLMResult(text=text)


def _full_payload(angle: str = "技术原理") -> dict[str, Any]:
    return {
        "topic": "test",
        "angle": angle,
        "audience": "入门级",
        "depth": "实操落地",
        "knowledge_type": "方法技能",
        "learning_goal": "工作落地",
        "graph_type": "learning",
        "summary": "ok",
    }


async def test_parse_three_concurrent_samples() -> None:
    llm = _ScriptedLLM([_full_payload(), _full_payload(), _full_payload()])
    parser = IntentParser(llm)  # type: ignore[arg-type]
    meta = await parser.parse("topic")
    assert isinstance(meta, IntentMeta)
    assert llm.calls == 3
    assert meta.get("angle").value == "技术原理"


async def test_parse_majority_vote() -> None:
    # Two of three samples agree on "技术原理", one is bogus.
    bogus = _full_payload(angle="未知角度")
    llm = _ScriptedLLM([_full_payload(), _full_payload(), bogus])
    parser = IntentParser(llm)  # type: ignore[arg-type]
    meta = await parser.parse("topic")
    # majority is "技术原理".
    assert meta.get("angle").value == "技术原理"


async def test_parse_snaps_unknown_value_to_first_allowed() -> None:
    # All three return a bogus enum → snap to first in the allowed list.
    bad = _full_payload(angle="totally-unknown")
    llm = _ScriptedLLM([bad, bad, bad])
    parser = IntentParser(llm)  # type: ignore[arg-type]
    meta = await parser.parse("topic")
    assert meta.get("angle").value == ANGLE_ENUM[0]


async def test_parse_handles_all_samples_exploding() -> None:
    """If every sample raises, parser returns a bare IntentMeta."""
    class Boom:
        async def chat_with_reasoning(self, *a, **k):
            raise RuntimeError("nope")

    parser = IntentParser(Boom())  # type: ignore[arg-type]
    meta = await parser.parse("any")
    assert meta.topic == "any"
    assert meta.dimensions == []


async def test_parse_handles_non_json_response() -> None:
    class Garbage:
        async def chat_with_reasoning(self, *a, **k):
            return LLMResult(text="not json at all")

    parser = IntentParser(Garbage())  # type: ignore[arg-type]
    meta = await parser.parse("any")
    assert meta.dimensions == []


async def test_parse_extracts_braced_substring() -> None:
    """LLM returns text with leading prose; parser still finds the JSON."""
    import json as _json

    class WithProse:
        async def chat_with_reasoning(self, *a, **k):
            return LLMResult(
                text='hello here is json ' + _json.dumps(_full_payload()) + ' bye'
            )

    parser = IntentParser(WithProse())  # type: ignore[arg-type]
    meta = await parser.parse("any")
    assert meta.get("angle").value == "技术原理"


async def test_parse_intent_async_convenience() -> None:
    llm = MockLLMClient(latency_sec=0)
    meta = await parse_intent_async("topic", "", llm)
    # Mock always returns the echoed user message, not JSON, so dims are empty.
    assert isinstance(meta, IntentMeta)