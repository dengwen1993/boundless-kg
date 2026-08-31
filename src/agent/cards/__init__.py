"""Cards package — dynamic per-turn prompt augmentation.

Public surface:

  * :class:`Card`                — single card data model
  * :class:`CardLibrary`         — load + query all cards
  * :func:`select_cards`         — pick active cards from user message + used tools
  * :func:`render_cards`         — format active cards into a SystemMessage fragment
  * :func:`extract_used_tools`   — helper for parsing chat history
  * :func:`last_user_message`    — helper for pulling the latest user turn
  * :class:`CardsMiddleware`     — deepagents-compatible middleware that injects cards
"""

from __future__ import annotations

from .library import CardLibrary
from .middleware import CardsMiddleware
from .models import Card, CardParseError, parse_card
from .selector import (
    extract_used_tools,
    last_user_message,
    render_cards,
    select_cards,
)

__all__ = [
    "Card",
    "CardLibrary",
    "CardParseError",
    "CardsMiddleware",
    "extract_used_tools",
    "last_user_message",
    "parse_card",
    "render_cards",
    "select_cards",
]
