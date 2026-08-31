"""``kg-engine`` CLI entry — subcommand dispatch.

也可以通过 ``python -m src.cli`` 调用。

每个子命令都会输出清晰的日志，让用户明确知道 CLI 正在做什么、
进行到了哪一步、结果如何。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from src import __version__

logger = logging.getLogger("kg_engine.cli")


# ------------------------------------------------------------------
# 日志初始化
# ------------------------------------------------------------------


def _setup_logging() -> None:
    """挂载项目日志系统：logs/{debug,info,error}.log + stderr 控制台。

    控制台级别由 ``KG_LOG_LEVEL`` 环境变量控制（默认 INFO）；
    文件日志始终完整记录（stdout 上的机器可读输出不受影响）。
    """
    from src.config import get_settings
    from src.observability.logging_config import setup_logging

    setup_logging(console_level=get_settings().log_level)


def _banner(title: str) -> None:
    """在 stderr 打印一条醒目的分隔横幅。"""
    line = "═" * 60
    logger.info(line)
    logger.info("  %s", title)
    logger.info(line)


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    _setup_logging()

    parser = argparse.ArgumentParser(prog="kg_engine", description="Knowledge graph engine")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="Run the FastAPI server")

    gen = sub.add_parser("generate", help="Run the five-stage generation pipeline")
    gen.add_argument("topic")
    gen.add_argument("--direction-hint", default="")
    gen.add_argument("--wait", action="store_true")

    val = sub.add_parser("validate", help="Validate + score a domain")
    val.add_argument("domain")

    rep = sub.add_parser("report", help="Print quality report for a domain")
    rep.add_argument("domain")

    cards = sub.add_parser("cards", help="Manage prompt cards (list / add / delete / view)")
    cards_sub = cards.add_subparsers(dest="cards_cmd", required=True)

    cards_sub.add_parser("list", help="List all loaded cards")

    cadd = cards_sub.add_parser("add", help="Create or update a card")
    cadd.add_argument("id", help="Card ID (alphanumeric / dash / underscore)")
    cadd.add_argument("--title", required=True, help="Card title")
    cadd.add_argument("--body", required=True, help="Card body (Markdown)")
    cadd.add_argument("--triggers", default="", help="Comma-separated trigger keywords")
    cadd.add_argument("--tools", default="", help="Comma-separated applies_to_tools")
    cadd.add_argument("--priority", type=int, default=100, help="Render priority (lower = earlier)")

    cdel = cards_sub.add_parser("delete", help="Delete a card")
    cdel.add_argument("id", help="Card ID to delete")

    cview = cards_sub.add_parser("view", help="View a card's full content")
    cview.add_argument("id", help="Card ID to view")

    assoc = sub.add_parser("associations", help="Manage associations.json derivation")
    assoc_sub = assoc.add_subparsers(dest="assoc_cmd", required=True)

    assoc_sync = assoc_sub.add_parser("sync", help="Full sync for a domain")
    assoc_sync.add_argument("domain")

    assoc_node = assoc_sub.add_parser("sync-node", help="Sync one node")
    assoc_node.add_argument("domain")
    assoc_node.add_argument("node")

    assoc_extract = assoc_sub.add_parser("extract", help="Force LLM extraction now")
    assoc_extract.add_argument("domain")

    assoc_stats = assoc_sub.add_parser("stats", help="Show statistics")
    assoc_stats.add_argument("domain")

    assoc_clear = assoc_sub.add_parser("clear", help="Clear associations.json")
    assoc_clear.add_argument("domain")

    args = parser.parse_args(argv)

    logger.info("kg_engine v%s  |  命令: %s", __version__, args.cmd)

    if args.cmd == "serve":
        return _serve(args)

    if args.cmd == "generate":
        return asyncio.run(_generate(args))

    if args.cmd == "validate":
        return _validate(args)

    if args.cmd == "report":
        return _report(args)

    if args.cmd == "cards":
        return _cards(args)

    if args.cmd == "associations":
        return _associations(args)
    return 1


# ------------------------------------------------------------------
# serve
# ------------------------------------------------------------------


def _serve(args) -> int:
    from src.api.server import create_app
    import uvicorn

    from src.config import get_settings, get_llm_provider, get_kb_root

    s = get_settings()
    provider = get_llm_provider()
    kb_root = get_kb_root()

    _banner("启动 API 服务")
    logger.info("  监听地址  : %s:%s", s.api.host, s.api.port)
    logger.info("  LLM 提供方: %s", provider)
    logger.info("  知识库根目录: %s", kb_root)
    logger.info("  CORS 来源 : %s", s.api.cors_origins)
    logger.info("  调试模式  : %s", s.api.debug)
    logger.info("  日志级别  : %s", s.log_level)
    logger.info("─" * 60)
    logger.info("按 Ctrl+C 停止服务")

    uvicorn.run(create_app(), host=s.api.host, port=s.api.port)
    return 0


# ------------------------------------------------------------------
# generate
# ------------------------------------------------------------------


async def _generate(args) -> int:
    from src.agent.dependencies import (
        get_generation_pipeline,
        get_graph_service,
        reset_dependencies,
    )

    _banner("启动五阶段图谱生成流水线")
    logger.info("  主题       : %s", args.topic)
    logger.info("  方向提示   : %s", args.direction_hint or "(无)")
    logger.info("  等待完成   : %s", args.wait)
    logger.info("─" * 60)

    reset_dependencies()
    logger.info("[1/4] 初始化服务依赖 …")
    pipeline = get_generation_pipeline()
    graph_svc = get_graph_service()
    logger.info("      ✓ GenerationPipeline 就绪")
    logger.info("      ✓ GraphService 就绪")

    logger.info("[2/4] 提交生成任务 …")
    task_id = await pipeline.start(args.topic, args.direction_hint)
    logger.info("      ✓ 任务已提交  task_id=%s", task_id)
    print(f"task_id={task_id}", flush=True)  # stdout 保留机器可读输出

    if not args.wait:
        logger.info("[3/4] 未指定 --wait，任务在后台运行中")
        logger.info("      使用以下命令查询进度:")
        logger.info("      kg-engine generate '%s' --wait", args.topic)
        logger.info("  或在 Agent 中调用 kg_check_status(task_id='%s')", task_id)
        _banner("任务已启动（后台运行）")
        return 0

    # --wait 模式：轮询进度直到完成
    logger.info("[3/4] 进入轮询模式，实时显示进度 …")
    last_stage = ""
    while True:
        p = await pipeline.status(task_id)
        if p is None:
            logger.error("任务丢失！task_id=%s 在注册表中不存在", task_id)
            return 1

        # 阶段变化时打印一条日志
        if p.stage != last_stage:
            stage_names = {
                "init": "初始化",
                "intent": "阶段1 — 意图解析 (intent parsing)",
                "hot-keywords": "阶段1.5 — 热词收集 (hot-keyword extraction)",
                "graph-synth": "阶段2 — 图谱合成 (graph synthesis)",
                "persist": "持久化 (persisting to disk)",
                "done": "完成",
                "error": "出错",
            }
            stage_label = stage_names.get(p.stage, p.stage)
            logger.info("  ▶ %-7s %3.0f%%  %s", p.stage, p.progress * 100, stage_label)
            last_stage = p.stage

        # 也打印到 stdout 供脚本解析
        print(f"[{p.progress:.0%}] {p.stage}", flush=True)

        if p.finished_at is not None:
            logger.info("─" * 60)
            if p.error:
                logger.error("[4/4] 任务失败: %s", p.error)
                logger.error("  task_id  = %s", task_id)
                logger.error("  domain   = %s", p.domain)
                logger.error("  started  = %s", p.started_at.isoformat())
                logger.error("  finished = %s", p.finished_at.isoformat())
                _banner("生成失败")
                return 1

            g = p.result
            if g:
                logger.info("[4/4] 任务完成！")
                logger.info("  ✓ 领域   : %s", g.domain)
                logger.info("  ✓ 节点数 : %d", len(g.nodes))
                logger.info("  ✓ 方向摘要: %s", (g.direction.summary or "(无)"))
                logger.info("  ✓ 生成时间: %s", g.generated_at or "(未记录)")
                logger.info("  ✓ 耗时   : %s", _elapsed(p.started_at, p.finished_at))
                print(f"saved {len(g.nodes)} nodes for {g.domain!r}", flush=True)
            _banner("生成完成")
            return 0

        await asyncio.sleep(0.5)


def _elapsed(start: datetime, end: datetime) -> str:
    """计算并格式化耗时。"""
    delta = end - start
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total}s"
    minutes, seconds = divmod(total, 60)
    return f"{minutes}m{seconds}s"


# ------------------------------------------------------------------
# validate
# ------------------------------------------------------------------


def _validate(args) -> int:
    from src.agent.dependencies import get_graph_service, reset_dependencies

    _banner("图谱校验 + 质量评分")
    logger.info("  领域: %s", args.domain)
    logger.info("─" * 60)

    reset_dependencies()
    logger.info("[1/3] 初始化服务依赖 …")
    svc = get_graph_service()
    logger.info("      ✓ GraphService 就绪")

    logger.info("[2/3] 执行校验 + 评分 …")

    async def go():
        return await svc.validate(args.domain), await svc.score(args.domain)

    issues, score = asyncio.run(go())

    logger.info("      ✓ 校验完成，发现 %d 个问题", len(issues))
    logger.info("[3/3] 结果:")
    if issues:
        for i, issue in enumerate(issues, 1):
            logger.warning("  ⚠ 问题 %d: %s", i, issue)
    else:
        logger.info("  ✓ 未发现问题")
    logger.info("  质量评分: %s", score.model_dump())
    logger.info("  质量等级: %s", score.level)

    # stdout 保留机器可读输出
    print(f"issues: {issues}")
    print(f"score: {score.model_dump()}")
    print(f"level: {score.level}")

    _banner("校验完成" if not issues else "校验完成（有问题）")
    return 0 if not issues else 1


# ------------------------------------------------------------------
# report
# ------------------------------------------------------------------


def _report(args) -> int:
    from src.agent.dependencies import get_graph_service, reset_dependencies

    _banner("图谱质量报告")
    logger.info("  领域: %s", args.domain)
    logger.info("─" * 60)

    reset_dependencies()
    logger.info("[1/3] 初始化服务依赖 …")
    svc = get_graph_service()
    logger.info("      ✓ GraphService 就绪")

    logger.info("[2/3] 读取图谱 + 评分 …")

    async def go():
        return await svc.view(args.domain), await svc.score(args.domain)

    g, score = asyncio.run(go())

    logger.info("      ✓ 图谱已读取，共 %d 个节点", len(g.nodes))
    logger.info("[3/3] 报告:")
    logger.info("  领域    : %s", g.domain)
    logger.info("  节点数  : %d", len(g.nodes))
    logger.info("  方向摘要: %s", g.direction.summary or "(无)")
    logger.info("  生成时间: %s", g.generated_at or "(未记录)")
    logger.info("  质量评分: %s", score.model_dump())
    logger.info("  质量等级: %s", score.level)

    if g.nodes:
        logger.info("─" * 60)
        logger.info("  节点列表:")
        for i, node in enumerate(g.nodes, 1):
            links_str = ", ".join(node.links) if node.links else "(无)"
            logger.info("  %2d. %-20s → %s", i, node.name, links_str)

    # stdout 保留机器可读输出
    print(f"domain: {g.domain}")
    print(f"nodes: {len(g.nodes)}")
    print(f"score: {score.model_dump()}")
    print(f"level: {score.level}")

    _banner("报告完成")
    return 0


# ------------------------------------------------------------------
# cards
# ------------------------------------------------------------------


def _cards(args) -> int:
    import json

    from src.agent.dependencies import get_card_service, reset_dependencies

    reset_dependencies()
    svc = get_card_service()

    _banner("提示词卡片管理")
    logger.info("  子命令: %s", args.cards_cmd)
    logger.info("─" * 60)

    if args.cards_cmd == "list":
        cards_list = asyncio.run(svc.list_cards())
        logger.info("  共 %d 张卡片", len(cards_list))
        if cards_list:
            logger.info("─" * 60)
            for i, c in enumerate(cards_list, 1):
                triggers = ", ".join(c["triggers"]) or "(无)"
                tools = ", ".join(c["applies_to_tools"]) or "(无)"
                logger.info(
                    "  %2d. [%-12s] prio=%-3d  triggers=[%s]  tools=[%s]  (%d 字)",
                    i,
                    c["id"],
                    c["priority"],
                    triggers,
                    tools,
                    c["body_chars"],
                )
        # stdout 机器可读
        print(json.dumps({"cards": cards_list, "count": len(cards_list)}, ensure_ascii=False))
        _banner("列表完成")
        return 0

    if args.cards_cmd == "add":
        triggers = [t.strip() for t in args.triggers.split(",") if t.strip()]
        tools = [t.strip() for t in args.tools.split(",") if t.strip()]
        result = asyncio.run(
            svc.upsert(
                card_id=args.id,
                title=args.title,
                body=args.body,
                triggers=triggers,
                applies_to_tools=tools,
                priority=args.priority,
            )
        )
        verb = "新建" if result["created"] else "更新"
        logger.info("  ✓ %s卡片 '%s'", verb, result["title"])
        logger.info("    id        : %s", result["id"])
        logger.info("    triggers  : %s", result["triggers"])
        logger.info("    tools     : %s", result["applies_to_tools"])
        logger.info("    priority  : %s", result["priority"])
        logger.info("    body_chars: %s", result["body_chars"])
        logger.info("    active    : %s 张", result["active_count"])
        print(json.dumps(result, ensure_ascii=False))
        _banner(f"{verb}完成")
        return 0

    if args.cards_cmd == "delete":
        result = asyncio.run(svc.delete(args.id))
        if not result["deleted"]:
            logger.warning("  ❌ 卡片 '%s' 不存在", args.id)
            print(json.dumps(result, ensure_ascii=False))
            _banner("删除失败")
            return 1
        logger.info("  ✓ 已删除卡片 '%s'", args.id)
        logger.info("    active: %s 张", result["active_count"])
        print(json.dumps(result, ensure_ascii=False))
        _banner("删除完成")
        return 0

    if args.cards_cmd == "view":
        card = asyncio.run(svc.get_card(args.id))
        if card is None:
            logger.warning("  ❌ 卡片 '%s' 不存在", args.id)
            print(json.dumps({"id": args.id, "found": False}, ensure_ascii=False))
            _banner("查看失败")
            return 1
        logger.info("  id        : %s", card["id"])
        logger.info("  title     : %s", card["title"])
        logger.info("  triggers  : %s", card["triggers"])
        logger.info("  tools     : %s", card["applies_to_tools"])
        logger.info("  priority  : %s", card["priority"])
        logger.info("─" * 60)
        logger.info("  正文:")
        print(card["body"])
        print()
        print(json.dumps(card, ensure_ascii=False))
        _banner("查看完成")
        return 0

    return 1


# ------------------------------------------------------------------
# associations
# ------------------------------------------------------------------


def _associations(args) -> int:
    """associations.json 子命令：sync / sync-node / extract / stats / clear。"""
    import json

    from src.agent.dependencies import (
        get_graph_repo,
        get_note_repo,
        get_resource_repo,
        get_plan_repo,
        get_association_repo,
        get_association_service,
        reset_dependencies,
    )
    from src.application.graph_sync_orchestrator import GraphSyncOrchestrator
    from src.api.routes._buffer_singleton import get_or_create_buffer

    reset_dependencies()

    domain = args.domain
    _banner("关联图谱派生管理")
    logger.info("  领域       : %s", domain)
    logger.info("  子命令     : %s", args.assoc_cmd)
    logger.info("─" * 60)

    graph_repo = get_graph_repo()
    note_repo = get_note_repo()
    resource_repo = get_resource_repo()
    plan_repo = get_plan_repo()
    assoc_repo = get_association_repo()
    assoc_svc = get_association_service()

    async def _build_orch():
        buf = await get_or_create_buffer(domain)
        return GraphSyncOrchestrator(
            domain,
            graph_repo=graph_repo,
            note_repo=note_repo,
            resource_repo=resource_repo,
            plan_repo=plan_repo,
            assoc_repo=assoc_repo,
            buffer=buf,
        )

    if args.assoc_cmd == "sync":
        async def go():
            orch = await _build_orch()
            return await orch.sync_full()

        stats = asyncio.run(go())
        logger.info("  ✓ 全量派生完成")
        logger.info("    concepts    : %d", stats["concepts"])
        logger.info("    resources   : %d", stats["resources"])
        logger.info("    associations: %d", stats["associations"])
        print(json.dumps(stats, ensure_ascii=False))
        _banner("派生完成")
        return 0

    if args.assoc_cmd == "sync-node":
        async def go():
            orch = await _build_orch()
            return await orch.sync_for_node(args.node, enqueue_llm=True)

        stats = asyncio.run(go())
        logger.info("  ✓ 节点「%s」派生完成", args.node)
        print(json.dumps(stats, ensure_ascii=False))
        _banner("派生完成")
        return 0

    if args.assoc_cmd == "extract":
        from src.api.routes._buffer_singleton import get_buffer_for_domain

        buf = get_buffer_for_domain(domain)
        if buf is None:
            logger.error("  ❌ buffer 未初始化（domain=%s）", domain)
            return 1
        result = asyncio.run(buf.force_flush())
        logger.info("  ✓ LLM 抽取完成")
        print(json.dumps(result, ensure_ascii=False))
        _banner("抽取完成")
        return 0

    if args.assoc_cmd == "stats":
        async def go():
            return await assoc_repo.read(domain)
        g = asyncio.run(go())
        stats = g.statistics()
        logger.info("  concepts    : %d", stats["concepts"])
        logger.info("  resources   : %d", stats["resources"])
        logger.info("  associations: %d", stats["associations"])
        logger.info("  derived     : %d events", stats["derived_events"])
        print(json.dumps(stats, ensure_ascii=False))
        _banner("统计完成")
        return 0

    if args.assoc_cmd == "clear":
        asyncio.run(assoc_repo.clear(domain))
        logger.info("  ✓ associations.json 已删除")
        _banner("清空完成")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]
