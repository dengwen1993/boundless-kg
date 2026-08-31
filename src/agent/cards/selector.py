"""Card selection — turns (user message, used tools) into a list of active cards.

Selector is a pure function over :class:`CardLibrary`; keeping it free of LLM
calls and side effects means it's trivially testable and the v2 LLM-based
selector can be slotted in behind the same signature later.

Activation rule (v1 — keyword ∪ tool-stack):

    A card is active if ANY of:
      * at least one of its ``triggers`` appears as a substring of the user
        message (case-sensitive — Chinese triggers dominate this codebase), OR
      * at least one of its ``applies_to_tools`` appears in the used-tools set.

The union (OR) is intentional. The alternative — requiring BOTH signals to
match — sounds conservative but empirically hurts coverage: the user often
*talks* about a tool before the agent has actually called it. We rely on
specific keywords / specific tool names to keep false-positives low rather
than stacking signals.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from .library import CardLibrary
from .models import Card

logger = logging.getLogger(__name__)


def select_cards(
    library: CardLibrary,
    *,
    user_message: str = "",
    used_tools: Iterable[str] = (),
) -> list[Card]:
    """Return every card that matches *user_message* OR any of *used_tools*.

    Ordering matches :meth:`CardLibrary.__iter__` (priority asc, then id asc).
    Empty result ⇒ caller renders no extra fragment.
    """
    used_tools_tuple = tuple(used_tools)
    if not library.all() or (not user_message and not used_tools_tuple):
        return []

    active = [
        card
        for card in library.all()
        if card.matches_user_message(user_message)
        or card.matches_used_tools(used_tools_tuple)
    ]

    if active:
        logger.info(
            "[Cards] activated %d / %d (user_msg_len=%d, tools=%s): %s",
            len(active),
            len(library),
            len(user_message),
            ",".join(used_tools_tuple) or "<none>",
            ", ".join(c.id for c in active),
        )
    return active


def extract_used_tools(messages: Iterable[object]) -> list[str]:
    """Walk a chat-history messages list and return unique tool names used.

    The list may contain LangChain ``BaseMessage`` instances; we tolerate any
    iterable of objects carrying ``.tool_calls`` (AIMessage) — past tool
    calls are how we infer which cards the conversation has been about.

    Order is the order of first appearance (set semantics, list storage).
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for msg in messages:
        for call in getattr(msg, "tool_calls", None) or ():
            name = call.get("name") if isinstance(call, dict) else None
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
    return ordered


def last_user_message(messages: Iterable[object]) -> str:
    """Return the text of the last HumanMessage in *messages*.

    Falls back to ``""`` for empty / unknown shapes. Used as the user-message
    signal for keyword matching.
    """
    last = ""
    for msg in messages:
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content:
            last = content
        elif isinstance(content, list):
            for block in content:
                text = block.get("text") if isinstance(block, dict) else None
                if isinstance(text, str) and text:
                    last = text
    return last


def render_cards(cards: Iterable[Card]) -> str:
    """Concatenate cards into the SystemMessage fragment to inject.

    Each card gets a header line so the model can attribute guidance; body
    is concatenated verbatim. Empty input returns ``""`` — caller should
    skip the append in that case to avoid spurious whitespace in the prompt.
    """
    cards = list(cards)
    if not cards:
        return ""

    parts = ["<active_cards>"]
    for card in cards:
        parts.append(f"\n## {card.title} ({card.id})\n")
        parts.append(card.body.rstrip())
    parts.append("\n</active_cards>")
    return "\n".join(parts)


__all__ = ["select_cards", "extract_used_tools", "last_user_message", "render_cards"]
