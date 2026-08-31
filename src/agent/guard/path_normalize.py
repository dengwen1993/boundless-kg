"""BUG-2026-08-19-003 L4 hard gate — absolute-path normalization middleware.

Why
---
deepagents' built-in ``FilesystemMiddleware`` (a.k.a. ``write_file`` /
``edit_file``) silently misroutes writes when the path starts with
``/home/wend/boundless_kg/workspace/`` — it treats the absolute path as
relative to the agent's ``cwd`` and creates a nested
``workspace/home/wend/boundless_kg/workspace/...`` tree instead of
writing where the user expects.  This is the exact symptom that created
BUG-2026-08-19-003 (``cordis_quiz.html`` written under the wrong
workspace tree).

This module implements the **tool-level hard gate** for BUG-2026-08-19-003:

  * ``_normalize_path()`` — pure function that strips a fixed set of
    absolute prefixes the agent commonly uses incorrectly
    (``/home/wend/boundless_kg/workspace/`` and its trailing-slashless
    variant).  Anything else is returned untouched so legitimate
    absolute paths elsewhere on the host still work.
  * ``PathNormalizeMiddleware`` — a deepagents ``AgentMiddleware`` that
    intercepts ``write_file`` / ``edit_file`` calls BEFORE they reach
    the filesystem layer and rewrites the offending path arg in place.

L1 vs L4 split
--------------
- **L1 (AGENTS.md rule)**: tells the model 「do not pass absolute paths
  to ``write_file``」.
- **L4 (this module)**: enforces the rule at the tool layer so the
  model can't slip through even if it ignores / forgets L1.

Why both
--------
Models forget prompt rules under long-context / multi-tool pressure —
the same failure mode that produced BUG-2026-08-19-003.  The middleware
here is the safety net.

Public API
----------
* :class:`PathNormalizeMiddleware` — drop into ``create_deep_agent(
  middleware=[...])``.  Inserted **before** ``WriteClaimMiddleware`` so
  the claim-tracker sees the canonical (relative) path the file
  actually landed at.
* :func:`_normalize_path` — pure function used by middleware AND tests.

Scope
-----
Only ``write_file`` / ``edit_file`` from deepagents' built-in
filesystem middleware.  The project's ``kg_*`` tools already accept
logical node names and route through the kg_engine backend; they don't
have the 「absolute-path-as-cwd-relative」 hazard.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Pure-function path normaliser
# ─────────────────────────────────────────────────────────────────────


#: Absolute prefixes deepagents misroutes.  Order matters: longest match
#: wins (we iterate in the list below), so the trailing-slash variant is
#: listed FIRST so that ``/home/wend/boundless_kg/workspace`` (no
#: trailing slash, exact-prefix case) collapses to ``""`` instead of
#: getting the slash-prefix branch to leave a stray ``"/"``.
#:
#: 2026-08-27 expanded to cover the container-side absolute paths the
#: agent commonly slipped through with (`/app/workspace`, `/workspace`,
#: `/data/workspace`) which previously produced the duplicate-write
#: bug tracked in ``/lessons.md`` T-017. The list is intentionally a
#: pure data structure so adding/removing sites needs no code change.
_ABS_PREFIXES: tuple[str, ...] = (
    "/home/wend/boundless_kg/workspace",
    "/home/wend/boundless_kg/workspace/",
    "/app/workspace",
    "/app/workspace/",
    "/data/workspace",
    "/data/workspace/",
    "/workspace",
    "/workspace/",
)


def _normalize_path(file_path: str) -> str:
    """Strip a known absolute prefix and return the relative path.

    Examples
    --------
    >>> _normalize_path("/home/wend/boundless_kg/workspace/AGENTS.md")
    'AGENTS.md'
    >>> _normalize_path("/app/workspace/knowledge_bases/foo/notes/x/note.md")
    'knowledge_bases/foo/notes/x/note.md'
    >>> _normalize_path("/workspace/lessons.md")
    'lessons.md'
    >>> _normalize_path("/home/wend/boundless_kg/workspace/")
    ''
    >>> _normalize_path("/home/wend/boundless_kg/workspace")
    ''
    >>> _normalize_path("AGENTS.md")
    'AGENTS.md'
    >>> _normalize_path("/home/wend/boundless_kg/workspaceX/AGENTS.md")
    '/home/wend/boundless_kg/workspaceX/AGENTS.md'  # similar but not our prefix
    >>> _normalize_path("/tmp/x")
    '/tmp/x'  # outside our prefix — left alone

    Args:
        file_path: The path passed to ``write_file`` / ``edit_file``.

    Returns:
        The relative path inside the workspace, or the original string
        untouched if it doesn't match any known absolute prefix.
    """
    if not file_path:
        return file_path
    p = file_path
    # Match the prefix with a strict boundary: either the path IS the
    # prefix, or the prefix is followed by ``/``.  ``startswith`` alone
    # would misfire on ``/home/wend/boundless_kg/workspaceX/foo.md``
    # (which should be left alone).
    for prefix in _ABS_PREFIXES:
        if p == prefix:
            return ""
        if p.startswith(prefix + "/"):
            rel = p[len(prefix):]
            # Always strip a leading ``/`` so the result never starts
            # with one — relative paths inside the workspace should
            # look the same whether the caller used a slash or not.
            return rel.lstrip("/")
    return p


# ─────────────────────────────────────────────────────────────────────
# Path-extraction helpers (mirror write_claim's style)
# ─────────────────────────────────────────────────────────────────────


#: Tools whose ``file_path``-like arg we want to normalise.  Mirrors
#: ``write_claim.py``'s ``_WRITE_TOOLS`` so we keep them in lockstep.
_WRITE_TOOLS = frozenset({"write_file", "edit_file"})

#: Possible names of the path-bearing argument.  ``file_path`` is the
#: canonical key deepagents uses; the others are accepted for
#: forward-compat with SDK renames.
_PATH_KEYS = ("file_path", "path", "target_file", "filepath")


def _extract_path_arg(args: dict[str, Any] | None) -> str | None:
    """Best-effort path extraction from a write tool call's args.

    Returns the FIRST matching key's value (string only — non-string
    args are skipped because we can't safely rewrite them).
    """
    if not isinstance(args, dict):
        return None
    for key in _PATH_KEYS:
        if key in args and isinstance(args[key], str):
            return args[key]
    return None


# ─────────────────────────────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────────────────────────────


class PathNormalizeMiddleware(AgentMiddleware):
    """deepagents middleware: strip the buggy ``/home/.../workspace/`` prefix.

    Wire into the agent BEFORE ``WriteClaimMiddleware`` so the tracker
    records the canonical path the file actually landed at::

        agent = create_deep_agent(
            ...,
            middleware=[
                ...,
                PathNormalizeMiddleware(),     # ← here
                WriteClaimMiddleware(),
                ...,
            ],
        )

    Behaviour
    ---------
    * :meth:`awrap_tool_call` — when the model calls ``write_file`` or
      ``edit_file``, look up the path arg and rewrite it via
      :func:`_normalize_path`.  The rewrite is logged at INFO so the
      operator can see what got coerced (and so the next BUG-2026-08-19
      -style incident leaves a breadcrumb).

    The middleware is opt-out via :attr:`enabled` (default ``True``);
    set ``enabled=False`` to temporarily disable without removing the
    middleware (useful for tests that want the model free to write
    absolute paths).
    """

    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__()
        self._enabled = bool(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        """Hot-flip the gate without rebuilding the agent."""
        self._enabled = bool(value)

    # ── Tool-call hooks ──

    def wrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        if not self._enabled:
            return handler(request)
        self._maybe_rewrite(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        if not self._enabled:
            return await handler(request)
        self._maybe_rewrite(request)
        return await handler(request)

    @staticmethod
    def _maybe_rewrite(request: Any) -> None:
        """Rewrite the path arg in-place if it matches a known prefix."""
        try:
            tool_call = request.tool_call or {}
        except AttributeError:
            return
        name = tool_call.get("name", "")
        if name not in _WRITE_TOOLS:
            return
        args = tool_call.get("args")
        if not isinstance(args, dict):
            return
        original = _extract_path_arg(args)
        if original is None:
            return
        normalized = _normalize_path(original)
        if normalized == original:
            return
        # Find which key held the path and rewrite in place.
        for key in _PATH_KEYS:
            if key in args and isinstance(args[key], str) and args[key] == original:
                args[key] = normalized
                break
        logger.info(
            "PathNormalizeMiddleware: rewrote %s -> %r (tool=%s)",
            original, normalized, name,
        )


__all__ = [
    "PathNormalizeMiddleware",
    "_normalize_path",
]