"""Knowledge-graph domain — models + validation + graph operations."""

from .models import (
    Direction,
    DomainSummary,
    Graph,
    LinkFixResult,
    Node,
    QualityScore,
    QualityLevel,
)
from .validator import GraphValidator, validate_graph
from .link_fixer import fix_missing_reverse_links
from .decorator import decorate_graph, node_tier, infer_hierarchy, gather_graph_context
from .association import (
    Association,
    AssociationGraph,
    AssociationMetadata,
    ConceptNode,
    DEFAULT_INTENSITY_BY_RELATION,
    EdgeIntensity,
    RelationType,
    ResourceNode,
    ResourceType,
    concept_id_to_name,
    make_association_id,
    make_concept_id,
    make_resource_id,
)

__all__ = [
    "Direction",
    "DomainSummary",
    "Graph",
    "LinkFixResult",
    "Node",
    "QualityScore",
    "QualityLevel",
    "GraphValidator",
    "validate_graph",
    "fix_missing_reverse_links",
    "decorate_graph",
    "node_tier",
    "infer_hierarchy",
    "gather_graph_context",
    # Association layer
    "Association",
    "AssociationGraph",
    "AssociationMetadata",
    "ConceptNode",
    "DEFAULT_INTENSITY_BY_RELATION",
    "EdgeIntensity",
    "RelationType",
    "ResourceNode",
    "ResourceType",
    "concept_id_to_name",
    "make_association_id",
    "make_concept_id",
    "make_resource_id",
]