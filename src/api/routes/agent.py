"""Agent SSE streaming route — uses deepagents ``astream_events``.

API contract:
  POST /api/agent/invoke  → SSE stream with events:
      text         {delta: "..."}
      tool-call    {name, args}
      tool-result  {name, result}
      error        {message, trace?}

The agent is built with ``deepagents.create_deep_agent`` and has full
tool-calling support: the LLM can call any ``kg_*`` tool, receive the
result, and continue reasoning until it produces a final text answer.

Multimodal attachments:
  The request body can include ``attachments: ["img.png", "doc.pdf"]``.
  Image attachments are loaded from ``.agent_memory/tmp/``, base64
  encoded, and pushed into the user message as Anthropic-format
  image content blocks — MiniMax-M3 (the configured model) is multimodal
  via either the OpenAI-compatible ``image_url`` form or the
  Anthropic-compatible ``type: image`` block, both of which
  ChatAnthropic serialises to the Anthropic wire format we use here.
  Non-image attachments are still surfaced as text references so the
  agent can call ``kg_parse_uploaded_file`` on them.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import traceback
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from src.agent.memory import ConversationLogger, generate_session_id, get_tmp_dir
from src.agent.orchestrator import ensure_agent_built, get_agent, get_agent_status

router = APIRouter(prefix="/api", tags=["agent"])

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------


# Image MIME types MiniMax-M3 accepts.  Per the provider doc the body
# limit per image is 10MB and the request body cap is 64MB; we enforce
# the per-image cap server-side so a single huge upload can't blow the
# whole conversation.  ``.bmp`` is NOT in MiniMax's supported list, so
# we silently skip it (the agent still sees the filename reference).
_IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
# Per-image cap (10 MiB) — MiniMax hard limit.
_MAX_IMAGE_BYTES: int = 10 * 1024 * 1024
# Total cap across all images in one request — the provider spec caps
# request body at 64MB; we leave headroom for text + tool definitions.
_MAX_IMAGE_TOTAL_BYTES: int = 50 * 1024 * 1024


class AgentInvokeReq(BaseModel):
    # Session / thread id. Frontend should send a 16-char UUID minted by
    # ``/api/agent/session`` — we mint one here too as a safety net so
    # legacy callers (curl, tests) keep working.
    thread_id: str | None = None
    message: str
    context: str = ""
    # Filenames of files currently sitting in ``.agent_memory/tmp/``
    # (uploaded via ``POST /api/tmp/upload``).  Image entries are read
    # into base64 image content blocks; non-image entries are surfaced
    # as a text reference so the agent can call
    # ``kg_parse_uploaded_file`` on them.
    attachments: list[str] = Field(default_factory=list)


class NewSessionResp(BaseModel):
    """Returned by ``POST /api/agent/session`` — the caller (frontend)
    should store this id and send it back as ``thread_id`` on every
    subsequent ``/api/agent/invoke``."""

    session_id: str
    date: str


# ------------------------------------------------------------------
# Streaming
# ------------------------------------------------------------------


def _extract_content(content: Any) -> str:
    """Extract plain text from a chat-model chunk's content.

    Anthropic / MiniMax models return content as a list of content
    blocks (e.g. ``[{"text": "...", "type": "text"}]``); some return
    a plain string.  Normalise to a single string.
    """
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def _safe_attach_name(name: str) -> str:
    """Strip any path component from an attachment name.

    Mirrors :func:`src.api.routes.tmp_uploads._safe_name` so a malicious
    ``../../etc/passwd`` can't escape the tmp dir.
    """
    cleaned = Path(name).name
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(f"非法的文件名：{name!r}")
    return cleaned


def _build_attachment_blocks(
    attachments: list[str],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]]:
    """Resolve ``attachments`` into image content blocks + text refs.

    Walks each filename in ``.agent_memory/tmp/``:

    * **Image** (PNG / JPG / JPEG / GIF / WEBP, ≤10MiB) → Anthropic
      ``type: image`` block with base64 data.  These get pushed into
      the user message as multimodal content so MiniMax-M3 can see
      the pixels directly.
    * **Other file** → text reference ("- foo.pdf, container path:
      /…") so the model knows what's available and can call
      ``kg_parse_uploaded_file`` on it.
    * **Missing / oversized / unsupported** → recorded in the warnings
      list (returned as ``attachment-warning`` SSE frames so the
      frontend can show the user).

    Returns ``(image_blocks, text_refs, warnings)``.
    """
    image_blocks: list[dict[str, Any]] = []
    text_refs: list[str] = []
    warnings: list[dict[str, str]] = []

    tmp = get_tmp_dir()
    total_image_bytes = 0

    for raw in attachments:
        try:
            safe = _safe_attach_name(raw)
        except ValueError as exc:
            warnings.append({"file": raw, "reason": str(exc)})
            continue

        target = tmp / safe
        if not target.exists() or not target.is_file():
            warnings.append(
                {
                    "file": safe,
                    "reason": (
                        "文件不在 tmp 目录中（可能已被自动清理，"
                        "请重新上传）"
                    ),
                }
            )
            continue

        suffix = target.suffix.lower()
        mime = _IMAGE_MIME_TYPES.get(suffix) or mimetypes.guess_type(safe)[0]

        if mime and mime.startswith("image/"):
            if target.stat().st_size > _MAX_IMAGE_BYTES:
                warnings.append(
                    {
                        "file": safe,
                        "reason": (
                            f"图片过大（{target.stat().st_size} bytes），"
                            f"单张图片上限 {_MAX_IMAGE_BYTES // (1024*1024)} MiB"
                        ),
                    }
                )
                continue
            total_image_bytes += target.stat().st_size
            if total_image_bytes > _MAX_IMAGE_TOTAL_BYTES:
                warnings.append(
                    {
                        "file": safe,
                        "reason": (
                            "本轮图片总大小超过请求体上限（"
                            f"{_MAX_IMAGE_TOTAL_BYTES // (1024*1024)} MiB）"
                        ),
                    }
                )
                continue
            try:
                b64 = base64.standard_b64encode(target.read_bytes()).decode("ascii")
            except OSError as exc:
                warnings.append(
                    {"file": safe, "reason": f"读取失败：{type(exc).__name__}: {exc}"}
                )
                continue
            image_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": b64,
                    },
                }
            )
            continue

        # Non-image — let the model know it exists and where to read it.
        size_kb = target.stat().st_size / 1024.0
        text_refs.append(
            f"- {safe}（{size_kb:.1f} KB，容器路径：`{target}`）"
        )

    return image_blocks, text_refs, warnings


def _parse_deliver_assets(text: str) -> list[str] | None:
    """Extract <deliver_assets> paths from *text* (returns None if no XML).

    The knowledge-digest / kg_make_* skills instruct the agent to wrap file
    paths in a <deliver_assets> block at the end of its reply.  When the
    accumulated text contains such a block, we emit a dedicated ``asset``
    SSE event so the frontend can surface download buttons.

    Returns the list of <path> entries inside the LAST complete
    ``<deliver_assets>...</deliver_assets>`` block, or ``None`` when no
    block has closed yet (so we wait for the stream to finish before
    firing the event).
    """
    if "<deliver_assets>" not in text:
        return None
    start = text.rfind("<deliver_assets>")
    end = text.find("</deliver_assets>", start)
    if end == -1:
        return None
    block = text[start:end]
    import re
    return re.findall(r"<path>(.*?)</path>", block)


def _extract_tool_output(out: Any) -> str:
    """Extract a tool's raw return value from an ``on_tool_end`` payload.

    ``ev["data"]["output"]`` is **not** the tool's return value — LangChain
    wraps it in a ``ToolMessage``.  Calling ``str()`` on that wrapper yields
    the repr-ish form::

        content='{"ok": true, ...}' name='kg_open_node' tool_call_id='...'

    which is not JSON, so the frontend's ``JSON.parse`` blows up with
    ``Unexpected token 'c', "content='{"...`` (the ``kg_open_node``
    navigation side-effect then silently never fires).

    Unwrap the known shapes and fall back to ``str()`` only for genuinely
    unstructured values:

      * ``ToolMessage``-like  — anything exposing ``.content``
      * ``{"content": ...}``  — the dict-serialised form
      * ``str``               — already the raw payload
      * list of content blocks — same shape the chat model streams
    """
    if out is None:
        return ""
    if isinstance(out, str):
        return out
    # ToolMessage (or any BaseMessage) — the tool's return value lives in
    # ``.content``, which may itself be a string or a block list.
    content = getattr(out, "content", None)
    if content is not None:
        return _extract_content(content)
    if isinstance(out, dict) and "content" in out:
        return _extract_content(out["content"])
    if isinstance(out, list):
        return _extract_content(out)
    return str(out)


async def _collect_recent_messages(
    agent: Any, config: dict, req: AgentInvokeReq, full_text: str,
) -> list[dict[str, Any]]:
    """Best-effort fetch of the last N messages for dossier reflection.

    Tries LangGraph ``get_state`` first; falls back to a minimal
    ``[user, assistant]`` pair built from the current request.
    """
    try:
        state = await agent.aget_state(config)
        msgs = []
        if state and hasattr(state, "values"):
            msgs = state.values.get("messages") or []
        out: list[dict[str, Any]] = []
        for m in list(msgs)[-6:]:
            role = getattr(m, "type", None) or getattr(m, "role", None)
            content = getattr(m, "content", None)
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            out.append({
                "role": role or "?",
                "content": str(content or ""),
            })
        if out:
            return out
    except Exception:
        pass
    # Fallback: minimal context
    return [
        {"role": "user", "content": req.message or ""},
        {"role": "assistant", "content": full_text or ""},
    ]


async def _stream_agent(req: AgentInvokeReq) -> AsyncIterator[dict]:
    """Stream deepagents events as SSE frames.

    Maps deepagents ``astream_events`` (v2) to the three SSE event types
    the Vue frontend expects:
      - ``on_chat_model_stream`` → ``text``  {delta}
      - ``on_tool_start``        → ``tool-call``  {name, args}
      - ``on_tool_end``          → ``tool-result`` {name, result}

    Also logs the full conversation to disk via ``ConversationLogger``.

    The SSE stream also emits a ``session`` event *first* with the
    resolved ``session_id`` so the frontend can confirm/sync what it
    actually used (in case the caller omitted ``thread_id`` and we
    minted a new one).

    Multimodal: when ``req.attachments`` contains images, the user
    message is upgraded to a content-block list (text + Anthropic
    ``type: image`` blocks) so MiniMax-M3 actually sees the pixels.
    Non-image attachments are still listed by filename so the agent
    can call ``kg_parse_uploaded_file`` on them.
    """
    # Mint a session id if the caller didn't supply one.  This keeps
    # every session isolated (its own JSONL file + its own LangGraph
    # thread) so "新会话" actually means a fresh conversation.
    session_id = req.thread_id or generate_session_id()

    agent = get_agent()
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 250,
    }

    full_message = req.message
    if req.context:
        full_message = f"[当前上下文]\n{req.context}\n\n[用户问题]\n{req.message}"

    # --- Conversation logger ---
    logger = ConversationLogger(
        session_id=session_id,
        initial_prompt=full_message,
    )

    # --- Resolve attachments: images → multimodal blocks, others → text refs ---
    image_blocks, text_refs, attach_warnings = _build_attachment_blocks(
        req.attachments or []
    )

    # Surface attachment warnings (over-size / wrong format) as an SSE
    # frame BEFORE the agent stream starts so the frontend can show them
    # in the chat panel without waiting for the model to acknowledge.
    for warn in attach_warnings:
        yield {
            "event": "attachment-warning",
            "data": json.dumps(warn, ensure_ascii=False),
        }

    # Append a short text reference list for non-image attachments so
    # the model still sees what was uploaded even if it can't see them.
    if text_refs:
        ref_block = "\n\n[其他附件 — 需要用 kg_parse_uploaded_file 读取]\n" + "\n".join(
            f"- {line}" for line in text_refs
        )
        full_message = full_message + ref_block

    # Build the user message payload.  When we have image blocks we
    # emit a content-block list (Anthropic multimodal); otherwise the
    # plain string keeps existing behaviour for text-only turns.
    user_payload: dict[str, Any]
    if image_blocks:
        user_payload = {
            "messages": [
                {
                    "role": "user",
                    "content": image_blocks
                    + [{"type": "text", "text": full_message}],
                }
            ]
        }
    else:
        user_payload = {
            "messages": [{"role": "user", "content": full_message}]
        }

    # Emit the resolved session id first so the frontend can sync state.
    yield {
        "event": "session",
        "data": json.dumps(
            {"session_id": session_id},
            ensure_ascii=False,
        ),
    }
    agent_text_parts: list[str] = []
    asset_emitted = False  # ensure one asset event per turn (deduplicate)

    try:
        async for ev in agent.astream_events(
            user_payload,
            config=config,
            version="v2",
        ):
            kind = ev.get("event")
            if kind == "on_chat_model_stream":
                chunk = ev["data"].get("chunk")
                content = getattr(chunk, "content", "") if chunk else ""
                text = _extract_content(content)
                if text:
                    agent_text_parts.append(text)
                    yield {
                        "event": "text",
                        "data": json.dumps(
                            {"delta": text}, ensure_ascii=False
                        ),
                    }
            elif kind == "on_tool_start":
                tool_args = ev["data"].get("input", {})
                await logger.log_tool(ev.get("name", ""), tool_args)
                yield {
                    "event": "tool-call",
                    "data": json.dumps(
                        {
                            "name": ev.get("name"),
                            "args": tool_args,
                        },
                        ensure_ascii=False,
                    ),
                }
            elif kind == "on_tool_end":
                out = ev["data"].get("output")
                result_str = _extract_tool_output(out)
                await logger.log_tool_result(ev.get("name", ""), result_str)
                yield {
                    "event": "tool-result",
                    "data": json.dumps(
                        {
                            "name": ev.get("name"),
                            "result": result_str,
                        },
                        ensure_ascii=False,
                    ),
                }
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        await logger.log_error(error_msg)
        yield {
            "event": "error",
            "data": json.dumps(
                {
                    "message": error_msg,
                    "trace": traceback.format_exc(),
                },
                ensure_ascii=False,
            ),
        }
    finally:
        # Flush accumulated agent text to the conversation log.
        if agent_text_parts:
            await logger.log_agent("".join(agent_text_parts))
        await logger.close()
        # Final pass: if the agent emitted <deliver_assets>…</deliver_assets>
        # we fire one ``asset`` event so the frontend can show downloads.
        # Deduplicated per turn to avoid double-firing when the model
        # prints the XML mid-stream and again at the end.
        if not asset_emitted:
            full_text = "".join(agent_text_parts)
            paths = _parse_deliver_assets(full_text)
            if paths:
                yield {
                    "event": "asset",
                    "data": json.dumps({"paths": paths}, ensure_ascii=False),
                }
                asset_emitted = True

        # NEW: fire-and-forget 异步归档反射器
        # Agent 主响应已发出,后台异步跑 LLM 判定可复用经验,
        # 写入档案后通过 ActivityBus 推时间线事件给前端。
        # 这里再起一个 SSE 尾巴:把 reflector 推过来的 DOSSIER_ENTRY_ADDED
        # 事件经由本地 queue yield 出去,前端 ChatPanel 能看到"🤖 学到了"。
        # 异常隔离:失败不影响 SSE 流。
        try:
            import asyncio
            import logging
            from src.agent.dependencies import get_dossier_reflector
            from src.observability.activity_bus import (
                ActivityKind,
                get_activity_bus,
            )
            reflector = get_dossier_reflector()

            # 队列 + 临时订阅,把 reflector 的 DOSSIER_ENTRY_ADDED 转成 SSE
            dossier_q: asyncio.Queue = asyncio.Queue()

            async def _capture_dossier(event):
                if event.get("type") == ActivityKind.DOSSIER_ENTRY_ADDED:
                    await dossier_q.put(event)

            bus = get_activity_bus()
            await bus.subscribe(_capture_dossier)

            try:
                # 收集最近消息(尽量用 LangGraph 状态,fallback 用 req)
                recent = await _collect_recent_messages(
                    agent, config, req,
                    full_text="".join(agent_text_parts),
                )
                reflector_task = asyncio.create_task(
                    reflector.reflect(
                        domain=req.context or "",
                        messages=recent,
                        session_id=session_id,
                    )
                )
            except Exception as e:
                logging.getLogger(__name__).warning(
                    "dossier reflector setup failed: %s", e,
                )
                reflector_task = None

            # Drain queue:最多等 25 秒,期间 reflector 把事件 push 进来
            deadline = asyncio.get_event_loop().time() + 25.0
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    ev = await asyncio.wait_for(
                        dossier_q.get(), timeout=min(remaining, 1.0),
                    )
                    yield {
                        "event": "dossier-archived",
                        "data": json.dumps({
                            "domain": ev.get("domain", ""),
                            "node": ev.get("node", ""),
                            "title": ev.get("title", ""),
                            "extra": ev.get("extra", {}) or {},
                        }, ensure_ascii=False),
                    }
                except asyncio.TimeoutError:
                    if reflector_task and reflector_task.done():
                        # reflector 已结束,drain 剩余 queue
                        while not dossier_q.empty():
                            ev = dossier_q.get_nowait()
                            yield {
                                "event": "dossier-archived",
                                "data": json.dumps({
                                    "domain": ev.get("domain", ""),
                                    "node": ev.get("node", ""),
                                    "title": ev.get("title", ""),
                                    "extra": ev.get("extra", {}) or {},
                                }, ensure_ascii=False),
                            }
                        break
                    continue

            await bus.unsubscribe(_capture_dossier)
        except Exception as e:
            # 不要让归档失败影响主流程
            import logging
            logging.getLogger(__name__).warning(
                "dossier reflector fire-and-forget failed: %s", e,
            )


@router.post("/agent/invoke")
async def agent_invoke(req: AgentInvokeReq):
    """Stream agent events as SSE."""
    # Ensure the agent is built without blocking the event loop.
    # On normal operation the agent was pre-built during server startup
    # (see the lifespan handler in server.py), so this is a no-op.
    # The fallback ensure_agent_built() runs the build in a thread pool
    # so the event loop stays responsive even if the pre-build was
    # skipped or the cache was reset.
    await ensure_agent_built()
    status = get_agent_status()
    if not status["agent_available"]:
        raise HTTPException(
            status_code=503,
            detail=f"agent unavailable: {status['agent_error'] or 'unknown error'}",
        )
    return EventSourceResponse(_stream_agent(req))


@router.post("/agent/session", response_model=NewSessionResp)
async def agent_new_session() -> NewSessionResp:
    """Mint a new session id for the frontend's "新会话" button.

    The returned id is what the client should pass as ``thread_id``
    on the next ``/api/agent/invoke`` call.  We *don't* create a
    file here — the file is created lazily by ``ConversationLogger``
    on the first user message.  This endpoint just hands the caller
    a fresh, unique id so each "新会话" gets its own JSONL log.
    """
    from src.agent.memory import get_conversation_path

    sid = generate_session_id()
    # Ensure the date directory exists so the frontend can pre-warm it
    # if it wants to (currently nobody does).
    get_conversation_path(sid).parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    return NewSessionResp(
        session_id=sid,
        date=datetime.now().strftime("%Y-%m-%d"),
    )


__all__ = ["router"]
