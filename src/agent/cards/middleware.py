"""CardsMiddleware — per-turn SystemMessage augmentation.

Sits in the deepagents middleware chain alongside :class:`MemoryMiddleware`.
On every model call:

  1. Reads the current message history from ``request.state``.
  2. Extracts the most recent user message and the set of tools invoked so far.
  3. Asks :func:`select_cards` for the active cards.
  4. Renders them via :func:`render_cards` and appends to the SystemMessage
     using deepagents' :func:`append_to_system_message` utility — the same
     mechanism ``MemoryMiddleware`` uses, so multiple middleware compose
     additively (no clobbering).

Phase-1 default: ``enabled = False`` ⇒ the middleware is registered but is a
no-op until ops flip ``KG_AGENT_CARDS_ENABLED=true``. Keeps the diff small
during rollout.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import AgentMiddleware

from .library import CardLibrary
from .selector import (
    extract_used_tools,
    last_user_message,
    render_cards,
    select_cards,
)

logger = logging.getLogger(__name__)


def _default_cards_dir() -> Path:
    """Return the package-bundled cards directory.

    Co-located with this file so distribution is trivial: drop a ``*.md`` into
    ``src/agent/cards/data/`` and it will be picked up.
    """
    return Path(__file__).parent / "data"


class CardsMiddleware(AgentMiddleware):
    """Inject intent-selected prompt cards into each model call.

    Subclasses langchain's :class:`AgentMiddleware` so it slots straight into
    ``create_deep_agent(middleware=[...])`` alongside ``MemoryMiddleware``.
    We keep the optional state schema lightweight — no per-turn card cache
    yet, that comes in Phase 4 if ever.
    """

    def __init__(
        self,
        library: CardLibrary | None = None,
        *,
        enabled: bool = False,
        cards_dir: Path | None = None,
    ) -> None:
        """Initialize the middleware.

        Args:
            library: Pre-loaded card collection. When ``None``, loads from
                ``cards_dir`` (or the package default).
            enabled: Master switch. When ``False``, every hook short-circuits
                and the SystemMessage is unchanged. Defaults to ``False``
                for Phase-1 rollout safety.
            cards_dir: Directory to scan for ``*.md`` cards. Ignored if
                *library* is provided.
        """
        super().__init__()
        if library is None:
            library = CardLibrary.from_directory(cards_dir or _default_cards_dir())
        self._library = library
        self._enabled = bool(enabled)

    # ── Properties ────────────────────────────────────────────────────

    @property
    def library(self) -> CardLibrary:
        return self._library

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        """Hot-flip the switch — useful from tests + reload scenarios."""
        self._enabled = bool(value)

    def reload(self) -> int:
        """Hot-reload the card library from disk.

        Called after a card is added / deleted via ``kg_add_card`` /
        ``kg_delete_card`` so the change takes effect on the next model
        call without restarting the process. Returns the new card count.
        """
        return self._library.reload()

    # ── Hooks ─────────────────────────────────────────────────────────

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        """Sync model-call interception. Pass-through when disabled."""
        if not self._enabled:
            return handler(request)
        new_request = self._augment(request)
        return handler(new_request)

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        """Async model-call interception. Pass-through when disabled."""
        if not self._enabled:
            return await handler(request)
        new_request = self._augment(request)
        return await handler(new_request)

    # ── Internals ─────────────────────────────────────────────────────

    def _augment(self, request: Any) -> Any:
        """Build a new request with the active-cards fragment appended."""
        messages = (request.state or {}).get("messages", []) or []
        user_msg = last_user_message(messages)
        used = extract_used_tools(messages)

        active = select_cards(
            self._library,
            user_message=user_msg,
            used_tools=used,
        )
        if not active:
            return request

        fragment = render_cards(active)
        if not fragment:
            return request

        new_system_message = append_to_system_message(request.system_message, fragment)
        logger.debug(
            "[Cards] request.override system_message += %d chars (%s)",
            len(fragment),
            ", ".join(c.id for c in active),
        )
        return request.override(system_message=new_system_message)


__all__ = ["CardsMiddleware"]
