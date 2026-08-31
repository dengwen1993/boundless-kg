"""LangChain ``@tool`` wrapper for the Bocha AI Search (博查搜索) backend.

Exposes a tool (``kg_bocha_web_search``) that lets the deepagents
runtime invoke Bocha directly with backend-specific knobs — the
generic :func:`kg_search_resources` goes through the multi-backend
fallback chain (DuckDuckGo → mmx → Bocha) and hides these options.

Use this tool when you want guaranteed Bocha quality and AI summary
fields (``summary=true``).  Use ``kg_search_resources`` when you just
want "any working backend".

The tool is a thin adapter over :class:`BochaSearchClient`:
* Auth (BOCHA_API_KEY), endpoint, proxy, timeout are read from
  :mod:`src.config.settings` — never from agent input.  This follows
  ENGINEERING_PLAN §1.1: secrets stay in env, not in tool args.
* Network / JSON / 4xx errors degrade to an empty ``SearchResult``
  list so the agent loop can decide what to do next instead of
  receiving a raw exception string.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from langchain_core.tools import tool

from src.agent.dependencies import reset_dependencies
from src.config.settings import (
    get_bocha_api_key,
    get_bocha_count,
    get_bocha_endpoint,
    get_bocha_proxy,
    get_bocha_timeout_sec,
)
from src.infrastructure.search import BochaSearchClient, SearchResult
from src.infrastructure.search.bocha import VALID_FRESHNESS
from src.observability.logged_tool import logged_tool

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_bocha_client() -> BochaSearchClient | None:
    """Build the singleton BochaSearchClient used by ``kg_bocha_web_search``.

    Returns ``None`` when ``BOCHA_API_KEY`` is not configured — the
    tool surfaces a friendly "not configured" message instead of
    building a half-broken client.
    """
    api_key = get_bocha_api_key()
    if not api_key:
        return None
    return BochaSearchClient(
        api_key=api_key,
        endpoint=get_bocha_endpoint(),
        proxy=get_bocha_proxy(),
        timeout_sec=get_bocha_timeout_sec(),
        default_count=get_bocha_count(),
    )


@tool
@logged_tool
async def kg_bocha_web_search(
    query: str,
    num_results: int = 10,
    freshness: str = "noLimit",
    include_summary: bool = True,
) -> str:
    """使用 Bocha AI Search（博查搜索）联网搜索；返回 JSON。

    与 ``kg_search_resources`` 不同：本工具**直接调用 Bocha API**，
    支持 Bocha 专属参数（时间范围 ``freshness``、是否返回 AI 摘要
    ``include_summary``），不走 DuckDuckGo / mmx 备用链路。

    Args:
        query: 自然语言搜索关键词（必填）。
        num_results: 返回条数，范围 1..50，默认 10。
        freshness: 时间范围过滤。可选值：
            ``"oneDay"`` / ``"oneWeek"`` / ``"oneMonth"`` /
            ``"oneYear"`` / ``"noLimit"``（默认）。
        include_summary: 是否让 Bocha 返回 AI 生成的更详细摘要
            （字段 ``summary``，长度通常显著大于 ``snippet``）。
            默认为 True。

    Returns:
        JSON 数组，每个 item 字段：``title`` / ``url`` /
        ``snippet`` / ``source="bocha"``。

    【注意】本工具只返回搜索结果 JSON，不会自动落盘。如需保存请
    调用 ``kg_add_learning_resources``。

    【凭证缺失】当 ``BOCHA_API_KEY`` 未配置时返回明确的提示，不会
    默默走其他搜索后端。
    """
    # ── Validate inputs before touching the network ──
    if not query or not query.strip():
        return "❌ query 不能为空。"

    if freshness not in VALID_FRESHNESS:
        return (
            f"❌ freshness 取值非法：{freshness!r}。"
            f"有效值：{sorted(VALID_FRESHNESS)}"
        )

    # Clamp to Bocha's documented range — same constant the client uses.
    from src.infrastructure.search.bocha import MAX_COUNT

    num_results = max(1, min(int(num_results), MAX_COUNT))

    client = get_bocha_client()
    if client is None:
        return (
            "❌ Bocha 搜索未启用：在 .env 中设置 BOCHA_API_KEY 后重启服务。"
            "如需通用搜索（DDG/mmx/Bocha 备用链），请改用 kg_search_resources。"
        )

    # The tool wants ``include_summary`` exposed; Bocha's HTTP body
    # toggles this at construction time. We rebuild the client for
    # each call when the flag differs from the singleton — cheap
    # (httpx client is created per-call inside search()) and keeps
    # the @tool signature honest.
    if include_summary != client._include_summary:  # noqa: SLF001 — internal flip is intentional
        from src.config.settings import (
            get_bocha_api_key,
            get_bocha_endpoint,
            get_bocha_proxy,
            get_bocha_timeout_sec,
            get_bocha_count,
        )

        client = BochaSearchClient(
            api_key=get_bocha_api_key() or "",
            endpoint=get_bocha_endpoint(),
            proxy=get_bocha_proxy(),
            timeout_sec=get_bocha_timeout_sec(),
            default_count=get_bocha_count(),
            include_summary=bool(include_summary),
        )

    try:
        results: list[SearchResult] = await client.search(query, num_results=num_results)
    except Exception as e:  # pragma: no cover — defensive net
        logger.exception("kg_bocha_web_search failed: %s", e)
        return f"❌ Bocha 搜索失败：{e}"

    # Match the shape returned by ``kg_search_resources`` so the
    # downstream ``kg_add_learning_resources`` accepts the JSON as-is.
    payload = [
        {
            "title": r.title,
            "url": r.link,
            "summary": r.snippet,
            "source": r.source or "bocha",
        }
        for r in results
    ]
    return json.dumps(payload, ensure_ascii=False)


# Reset hook — kept parallel to other singletons in
# ``src/agent/dependencies.py`` so tests that call
# ``reset_dependencies()`` also wipe the Bocha client.
_reset_dependencies = reset_dependencies


def reset_bocha_client() -> None:
    """Clear the cached BochaSearchClient (for tests)."""
    get_bocha_client.cache_clear()


__all__ = [
    "kg_bocha_web_search",
    "get_bocha_client",
    "reset_bocha_client",
]