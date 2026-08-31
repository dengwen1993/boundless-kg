"""Transient-file tools — list, preview, and parse uploaded files.

The user can drop files into the chat via the paperclip button next to
the chat composer. Those files land in ``.agent_memory/tmp/`` and are
surfaced to the agent through these tools so it can answer questions
like "summarise this PDF" / "extract the table from this CSV" / "how
many slides does this deck have?" without having to round-trip back
through the per-node resource uploader.

Auto-cleanup: the background loop in :mod:`src.api.server` deletes
files older than :data:`TMP_MAX_AGE_DAYS`, so the tools always reflect
"what's currently in the tmp dir" rather than a long-lived catalog.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool

from src.agent.dependencies import get_resource_repo, get_resource_service
from src.agent.memory import get_tmp_dir
from src.application.tmp_parser import parse_file_to_text
from src.observability.logged_tool import logged_tool

logger = logging.getLogger(__name__)


def _safe_filename(name: str) -> str:
    """Strip any path components so a malicious ``../../etc`` can't escape.

    Mirrors the same defence in the ``/api/tmp/...`` route layer so the
    tool surface and the HTTP surface share one definition of "safe".
    """
    cleaned = Path(name).name
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(f"非法的文件名：{name!r}")
    return cleaned


@tool
@logged_tool
async def kg_list_uploaded_files() -> str:
    """查看当前 tmp 目录里用户上传了哪些文件。

    返回 JSON 数组，每个元素含 ``file`` / ``size`` / ``mtime`` /
    ``path`` 四个字段。``path`` 是容器内绝对路径，可直接喂给
    ``kg_parse_uploaded_file`` 的 ``filename`` 参数。
    """
    tmp = get_tmp_dir()
    if not tmp.exists():
        return json.dumps({"items": [], "total": 0}, ensure_ascii=False)

    items: list[dict] = []
    for p in tmp.iterdir():
        if not p.is_file():
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        items.append(
            {
                "file": p.name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "path": str(p),
            }
        )
    items.sort(key=lambda it: it["mtime"], reverse=True)
    return json.dumps({"items": items, "total": len(items)}, ensure_ascii=False)


@tool
@logged_tool
async def kg_parse_uploaded_file(filename: str, max_chars: int = 8000) -> str:
    """解析用户上传的文件，返回分类信息 + 短预览。

    目标不是高质量全文重建（要全文请走对应 skill：PPTX→pptx-generator、
    DOCX→minimax-docx），而是**轻量级分类 + 摘要预览** —— 让 LLM 知道
    这是什么文件、关键元数据（页数/sheet 名/作者）、以及开头片段。

    支持的格式（按扩展名自动分派）：

    - 纯文本 / 代码：.txt .md .json .yaml .py .js .ts .go .rs .java ...
    - 结构化：.csv（markdown 表格，前 200 行）、.html / .htm（剥标签）
    - Office：.pdf（pypdf）、.docx / .pptx（**markitdown** → Markdown；fallback stdlib zipfile）
    - 表格：.xlsx / .xlsm（openpyxl）
    - 图片：.png / .jpg / .webp ... — 返回元数据（PIL 读 size / EXIF）。
      注意：**图片附件在用户发消息时已经作为多模态 content block
      直接喂给 MiniMax-M3 了**，所以你不需要再调本工具去"看"图；
      本工具对图片仍只返回元数据，是给 LLM 一个可复述的事实来源

    DOCX/PPTX 优先用 markitdown 解析（高质量，保留标题/列表/表格），
    markitdown 不可用或失败时降级到 stdlib zipfile + XML（始终可用）。
    不可解析格式返回友好提示，永不抛异常。

    返回头部会带 ``分类信息`` block：页数、sheet 列表、作者等结构化
    hint，方便回答「这份 PPT 多少页？」之类问题。
    """
    try:
        safe = _safe_filename(filename)
    except ValueError as exc:
        return f"❌ {exc}"

    target = get_tmp_dir() / safe
    if not target.exists() or not target.is_file():
        siblings = [p.name for p in get_tmp_dir().iterdir() if p.is_file()]
        hint = f"目前 tmp 目录里有：{siblings}" if siblings else "tmp 目录为空"
        return f"❌ 文件不存在：{safe}。{hint}。可先用 ``kg_list_uploaded_files()`` 查准确文件名。"

    if max_chars < 500:
        max_chars = 500
    if max_chars > 200000:
        max_chars = 200000

    parsed = await parse_file_to_text(target, max_chars=max_chars)

    # Images: the model has already received the pixels via the
    # multimodal content block the chat layer injected — there is no
    # text body to extract.  We only return metadata so the model has
    # a canonical fact source ("this was a 1920x1080 PNG") it can
    # refer back to without re-asking the user.
    if parsed["format"] == "image":
        return (
            f"🖼️ {safe}（{parsed['format']} · {parsed['size']} bytes）\n\n"
            f"{parsed['text']}\n\n"
            "ℹ️ 图片像素内容已经在用户消息中以多模态内容块形式提供（本模型就是多模态），"
            "无需再次解析；这里只是元数据供你引用。\n"
            f"图片路径：{target}"
        )

    # Compose a header that surfaces structured hints (page count /
    # sheet names / author) so the model can answer "how many slides
    # does this deck have?" without re-reading the body.
    hint_lines: list[str] = []
    for k, v in parsed.get("hints", {}).items():
        if k == "kind":
            continue
        if isinstance(v, list):
            hint_lines.append(f"  - {k}: {', '.join(str(x) for x in v)}")
        else:
            hint_lines.append(f"  - {k}: {v}")
    hint_block = ("\n分类信息：\n" + "\n".join(hint_lines)) if hint_lines else ""

    head = (
        f"📄 {safe}（{parsed['format']} · {parsed['size']} bytes · "
        f"{parsed['chars']} chars"
        f"{' · 已截断' if parsed['truncated'] else ''}）"
        f"{hint_block}\n"
    )
    return head + "\n" + parsed["text"]


@tool
@logged_tool
async def kg_delete_uploaded_file(filename: str) -> str:
    """从 tmp 目录里删除一个用户上传的文件。

    用于「上传错了，帮我删掉」「清理一下」之类的场景。删除前会先
    校验文件名（防止路径穿越），找不到文件时返回 ``❌`` 而不是崩。
    """
    try:
        safe = _safe_filename(filename)
    except ValueError as exc:
        return f"❌ {exc}"

    target = get_tmp_dir() / safe
    if not target.exists() or not target.is_file():
        return f"❌ 文件不存在：{safe}"

    target.unlink()
    return f"✅ 已删除 {safe}"


@tool
@logged_tool
async def kg_auto_place_uploaded_file(
    domain: str,
    filename: str,
    *,
    create_new_node: bool = True,
    max_chars: int = 8000,
) -> str:
    """把用户上传的 tmp 文件**自动解析 + 自动归类 + 自动放到正确的节点下**。

    一站式调用，执行下面 4 步：

    1. **解析文档内容**：用 :func:`src.application.tmp_parser.parse_file_to_text`
       抽取轻量级预览（页数/sheet 名/作者 + 前 ~8KB 文本）。
    2. **读大纲 JSON**：加载 ``<kb>/<domain>/knowledge_graph.json``，
       渲染成树形结构（根 → 子 → 叶），作为 LLM 的选择空间。
    3. **大模型归类**：把文件名 + 元数据 + 节点树 + 内容预览发给 LLM，
       让它挑出最合适的**已有节点**（或建议一个新节点名）。
    4. **自动落盘**：把文件复制到目标节点的 ``user_uploads/`` 目录下，
       追加 ``user_uploads/index.json`` 一条记录，并发 timeline 事件。

    Args:
        domain: 领域名（与知识库的目录名一致）。
        filename: 用户上传到 tmp 目录的文件名。
        create_new_node: 当 LLM 判定需要新建节点时是否真的创建；
            设为 ``False`` 时返回决策但不创建、不落盘，便于人工 review。
        max_chars: 喂给 LLM 的预览字符数上限（500–32000）。

    Returns:
        JSON 字符串，包含 ``node`` / ``path`` / ``rationale`` /
        ``new_node_created`` / ``decision`` 等字段。
        失败时返回 ``❌`` 开头的错误说明。
    """
    try:
        safe = _safe_filename(filename)
    except ValueError as exc:
        return f"❌ {exc}"

    target = get_tmp_dir() / safe
    if not target.exists() or not target.is_file():
        siblings = [p.name for p in get_tmp_dir().iterdir() if p.is_file()]
        hint = f"目前 tmp 目录里有：{siblings}" if siblings else "tmp 目录为空"
        return f"❌ 文件不存在：{safe}。{hint}。可先用 ``kg_list_uploaded_files()`` 查准确文件名。"

    if max_chars < 500:
        max_chars = 500
    if max_chars > 32000:
        max_chars = 32000

    parsed = await parse_file_to_text(target, max_chars=max_chars)

    svc = get_resource_service()
    try:
        result = await svc.auto_place_upload(
            domain=domain,
            tmp_path=target,
            filename=safe,
            parsed=parsed,
            create_new_node=create_new_node,
        )
    except Exception as exc:
        logger.exception("auto_place_upload failed for %s/%s", domain, safe)
        return (
            f"❌ 自动归类失败（{type(exc).__name__}: {exc}）。"
            f"文件仍保留在 tmp 目录里，可稍后重试或用 ``kg_classify_pending`` 手动归类。"
        )

    # Pretty-print the result for the model.
    if result.get("needs_review"):
        head = (
            f"⚠️ 自动归类不确定「{safe}」({parsed['format']}, {parsed['size']} bytes) "
            f"→ 未落盘，等用户选择\n"
            f"📄 文件仍在 tmp：{result.get('file_kept_in_tmp', target)}\n"
            f"💡 理由：{result.get('rationale') or '（无）'}\n"
            f"🔍 候选节点（按确定性排序）："
        )
        cands = result.get("candidates") or []
        if cands:
            for c in cands[:5]:
                head += (
                    f"\n  - {c['node']} "
                    f"(confidence={c.get('confidence', 0):.0%}, "
                    f"matched={c.get('matched_tokens', [])[:5]})"
                )
        else:
            head += "\n  （无候选 — 领域可能为空图）"
        if result.get("error"):
            head += f"\n🐛 详情：{result['error']}"
        head += (
            "\n\n👉 请告诉我要挂到哪个节点（或新建什么名字）后重试；"
            "或在 ChatPanel 里手动选节点后调 kg_create_node_with_resource。"
        )
    else:
        head = (
            f"✅ 已自动归类「{safe}」({parsed['format']}, {parsed['size']} bytes) → "
            f"节点「{result['node']}」"
        )
        if result.get("new_node_created"):
            head += "（新建节点）"
        if result.get("skipped"):
            head += f"\n⚠️  跳过落盘：{result['skipped']}"
        head += f"\n📄 文件位置：{result['path'] or '（未落盘）'}"
        if result.get("rationale"):
            head += f"\n💡 归类理由：{result['rationale']}"
        if result.get("confidence"):
            head += f"\n🎯 确定性：{result['confidence']:.0%}"
        # Defence-in-depth: refuse to display a garbage fallback node name.
        if result.get("node") in {"未命名资料", "未分类资料", "未知资料"}:
            head += (
                "\n\n⚠️ 检测到占位节点名 — 这通常是上游 bug。请用 "
                "kg_create_node_with_resource 手动归档。"
            )
    head += "\n\n" + json.dumps(result, ensure_ascii=False, indent=2)
    return head


__all__ = [
    "kg_list_uploaded_files",
    "kg_parse_uploaded_file",
    "kg_delete_uploaded_file",
    "kg_auto_place_uploaded_file",
]