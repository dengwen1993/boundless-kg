"""Knowledge-graph Pydantic models — single source of truth for shape."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class QualityLevel(str, Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"


class Direction(BaseModel):
    """The three-axis framing: angle × audience × depth."""

    angle: str = ""
    audience: str = ""
    depth: str = ""
    summary: str = ""


class Node(BaseModel):
    """A single concept node.

    ``links`` are the parent→child hierarchy edges. They participate in BFS
    level computation and define the L0/L1/L2/L3/leaf tiers.
    """

    name: str = Field(min_length=1)
    links: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _trim_name(cls, v: str) -> str:
        return v.strip()

    @field_validator("links", mode="before")
    @classmethod
    def _coerce_links(cls, v):
        """Tolerate SDK adapters that wrap ``list[str]`` as ``{"item": [...]}``.

        Some tool-call adapters (notably the deepagents SDK round-trip in
        this codebase) serialise list arguments into a single-key dict
        container before handing them to the model.  Without this
        validator ``Node(links={"item": ["a", "b"]})`` raises a Pydantic
        ``list_type`` error even though the underlying intent is a plain
        list.  We unwrap a fixed set of wrapper keys; everything else
        defers to the default list coercion.

        Trade-off: this is intentionally narrow — only known wrapper
        shapes are accepted.  Malformed payloads still raise, preserving
        the safety net; only the spurious wrapping is forgiven.
        """
        if isinstance(v, dict):
            for key in ("item", "items", "link", "links", "$text", "text", "value"):
                if key in v:
                    return v[key]
            # unknown dict → let the default coercion try its best
            return list(v.values())
        return v


class Graph(BaseModel):
    """The whole knowledge graph for one domain."""

    domain: str = Field(min_length=1)
    direction: Direction = Field(default_factory=Direction)
    nodes: list[Node] = Field(default_factory=list)
    generated_at: str | None = None
    domain_match_score: dict[str, Any] | None = None

    def node_names(self) -> list[str]:
        return [n.name for n in self.nodes]

    def find_node(self, name: str) -> Node | None:
        for n in self.nodes:
            if n.name == name:
                return n
        return None

    def add_node(self, node: Node) -> None:
        if self.find_node(node.name) is not None:
            raise ValueError(f"node {node.name!r} already exists")
        self.nodes.append(node)


class DomainSummary(BaseModel):
    """Lightweight domain summary for list endpoints."""

    domain: str
    node_count: int
    has_direction: bool
    generated_at: str | None = None
    quality_level: QualityLevel | None = None


class QualityScore(BaseModel):
    """Six-dimension quality scoring (0-100 each)."""

    coverage: int = 0
    hierarchy: int = 0
    linkage: int = 0
    coherence: int = 0
    specificity: int = 0
    freshness: int = 0

    @property
    def total(self) -> int:
        return (
            self.coverage
            + self.hierarchy
            + self.linkage
            + self.coherence
            + self.specificity
            + self.freshness
        ) // 6

    @property
    def level(self) -> QualityLevel:
        t = self.total
        if t >= 85:
            return QualityLevel.EXCELLENT
        if t >= 70:
            return QualityLevel.GOOD
        if t >= 50:
            return QualityLevel.FAIR
        return QualityLevel.POOR


class LinkFixResult(BaseModel):
    """Summary of a reverse-link fix-up pass."""

    added: int
    scanned: int
    when: datetime = Field(default_factory=datetime.utcnow)


__all__ = [
    "Direction",
    "DomainSummary",
    "Graph",
    "LinkFixResult",
    "Node",
    "QualityLevel",
    "QualityScore",
]