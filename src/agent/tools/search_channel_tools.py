"""Search-channel preference tools.

Exposes two tools that let the deepagents runtime mutate the
:func:`SearchPreferenceStore` directly:

* ``kg_set_search_channel`` — persist the user's preferred search
  backend so subsequent ``kg_search_resources`` calls hoist it to the
  front of the fallback chain.
* ``kg_clear_search_channel`` — wipe the preference so the chain
  reverts to its static / adaptive behaviour.

Design notes
------------

* Validation is **strict** — if the requested channel has no working
  client (Bocha without an API key, DDG without a proxy) we return
  an error and do NOT touch the preference file.  Persisting an
  unreachable preference would be silently broken.
* On success we also clear any active quarantine for that backend —
  the user is opting in, so a past auto-isolation shouldn't keep it
  off the chain.
* The tools are thin wrappers over the existing singletons wired in
  :mod:`src.agent.dependencies`.  No new I/O surface, no new env vars.
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from src.agent.dependencies import (
    get_search_client,
    get_search_preference_store,
)
from src.infrastructure.search.preference import (
    ALL_BACKENDS,
    BACKEND_BOCHA,
    BACKEND_DDG,
    BACKEND_MMX,
)
from src.observability.logged_tool import logged_tool

logger = logging.getLogger(__name__)


# Backend → env-var hint shown when the user picks an unconfigured
# channel.  Keeps the error actionable instead of dead-ending on
# "channel unavailable".
_BACKEND_HINTS: dict[str, str] = {
    BACKEND_BOCHA: "需要在 .env 中配置 BOCHA_API_KEY 后重启服务。",
    BACKEND_DDG: "DDG 需要检测到代理；如未配置代理，请改用 'mmx' 或 'bocha'。",
    BACKEND_MMX: "mmx 客户端应在启动时自动注入；请检查启动日志。",
}


def _format_chain_summary(summary: dict | None) -> str:
    """Render the client's chain snapshot for the tool's return text."""
    if not summary:
        return ""
    chain = summary.get("chain") or []
    primary = summary.get("primary_source") or ""
    adaptive = summary.get("adaptive")
    if adaptive is None:
        header = f"当前链：{' → '.join(chain) or '(空)'}"
    else:
        header = (
            f"当前链（自适应={'开' if adaptive else '关'}）："
            f"{' → '.join(chain) or '(空)'}"
        )
    if primary and primary not in chain:
        header += f"（静态首选：{primary}）"
    return header


@tool
@logged_tool
async def kg_set_search_channel(channel: str) -> str:
    """把用户的首选搜索渠道持久化为指定 channel。

    后续 ``kg_search_resources``（不带 channel 参数）将优先使用该渠道。

    可选值：
      * ``"duckduck"`` —— DuckDuckGo（需要代理）
      * ``"mmx"`` —— MiniMax 搜索
      * ``"bocha"`` —— 博查 AI Search（需要 BOCHA_API_KEY）

    如果所选渠道暂未配置（例如没设 BOCHA_API_KEY 就选 bocha），
    会返回错误提示，**不会**修改偏好文件。如需清除已设置的渠道，
    请调用 :func:`kg_clear_search_channel`。

    注意：本工具只持久化偏好。``kg_search_resources`` 临时指定渠道
    （通过 ``channel=`` 参数）不会写入偏好。
    """
    channel = (channel or "").strip().lower()
    if channel not in ALL_BACKENDS:
        return (
            f"❌ 渠道 '{channel}' 不合法。\n"
            f"有效值：{', '.join(repr(b) for b in ALL_BACKENDS)}\n"
            f"  duckduck — DuckDuckGo（需代理）\n"
            f"  mmx      — MiniMax 搜索\n"
            f"  bocha    — 博查 AI Search（需 BOCHA_API_KEY）"
        )

    client = get_search_client()
    if not client.has_backend(channel):
        hint = _BACKEND_HINTS.get(channel, "请检查启动日志确认该渠道是否注入。")
        return (
            f"❌ 渠道 '{channel}' 暂未配置，无法设为首选。{hint}\n"
            f"偏好文件未修改。"
        )

    store = get_search_preference_store()
    # Clear any existing quarantine on this backend — user is opting
    # in, the auto-isolation shouldn't keep blocking it.
    backend_state = store._backends.get(channel)  # noqa: SLF001 — intentional
    if backend_state is not None and backend_state.quarantined_until:
        backend_state.quarantined_until = ""
        logger.info("kg_set_search_channel: cleared quarantine on %s", channel)

    store._preferred = channel  # noqa: SLF001 — intentional
    await store._persist()       # noqa: SLF001 — intentional

    summary = client.summary() if hasattr(client, "summary") else None
    extra = _format_chain_summary(summary)
    msg = (
        f"✅ 已设置首选搜索渠道为 '{channel}'。"
        f"后续 kg_search_resources（不带 channel 参数）将优先使用此渠道。"
    )
    if extra:
        msg += f"\n{extra}"
    return msg


@tool
@logged_tool
async def kg_clear_search_channel() -> str:
    """清除用户的首选搜索渠道设置，恢复自适应 fallback 行为。

    调用后 ``kg_search_resources`` 会按默认链（DDG → mmx → Bocha）
    + 自适应学习逐个尝试，不再优先使用任何特定渠道。
    """
    store = get_search_preference_store()
    if not store._preferred:  # noqa: SLF001 — intentional
        # Nothing to do — but still confirm so the agent doesn't loop.
        return "ℹ️ 当前没有设置首选渠道，无需清除。"

    previous = store._preferred  # noqa: SLF001 — intentional
    store._preferred = ""        # noqa: SLF001 — intentional
    await store._persist()       # noqa: SLF001 — intentional

    client = get_search_client()
    summary = client.summary() if hasattr(client, "summary") else None
    extra = _format_chain_summary(summary)
    msg = f"✅ 已清除首选搜索渠道（之前是 '{previous}'），恢复自适应 fallback。"
    if extra:
        msg += f"\n{extra}"
    return msg


__all__ = [
    "kg_set_search_channel",
    "kg_clear_search_channel",
]