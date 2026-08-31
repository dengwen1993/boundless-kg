"""Graph generation pipeline.

Stages:
  1. Intent parsing — three concurrent LLM samples, majority vote.
  2. Hot-keyword collection — web search + LLM extraction / fallback.
  3. Hierarchical expansion — LLM produces a 3-level tree of
     sub-topics, sub-sub-topics, and leaf topics (target: 200+ nodes).
  4. Graph synthesis — assembles the Graph model with cross-links.
  5. Persist — atomic write to ``knowledge_graph.json``.

Long-running; progress is published to an in-memory task tracker that
the API surface can poll.

Logging: every stage transition prints a timestamped line so the user
can see progress in real time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.application.graph_service import GraphService
from src.domain.graph.models import Direction, Graph, Node
from src.domain.protocols import SearchClientProtocol, SearchResult
from src.domain.hot_keyword import build_queries, extract_keywords_async
from src.domain.hot_keyword.dedup import jaccard
from src.domain.intent import IntentParser, parse_intent_async
from src.infrastructure.build_log import BuildLogger
from src.infrastructure.llm import AsyncLLMClient, MockLLMClient
from src.infrastructure.pipeline_state_store import PipelineStateStore
from src.infrastructure.repository.graph_repo import GraphRepository

logger = logging.getLogger("kg_engine.pipeline")


@dataclass
class PipelineProgress:
    task_id: str
    domain: str
    stage: str = "init"
    progress: float = 0.0
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    error: str | None = None
    result: Graph | None = None
    # 诊断信息：每个阶段的时间戳和耗时
    stage_history: list[dict[str, Any]] = field(default_factory=list)
    # 当前阶段的开始时间，用于精确计算 stage_elapsed_sec
    stage_started_at: datetime | None = None


class _TaskRegistry:
    """In-memory progress tracker. Replace with Redis/SQLite in production."""

    def __init__(self) -> None:
        self._tasks: dict[str, PipelineProgress] = {}

    def add(self, p: PipelineProgress) -> None:
        self._tasks[p.task_id] = p

    def get(self, task_id: str) -> PipelineProgress | None:
        return self._tasks.get(task_id)

    def update(self, task_id: str, **kw: Any) -> None:
        p = self._tasks.get(task_id)
        if p is None:
            return
        for k, v in kw.items():
            setattr(p, k, v)


class GenerationPipeline:
    """Five-stage graph generator with progress tracking."""

    # 搜索阶段的全局超时（秒）。DualSearchClient 中 DDG 超时 8s + mmx 兜底 12s，
    # 所以 20s 足够覆盖 "DDG 失败 → mmx 回退" 的完整链路。
    SEARCH_TIMEOUT_SEC: int = 20
    # 搜索查询数量上限
    MAX_SEARCH_QUERIES: int = 5
    # 每条搜索结果的条数
    SEARCH_RESULTS_PER_QUERY: int = 5

    # ---- Hierarchical expansion targets ----
    # 目标节点总数。流水线会把图谱扩张到接近这个数字。
    TARGET_NODE_COUNT: int = 220
    # 一级子主题数（root → L1）
    TARGET_L1_COUNT: int = 12
    # 每个 L1 下的二级子主题数（L1 → L2）
    TARGET_L2_PER_L1: int = 8
    # 每个 L2 下的三级叶子节点数（L2 → L3 leaf）；设 0 表示不展开第三层
    TARGET_L3_PER_L2: int = 2

    def __init__(
        self,
        llm: AsyncLLMClient,
        graph_service: GraphService,
        search_client: SearchClientProtocol | None = None,
        registry: _TaskRegistry | None = None,
        build_logger: BuildLogger | None = None,
        state_store: PipelineStateStore | None = None,
    ) -> None:
        self._llm = llm
        self._graph_service = graph_service
        self._search = search_client
        self._registry = registry or _TaskRegistry()
        self._build_log = build_logger
        self._state_store = state_store

    async def start(self, topic: str, direction_hint: str = "") -> str:
        """Kick off the pipeline as a background task; return the task_id."""
        task_id = str(uuid.uuid4())
        progress = PipelineProgress(task_id=task_id, domain=topic)
        self._registry.add(progress)
        if self._state_store:
            self._state_store.add(task_id, topic)
        logger.info("[pipeline] 启动任务 task_id=%s topic=%r hint=%r", task_id, topic, direction_hint)
        asyncio.create_task(self._run(progress, topic, direction_hint))
        return task_id

    async def _run(self, p: PipelineProgress, topic: str, direction_hint: str) -> None:
        try:
            # ---- Stage 1: Intent parsing (0% → 10%) ----
            self._set_stage(p, "intent", 0.1)
            logger.info("[pipeline:%s] 阶段1/5 — 意图解析（LLM 3 次采样投票）", p.task_id[:8])
            await self._blog(p.domain, "intent", f"阶段1/5 开始 — 意图解析（task_id={p.task_id[:8]}）")
            intent = await parse_intent_async(topic, direction_hint, self._llm)
            logger.info(
                "[pipeline:%s] 意图解析完成: graph_type=%s summary=%s",
                p.task_id[:8],
                intent.graph_type,
                intent.summary[:60],
            )
            await self._blog(
                p.domain, "intent",
                f"意图解析完成: graph_type={intent.graph_type}, summary={intent.summary[:60]}",
            )

            # ---- Stage 2: Hot-keyword collection (10% → 30%) ----
            self._set_stage(p, "hot-keywords", 0.25)
            logger.info("[pipeline:%s] 阶段2/5 — 热点关键词采集（搜索 + LLM 提取）", p.task_id[:8])
            await self._blog(p.domain, "hot-keywords", "阶段2/5 开始 — 热点关键词采集")
            keywords = await self._collect_hot_keywords(p, intent)
            logger.info(
                "[pipeline:%s] 关键词采集完成: %d 个关键词",
                p.task_id[:8],
                len(keywords),
            )
            await self._blog(p.domain, "hot-keywords", f"关键词采集完成: {len(keywords)} 个关键词")

            # ---- Stage 3: Hierarchical expansion (30% → 65%) ----
            self._set_stage(p, "expand", 0.35)
            logger.info(
                "[pipeline:%s] 阶段3/5 — 多层子树扩展（目标 ~%d 节点）",
                p.task_id[:8],
                self.TARGET_NODE_COUNT,
            )
            await self._blog(
                p.domain, "expand",
                f"阶段3/5 开始 — 多层子树扩展（目标 ~{self.TARGET_NODE_COUNT} 节点）",
            )
            subtree = await self._expand_to_hierarchy(intent, keywords)
            logger.info(
                "[pipeline:%s] 多层子树扩展完成: L1=%d, L2+~%d",
                p.task_id[:8],
                len(subtree.get("l1", [])),
                sum(len(x.get("children", [])) for x in subtree.get("l1", [])),
            )
            await self._blog(
                p.domain, "expand",
                f"多层子树扩展完成: L1={len(subtree.get('l1', []))}, "
                f"L2+~{sum(len(x.get('children', [])) for x in subtree.get('l1', []))}",
            )

            # ---- Stage 4: Graph synthesis (65% → 90%) ----
            self._set_stage(p, "graph-synth", 0.75)
            logger.info("[pipeline:%s] 阶段4/5 — 图谱合成", p.task_id[:8])
            await self._blog(p.domain, "graph-synth", "阶段4/5 开始 — 图谱合成")
            graph = await self._synthesise_graph(intent, keywords, subtree)
            logger.info(
                "[pipeline:%s] 图谱合成完成: %d 个节点",
                p.task_id[:8],
                len(graph.nodes),
            )
            await self._blog(
                p.domain, "graph-synth",
                f"图谱合成完成: {len(graph.nodes)} 个节点",
            )

            # ---- Stage 5: Persist (90% → 100%) ----
            self._set_stage(p, "persist", 0.95)
            logger.info("[pipeline:%s] 阶段5/5 — 持久化到 knowledge_graph.json", p.task_id[:8])
            await self._blog(p.domain, "persist", "阶段5/5 开始 — 持久化")
            await self._graph_service.save_graph(graph)
            logger.info(
                "[pipeline:%s] 持久化完成: domain=%r",
                p.task_id[:8],
                graph.domain,
            )
            await self._blog(
                p.domain, "persist",
                f"持久化完成: domain={graph.domain!r}, {len(graph.nodes)} 个节点",
            )

            self._set_stage(
                p,
                "done",
                1.0,
                finished_at=datetime.utcnow(),
                result=graph,
            )
            logger.info("[pipeline:%s] ✅ 全部完成", p.task_id[:8])
            await self._blog(p.domain, "done", "✅ 图谱生成全部完成")

        except Exception as e:
            logger.exception("[pipeline:%s] ❌ 流水线失败: %s", p.task_id[:8], e)
            await self._blog(p.domain, "error", f"❌ 流水线失败: {e}", level="ERROR")
            self._set_stage(
                p,
                "error",
                error=str(e),
                finished_at=datetime.utcnow(),
            )

    def _set_stage(self, p: PipelineProgress, stage: str, progress: float = 0.0, **kw: Any) -> None:
        """Update stage + progress + record history entry."""
        now = datetime.utcnow()
        p.stage_history.append({
            "stage": stage,
            "progress": progress,
            "timestamp": now.isoformat(),
        })
        self._registry.update(
            p.task_id,
            stage=stage,
            progress=progress,
            stage_history=p.stage_history,
            stage_started_at=now,
            **kw,
        )
        if self._state_store:
            self._state_store.update(
                p.task_id,
                stage=stage,
                progress=progress,
                stage_started_at=now,
                **kw,
            )

    def _update_progress(self, p: PipelineProgress, progress: float) -> None:
        """Update progress within the current stage without a history entry.

        Used for fine-grained progress during long-running stages
        (e.g. per-search-query updates in hot-keyword collection).
        """
        self._registry.update(p.task_id, progress=progress)
        if self._state_store:
            self._state_store.update(p.task_id, progress=progress)

    async def status(self, task_id: str) -> PipelineProgress | None:
        """Return task progress.

        Checks the in-memory registry first (active tasks with full
        state including ``result`` and ``stage_history``).  If not
        found, falls back to the persistent store so that
        ``kg_check_status`` still returns after a backend restart.
        """
        p = self._registry.get(task_id)
        if p is not None:
            return p
        if self._state_store:
            entry = self._state_store.get(task_id)
            if entry is not None:
                # Reconstruct a PipelineProgress from the persisted dict.
                # ``result`` and ``stage_history`` are not persisted —
                # they're only useful during the active run.
                return PipelineProgress(
                    task_id=entry.get("task_id", task_id),
                    domain=entry.get("domain", ""),
                    stage=entry.get("stage", "unknown"),
                    progress=entry.get("progress", 0.0),
                    started_at=_parse_dt(entry.get("started_at")),
                    finished_at=_parse_dt(entry.get("finished_at")),
                    error=entry.get("error"),
                    stage_started_at=_parse_dt(entry.get("stage_started_at")),
                )
        return None

    async def _blog(
        self,
        domain: str,
        stage: str,
        message: str,
        level: str = "INFO",
    ) -> None:
        """Write a domain-level build.log entry (no-op when logger is absent)."""
        if self._build_log is None:
            return
        try:
            await self._build_log.log_domain(domain, stage, message, level=level)
        except Exception:
            logger.debug("[pipeline] build.log 写入失败（已忽略）", exc_info=True)

    async def _collect_hot_keywords(self, p: PipelineProgress, intent) -> list[dict[str, Any]]:
        """Collect hot keywords via web search + LLM extraction.

        Has a global timeout (SEARCH_TIMEOUT_SEC). If search fails or
        times out, falls back to LLM-only keyword generation.
        """
        queries = build_queries(intent, max_queries=self.MAX_SEARCH_QUERIES)
        if not self._search or not queries:
            logger.warning(
                "[pipeline:%s] 搜索客户端不可用或查询为空，跳过搜索阶段",
                p.task_id[:8],
            )
            return await self._llm_fallback_keywords(intent)

        logger.info(
            "[pipeline:%s] 生成 %d 条搜索查询，并发上限 5",
            p.task_id[:8],
            len(queries),
        )

        sem = asyncio.Semaphore(5)
        search_start = datetime.utcnow()
        total_queries = len(queries)
        completed = 0

        async def search_one(q: str) -> list[SearchResult]:
            nonlocal completed
            async with sem:
                try:
                    res = await self._search.search(q, num_results=self.SEARCH_RESULTS_PER_QUERY)
                    return res
                except Exception as e:
                    logger.debug(
                        "[pipeline:%s] 搜索查询失败 (将被忽略): %r → %s",
                        p.task_id[:8],
                        q[:40],
                        e,
                    )
                    return []
                finally:
                    completed += 1
                    # 动态进度：10% → 30% 按搜索完成比例推进
                    frac = completed / total_queries if total_queries else 1.0
                    self._update_progress(p, 0.10 + frac * 0.20)

        try:
            # 全局超时保护：超过 SEARCH_TIMEOUT_SEC 秒就放弃搜索
            all_results_nested = await asyncio.wait_for(
                asyncio.gather(*(search_one(q) for q in queries)),
                timeout=self.SEARCH_TIMEOUT_SEC,
            )
            flat = [r for batch in all_results_nested for r in batch]
            elapsed = (datetime.utcnow() - search_start).total_seconds()
            logger.info(
                "[pipeline:%s] 搜索完成: %d 条查询 → %d 条结果 (%.1fs)",
                p.task_id[:8],
                len(queries),
                len(flat),
                elapsed,
            )
        except asyncio.TimeoutError:
            elapsed = (datetime.utcnow() - search_start).total_seconds()
            logger.warning(
                "[pipeline:%s] 搜索全局超时 (%.0fs)，改用 LLM 直接生成关键词",
                p.task_id[:8],
                elapsed,
            )
            return await self._llm_fallback_keywords(intent)

        if not flat:
            logger.warning(
                "[pipeline:%s] 搜索结果为空，改用 LLM 直接生成关键词",
                p.task_id[:8],
            )
            return await self._llm_fallback_keywords(intent)

        # LLM 关键词提取
        self._update_progress(p, 0.28)
        logger.info(
            "[pipeline:%s] 调用 LLM 从 %d 条搜索结果中提取关键词...",
            p.task_id[:8],
            len(flat),
        )
        try:
            keywords = await extract_keywords_async(
                intent.topic, flat, self._llm, top_k=20,
            )
            return keywords
        except Exception as e:
            logger.warning(
                "[pipeline:%s] LLM 关键词提取失败 (%s)，改用 LLM 直接生成",
                p.task_id[:8],
                e,
            )
            return await self._llm_fallback_keywords(intent)

    async def _llm_fallback_keywords(self, intent) -> list[dict[str, Any]]:
        """When web search is unavailable, ask the LLM to propose keywords directly."""
        logger.info("[pipeline] 使用 LLM 直接生成关键词（搜索不可用时的降级方案）")
        prompt = (
            f"你是知识图谱规划助手。针对主题「{intent.topic}」，"
            f"请给出 8-15 个最核心的子主题关键词。\n"
            f"方向提示：{intent.direction_hint or '（无）'}\n"
            f"摘要：{intent.summary or '（无）'}\n\n"
            f"只返回 JSON 数组：[{{\"keyword\": \"...\", \"score\": 0.9, \"evidence\": \"核心概念\"}}, ...]"
        )
        try:
            result = await self._llm.chat(
                "你是知识图谱规划助手，只返回 JSON。",
                prompt,
                temperature=0.5,
                max_tokens=1500,
                json_mode=True,
            )
            from src.utils.json_repair import try_parse_json_with_llm

            data = await try_parse_json_with_llm(
                self._llm, result,
                schema_hint=(
                    "a JSON array of keyword items: "
                    "[{\"keyword\": str, \"score\": float, \"evidence\": str}]"
                ),
                max_tokens=2000,
            )
            if data is None:
                logger.warning(
                    "[pipeline] LLM 降级关键词生成：JSON 解析失败（含 LLM 修复），原始结果: %s",
                    result[:200],
                )
                return []
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "keywords" in data:
                return list(data["keywords"])
            return []
        except Exception as e:
            logger.warning("[pipeline] LLM 降级关键词生成也失败 (%s): %s", type(e).__name__, e)
            return []

    async def _expand_to_hierarchy(
        self,
        intent,
        keywords: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Use the LLM to expand the topic into a 2-3 level hierarchy.

        Returns a dict shaped as::

            {
                "l1": [
                    {
                        "name": "...",
                        "children": [
                            {"name": "...", "children": [{"name": "..."}, ...]},
                            ...
                        ],
                    },
                    ...
                ],
                "summary": "short description of the hierarchy shape",
            }

        On any failure (LLM error / JSON parse / structure mismatch),
        falls back to a flat keyword-driven hierarchy so downstream
        synthesis still runs.
        """
        kw_hint = ", ".join(
            k["keyword"] for k in (keywords or [])[: self.TARGET_L1_COUNT]
        ) or "（无关键词，基于主题发散）"

        prompt = (
            f"你是知识图谱架构师，请把「{intent.topic}」扩展为多层结构。\n"
            f"主题方向提示：{intent.direction_hint or '（无）'}\n"
            f"主题摘要：{intent.summary or '（无）'}\n"
            f"已有关键词（可参考选用）：{kw_hint}\n\n"
            f"要求：\n"
            f"1) 一级子主题 ~{self.TARGET_L1_COUNT} 个，覆盖该主题的主要大类\n"
            f"2) 每个一级下给出 ~{self.TARGET_L2_PER_L1} 个二级子主题（二级是可直接学习掌握的知识点）\n"
            f"3) 每个二级再给出 ~{self.TARGET_L3_PER_L2} 个三级叶子节点（具体概念/工具/案例/题库等）\n"
            f"4) 节点名称用 2-8 字中文短语，不带括号、不带解释\n"
            f"5) 避免生成含义相同或高度相似的节点 ——「{intent.topic}」和「{intent.topic}概述」、"
            f"「模型量化」和「模型量化技术」、「深度学习」和「深度学习方法」被视为同一个节点，"
            f"整张图中只出现一次，保留更具体的名字\n"
            f"6) 尽量贴近中国学习者常用术语\n\n"
            f"示例 — 好的输出（一个节点只出现一次）：\n"
            "{\n"
            '  "summary": "AI 应用开发学习路径",\n'
            '  "l1": [\n'
            '    {"name": "AI应用开发", "children": [\n'
            '      {"name": "模型微调", "children": [\n'
            '        {"name": "模型合并与部署", "children": [{"name": "模型量化技术"}]},\n'
            '        {"name": "推理优化", "children": []}\n'
            '      ]}\n'
            '    ]},\n'
            '    {"name": "AI基础设施", "children": [{"name": "GPU集群管理"}]}\n'
            '  ]\n'
            "}\n\n"
            f"避免出现（含义相同却分散在不同分支）：\n"
            '{\n'
            '  "l1": [\n'
            '    {"name": "AI应用开发", "children": [\n'
            '      {"name": "模型微调", "children": [\n'
            '        {"name": "模型合并与部署", "children": [{"name": "模型量化"}]}\n'
            '      ]}\n'
            '    ]},\n'
            '    {"name": "AI基础设施与部署", "children": [{"name": "模型量化技术"}]}\n'
            '  ]\n'
            "}\n\n"
            f"只返回如下结构的 JSON，不要其它任何文字：\n"
            "{\n"
            '  "summary": "本图谱覆盖范围的简短说明",\n'
            '  "l1": [\n'
            '    {"name": "一级主题1", "children": [\n'
            '      {"name": "二级主题A", "children": [{"name": "三级叶子a1"}, {"name": "三级叶子a2"}]},\n'
            '      {"name": "二级主题B", "children": [{"name": "三级叶子b1"}]}\n'
            '    ]},\n'
            '    {"name": "一级主题2", "children": []}\n'
            '  ]\n'
            "}"
        )

        try:
            raw = await self._llm.chat(
                "你是知识图谱架构师，只返回严格 JSON。",
                prompt,
                temperature=0.6,
                max_tokens=8000,
                json_mode=True,
            )
        except Exception as e:
            logger.warning("[pipeline] 多层扩展 LLM 调用失败 (%s)，回退关键词扁平结构", e)
            return self._fallback_hierarchy(intent, keywords)

        from src.utils.json_repair import try_parse_json_with_llm

        data = await try_parse_json_with_llm(
            self._llm, raw,
            schema_hint=(
                "a hierarchy JSON: "
                "{\"l1\": [{\"name\": str, \"children\": [{\"name\": str, "
                "\"keywords\": [str]}]}], \"summary\": str}"
            ),
            max_tokens=6000,
        )
        if not isinstance(data, dict):
            logger.warning("[pipeline] 多层扩展 JSON 解析失败（含 LLM 修复），原始: %s", (raw or "")[:200])
            return self._fallback_hierarchy(intent, keywords)

        l1 = data.get("l1")
        if not isinstance(l1, list) or not l1:
            logger.warning("[pipeline] 多层扩展结果缺少 l1 列表，回退关键词扁平结构")
            return self._fallback_hierarchy(intent, keywords)

        cleaned = self._normalise_subtree({"l1": l1, "summary": data.get("summary", "")}, intent.topic)
        if not cleaned["l1"]:
            logger.warning("[pipeline] 多层扩展清洗后无有效节点，回退关键词扁平结构")
            return self._fallback_hierarchy(intent, keywords)
        return cleaned

    def _normalise_subtree(self, data: dict[str, Any], topic: str) -> dict[str, Any]:
        """Sanitise the LLM-returned tree:

        * strip whitespace, reject empty / topic-name collisions
        * enforce the depth config limits (drop excessive children)
        * dedupe by Jaccard similarity within each sibling group
        """
        l1 = data.get("l1") or []
        if not isinstance(l1, list):
            return {"l1": [], "summary": data.get("summary", "")}

        l1_clean: list[dict[str, Any]] = []
        l1_seen: list[str] = []
        for entry in l1[: self.TARGET_L1_COUNT]:
            if not isinstance(entry, dict):
                continue
            name = (entry.get("name") or "").strip()
            if not name or name == topic:
                continue
            if any(jaccard(name, seen) >= 0.85 for seen in l1_seen):
                continue
            l1_seen.append(name)
            l2_list = entry.get("children") or []
            if not isinstance(l2_list, list):
                l2_list = []

            l2_clean: list[dict[str, Any]] = []
            l2_seen: list[str] = []
            for child in l2_list[: self.TARGET_L2_PER_L1]:
                if not isinstance(child, dict):
                    continue
                l2_name = (child.get("name") or "").strip()
                if not l2_name or l2_name == topic or l2_name == name:
                    continue
                if any(jaccard(l2_name, seen) >= 0.85 for seen in l2_seen):
                    continue
                l2_seen.append(l2_name)
                l3_list = child.get("children") or []
                if not isinstance(l3_list, list):
                    l3_list = []

                l3_clean: list[dict[str, Any]] = []
                l3_seen: list[str] = []
                for leaf in l3_list[: max(1, self.TARGET_L3_PER_L2)]:
                    if not isinstance(leaf, dict):
                        continue
                    l3_name = (leaf.get("name") or "").strip()
                    if not l3_name:
                        continue
                    if l3_name == topic or l3_name == name or l3_name == l2_name:
                        continue
                    if any(jaccard(l3_name, seen) >= 0.85 for seen in l3_seen):
                        continue
                    l3_seen.append(l3_name)
                    l3_clean.append({"name": l3_name})

                l2_clean.append({"name": l2_name, "children": l3_clean})

            l1_clean.append({"name": name, "children": l2_clean})

        # 至少要有一个 L1；否则视为失败
        if not l1_clean:
            return {"l1": [], "summary": data.get("summary", "")}
        return {"l1": l1_clean, "summary": data.get("summary", "")}

    def _fallback_hierarchy(
        self,
        intent,
        keywords: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a flat hierarchy from the keyword list when LLM expansion fails.

        Each keyword becomes an L1; the rest of the keyword list is scattered
        as L2 leaves under the first ``TARGET_L1_COUNT`` keywords.
        """
        topic = intent.topic
        l1: list[dict[str, Any]] = []
        if not keywords:
            return {"l1": [{"name": f"{topic}入门", "children": []}], "summary": "fallback"}
        # 去重并按出现顺序保留
        seen: set[str] = set()
        ordered: list[str] = []
        for k in keywords:
            kw = (k.get("keyword") or "").strip() if isinstance(k, dict) else ""
            if not kw or kw == topic or kw in seen:
                continue
            seen.add(kw)
            ordered.append(kw)

        l1_names = ordered[: self.TARGET_L1_COUNT] or [f"{topic}基础"]
        remainder = ordered[self.TARGET_L1_COUNT:]
        per_l1 = max(1, self.TARGET_L2_PER_L1)
        for i, n in enumerate(l1_names):
            chunk = remainder[i * per_l1 : (i + 1) * per_l1]
            l1.append({
                "name": n,
                "children": [{"name": c, "children": []} for c in chunk],
            })
        return {"l1": l1, "summary": "fallback"}

    async def _synthesise_graph(
        self,
        intent,
        keywords: list[dict[str, Any]],
        subtree: dict[str, Any] | None = None,
    ) -> Graph:
        """Build the Graph from intent + hierarchical subtree.

        Forward-only tree: each node's ``links`` field contains ONLY its
        children (never the parent, never siblings). Hierarchy levels are
        computed at read-time by ``_compute_levels`` (BFS over incoming
        edges). This matches the verified example structure where the
        root's children form a tree that can be navigated without
        reverse-link pollution.
        """
        direction = Direction(
            angle=intent.get("angle").value if intent.get("angle") else "",
            audience=intent.get("audience").value if intent.get("audience") else "",
            depth=intent.get("depth").value if intent.get("depth") else "",
            summary=intent.summary,
        )

        topic = (intent.topic or "").strip() or "未命名领域"
        node_by_name: dict[str, Node] = {}

        def _ensure_node(name: str) -> Node:
            """Create or fetch a node with ``links=[]`` (forward-only)."""
            node = node_by_name.get(name)
            if node is None:
                node = Node(name=name, links=[])
                node_by_name[name] = node
            return node

        def _add_child_edge(parent_name: str, child_name: str) -> None:
            """Register parent→child forward edge (deduped)."""
            if not parent_name or parent_name == child_name:
                return
            parent = _ensure_node(parent_name)
            if child_name not in parent.links:
                parent.links.append(child_name)

        # Always include the root node (the topic itself).
        _ensure_node(topic)

        l1_list = (subtree or {}).get("l1") or []

        for l1_entry in l1_list:
            if not isinstance(l1_entry, dict):
                continue
            l1_name = (l1_entry.get("name") or "").strip()
            if not l1_name or l1_name == topic:
                continue
            _ensure_node(l1_name)
            _add_child_edge(topic, l1_name)

            l2_entries = l1_entry.get("children") or []
            l2_clean: list[str] = []
            for child in l2_entries:
                if not isinstance(child, dict):
                    continue
                l2_name = (child.get("name") or "").strip()
                if not l2_name or l2_name in (topic, l1_name):
                    continue
                if l2_name in l2_clean:
                    continue
                l2_clean.append(l2_name)
                _ensure_node(l2_name)
                _add_child_edge(l1_name, l2_name)

                # L3 leaves directly under each L2 node.
                l3_entries = child.get("children") or []
                for leaf in l3_entries:
                    if not isinstance(leaf, dict):
                        continue
                    l3_name = (leaf.get("name") or "").strip()
                    if not l3_name or l3_name in (topic, l1_name, l2_name):
                        continue
                    _ensure_node(l3_name)
                    _add_child_edge(l2_name, l3_name)

        # Backstop: if the LLM subtree was empty and we have no children,
        # guarantee at least the root plus a flat list of keyword L1s so
        # the graph is never empty.
        root = node_by_name[topic]
        if len(root.links) < 2 and keywords:
            for k in keywords:
                kw = (k.get("keyword") or "").strip() if isinstance(k, dict) else ""
                if not kw or kw == topic or kw in root.links:
                    continue
                _ensure_node(kw)
                _add_child_edge(topic, kw)

        # Stable iteration order: root first, then the order they were inserted.
        ordered: list[Node] = []
        seen: set[str] = set()
        if topic in node_by_name:
            ordered.append(node_by_name[topic])
            seen.add(topic)
        for n in node_by_name.values():
            if n.name not in seen:
                ordered.append(n)
                seen.add(n.name)

        # Sanity: strip empty / self / dup links before returning.
        for n in ordered:
            cleaned: list[str] = []
            seen_links: set[str] = set()
            for ln in n.links:
                if not ln or ln == n.name or ln in seen_links:
                    continue
                seen_links.add(ln)
                cleaned.append(ln)
            n.links = cleaned

        return Graph(
            domain=topic,
            direction=direction,
            nodes=ordered,
            generated_at=datetime.utcnow().isoformat(),
        )


def _parse_dt(iso_str: str | None) -> datetime | None:
    """Parse an ISO-format datetime string; return ``None`` on failure."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None


__all__ = ["GenerationPipeline", "PipelineProgress"]
