"""Intent-understanding models — enums + the meta envelope."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IntentDimension(BaseModel):
    """One categorical axis (angle / audience / depth / ...)."""

    key: str
    value: str
    confidence: float = 1.0


# Mirrors skills/.../constants.py — kept in sync via the validator
# rules below. Adding a new axis here requires updating the enums.
ANGLE_ENUM = [
    "知识体系",
    "技术原理",
    "工程实践",
    "应用场景",
    "行业纵深",
    "前沿动态",
    "考点梳理",
    "面试专题",
]
AUDIENCE_ENUM = [
    "零基础",
    "入门级",
    "进阶级",
    "精通级",
    "专家级",
    "学生群体",
    "职场通用",
]
DEPTH_ENUM = [
    "科普认知",
    "实操落地",
    "原理拆解",
    "深度进阶",
    "前沿研究",
    "对比分析",
]
KNOWLEDGE_TYPE_ENUM = [
    "基础概念",
    "核心原理",
    "方法技能",
    "最佳实践",
    "思维框架",
]
LEARNING_GOAL_ENUM = [
    "兴趣科普",
    "求职面试",
    "工作落地",
    "系统进阶",
    "学术研究",
    "应试考证",
    "教学备课",
    "内容创作",
    "竞赛备赛",
]
GRAPH_TYPE_ENUM = [
    "learning",
    "person_relationship",
    "competitor_analysis",
    "event_timeline",
    "other_non_learning",
]


ALL_DIMENSION_ENUMS: dict[str, list[str]] = {
    "angle": ANGLE_ENUM,
    "audience": AUDIENCE_ENUM,
    "depth": DEPTH_ENUM,
    "knowledge_type": KNOWLEDGE_TYPE_ENUM,
    "learning_goal": LEARNING_GOAL_ENUM,
    "graph_type": GRAPH_TYPE_ENUM,
}


class IntentMeta(BaseModel):
    """Complete intent snapshot returned by ``IntentParser``."""

    topic: str
    dimensions: list[IntentDimension] = Field(default_factory=list)
    graph_type: str = "learning"
    direction_hint: str = ""
    summary: str = ""

    def get(self, key: str) -> IntentDimension | None:
        for d in self.dimensions:
            if d.key == key:
                return d
        return None


__all__ = [
    "IntentDimension",
    "IntentMeta",
    "ANGLE_ENUM",
    "AUDIENCE_ENUM",
    "DEPTH_ENUM",
    "KNOWLEDGE_TYPE_ENUM",
    "LEARNING_GOAL_ENUM",
    "GRAPH_TYPE_ENUM",
    "ALL_DIMENSION_ENUMS",
]