"""Unit tests for GraphSyncService — sync logic and level inference."""

import pytest

from src.application.graph_sync_service import _infer_level, NODE_NAME_PATTERN
from src.domain.graph.models import Graph, Node


class TestInferLevel:
    def test_root_node(self):
        graph = Graph(domain="test", nodes=[
            Node(name="Root", links=["Child1", "Child2"]),
            Node(name="Child1", links=[]),
            Node(name="Child2", links=["Grandchild"]),
            Node(name="Grandchild", links=[]),
        ])
        root = graph.find_node("Root")
        assert _infer_level(root, graph) == 0

    def test_child_level(self):
        graph = Graph(domain="test", nodes=[
            Node(name="Root", links=["Child"]),
            Node(name="Child", links=["Grand"]),
            Node(name="Grand", links=[]),
        ])
        child = graph.find_node("Child")
        assert _infer_level(child, graph) == 1

    def test_grandchild_level(self):
        graph = Graph(domain="test", nodes=[
            Node(name="Root", links=["Child"]),
            Node(name="Child", links=["Grand"]),
            Node(name="Grand", links=[]),
        ])
        grand = graph.find_node("Grand")
        assert _infer_level(grand, graph) == 2

    def test_cycle_robustness(self):
        """Cycles should not cause infinite loops."""
        graph = Graph(domain="test", nodes=[
            Node(name="A", links=["B"]),
            Node(name="B", links=["C"]),
            Node(name="C", links=["A"]),  # cycle back
        ])
        # Should not hang
        node_a = graph.find_node("A")
        level = _infer_level(node_a, graph)
        assert level >= 0

    def test_multi_root(self):
        graph = Graph(domain="test", nodes=[
            Node(name="Root1", links=["Shared"]),
            Node(name="Root2", links=["Shared"]),
            Node(name="Shared", links=[]),
        ])
        shared = graph.find_node("Shared")
        level = _infer_level(shared, graph)
        assert level >= 1

    def test_empty_graph(self):
        graph = Graph(domain="test", nodes=[])
        node = Node(name="Lone", links=[])
        assert _infer_level(node, graph) == 0

    def test_isolated_node(self):
        graph = Graph(domain="test", nodes=[
            Node(name="Root", links=["Child"]),
            Node(name="Child", links=[]),
            Node(name="Isolated", links=[]),
        ])
        iso = graph.find_node("Isolated")
        level = _infer_level(iso, graph)
        # Isolated node has no parent → level 0
        assert level == 0


class TestNodeNamePattern:
    def test_simple_english(self):
        match = NODE_NAME_PATTERN.search("see @ReAct for details")
        assert match is not None
        assert match.group(1) == "ReAct"

    def test_chinese(self):
        match = NODE_NAME_PATTERN.search("参考 @思维链推理 了解更多")
        assert match is not None
        assert match.group(1) == "思维链推理"

    def test_mixed(self):
        match = NODE_NAME_PATTERN.search("uses @MCP工具调用 protocol")
        assert match is not None

    def test_no_match(self):
        match = NODE_NAME_PATTERN.search("no references here")
        assert match is None

    def test_multiple_matches(self):
        text = "see @ReAct and @CoT for reasoning"
        matches = NODE_NAME_PATTERN.findall(text)
        assert "ReAct" in matches
        assert "CoT" in matches

    def test_trailing_punctuation(self):
        match = NODE_NAME_PATTERN.search("see @ReAct.")
        assert match is not None
        # The pattern should capture "ReAct" not "ReAct."
        assert "." not in match.group(1)
