"""CardService — CRUD over agent prompt cards.

Cards live as ``*.md`` files with YAML front-matter (see
:mod:`src.agent.cards.models`). This service wraps file I/O so that the
agent itself can create / update / delete cards at runtime via
``kg_add_card`` / ``kg_delete_card`` tools — no code change or restart
needed.

After every mutation the service calls :meth:`CardLibrary.reload` so
the in-memory library (and thus the :class:`CardsMiddleware`) picks up
the change for the next model call.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.agent.cards.library import CardLibrary
from src.agent.cards.models import Card

# Card ids must be filesystem-safe: ascii alphanumeric + dash + underscore.
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


class CardService:
    """Create / read / list / delete card files.

    Args:
        library: The shared :class:`CardLibrary` instance. The service
            reloads it after every mutation so the middleware sees the
            change immediately.
    """

    def __init__(self, library: CardLibrary) -> None:
        self._library = library

    @property
    def cards_dir(self) -> Path:
        """The directory cards are stored in."""
        d = self._library.source_dir
        if d is None:
            raise RuntimeError(
                "CardLibrary has no source_dir — cannot persist cards. "
                "Ensure the library was built via from_directory()."
            )
        return d

    # ── Create / Update ───────────────────────────────────────────────

    async def upsert(
        self,
        *,
        card_id: str,
        title: str,
        body: str,
        triggers: list[str] | None = None,
        applies_to_tools: list[str] | None = None,
        priority: int = 100,
    ) -> dict[str, Any]:
        """Create or overwrite a card file.

        Returns a dict with the card's metadata + ``created`` (bool)
        indicating whether it was a new file or an overwrite.
        """
        card_id = (card_id or "").strip()
        if not card_id:
            raise ValueError("card_id 不能为空")
        if not _SAFE_ID_RE.match(card_id):
            raise ValueError(
                f"card_id 只能包含字母、数字、下划线和连字符，收到: {card_id!r}"
            )
        if not title.strip():
            raise ValueError("title 不能为空")
        if not body.strip():
            raise ValueError("body 不能为空")

        triggers = [t.strip() for t in (triggers or []) if t.strip()]
        applies = [t.strip() for t in (applies_to_tools or []) if t.strip()]
        priority = max(0, int(priority))

        card = Card(
            id=card_id,
            title=title.strip(),
            triggers=tuple(triggers),
            applies_to_tools=tuple(applies),
            priority=priority,
            body=body.strip(),
        )

        self.cards_dir.mkdir(parents=True, exist_ok=True)
        path = self.cards_dir / f"{card_id}.md"
        created = not path.exists()
        path.write_text(card.to_markdown(), encoding="utf-8")

        self._library.reload()

        return {
            "id": card.id,
            "title": card.title,
            "triggers": list(card.triggers),
            "applies_to_tools": list(card.applies_to_tools),
            "priority": card.priority,
            "body_chars": len(card.body),
            "source_path": str(path),
            "created": created,
            "active_count": len(self._library),
        }

    # ── Read ──────────────────────────────────────────────────────────

    async def list_cards(self) -> list[dict[str, Any]]:
        """Return summary info for every loaded card."""
        return [
            {
                "id": c.id,
                "title": c.title,
                "triggers": list(c.triggers),
                "applies_to_tools": list(c.applies_to_tools),
                "priority": c.priority,
                "body_chars": len(c.body),
                "source_path": c.source_path,
            }
            for c in self._library.all()
        ]

    async def get_card(self, card_id: str) -> dict[str, Any] | None:
        """Full detail for one card (including body). None if not found."""
        card = self._library.get(card_id.strip())
        if card is None:
            return None
        return {
            "id": card.id,
            "title": card.title,
            "triggers": list(card.triggers),
            "applies_to_tools": list(card.applies_to_tools),
            "priority": card.priority,
            "body": card.body,
            "source_path": card.source_path,
        }

    # ── Delete ────────────────────────────────────────────────────────

    async def delete(self, card_id: str) -> dict[str, Any]:
        """Delete a card file. Returns a summary dict."""
        card_id = (card_id or "").strip()
        if not card_id:
            raise ValueError("card_id 不能为空")
        if not _SAFE_ID_RE.match(card_id):
            raise ValueError(f"card_id 格式无效: {card_id!r}")

        path = self.cards_dir / f"{card_id}.md"
        if not path.exists():
            return {"id": card_id, "deleted": False, "error": "card file not found"}

        path.unlink()
        self._library.reload()

        return {
            "id": card_id,
            "deleted": True,
            "active_count": len(self._library),
        }

    # ── Reload ─────────────────────────────────────────────────────────

    async def reload(self) -> int:
        """Force a library reload from disk. Returns card count."""
        return self._library.reload()


__all__ = ["CardService"]
