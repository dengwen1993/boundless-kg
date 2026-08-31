"""Note generator — structured 3-section prompt with rich graph context.

The generator produces notes with three mandatory sections:
  1. **定义** — precise concept definition
  2. **重要概念与知识点** — key knowledge points (5-10 items)
  3. **如何开启快速学习** — actionable learning path

Graph context (parents / children / siblings / hierarchy) is fully
leveraged so the LLM can tailor depth and emphasis to the node's
position in the knowledge graph.
"""

from __future__ import annotations

from typing import Any

from src.domain.protocols import (
    LLMClientProtocol,
    SearchResult,
    WikiClientProtocol,
)

# ── Prompt version label (for frontmatter tracing) ──
PROMPT_VERSION = "v2-structured"

# ── System prompt ──
NOTE_SYSTEM_PROMPT = """\
你是知识图谱笔记助手，专为学习者撰写高质量的概念笔记。

你的任务是为知识图谱中的节点撰写一份结构化、可拓展的中文 Markdown 笔记，\
帮助读者快速理解概念定义并规划学习路径。

════════════════════════════════════════════
必须包含的三大板块（缺一不可，严格按此顺序输出）
════════════════════════════════════════════

## 定义
用 2-4 段精炼文字给出该概念的准确定义：
- 第一段：用一句话给出核心定义（核心句 **加粗**）
- 第二段：解释该概念的本质特征、解决什么问题
- 第三段（可选）：说明该概念在所属领域中的定位和重要性
- 如有容易混淆的近义术语，简要辨析

## 重要概念与知识点
列出理解该概念必须掌握的关键知识点（5-10 个）。使用有序列表，\
每个知识点包含：
  - **知识点名称**：一句话解释其含义
  - 缩进补充：为什么重要 / 与本概念的关系 / 易错点

如果知识图谱中该节点已有子节点（细分主题），应将其自然融入知识点列表，\
让读者知道这些子主题在本概念中的位置。

## 如何开启快速学习
给出一条清晰、可执行的学习路径，包含以下子项：
- **前置知识**：学习此概念前需要先掌握什么（可引用父节点 / 同级节点）
- **学习步骤**：按由浅入深的顺序列出 3-5 个步骤
- **实践建议**：推荐动手做什么来加深理解
- **常见误区**：学习时容易踩的 1-3 个坑

════════════════════════════════════════════
写作要求
════════════════════════════════════════════
- 内容必须准确，不编造事实；不确定的标注「待补充」
- 结合知识图谱上下文（父节点、子节点、同级节点）确定内容侧重点
  · 如果是 L1 主题节点，侧重宏观框架和知识体系
  · 如果是叶子节点，侧重具体概念和实操
- 面向该领域的目标受众，使用他们能理解的语言
- 每个知识点必须有实质解释，不能只列标题不解释
- 输出纯 Markdown 正文，不要写 H1 标题（标题由系统模板添加）
"""


class NoteGenerator:
    """Compose the note prompt + invoke the LLM asynchronously.

    A single comprehensive prompt replaces the old 4-tier ladder.
    The prompt always requests the three mandatory sections (定义 /
    重要概念与知识点 / 如何开启快速学习) and leverages all available
    graph context to tailor depth and emphasis.
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        wiki_client: WikiClientProtocol | None = None,
    ) -> None:
        self._llm = llm_client
        self._wiki = wiki_client

    async def generate(
        self,
        node_name: str,
        domain: str,
        *,
        graph_ctx: dict[str, Any] | None = None,
        wiki_def: str = "",
        search_results: list[SearchResult] | None = None,
    ) -> str:
        prompt = self._build_prompt(
            node_name=node_name,
            domain=domain,
            graph_ctx=graph_ctx or {},
            wiki_def=wiki_def,
            search_results=search_results or [],
        )
        body = await self._llm.chat(
            NOTE_SYSTEM_PROMPT,
            prompt,
            temperature=0.4,
            max_tokens=4096,
        )
        return self._format_markdown(body)

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        *,
        node_name: str,
        domain: str,
        graph_ctx: dict[str, Any],
        wiki_def: str,
        search_results: list[SearchResult],
    ) -> str:
        """Build the user-turn prompt with full graph context.

        The context block surfaces *all* available structural information
        (hierarchy path, parents, children, siblings, domain direction)
        so the LLM can tailor depth and emphasis.  Wiki definitions and
        web search results are appended as reference material.
        """
        ctx_lines: list[str] = []

        # ── Graph structural context ──
        hierarchy = graph_ctx.get("hierarchy_path", "")
        if hierarchy:
            ctx_lines.append(f"- 层级路径：{hierarchy}")

        parents = graph_ctx.get("parents", [])
        if parents:
            ctx_lines.append(f"- 父节点（上级概念）：{'、'.join(parents)}")

        children = graph_ctx.get("children", [])
        if children:
            ctx_lines.append(
                f"- 子节点（已有细分主题）：{'、'.join(children)}"
            )

        siblings = graph_ctx.get("siblings", [])
        if siblings:
            ctx_lines.append(
                f"- 同级节点：{'、'.join(siblings[:10])}"
            )

        direction = graph_ctx.get("direction_summary", "")
        if direction:
            ctx_lines.append(f"- 领域方向：{direction}")

        # Backward-compat: old callers may pass "neighbours"
        neighbours = graph_ctx.get("neighbours", [])
        if neighbours:
            ctx_lines.append(f"- 相关节点：{'、'.join(neighbours)}")

        graph_summary = graph_ctx.get("graph_summary", "")
        if graph_summary and not direction:
            ctx_lines.append(f"- 领域概述：{graph_summary}")

        graph_block = (
            "\n".join(ctx_lines) if ctx_lines else "（无图谱上下文）"
        )

        # ── Wiki definition ──
        wiki_block = ""
        if wiki_def:
            wiki_block = f"\n\n## 维基百科摘要\n{wiki_def}"

        # ── Web search results ──
        search_block = ""
        if search_results:
            lines = []
            for r in search_results[:8]:
                snippet = (r.snippet or "")[:160]
                lines.append(f"- {r.title}: {snippet} ({r.link})")
            search_block = "\n\n## 搜索参考\n" + "\n".join(lines)

        return (
            f"请为以下节点撰写一份结构化笔记。\n\n"
            f"节点：{node_name}\n"
            f"领域：{domain}\n\n"
            f"## 知识图谱上下文\n{graph_block}"
            f"{wiki_block}"
            f"{search_block}\n\n"
            "请严格按照「定义 → 重要概念与知识点 → 如何开启快速学习」"
            "三大板块输出。\n"
            "不要输出 H1 标题。"
        )

    # ------------------------------------------------------------------
    # Output formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_markdown(body: str) -> str:
        """Return only the LLM body — title + metadata are added by the
        caller's note template to avoid duplicate headers."""
        return body.strip() + "\n"


# ------------------------------------------------------------------
# Convenience function
# ------------------------------------------------------------------


async def generate_note_async(
    node_name: str,
    domain: str,
    llm_client: LLMClientProtocol,
    *,
    graph_ctx: dict[str, Any] | None = None,
    wiki_def: str = "",
    search_results: list[SearchResult] | None = None,
    wiki_client: WikiClientProtocol | None = None,
) -> str:
    """Top-level convenience."""
    return await NoteGenerator(llm_client, wiki_client).generate(
        node_name,
        domain,
        graph_ctx=graph_ctx,
        wiki_def=wiki_def,
        search_results=search_results,
    )


__all__ = [
    "NoteGenerator",
    "generate_note_async",
    "NOTE_SYSTEM_PROMPT",
    "PROMPT_VERSION",
]
