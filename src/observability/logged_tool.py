"""Decorator: ``@logged_tool`` — auto-log every exception raised by an
async tool function and turn it into a uniform error string returned to
the LLM agent loop.

Why
---

Per-tool ``try/except Exception: return f"❌ ... 失败: {e}"`` blocks
silently swallow errors so the LLM never knows anything went wrong, and
ops has no on-disk record to debug from.  This decorator centralises
both behaviours:

  * logs the exception at ``ERROR`` level (lands in ``logs/error.log``)
  * returns ``"❌ <tool_qualname> 失败: <ExceptionClass>: <message>"``
    so the LLM loop stays alive but is informed

How
---

::

    from langchain_core.tools import tool
    from src.observability.logged_tool import logged_tool

    @tool
    @logged_tool
    async def kg_xxx(...) -> str:
        ...

``@logged_tool`` is applied **first** (closest to the function).  The
result is still an async function with the same signature (via
``functools.wraps``), so langchain's ``@tool`` introspects it
unchanged.

Sync functions pass through unchanged — every tool in this codebase is
async today; if you add a sync tool, decorate it with a sync-aware
``try/except`` until this decorator grows an async-or-sync branch.
"""

from __future__ import annotations

import inspect
import logging
from functools import wraps

logger = logging.getLogger("src.agent.tools")


def logged_tool(func):
    """Catch any exception from ``func``, log it, return a uniform error string.

    Idempotent against double-application: if ``func`` already carries
    :attr:`__logged_tool_wrapped__`, it's returned as-is.
    """
    if getattr(func, "__logged_tool_wrapped__", False):
        return func

    if not inspect.iscoroutinefunction(func):
        # Sync tools are pass-through — keeps decorator safe for legacy
        # / utility callers that don't need exception logging.
        return func

    @wraps(func)
    async def _inner(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(
                "%s failed | args=%r kwargs=%r",
                func.__qualname__,
                args,
                kwargs,
                exc_info=True,
            )
            return f"❌ {func.__qualname__} 失败: {type(e).__name__}: {e}"

    _inner.__logged_tool_wrapped__ = True  # type: ignore[attr-defined]
    return _inner


__all__ = ["logged_tool"]
