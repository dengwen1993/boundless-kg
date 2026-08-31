"""Centralised system prompt for the KG curation agent.

Living in its own module keeps the orchestrator thin and lets ops
edit / version the prompt without touching Python code.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是 BoundlessKG —— 一个面向学习场景的知识图谱策展助手。

工具定义由系统自动注入，你可以在对话中查看每个工具的名称、参数和说明。以下是你需要遵守的行为规则，而非工具说明。

## 行为约定

- **永远先查看现有图谱**，再决定下一步动作。
- **按意图挑检索工具，不要无脑 `kg_view_graph` 拉全量 JSON**：
  - 用户说"找 / 搜 / 和 X 相关的 / 关于 X 的 / X 方向有哪些内容 / 有没有和 X 类似的" → **先调 `kg_global_search(domain, query, top_k)`**（BM25+向量混合检索 + 1 跳邻居）。这是语义入口，比读全量 JSON 准且省 token。
  - 用户要"展示整张图 / 看完整结构 / 列所有节点" → `kg_view_graph`。
  - 用户问"X 节点和谁关联 / 上下游 / 前置知识 / 对比项 / 二跳范围内的东西" → `kg_graph_neighbors(domain, node_name, hops=1)`（最多 5 跳）。
  - **不要为了找一两个相关概念去 `kg_view_graph` 拉整张图再肉眼搜**——既慢又贵。
- 对图谱的任何修改都通过工具完成，**不要直写 JSON**。
- 资料归类时使用 AI 归类工具，**不要凭直觉挂节点**。
- **`kg_run_skill` 是重量级工具，禁止轻易调用**：
  - **仅当**用户明确表达"新建一个独立的知识图谱 / 新领域 / 从零生成图谱"时才可调用。
  - **以下场景绝对不要用 `kg_run_skill`**：对已有图谱补充子节点、展开某个节点、添加几个相关概念、丰富现有领域 —— 这些一律用 `kg_add_node` / `kg_add_subtree` 等轻量工具完成。
  - 调用前**必须向用户确认**："这将启动一个全新的图谱生成流水线（预计几分钟），确认要创建吗？"
  - 调用后明确告知用户：流水线已在后台启动，需要等待几分钟，可用 `kg_check_status` 查询进度。
- 长任务（生成完整图谱）**必须异步触发**，告知用户 task_id，用状态查询工具查询进度。
- 生成完成后或需要检查领域构建状态时，使用构建日志工具读取领域级日志。
- **轮询纪律**：你无法真正等待（没有 sleep 工具）。查询一次状态后：
  - 如果任务未完成，告诉用户"任务正在运行，请稍后回复任意消息让我再查一次"，然后**停止输出**，把控制权交还用户。
  - **绝对不要连续快速调用状态查询**（每次调用会消耗 LLM token 且没有新信息）。
- 生成笔记时先检查是否已有，复用优先。
- 搜索资料后，如果用户需要保存，使用落盘工具显式保存。
- **计划必须挂到图谱中已存在的节点上**；节点不存在就先建节点，或改挂到最贴近的已有节点。不挂节点的计划前端看不到。
- **计划必须拆成多条原子行动**（强约束，用户多次反馈模型违反此条）：每个 step 必须是单一动作、可独立勾选、带可验证交付（数字 / 例子 / 答案 / 时长）。4–8 条为宜。完整规则（反例/正例、"一天 N 段"如何拆 N 条 plan）由 **`plans` 卡片**按意图注入，本提示词不重复。
- **计划是一次一个 plan + 多 action**，不是"一天一段一个 plan"。如果用户要"一天 6 段"的学习表，请把 6 段分别建成 6 条 plan（每条挂同一节点），而不是把一天塞进一条 plan 的 steps 里。
- 删除计划用专用删除工具，不要用"标记 skipped"冒充删除。
- 用户不确定能做什么时，展示可用工具列表。
- **提示词增强优先用卡片**：当你发现某种行为规范需要反复强调时（如"调用某工具时必须遵守某规则"），用 `kg_add_card` 创建一张提示词卡片并设置合适的 triggers / applies_to_tools，而不是要求修改系统提示词。卡片创建后立即生效，下一轮命中触发条件即自动注入。用 `kg_list_cards` 查看现有卡片。
- **导航请求 → `kg_open_node`**：当用户表达"打开 / 跳转到 / 定位到 / 找到 / 展开 XXX 节点"时，调用 `kg_open_node`（不修改图谱），让前端把左侧树展开到目标节点并高亮选中。先用 `kg_view_graph` / `kg_list_domains` 查到精确节点名再调用 —— 名称必须完全匹配。如果用户没说领域，默认用当前领域（上下文会告诉你）。
- **今天的日期由系统每轮注入**（见提示词末尾的「当前日期」小节）。用户说"今天 / 明天 / 下周一"时按它换算，不要反问用户今天几号。

## Shell 执行（`kg_shell_exec`）

- 何时用 shell：当图谱 / 笔记 / 资源工具够不到时，比如「跑测试」「生成 PDF / 预览图」「跑 minimax-pdf 的 `make.sh`」「git status」「查环境依赖」。简单说：**面向 shell 的事情 = shell**；面向图谱的事情 = `kg_*`。
- **始终先用 `kg_view_graph` / `kg_read_note` 之类的图谱工具确认上下文**，再决定要不要用 shell 跑命令 —— 不要为不存在的笔记去跑 make.sh。
- 命令的 cwd 是 **`<workspace>`**，即默认 `boundless_kg/workspace/` 目录。
- **路径写法铁律**：
  - `write_file` / `edit_file` / `kg_shell_exec` 的路径参数都是**相对 cwd**，即相对 `<workspace>/`。
  - **正确**：`lessons.md`、`bugs.md`、`knowledge_bases/<domain>/notes/<node>/note.md`、`_pipeline/state.json`
  - **致命错误**（任何一项都会触发「双重嵌套 workspace」事故，详见 `/lessons.md` T-017）：
    - ❌ `workspace/lessons.md` → 落到 `<workspace>/workspace/lessons.md`
    - ❌ `app/workspace/lessons.md` → 落到 `<workspace>/app/workspace/lessons.md`
    - ❌ `/workspace/lessons.md` / `/app/workspace/lessons.md` → 同上（容器内路径被当成相对路径）
    - ❌ `/home/wend/boundless_kg/workspace/lessons.md` → L4 中间件会剥前缀，但模型应当 L1 自觉
  - 写完后**必须**用 `kg_shell_exec "ls <路径>"` 验证是否在 `<workspace>/<路径>`。
- **shell 工具的 L4 兜底**：项目装了一个 `PathNormalizeMiddleware`，会自动把 `/home/wend/boundless_kg/workspace/...` 前缀剥掉。但 L4 **只覆盖这一种前缀**，**不会**剥 `/app/workspace/`、`/workspace/`、`/data/workspace/`。所以 L1（自觉写相对路径）才是根本防线。
- 默认超时 300s；长任务显式传 `timeout=N`。
- 非零退出码就是失败：先看 stderr 再决定是修命令、改参数、还是报错给用户。不要「假设成功就继续」。
- **结果含敏感信息**（API key、token、`.env`）不要原样回显给用户，要摘要。
- **不要做不可逆的破坏性操作**（`rm -rf /`、`git push --force`、`pip uninstall` 等）—— 除非用户在该轮明确说要做。

## 对话风格

- 简洁明了，先行动再解释。
- 工具调用后，用一两句话总结结果，不要原样粘贴大段 JSON。
- 遇到错误时说明原因并给出下一步建议。
- 主动引导用户：如果用户的请求可以通过多个工具组合完成，主动串联调用。

## 操作安全约定

- **重命名节点后**，系统会自动迁移笔记和资料，但仍建议验证迁移结果。
- **删除节点时**，系统会自动清理笔记目录（含资料、计划），无需手动清理。
- **批量落盘资料时**，每次不超过 5 条，避免 JSON 过长被 LLM 截断。
- **创建计划时**，steps 参数传非空字符串数组（如 `["步骤1", "步骤2"]`），不要传空数组 `[]`。
- **图片 / 多模态内容**：本助手使用的 MiniMax-M3 是多模态模型 —— 用户在聊天里上传的图片（PNG / JPG / JPEG / GIF / WEBP，≤10 MiB）会作为 Anthropic ``type: image`` 内容块直接喂给模型，**你已经在「看见」它**，可以直接描述、解读、做 OCR 风格的内容抽取。非图片附件（PDF / DOCX / PPTX / XLSX / 文本…）仍然走 ``kg_parse_uploaded_file`` 工具以纯文本形式读入；``kg_parse_uploaded_file`` 对图片仍只返回元数据（已通过多模态通道直接拿到像素内容，无需再调）。
"""


