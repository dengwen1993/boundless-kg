"""TimelineService — read-only activity stream aggregator."""

from __future__ import annotations

from typing import Any

from src.infrastructure.repository.timeline_repo import TimelineRepository


class TimelineService:
    def __init__(self, repo: TimelineRepository) -> None:
        self._repo = repo

    async def feed(
        self,
        domain: str,
        *,
        date: str | None = None,
        node: str | None = None,
        type_: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        items = await self._repo.get_timeline(
            domain, date=date, node=node, type_=type_
        )
        return items[:limit]


__all__ = ["TimelineService"]