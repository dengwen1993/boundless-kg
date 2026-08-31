"""Resource staging — pure helpers around the staging repository."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class StageOutcome:
    domain: str
    node: str
    target_path: str


def stage_to_node(staged_path: str, domain: str, node: str) -> StageOutcome:
    """Resolve a staged file path into its final per-node location.

    Pure function: no IO. The actual file move is performed by
    :class:`ResourceRepository`.
    """
    from pathlib import Path

    p = Path(staged_path)
    target = p.parent.parent / domain / "web_resources" / "files" / node / p.name
    return StageOutcome(domain=domain, node=node, target_path=str(target))


__all__ = ["stage_to_node", "StageOutcome"]