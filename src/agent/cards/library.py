"""CardLibrary — loads all ``.md`` cards from a directory into memory.

Loaded once per process; the orchestrator caches the library on the middleware
instance so card enumeration is cheap (one O(n_cards) filter per LLM request).

Adding a new card is just dropping a file under the cards directory — no code
change required for the data side. To wire its trigger into the agent you
declare the file's front-matter (triggers / applies_to_tools); the selector
picks it up automatically.

The library is also **hot-reloadable**: after the agent writes or deletes a
card file via ``kg_add_card`` / ``kg_delete_card``, calling :meth:`reload`
re-reads the directory so the change takes effect on the very next model call
— no restart needed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .models import Card, CardParseError, parse_card

logger = logging.getLogger(__name__)


class CardLibrary:
    """An eagerly-loaded collection of :class:`Card` objects.

    The internal card tuple can be replaced at runtime via :meth:`reload`,
    so that cards added / deleted through the agent's ``kg_add_card`` /
    ``kg_delete_card`` tools take effect on the very next model call
    — without restarting the process.
    """

    def __init__(
        self,
        cards: tuple[Card, ...] = (),
        *,
        source_dir: Path | None = None,
    ):
        # Stable ordering: priority asc, then id asc. This becomes the render
        # order when multiple cards are active in a single request.
        self._cards: tuple[Card, ...] = tuple(
            sorted(cards, key=lambda c: (c.priority, c.id))
        )
        self._source_dir = source_dir

    # ── Public API ──────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._cards)

    def __iter__(self):
        return iter(self._cards)

    def all(self) -> tuple[Card, ...]:
        """Return every card (defensive copy)."""
        return self._cards

    def get(self, card_id: str) -> Card | None:
        """Look up a card by its stable id (None if not loaded)."""
        for c in self._cards:
            if c.id == card_id:
                return c
        return None

    @property
    def source_dir(self) -> Path | None:
        """The directory this library was loaded from (None for ad-hoc)."""
        return self._source_dir

    def reload(self) -> int:
        """Re-read the source directory and replace the internal card tuple.

        Called after the agent writes / deletes a card file via
        ``kg_add_card`` / ``kg_delete_card``. Returns the new card count.
        """
        if self._source_dir is None:
            logger.warning("[Cards] reload() skipped — no source_dir tracked")
            return len(self._cards)
        if not self._source_dir.exists():
            self._cards = ()
            return 0

        cards: list[Card] = []
        for entry in sorted(self._source_dir.glob("*.md")):
            try:
                cards.append(parse_card(entry))
            except CardParseError as exc:
                logger.warning("[Cards] skipping %s: %s", entry, exc)
        self._cards = tuple(sorted(cards, key=lambda c: (c.priority, c.id)))
        logger.info(
            "[Cards] reloaded %d card(s) from %s: %s",
            len(self._cards),
            self._source_dir,
            ", ".join(c.id for c in self._cards),
        )
        return len(self._cards)

    # ── Construction ────────────────────────────────────────────────────

    @classmethod
    def from_directory(cls, dir_path: Path) -> "CardLibrary":
        """Load every ``*.md`` file in *dir_path* (non-recursive).

        Errors are logged and the offending file is skipped — one bad card
        must not break the whole library. Returns an empty library when the
        directory does not exist (logged at INFO so missing data is visible
        in tests).
        """
        if not dir_path.exists():
            logger.info("[Cards] cards directory %s does not exist; library is empty", dir_path)
            return cls((), source_dir=dir_path)

        cards: list[Card] = []
        for entry in sorted(dir_path.glob("*.md")):
            try:
                cards.append(parse_card(entry))
            except CardParseError as exc:
                logger.warning("[Cards] skipping %s: %s", entry, exc)
        if not cards:
            logger.info("[Cards] no cards loaded from %s", dir_path)
        else:
            logger.info(
                "[Cards] loaded %d card(s) from %s: %s",
                len(cards),
                dir_path,
                ", ".join(c.id for c in cards),
            )
        return cls(tuple(cards), source_dir=dir_path)


__all__ = ["CardLibrary"]
