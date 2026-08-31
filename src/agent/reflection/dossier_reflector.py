"""DossierReflector — 对话后异步归档(fire-and-forget)。

设计要点:
- Agent 主响应完成后,异步触发 reflector
- 两段式触发:cheap 判定 → 只有 true 才走完整归档
  - Stage 1 (cheap classify):极小 prompt,LLM 输出 `{"should_archive": bool}`
    + 关键词预筛(命中"以后/下次/记住/不要再/踩坑/sop/陷阱"等触发词才进入 LLM)
  - Stage 2 (extract & archive):完整 prompt,提取条目 → 写入 dossier.json
- 失败不影响主流程(异常隔离)
- 归档结果通过 ActivityBus 推给前端
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.application.dossier_service import DossierService
from src.observability.activity_bus import ActivityKind, get_activity_bus

logger = logging.getLogger(__name__)

#: 默认最多看最近 6 条消息(3 轮)
DEFAULT_RECENT_MESSAGES = 6

#: Stage 1 关键词预筛:用户消息里出现这些词才值得让 LLM 判定
_TRIGGER_WORDS = (
    "以后", "下次", "记住", "不要再", "踩坑", "经验", "教训",
    "sop", "SOP", "陷阱", "技巧", "备忘", "重要", "关键",
    "注意", "提醒自己", "复盘", "总结一下", "归纳",
    "remember", "note to self", "gotcha", "watch out",
)
_TRIGGER_RE = re.compile("|".join(re.escape(w) for w in _TRIGGER_WORDS))

#: Stage 1 长度阈值:总字符数低于此直接跳过(连 LLM 都懒得问)
MIN_TOTAL_CHARS = 40


# ─────────────────────────────────────────────────────────────────────
# Stage 1 — cheap classifier:这条对话是否值得归档?
# ─────────────────────────────────────────────────────────────────────

_CLASSIFY_PROMPT = """你是 BoundlessKG 的"经验沉淀前置判定器"。看下面这段对话,
判断是否产生了"以后能复用的经验"(SOP / 陷阱 / 技巧 / 术语 / 设计模式)。

只输出 JSON:
{{"should_archive": true|false, "reason": "一句话说明"}}

判定标准:
- 用户说了具体踩坑、修正、规律、流程 → true
- 用户只是闲聊 / 提问 / 查询结果 / 临时数据 → false
- 短问候("你好"、"hi")、确认("好的")、单纯情绪 → false
- 不确定时倾向 false(避免假归档)"""


def _parse_json_bool(text: str) -> dict[str, Any]:
    """Best-effort parse of {"should_archive": bool, "reason": str}."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or start >= end:
        return {"should_archive": False, "reason": "no-json"}
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"should_archive": False, "reason": "parse-fail"}
    if not isinstance(obj, dict):
        return {"should_archive": False, "reason": "non-dict"}
    val = obj.get("should_archive")
    return {
        "should_archive": bool(val) if isinstance(val, bool) else False,
        "reason": str(obj.get("reason") or ""),
    }


# ─────────────────────────────────────────────────────────────────────
# Stage 2 — extract & archive:完整的条目提取
# ─────────────────────────────────────────────────────────────────────

