"""LangChain @tool definitions grouped by responsibility.

Public surface mirrors the baseline ``main.py`` tool names so existing
prompt contracts keep working.

Document generation (PDF / PPTX / DOCX) and knowledge-digest are NOT
exposed as @tools here — they are skills under ``src/skills/`` that
deepagents' SkillsMiddleware feeds directly via each SKILL.md.  The
agent reads the relevant SKILL on demand and runs the bundled scripts
itself, so we don't re-implement any of that here.
"""

from .graph_tools import (
    kg_list_domains,
    kg_view_graph,
    kg_add_node,
    kg_add_subtree,
    kg_fix_links,
    kg_delete_node,
    kg_update_node,
    kg_validate_graph,
    kg_open_node,
)
from .dossier_tools import (
    kg_add_dossier_entry,
    kg_view_dossier,
    kg_search_dossier,
    kg_update_dossier_entry,
    kg_remove_dossier_entry,
)
from .note_tools import kg_generate_note, kg_read_note, kg_list_notes
from .resource_tools import (
    kg_search_resources,
    kg_view_resources,
    kg_add_learning_resources,
)
from .search_bocha_tool import kg_bocha_web_search
from .search_channel_tools import (
    kg_clear_search_channel,
    kg_set_search_channel,
)
from .staging_tools import (
    kg_stage_file,
    kg_classify_pending,
    kg_create_node_with_resource,
)
from .pipeline_tools import kg_run_skill, kg_check_status
from .plan_tools import (
    kg_add_plan,
    kg_delete_plan,
    kg_list_plans,
    kg_update_plan_status,
)
from .timeline_tools import kg_view_timeline
from .card_tools import (
    kg_add_card,
    kg_list_cards,
    kg_view_card,
    kg_delete_card,
)
from .json_repair_tool import kg_repair_json
from .memory_tools import kg_search_memory, kg_recall_recent, kg_recall_session
from .association_tools import (
    kg_view_associations,
    kg_query_neighbors,
    kg_sync_associations,
    kg_sync_node_associations,
    kg_add_edge,
    kg_delete_edge,
)
from .search_tools import kg_global_search, kg_graph_neighbors
from .tmp_file_tools import (
    kg_list_uploaded_files,
    kg_parse_uploaded_file,
    kg_delete_uploaded_file,
    kg_auto_place_uploaded_file,
)

__all__ = [
    # Graph management
    "kg_list_domains",
    "kg_view_graph",
    "kg_add_node",
    "kg_add_subtree",
    "kg_fix_links",
    "kg_delete_node",
    "kg_update_node",
    "kg_validate_graph",
    "kg_open_node",
    # Notes
    "kg_generate_note",
    "kg_read_note",
    "kg_list_notes",
    # Resources
    "kg_search_resources",
    "kg_view_resources",
    "kg_add_learning_resources",
    "kg_bocha_web_search",
    "kg_set_search_channel",
    "kg_clear_search_channel",
    # Staging
    "kg_stage_file",
    "kg_classify_pending",
    "kg_create_node_with_resource",
    # Pipeline
    "kg_run_skill",
    "kg_check_status",
    # Plans
    "kg_add_plan",
    "kg_delete_plan",
    "kg_list_plans",
    "kg_update_plan_status",
    # Timeline
    "kg_view_timeline",
    # Cards
    "kg_add_card",
    "kg_list_cards",
    "kg_view_card",
    "kg_delete_card",
    # JSON repair (chat tool — generic utility for LLM-driven chains)
    "kg_repair_json",
    # Memory / session search
    "kg_search_memory",
    "kg_recall_recent",
    "kg_recall_session",
    # Associations (L1/L2/L3 derivation layer)
    "kg_view_associations",
    "kg_query_neighbors",
    "kg_sync_associations",
    "kg_sync_node_associations",
    "kg_add_edge",
    "kg_delete_edge",
    # Global search (FalkorDB + Embedding)
    "kg_global_search",
    "kg_graph_neighbors",
    # Dossier (节点经验档案)
    "kg_add_dossier_entry",
    "kg_view_dossier",
    "kg_search_dossier",
    "kg_update_dossier_entry",
    "kg_remove_dossier_entry",
    # Transient file tools (chat attachments)
    "kg_list_uploaded_files",
    "kg_parse_uploaded_file",
    "kg_delete_uploaded_file",
    "kg_auto_place_uploaded_file",
]