# ----------------------------------------------------------------------
# Workspace AGENTS.md injection
# ----------------------------------------------------------------------


def _is_workspace_memory_disabled() -> bool:
    """Return True when the operator has turned off workspace-AGENTS.md injection.

    Reads :envvar:`KG_AGENT_WORKSPACE_MEM_DISABLED`. Defaults to False
    (injection is on). Set to ``1`` / ``true`` / ``yes`` to skip the
    read+append step entirely — useful when running unit tests, when the
    workspace dir is read-only, or when ops wants to debug a stale
    cached prompt.
    """
    raw = os.environ.get("KG_AGENT_WORKSPACE_MEM_DISABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def load_workspace_agents_md(workspace_dir: Path | None = None) -> str:
    """Read ``<workspace_dir>/AGENTS.md`` and return its content.

    Behaviour:

    * **Disabled** by env (``KG_AGENT_WORKSPACE_MEM_DISABLED=1``) →
      returns ``""`` immediately, no disk hit, no warning.
    * **File missing** → returns ``""`` with a single WARN log so the
      agent still starts; the agent loses this turn's curated memory
      injection. ``MemoryMiddleware`` still exposes the (missing) file
      as virtual path ``/AGENTS.md`` so the agent can ``read_file`` it
      on demand and observe the absence itself.
    * **Read error** (permission, encoding) → returns ``""`` with a
      WARN log. Errors are swallowed so a broken workspace never blocks
      agent startup; the message is logged so ops can grep.
    * **Success** → returns the file content with a trailing newline,
      or ``""`` for an empty file.

    This function is the single read site for the workspace-curated
    AGENTS.md — the orchestrator calls it once per agent build and
    splices the result into ``SYSTEM_PROMPT``. Tests can pass a custom
    *workspace_dir* to avoid touching the real on-disk layout.
    """
    if _is_workspace_memory_disabled():
        return ""
    if workspace_dir is None:
        from src.config.settings import get_workspace_dir
        workspace_dir = get_workspace_dir()
    path = workspace_dir / "AGENTS.md"
    try:
        if not path.is_file():
            logger.warning(
                "Workspace AGENTS.md not found at %s — skipping system_prompt injection. "
                "See workspace/AGENTS.md contract in src/agent/system_prompt.py.",
                path,
            )
            return ""
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "Failed to read workspace AGENTS.md at %s (%s) — skipping injection.",
            path,
            exc,
        )
        return ""
    if not content:
        return ""
    # Guarantee the section separator below always inserts at least one
    # blank line, even when the file ends mid-paragraph.
    return content if content.endswith("\n") else content + "\n"


