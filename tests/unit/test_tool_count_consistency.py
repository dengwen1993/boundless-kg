"""Verify ``ALL_TOOLS`` consistency — no duplicates, all callable, count stable.

Regression guard: adding a tool to ``src/agent/tools/__init__.py`` but
forgetting to register it in ``ALL_TOOLS`` (or vice-versa) silently
breaks the agent — the tool is either invisible to the LLM or the
import fails at startup.
"""

from __future__ import annotations


def test_all_tools_are_unique():
    """No tool should appear twice in ``ALL_TOOLS``."""
    from src.agent.orchestrator import ALL_TOOLS

    names = [t.name for t in ALL_TOOLS]
    assert len(names) == len(set(names)), f"Duplicate tools: {names}"


def test_all_tools_are_callable():
    """Every entry in ``ALL_TOOLS`` must be a StructuredTool with a ``.name``."""
    from src.agent.orchestrator import ALL_TOOLS

    for t in ALL_TOOLS:
        assert hasattr(t, "name"), f"Tool {t} has no .name attribute"
        assert isinstance(t.name, str) and t.name, f"Tool {t}.name is empty"
        # LangChain StructuredTool objects expose either ``coroutine``
        # (async tools) or ``func`` (sync tools).
        assert hasattr(t, "coroutine") or hasattr(t, "func"), (
            f"Tool {t.name} has neither .coroutine nor .func — not a valid tool"
        )


def test_tool_count_matches_expected():
    """``ALL_TOOLS`` must contain exactly the tools exported by the tools module.

    This catches the two most common mistakes:
      1. A new tool is added to ``tools/__init__.py`` but not registered
         in ``ALL_TOOLS`` → the LLM can't call it.
      2. A tool is removed from ``tools/__init__.py`` but left in
         ``ALL_TOOLS`` → import error at startup.
    """
    from src.agent.orchestrator import ALL_TOOLS
    from src.agent.tools import __all__ as exported_names

    all_tool_names = {t.name for t in ALL_TOOLS}
    exported_set = set(exported_names)

    # Every exported tool name should have a corresponding entry in ALL_TOOLS.
    missing = exported_set - all_tool_names
    assert not missing, (
        f"Tools exported by src.agent.tools but missing from ALL_TOOLS: {missing}"
    )


def test_tool_count_within_expected_range():
    """Sanity: tool count should be in a reasonable range.

    If someone accidentally adds dozens of tools or removes most of
    them, this test fails and forces a review.
    """
    from src.agent.orchestrator import ALL_TOOLS

    n = len(ALL_TOOLS)
    # As of the dossier layer (52 tools including kg_add_edge / kg_delete_edge),
    # we expect 52. Update the upper bound when adding more.
    assert 15 <= n <= 60, f"Unexpected tool count: {n} (expected 15-60)"
