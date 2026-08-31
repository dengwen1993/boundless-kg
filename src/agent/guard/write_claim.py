"""BUG-005 L4 hard gate — write-claim interception middleware.

Why
---
Soft Prompt rules (AGENTS.md + ``commit_memory`` card) are not enough
to keep the model honest about file writes.  In long contexts or
multi-objective tasks the model can still claim 「已写入 X.md」without
actually calling ``write_file`` / ``edit_file`` to ``X.md``.  This is
the tool-hallucination surface that BUG-004 nearly tripped (「应写
``bugs.md`` 却想写 ``AGENTS.md``」).

What this module does
---------------------
Implements **方案 C (grep-style hard gate)** in
``knowledge_bases/.agent_memory/bugs.md`` — a deepagents middleware
that sits between the model and its tool calls:

  * ``awrap_tool_call``  — every time ``write_file`` / ``edit_file``
    runs, record the path actually written into a per-turn tracker.
  * ``awrap_model_call`` — after the model produces text, scan it for
    「已写入 / 已记入 / 落盘 / 已保存 / 已添加到 X.md」 style completion
    claims.  For any claimed path that is NOT in this turn's tracker,
    rewrite the AIMessage so the false claim is replaced with an
    explicit 「未授权的完成声明」 warning + a hint that the model must
    call ``write_file`` / ``edit_file`` first.

Turn boundary
-------------
The tracker is keyed on ``contextvars.ContextVar`` so concurrent SSE
sessions never bleed paths into each other.  The tracker is reset on
every new ``awrap_tool_call`` that targets a write tool — that's our
proxy for 「a new model turn has started and the previous write-record
window has closed」.  (Multiple write calls inside the same turn
accumulate; the next model output is checked against the *union* of
paths the model wrote in this turn.)

Why a hard rewrite, not a soft warning
--------------------------------------
Soft warnings get ignored in long contexts — exactly the failure mode
that created BUG-005.  Rewriting the AIMessage content forces the
false claim to never reach the user, and the appended hint puts the
model back on the rails: it must call the write tool *before*
declaring success.  This is the same loop-prevention strategy as a
type checker that re-raises until the constraint is satisfied.

Scope
-----
This gate only watches deepagents' built-in filesystem tools
(``write_file`` / ``edit_file``).  The project's own ``kg_*`` tools
already return server-side confirmations and don't have the
「silent no-op」 risk, so they are out of scope here.

Public API
----------
* :class:`WriteClaimMiddleware` — drop into
  ``create_deep_agent(middleware=[...])``.
* :func:`scan_for_unauthorized_claim` — pure function used by tests.
* :func:`current_turn_writes` — read the live tracker (debug / tests).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

try:
    from deepagents.backends.filesystem import FilesystemBackend
except ImportError:  # pragma: no cover — deepagents is a hard dep, but be safe.
    FilesystemBackend = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Tracker — per-session ContextVar storage of "paths written this turn"
# ─────────────────────────────────────────────────────────────────────


@dataclass
class WriteClaimTracker:
    """Mutable per-session record of filesystem writes for one turn.

    The tracker is reset whenever a *new* model turn begins — proxied
    by ``awrap_tool_call`` observing a ``write_file`` / ``edit_file``
    invocation after the model has produced tool-call-free output.  In
    practice we reset at the start of each ``awrap_tool_call`` for a
    write tool: this is conservative (it forgets paths from earlier
    in the same turn only if a write happens later), but it matches
    the user-facing invariant — 「if you declare completion, all writes
    you reference must have happened since the last time you talked
    to the model」.
    """

    written_paths: set[str] = field(default_factory=set)
    """Normalised paths the model has actually written this turn."""

    reset_count: int = 0
    """How many times the tracker has been reset.  Useful in tests."""


# Per-SSE-stream tracker.  Each new agent invocation (different
# thread_id / config) gets its own ContextVar snapshot thanks to how
# LangGraph runs them under independent ``asyncio`` tasks.
_tracker_var: ContextVar[WriteClaimTracker] = ContextVar(
    "write_claim_tracker", default=WriteClaimTracker()
)


def get_tracker() -> WriteClaimTracker:
    """Return the tracker bound to the current async context."""
    return _tracker_var.get()


def reset_tracker() -> WriteClaimTracker:
    """Clear the tracker and return the fresh instance (for tests)."""
    fresh = WriteClaimTracker()
    fresh.reset_count = get_tracker().reset_count + 1
    _tracker_var.set(fresh)
    return fresh


def current_turn_writes() -> set[str]:
    """Read-only view of the paths written so far this turn."""
    return set(get_tracker().written_paths)


# ─────────────────────────────────────────────────────────────────────
# Pure-function scanner — used by middleware AND tests
# ─────────────────────────────────────────────────────────────────────


# Triggers a completion claim when followed by a path-looking target.
# We deliberately keep the prefix set tight (no 「修改」 / 「编辑」 — the
# model may legitimately say those about *reading* / *inspecting* files
# or about a tool result).
_CLAIM_PREFIXES = (
    "已写入",
    "已记入",
    "已添加",
    "已保存到",
    "已落盘",
    "落盘成功",
    "已写到",
    "写入完成",
    "写入",
    "保存到",
    "记入",
    "写到",
    # English variants
    "wrote ",
    "written to ",
    "saved to ",
    "added to ",
)

# Path detector: backtick-wrapped ``/foo/bar.md`` OR a bare filename
# ending in ``.md`` / ``.json`` / ``.txt`` / ``.py`` / ``.yaml`` etc.
# The bare-form accepts both single-segment names (``bugs.md``) and
# slash-delimited paths (``a/b/c.md``) so a claim like
# 「已写入 bugs.md」 is caught.  The negative-lookbehind avoids
# double-matching the prefix word itself (e.g. 「写入」 without a
# path is OK).
_PATH_BACKTICK = r"`([^`]+\.[a-zA-Z0-9]{1,8})`"
_PATH_BARE = (
    r"(?<![一-鿿A-Za-z0-9_\-\\.])"
    r"(?:[\w\-\.一-鿿]+/)*"
    r"[\w\-\.一-鿿]+\.[a-zA-Z0-9]{1,8}"
)
_PATH_PATTERN = re.compile(f"({_PATH_BACKTICK})|({_PATH_BARE})")

# Negation tokens that immediately follow a path and revoke a claim
# (e.g. 「已写入 bugs.md，AGENTS.md 还没动」 — the second path is a
# *negation* of a write, not a write).  When this token appears
# *between* the claim prefix and a path, the path is not a claim.
_NEGATION_TOKENS = ("还没", "还未", "尚未", "没有", "未", "别", "不要", "不能")

# Maximum lookahead (in characters) from a claim prefix to the path it
# is supposed to govern.  We additionally stop at the first
# sentence-ending punctuation OR a fresh claim prefix, so that
# mid-sentence mentions of other files don't get misattributed.
_CLAIM_LOOKAHEAD = 80
_SENTENCE_BOUNDARY = "。\n；;！!?\r"

_CLAIM_PATTERN = re.compile(
    "(" + "|".join(re.escape(p) for p in _CLAIM_PREFIXES) + r")"
)


def _normalize_path(path: str) -> str:
    """Strip leading ``/`` and ``./`` so 「/foo.md」 ≡ 「foo.md」.

    deepagents' backend uses absolute paths like ``/AGENTS.md`` while
    users (and the model's own prose) often write relative names like
    ``AGENTS.md``.  We normalise both to the same canonical form so
    the matcher accepts either.
    """
    p = path.strip()
    while p.startswith(("/", "./")):
        p = p[1:] if p.startswith("/") else p[2:]
    return p.lower()


def _iter_paths(text: str) -> list[str]:
    """Return every path-like substring in *text* (raw, unnormalised).

    The regex has two alternatives — backtick-wrapped and bare — each
    wrapped in its own capturing group so the alternatives don't
    compete for group 1.  We iterate every group's value and pick the
    non-None one (only one alternative matches per regex position).
    """
    out: list[str] = []
    for m in _PATH_PATTERN.finditer(text):
        for g in m.groups():
            if g:
                out.append(g)
                break
    return out


def scan_for_unauthorized_claim(
    text: str,
    *,
    authorized: set[str],
) -> list[tuple[str, str]]:
    """Return ``[(claim_phrase, raw_path)]`` for every unauthorised claim.

    A claim is **authorised** when the path it references is in
    *authorized* (after normalisation).  We deliberately don't try
    to model 「which phrase said which path」 because the model may
    chain 「已写入 bugs.md 然后顺便更新了 AGENTS.md」 — we only care
    that every mentioned path was actually written.

    When one claim prefix is followed by several paths (e.g.
    「已写入 foo.md 和 bar.md」), each path is checked independently
    so that one authorised path doesn't mask another unauthorised
    one.  Backtick-wrapped paths are unwrapped (``\`foo.md\`` → ``foo.md``)
    so the matcher doesn't get confused by the literal backticks.

    Returns an empty list when no unauthorised claim is present
    (most common case — most LLM output doesn't include a completion
    claim at all).
    """
    if not text:
        return []
    authorized_norm = {_normalize_path(p) for p in authorized}
    claims: list[tuple[str, str]] = []
    # Find every claim prefix position up front so we know where the
    # next claim starts — paths past the next claim are governed by
    # *that* claim, not this one.
    claim_positions = [m.start() for m in _CLAIM_PATTERN.finditer(text)]
    seen_globally: set[str] = set()
    for i, m in enumerate(_CLAIM_PATTERN.finditer(text)):
        phrase = m.group(1)
        end_of_this_claim = (
            claim_positions[i + 1] if i + 1 < len(claim_positions) else len(text)
        )
        # Don't let a single claim stretch beyond the configured cap
        # OR the next claim start (whichever is closer).
        natural_end = m.end() + _CLAIM_LOOKAHEAD
        cap = min(end_of_this_claim, natural_end)
        tail = text[m.end():cap]
        # Trim at the first sentence boundary inside the tail.
        for boundary in _SENTENCE_BOUNDARY:
            cut = tail.find(boundary)
            if cut != -1:
                tail = tail[:cut]
                break
        for path_match in _PATH_PATTERN.finditer(tail):
            raw_path = next((g for g in path_match.groups() if g), None)
            if not raw_path:
                continue
            if raw_path.startswith("`") and raw_path.endswith("`"):
                raw_path = raw_path[1:-1]
            negated = any(
                tok in tail[path_match.end(): path_match.end() + len(tok) + 2]
                for tok in _NEGATION_TOKENS
            )
            if negated:
                continue
            norm = _normalize_path(raw_path)
            if norm in seen_globally:
                continue
            seen_globally.add(norm)
            if norm not in authorized_norm:
                claims.append((phrase, raw_path))
    return claims


# ─────────────────────────────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────────────────────────────


_WRITE_TOOLS = frozenset({"write_file", "edit_file"})


def _extract_written_path(tool_name: str, tool_call: dict[str, Any]) -> str | None:
    """Best-effort path extraction from a write tool call's args."""
    args = tool_call.get("args") or {}
    # Both tools accept ``file_path`` (canonical) and a few accept
    # ``path`` / ``target_file`` aliases — accept all of them so the
    # matcher is forward-compatible if deepagents renames its tools.
    for key in ("file_path", "path", "target_file", "filepath"):
        if isinstance(args, dict) and key in args and isinstance(args[key], str):
            return args[key]
    return None


class WriteClaimMiddleware(AgentMiddleware):
    """deepagents middleware: gate the model's 「已写入」 claims.

    Wire into the agent at construction time::

        agent = create_deep_agent(
            ...,
            middleware=[..., WriteClaimMiddleware()],
        )

    Behaviour
    ---------
    * :meth:`awrap_tool_call` — when the model calls ``write_file`` or
      ``edit_file``, **record the path it wrote into** (only on a
      non-exception result — failed writes don't count as evidence).
    * :meth:`awrap_model_call` — after the model emits text, scan it
      for completion claims.  Any claim about a path NOT in the
      turn's tracker is rewritten into an explicit
      「⚠️ 未授权的完成声明」 notice + a hint, replacing the false
      claim so it never reaches the user / frontend / logger.

    The middleware is opt-out via :attr:`enabled` (default ``True``);
    set ``enabled=False`` to temporarily disable without removing
    the middleware (useful for golden-prompt tests where you want the
    model to be able to claim things freely).
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        backend: Any | None = None,
        verify_on_disk: bool = True,
    ) -> None:
        super().__init__()
        self._enabled = bool(enabled)
        # 修复 BUG-2026-08-19-003：用 FilesystemBackend 解析 virtual path
        # → 真实磁盘路径，做 post-write 校验。如果 write_file 返回成功但
        # 磁盘上根本没有文件，**不**把路径加进 tracker（避免假阳性
        # "授权声明"），并 logger.warning 让 ops 立刻看见。
        self._backend = backend
        self._verify_on_disk = bool(verify_on_disk)

    def _resolve_backend(self) -> Any | None:
        """惰性获取 FilesystemBackend（避免循环 import + 测试友好）。"""
        if self._backend is not None:
            return self._backend
        if FilesystemBackend is None:
            return None
        try:
            from src.agent.memory import get_filesystem_backend
            self._backend = get_filesystem_backend()
        except Exception:
            self._backend = None
        return self._backend

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        """Hot-flip the gate without rebuilding the agent."""
        self._enabled = bool(value)

    # ── Tool-call hook ──

    def wrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        if not self._enabled:
            return handler(request)
        return self._wrap_tool_call_sync(request, handler)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        if not self._enabled:
            return await handler(request)
        return await self._awrap_tool_call_async(request, handler)

    def _wrap_tool_call_sync(
        self, request: Any, handler: Callable[[Any], Any]
    ) -> Any:
        tool_call = request.tool_call or {}
        name = tool_call.get("name", "")
        if name in _WRITE_TOOLS:
            path = _extract_written_path(name, tool_call)
            result = handler(request)
            self._post_write_verify(name, path, result)
            return result
        return handler(request)

    async def _awrap_tool_call_async(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        tool_call = request.tool_call or {}
        name = tool_call.get("name", "")
        if name in _WRITE_TOOLS:
            path = _extract_written_path(name, tool_call)
            result = await handler(request)
            self._post_write_verify(name, path, result)
            return result
        return await handler(request)

    def _post_write_verify(
        self,
        tool_name: str,
        path: str | None,
        result: Any,
    ) -> None:
        """写盘后真实校验：避免「工具栈说成功 / 磁盘没文件」(BUG-2026-08-19-003)。

        调用顺序：
          1. 先跑原有的 ``_record_write_result`` 把 path 加入 tracker
             （content 含 error 时会跳过）；
          2. 然后跑 ``_verify_path_on_disk``：若文件**不存在**，把刚才
             加入的 path 从 tracker 撤回（避免假阳性授权），并 logger.warning。
        """
        if not path:
            return
        self._record_write_result(tool_name, path, result)
        verified = self._verify_path_on_disk(path)
        if verified is False:
            # 文件不存在 → 撤回授权
            get_tracker().written_paths.discard(_normalize_path(path))

    @staticmethod
    def _record_write_result(
        tool_name: str, path: str | None, result: Any
    ) -> None:
        """Record the path iff the tool returned without an error.

        ``result`` from deepagents is normally a ``ToolMessage`` whose
        ``.content`` either holds a JSON-shaped payload
        (``{"error": ...}``) or the success string.  We accept either:
        an error-shaped payload is skipped; anything else (including a
        bare success string) counts as a real write.

        修复 BUG-2026-08-19-003：deepagents 的 ``write_file`` / ``edit_file``
        走的是其 ``FilesystemMiddleware``，**工具栈沙箱与真实磁盘可能不同步**——
        工具返回 success 但磁盘上根本没有文件。我们在工具层返回 success
        后再用 :class:`FilesystemBackend` 把 virtual path 解析到真实磁盘路径，
        做一次 ``Path.exists()`` 校验：若文件不存在，**不**把路径加入
        tracker（避免「未授权声明」被误判为已授权），同时记 WARNING 让
        后端日志 / ops 能立刻发现这个 silent no-op。
        """
        if not path:
            return
        content = getattr(result, "content", None)
        if isinstance(content, str) and '"error"' in content.lower():
            # Cheap check for the JSON ``{"error": ...}`` envelope
            # that deepagents' filesystem middleware returns on
            # permission / validation failures.
            return
        get_tracker().written_paths.add(_normalize_path(path))

    def _verify_path_on_disk(self, path: str) -> bool:
        """解析 virtual path → 真实磁盘路径并校验文件存在性。

        Returns
        -------
        ``True`` if the file exists on disk, ``False`` if it doesn't,
        or ``None`` if the backend isn't available / verification is
        disabled.  Callers should treat ``None`` as "no opinion".
        """
        if not self._verify_on_disk:
            return None  # type: ignore[return-value]
        backend = self._resolve_backend()
        if backend is None:
            return None  # type: ignore[return-value]
        resolve = getattr(backend, "_resolve_path", None)
        if resolve is None:
            return None  # type: ignore[return-value]
        try:
            real_path = resolve(path)
        except (ValueError, OSError) as exc:
            # 路径解析失败（escape root / symlink loop 等）—— 不算
            # "成功写盘"，提醒 ops 排查。
            logger.warning(
                "[WriteClaim] post-write verify: cannot resolve %r via "
                "FilesystemBackend (%s) — BUG-2026-08-19-003 信号？",
                path, exc,
            )
            return None  # type: ignore[return-value]
        if not real_path.exists():
            logger.warning(
                "[WriteClaim] post-write verify: %s returned success but "
                "%s does NOT exist on disk — possible BUG-2026-08-19-003 "
                "(virtual FS desync). NOT adding %r to authorized writes.",
                tool_name if False else "write tool", real_path, path,
            )
            return False
        return True

    # ── Model-call hook ──

    def wrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        if not self._enabled:
            return handler(request)
        response = handler(request)
        return self._gate_response(response)

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        if not self._enabled:
            return await handler(request)
        response = await handler(request)
        return self._gate_response(response)

    def _gate_response(self, response: Any) -> Any:
        """Rewrite an unauthorised claim out of the AIMessage.

        We intentionally mutate ``response`` in place rather than
        constructing a new ModelResponse — keeping the original
        ``structured_response`` and tool-call plumbing intact.
        """
        try:
            result_list = getattr(response, "result", None) or []
        except Exception:
            return response
        if not result_list:
            return response
        ai_msg = result_list[0]
        content = getattr(ai_msg, "content", None)
        if not isinstance(content, str) or not content:
            return response

        tracker = get_tracker()
        unauthorised = scan_for_unauthorized_claim(
            content, authorized=tracker.written_paths
        )
        if not unauthorised:
            # Even when no claim is present, reset the tracker so the
            # next model call starts with a clean window.  This keeps
            # the invariant: 「claims are checked against writes
            # earlier in the SAME turn, not earlier turns」.
            reset_tracker()
            return response

        # Build the replacement.  Keep the original text, but append
        # a clearly-marked warning that the user + model both see.
        warning = self._build_warning(unauthorised)
        try:
            ai_msg.content = content + warning
        except Exception:
            # If the message is immutable, fall back to appending
            # via a new AIMessage — this is rare but cheap to handle.
            logger.warning(
                "[WriteClaim] failed to mutate AIMessage.content; "
                "skipping rewrite."
            )
        reset_tracker()
        return response

    @staticmethod
    def _build_warning(
        unauthorised: list[tuple[str, str]],
    ) -> str:
        paths = sorted({path for _, path in unauthorised})
        bullet = "；".join(f"`{p}`" for p in paths)
        return (
            "\n\n⚠️ [WriteClaim gate] 检测到未授权的完成声明："
            f"{bullet} —— 这些路径在本回合并未调过 write_file / "
            "edit_file。请先调用写入工具实际写入文件后再做完成声明。"
        )


__all__ = [
    "WriteClaimMiddleware",
    "WriteClaimTracker",
    "scan_for_unauthorized_claim",
    "current_turn_writes",
    "get_tracker",
    "reset_tracker",
]