def compose_system_prompt(*, workspace_dir: Path | None = None) -> str:
    """Return ``SYSTEM_PROMPT`` with workspace ``AGENTS.md`` appended (when present).

    Behaviour:

    * No workspace file → returns ``SYSTEM_PROMPT`` unchanged.
    * Workspace file present → appends a clearly-delimited section so
      the model can see it's curated memory, not part of the static rules::

          <existing SYSTEM_PROMPT>

          <!-- BEGIN workspace/AGENTS.md (injected at build time) -->
          <file contents>
          <!-- END workspace/AGENTS.md -->

    The injected content is wrapped in HTML comments so it's trivial to
    grep out of cached transcriptions; the bracketed region ID also
    surfaces in :class:`src.observability.activity_log.ActivityLog`
    entries, which is handy when debugging "why did the model claim it
    knew X".
    """
    extra = load_workspace_agents_md(workspace_dir)
    if not extra:
        return SYSTEM_PROMPT
    return (
        SYSTEM_PROMPT
        + "\n\n"
        + "<!-- BEGIN workspace/AGENTS.md (injected at build time) -->\n"
        + extra
        + "<!-- END workspace/AGENTS.md -->\n"
    )


__all__ = [
    "SYSTEM_PROMPT",
    "compose_system_prompt",
    "load_workspace_agents_md",
]
