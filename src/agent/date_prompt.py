"""Per-turn 「今天是几月几号」 injection.

Why a middleware instead of a f-string in :mod:`src.agent.system_prompt`
------------------------------------------------------------------------
``SYSTEM_PROMPT`` is a module-level constant and the agent is built once
then cached for the whole process lifetime (see ``_agent_holder`` in
:mod:`src.agent.orchestrator`).  Formatting the date into the constant
would freeze it at import time — a backend that stays up over midnight
would keep telling the model it is still yesterday, and plan dates
(``kg_add_plan(date=...)``) would silently land on the wrong day.

Appending the date on every model call keeps it correct without
restarts, and reuses the same ``append_to_system_message`` mechanism as
:class:`~src.agent.cards.CardsMiddleware` so the fragments compose
additively rather than clobbering each other.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger(__name__)

# ``datetime.weekday()`` is Monday-indexed.
_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def today_fragment(today: date | None = None) -> str:
    """Render the date block appended to the SystemMessage.

    Args:
        today: Override for tests. Defaults to the server's local date —
            same clock as ``PlanService`` / ``ConversationLogger``, so the
            model's notion of 「今天」 matches what those write to disk.
    """
    d = today or datetime.now().date()
    return (
        "## 当前日期\n\n"
        f"今天是 {d.year} 年 {d.month} 月 {d.day} 日（{d.isoformat()}，"
        f"{_WEEKDAYS[d.weekday()]}）。\n"
        "用户说「今天 / 明天 / 本周 / 三天后」时，以此为基准换算成具体日期，"
        "不要猜，也不要反问用户今天几号。写入计划的 `date` 参数用 "
        "`YYYY-MM-DD` 格式。"
    )


class DateContextMiddleware(AgentMiddleware):
    """Append today's date to the SystemMessage on every model call."""

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._augment(request))

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        return await handler(self._augment(request))

    def _augment(self, request: Any) -> Any:
        fragment = today_fragment()
        new_system_message = append_to_system_message(request.system_message, fragment)
        logger.debug("[Date] request.override system_message += %d chars", len(fragment))
        return request.override(system_message=new_system_message)


__all__ = ["DateContextMiddleware", "today_fragment"]
