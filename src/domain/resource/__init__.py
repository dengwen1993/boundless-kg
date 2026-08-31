"""Resource-management domain — classifier + staging helpers."""
from .archive_metadata import ArchiveMetadata, extract_archive_metadata
from .auto_classify import (
    AutoClassifyDecision,
    FORBIDDEN_NEW_NODE_NAMES,
    auto_classify_async,
    build_node_tree,
)
from .classifier import classify_pending_async, ClassificationDecision
from .keyword_matcher import (
    DEFAULT_MIN_CONFIDENCE,
    MatchResult,
    NodeMatch,
    match_nodes,
)
from .staging import stage_to_node

__all__ = [
    "auto_classify_async",
    "AutoClassifyDecision",
    "FORBIDDEN_NEW_NODE_NAMES",
    "ArchiveMetadata",
    "extract_archive_metadata",
    "build_node_tree",
    "classify_pending_async",
    "ClassificationDecision",
    "DEFAULT_MIN_CONFIDENCE",
    "MatchResult",
    "NodeMatch",
    "match_nodes",
    "stage_to_node",
]