_REFLECT_PROMPT = """你是 BoundlessKG 的"经验沉淀助手"。请审视下面这段对话,判断是否产生了
值得归档为节点档案的可复用经验。

【对话】
{messages}

【当前领域】
{domain}

【已有档案条目(避免重复,新条目应与这些不重叠)】
{existing_entries}

【你的任务】

输出一个 JSON 数组,每条形如:
{{
  "node": "节点名(必须与知识图谱中已存在的节点完全一致,或空字符串表示无可挂载节点)",
  "type": "sop|pitfall|tip|term|pattern|link|note",
  "title": "一句话标题",
  "body": "正文,支持 markdown,建议 < 500 字",
  "tags": "逗号分隔的关键词,如 asyncio,cancel",
  "evidence": "归档依据(用户原话 / Agent 反思),便于追溯",
  "score": 重要性 0~1
}}

【规则】
- 只归档"以后再做同类事能复用"的经验(SOP / 陷阱 / 技巧 / 术语)
- 不要归档"一次性闲聊"、"纯查询结果"、"临时性数据"
- 没有可复用经验时,返回空数组 []
- 优先识别用户原话中带"以后"、"记住"、"下次"、"不要再"、"踩坑"等触发词的内容
- 修正类内容(Agent 之前说错了用户纠正了)算 pitfall
- 不要复述对话原话,要提炼成可独立阅读的经验

【输出】
只输出 JSON 数组,不要任何解释。"""


def _format_messages(messages: list[Any]) -> str:
    """Render recent messages as plain text for the reflector LLM."""
    lines: list[str] = []
    for m in messages[-DEFAULT_RECENT_MESSAGES:]:
        role = getattr(m, "role", m.get("role") if isinstance(m, dict) else "?")
        content = (
            getattr(m, "content", None)
            or (m.get("content") if isinstance(m, dict) else "")
        )
        if isinstance(content, list):
            # multimodal
            content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if not content:
            continue
        lines.append(f"[{role}] {content[:800]}")
    return "\n\n".join(lines) or "(空)"


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """Best-effort parse of LLM response as JSON array.

    Tolerant to markdown code fences and trailing prose.
    """
    text = text.strip()
    # strip ```json fences
    if text.startswith("```"):
        # find first \n and last ```
        lines = text.split("\n")
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # find first [ and last ]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or start >= end:
        return []
    body = text[start:end + 1]
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _cheap_pre_filter(messages: list[Any]) -> bool:
    """极简预筛:对话里是否出现触发词或内容够长?

    避免对"你好"、"hi"这种寒暄连 LLM 都去问一次。
    """
    blob_parts: list[str] = []
    for m in messages[-DEFAULT_RECENT_MESSAGES:]:
        content = (
            getattr(m, "content", None)
            or (m.get("content") if isinstance(m, dict) else "")
        )
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if content:
            blob_parts.append(str(content))
    blob = " ".join(blob_parts)
    if len(blob) < MIN_TOTAL_CHARS:
        return False
    if _TRIGGER_RE.search(blob):
        return True
    # 没命中触发词但内容够长 → 放给 LLM 判定(可能用户讲了完整流程)
    return len(blob) >= 200


