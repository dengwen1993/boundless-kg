"""AI-assisted classification of pending uploads into the right node."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.domain.graph.models import Graph
from src.domain.protocols import LLMClientProtocol


SYSTEM_PROMPT = """你是资料归类助手。
给定一个文件描述 + 当前领域的节点列表，输出 JSON：
{
  "node": "<节点名；如无合适节点则填 \"new_node\">",
  "new_node_name": "<新建节点建议名；node 为 new_node 时填>",
  "rationale": "<一句话理由>"
}
只返回 JSON。"""


@dataclass(slots=True)
class ClassificationDecision:
    node: str
    new_node_name: str = ""
    rationale: str = ""

    @property
    def is_new(self) -> bool:
        return self.node == "new_node"


async def classify_pending_async(
    file_meta: dict,
    graph: Graph,
    llm_client: LLMClientProtocol,
) -> ClassificationDecision:
    """Ask the LLM which node a pending upload belongs to."""
    user_msg = (
        f"文件描述：{file_meta.get('description', file_meta.get('name', '?'))}\n"
        f"当前节点：{', '.join(graph.node_names()) or '（空）'}\n"
        "请归类。"
    )
    result = await llm_client.chat(
        SYSTEM_PROMPT,
        user_msg,
        temperature=0.3,
        max_tokens=400,
        json_mode=True,
    )
    return _parse(result)


def _parse(text: str) -> ClassificationDecision:
    from src.utils.json_repair import try_parse_json

    data = try_parse_json(text)
    if isinstance(data, dict):
        return ClassificationDecision(
            node=str(data.get("node", "")),
            new_node_name=str(data.get("new_node_name", "")),
            rationale=str(data.get("rationale", "")),
        )
    return ClassificationDecision(node="new_node", rationale="parse-failed")


__all__ = ["classify_pending_async", "ClassificationDecision"]