"""File-staging + classify tools."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import tool

from src.agent.dependencies import get_resource_service
from src.observability.logged_tool import logged_tool


@tool
@logged_tool
async def kg_stage_file(src_path: str, suggested_name: str = "") -> str:
    """把本地文件移动到暂存区。"""
    svc = get_resource_service()
    src = Path(src_path)
    name = suggested_name or src.name
    staged = await svc.stage_upload(src, name)
    return f"已暂存：{staged}"


@tool
@logged_tool
async def kg_classify_pending(domain: str, staged_path: str, description: str = "") -> str:
    """AI 归类暂存文件应该挂在哪个节点下。"""
    svc = get_resource_service()
    decision = await svc.classify_and_promote(
        domain,
        Path(staged_path),
        {"name": Path(staged_path).name, "description": description},
    )
    return json.dumps(decision, ensure_ascii=False)


@tool
@logged_tool
async def kg_create_node_with_resource(
    domain: str, node_name: str, src_path: str
) -> str:
    """新建节点 + 把本地文件挂到该节点下。"""
    svc = get_resource_service()
    staged = await svc.stage_upload(Path(src_path), Path(src_path).name)
    result = await svc.classify_and_promote(
        domain,
        staged,
        {"name": Path(src_path).name, "description": node_name},
    )
    return f"✅ 节点 {result['node']!r} 已建，文件：{result['path']}"


__all__ = [
    "kg_stage_file",
    "kg_classify_pending",
    "kg_create_node_with_resource",
]