class DossierReflector:
    """异步归档 — 在 Agent 主响应后被 fire-and-forget 调用。"""

    def __init__(
        self,
        llm: BaseChatModel,
        dossier_service: DossierService,
    ) -> None:
        self._llm = llm
        self._svc = dossier_service
        self._bus = get_activity_bus()

    async def reflect(
        self,
        *,
        domain: str,
        messages: list[Any],
        session_id: str = "",
    ) -> list[str]:
        """Reflect on recent messages and archive reusable insights.

        Returns:
            写入的 entry_id 列表(失败返回 [])
        """
        started = time.monotonic()
        written_ids: list[str] = []

        try:
            # === Stage 1a: cheap 预筛 — 关键词/长度 ===
            if not _cheap_pre_filter(messages):
                logger.info(
                    "[dossier_reflector] pre-filter skipped "
                    "(no trigger words / too short)",
                )
                return []

            # === Stage 1b: LLM cheap classifier — yes/no 判定 ===
            msg_text = _format_messages(messages)
            verdict_resp = await asyncio.to_thread(
                self._invoke_llm,
                _CLASSIFY_PROMPT.format(),
            )
            verdict = _parse_json_bool(verdict_resp or "")
            if not verdict["should_archive"]:
                logger.info(
                    "[dossier_reflector] LLM classifier said no "
                    "(reason=%s, took=%.2fs)",
                    verdict.get("reason", ""),
                    time.monotonic() - started,
                )
                return []

            # === Stage 2: extract & archive ===
            existing = await self._collect_existing(domain)
            existing_text = (
                json.dumps(existing, ensure_ascii=False, default=str)[:2000]
                if existing else "(空)"
            )
            prompt = _REFLECT_PROMPT.format(
                messages=msg_text,
                domain=domain,
                existing_entries=existing_text,
            )
            llm_response = await asyncio.to_thread(
                self._invoke_llm, prompt,
            )

            entries = _parse_json_array(llm_response or "")
            if not entries:
                logger.info(
                    "[dossier_reflector] classifier said yes but "
                    "extractor returned nothing",
                )
                return []

            # 批量归档
            for entry in entries:
                node = (entry.get("node") or "").strip()
                if not node:
                    continue  # 没挂载节点,跳过
                entry_type = entry.get("type") or "note"
                title = (entry.get("title") or "").strip()
                body = (entry.get("body") or "").strip()
                if not title or not body:
                    continue

                tags_raw = entry.get("tags") or ""
                tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
                score = entry.get("score") or 0.5

                try:
                    added, created = await self._svc.add_entry(
                        domain=domain, node=node,
                        type=entry_type,
                        title=title, body=body,
                        tags=tags,
                        evidence=entry.get("evidence") or "",
                        score=float(score),
                        created_by="agent",
                    )
                    if not created:
                        logger.info(
                            "[dossier_reflector] dedup hit for '%s', skip emit",
                            title,
                        )
                        continue
                    written_ids.append(added.id)
                    await self._bus.emit(
                        ActivityKind.DOSSIER_ENTRY_ADDED,
                        domain=domain, node=node,
                        title=f"🤖 学到了 [{added.type.value}]: {title}",
                        source="agent_reflection",
                        ref=f"entry:{added.id}",
                        extra={
                            "type": added.type.value,
                            "score": added.score,
                            "session_id": session_id,
                        },
                    )
                except Exception as e:
                    logger.warning(
                        "[dossier_reflector] add_entry failed for %s: %s",
                        node, e,
                    )

            elapsed = time.monotonic() - started
            logger.info(
                "[dossier_reflector] archived %d entries in %.2fs "
                "(domain=%s, session=%s)",
                len(written_ids), elapsed, domain, session_id,
            )
            return written_ids

        except Exception as e:
            logger.warning(
                "[dossier_reflector] reflect failed: %s", e,
            )
            return []

    def _invoke_llm(self, prompt: str) -> str:
        """Invoke the LLM and return text content."""
        # Chat models use .invoke; could also be BaseChatModel variants.
        result = self._llm.invoke(prompt)
        if hasattr(result, "content"):
            return str(result.content or "")
        return str(result)

    async def _collect_existing(
        self, domain: str,
    ) -> list[dict[str, Any]]:
        """Collect all existing entries' titles + first 100 chars for dedup."""
        from src.infrastructure.graph_store.client import GraphStoreClient
        from src.agent.dependencies import get_graph_store
        store: GraphStoreClient = get_graph_store()
        try:
            await store.ensure_available()
        except Exception:
            store = None
        if store is None or not await store.ensure_available():
            return []

        # 拉所有节点
        try:
            concepts = store.all_concepts(domain)
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for c in concepts[:50]:  # 上限保护
            node = c.get("name", "")
            if not node:
                continue
            try:
                entries = await self._svc.list_entries(domain, node)
            except Exception:
                continue
            for e in entries:
                out.append({
                    "node": node,
                    "type": e.type.value,
                    "title": e.title,
                    "snippet": e.body[:80],
                })
                if len(out) >= 80:
                    return out
        return out


__all__ = [
    "DossierReflector",
    "_cheap_pre_filter",
    "_parse_json_bool",
    "_parse_json_array",
    "_CLASSIFY_PROMPT",
    "_REFLECT_PROMPT",
]