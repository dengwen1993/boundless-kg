"""Note generator structured-prompt + resource classifier."""

from __future__ import annotations

from src.domain.graph import Direction, Graph, Node
from src.domain.note.generator import NoteGenerator, PROMPT_VERSION
from src.domain.resource.classifier import ClassificationDecision, classify_pending_async
from src.infrastructure.llm import LLMResult, MockLLMClient
from src.infrastructure.search.base import SearchResult


# ------------------------------------------------------------------
# Note generator — prompt construction & output
# ------------------------------------------------------------------


async def test_generate_returns_body_text() -> None:
    """generate() returns the LLM body stripped of surrounding whitespace."""
    g = NoteGenerator(MockLLMClient(latency_sec=0))
    body = await g.generate("alpha", "d", graph_ctx={"parents": ["root"]})
    assert isinstance(body, str)
    assert len(body) > 0


def test_build_prompt_includes_graph_context() -> None:
    """The user prompt must surface parents / children / siblings / hierarchy."""
    g = NoteGenerator(MockLLMClient(latency_sec=0))
    prompt = g._build_prompt(
        node_name="alpha",
        domain="d",
        graph_ctx={
            "parents": ["root"],
            "children": ["beta", "gamma"],
            "siblings": ["delta"],
            "hierarchy_path": "d - root - alpha",
            "direction_summary": "audience=engineers",
        },
        wiki_def="",
        search_results=[],
    )
    assert "alpha" in prompt
    assert "root" in prompt          # parent
    assert "beta" in prompt          # child
    assert "delta" in prompt         # sibling
    assert "d - root - alpha" in prompt  # hierarchy
    assert "engineers" in prompt     # direction


def test_build_prompt_includes_wiki_and_search() -> None:
    """Wiki definition and search results are appended as reference."""
    g = NoteGenerator(MockLLMClient(latency_sec=0))
    prompt = g._build_prompt(
        node_name="alpha",
        domain="d",
        graph_ctx={},
        wiki_def="Alpha is the first letter.",
        search_results=[
            SearchResult(title="Wiki", link="http://x", snippet="alpha info"),
        ],
    )
    assert "Alpha is the first letter." in prompt
    assert "alpha info" in prompt
    assert "http://x" in prompt


def test_build_prompt_requests_three_sections() -> None:
    """The prompt must ask for the three mandatory sections."""
    g = NoteGenerator(MockLLMClient(latency_sec=0))
    prompt = g._build_prompt(
        node_name="x",
        domain="d",
        graph_ctx={},
        wiki_def="",
        search_results=[],
    )
    assert "定义" in prompt
    assert "重要概念与知识点" in prompt
    assert "如何开启快速学习" in prompt


def test_prompt_version_is_set() -> None:
    """PROMPT_VERSION must be a non-empty string for frontmatter tracing."""
    assert isinstance(PROMPT_VERSION, str)
    assert len(PROMPT_VERSION) > 0


# ---------- resource classifier ----------


class _ClassifyLLM:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    async def chat(self, *a, **kw) -> str:
        self.calls += 1
        return self._response


async def test_classify_returns_existing_node() -> None:
    llm = _ClassifyLLM('{"node": "alpha", "rationale": "matches"}')
    g = Graph(
        domain="d",
        direction=Direction(summary="x" * 30),
        nodes=[Node(name="alpha")],
    )
    out = await classify_pending_async({"name": "x.pdf"}, g, llm)  # type: ignore[arg-type]
    assert isinstance(out, ClassificationDecision)
    assert out.node == "alpha"
    assert out.is_new is False


async def test_classify_returns_new_node_marker() -> None:
    llm = _ClassifyLLM('{"node": "new_node", "new_node_name": "newness"}')
    g = Graph(domain="d", direction=Direction(summary="x" * 30))
    out = await classify_pending_async({"name": "x.pdf"}, g, llm)  # type: ignore[arg-type]
    assert out.is_new is True
    assert out.new_node_name == "newness"


async def test_classify_fallback_on_unparseable() -> None:
    llm = _ClassifyLLM("not json")
    g = Graph(domain="d", direction=Direction(summary="x" * 30))
    out = await classify_pending_async({"name": "x.pdf"}, g, llm)  # type: ignore[arg-type]
    assert out.is_new is True
    assert out.rationale == "parse-failed"


async def test_classify_handles_dict_missing_fields() -> None:
    """Empty JSON object → node defaults to empty string, is_new=False.

    (A truly malformed response is the only path that ends up with
    ``node == "new_node"`` — see ``test_classify_fallback_on_unparseable``.)
    """
    llm = _ClassifyLLM("{}")
    g = Graph(domain="d", direction=Direction(summary="x" * 30))
    out = await classify_pending_async({"name": "x.pdf"}, g, llm)  # type: ignore[arg-type]
    assert out.node == ""
    assert out.is_new is False