"""SSE deliver_assets XML parser — exercises the (text → asset-list) bridge."""

from __future__ import annotations

from src.api.routes.agent import _parse_deliver_assets


def test_single_item() -> None:
    text = (
        "任务完成，产物：\n"
        "<deliver_assets><item><path>/tmp/a.pdf</path></item></deliver_assets>"
    )
    assert _parse_deliver_assets(text) == ["/tmp/a.pdf"]


def test_multiple_items() -> None:
    text = (
        "已生成：\n"
        "<deliver_assets>"
        "<item><path>/x/mindmap.png</path></item>"
        "<item><path>/x/slides.pdf</path></item>"
        "<item><path>/x/quiz.html</path></item>"
        "</deliver_assets>"
    )
    paths = _parse_deliver_assets(text)
    assert paths == ["/x/mindmap.png", "/x/slides.pdf", "/x/quiz.html"]


def test_unclosed_block_returns_none() -> None:
    """Mid-stream partial mention shouldn't trigger premature emit."""
    assert _parse_deliver_assets("<deliver_assets><item><path>/x") is None


def test_no_block_returns_none() -> None:
    assert _parse_deliver_assets("普通对话文本，无产物") is None


def test_picks_last_complete_block_when_multiple() -> None:
    """If the agent prints the XML mid-stream and again at end, we wait
    for the LAST fully-closed block — no premature emit."""
    # We only return a list when the LAST <deliver_assets>…</deliver_assets>
    # is fully closed; rfind + find from there gives us that property.
    text = (
        "<deliver_assets><item><path>/partial</path></item>"
        "<deliver_assets><item><path>/final.pdf</path></item></deliver_assets>"
    )
    # The first <deliver_assets> is not closed, but the second is.
    # rfind finds the second <deliver_assets>, then find closes it.
    # Result: ['/final.pdf']
    paths = _parse_deliver_assets(text)
    assert paths == ["/final.pdf"]