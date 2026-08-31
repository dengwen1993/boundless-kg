"""Pipeline tools — kick off + check the generation pipeline."""

from __future__ import annotations

import json
from datetime import datetime

from langchain_core.tools import tool

from src.agent.dependencies import get_generation_pipeline
from src.observability.logged_tool import logged_tool


@tool
@logged_tool
async def kg_run_skill(topic: str, direction_hint: str = "") -> str:
    """异步触发三阶段图谱生成；返回 task_id。"""
    pipeline = get_generation_pipeline()
    task_id = await pipeline.start(topic, direction_hint)
    return (
        f"✅ 已启动图谱生成流水线（task_id={task_id}）。\n"
        f"   主题：{topic}\n"
        f"   流水线分 5 个阶段：意图解析 → 热点关键词采集 → 多层子树扩展（目标 200+ 节点） → 图谱合成 → 持久化\n"
        f"   请用 kg_check_status 查询进度。注意：不要连续快速轮询，"
        f"   每次查询后告诉用户稍等片刻再查下一次。"
    )


@tool
@logged_tool
async def kg_check_status(task_id: str) -> str:
    """查询图谱生成任务进度。

    返回包含：当前阶段、进度百分比、已运行秒数、当前阶段已耗时秒数。
    如果阶段长时间不变，说明该阶段正在执行耗时操作（如 LLM 调用或搜索）。
    """
    pipeline = get_generation_pipeline()
    p = await pipeline.status(task_id)
    if p is None:
        return f"❌ 任务 {task_id} 不存在"

    now = datetime.utcnow()
    elapsed_sec = (now - p.started_at).total_seconds()

    # 当前阶段已耗时
    stage_elapsed_sec = 0.0
    if p.stage_started_at:
        stage_elapsed_sec = (now - p.stage_started_at).total_seconds()

    # 阶段说明
    stage_desc = {
        "intent": "意图解析（LLM 3 次采样投票）",
        "hot-keywords": "热点关键词采集（搜索 + LLM 提取）",
        "expand": "多层子树扩展（目标 200+ 节点）",
        "graph-synth": "图谱合成（构建节点和链接）",
        "persist": "持久化（写入 knowledge_graph.json）",
        "done": "已完成",
        "error": "出错",
    }.get(p.stage, p.stage)

    out = {
        "task_id": p.task_id,
        "domain": p.domain,
        "stage": p.stage,
        "stage_desc": stage_desc,
        "progress": p.progress,
        "elapsed_sec": round(elapsed_sec, 1),
        "stage_elapsed_sec": round(stage_elapsed_sec, 1),
        "started_at": p.started_at.isoformat(),
        "finished_at": p.finished_at.isoformat() if p.finished_at else None,
        "error": p.error,
    }

    # 添加建议
    if p.finished_at is None and p.error is None:
        if p.stage == "hot-keywords" and stage_elapsed_sec > 30:
            out["hint"] = "搜索阶段耗时较长（可能搜索后端不可达），系统会自动降级到 LLM 直接生成。"
        elif stage_elapsed_sec < 10:
            out["hint"] = "阶段刚开始，请稍候 15-30 秒后再查询。"
        else:
            out["hint"] = "正在处理中，请稍候 15-30 秒后再查询。"
    elif p.error:
        out["hint"] = "任务失败，可以尝试重新启动（kg_run_skill）。"

    return json.dumps(out, ensure_ascii=False)


__all__ = ["kg_run_skill", "kg_check_status"]
