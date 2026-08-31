"""mmx-cli and DuckDuckGo async search backends with proxy-aware routing."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

from .base import AsyncSearchClient, SearchResult
from .bocha import BochaSearchClient
from .preference import (
    BACKEND_BOCHA,
    BACKEND_DDG,
    BACKEND_MMX,
    SearchPreferenceStore,
)


# ---------------------------------------------------------------------------
# Proxy detection
# ---------------------------------------------------------------------------

def detect_proxy() -> str:
    """Detect an available HTTP proxy.

    Checks in order:
      1. ``DDG_PROXY`` / ``SEARCH_PROXY`` env vars (explicit override)
      2. ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``ALL_PROXY`` env vars
      3. Windows registry system proxy (Clash / V2Ray / etc.)
      4. Common local proxy ports (7890, 7897, 1080, 10809)

    Returns the proxy URL string (e.g. ``http://127.0.0.1:7897``),
    or empty string if no proxy is detected.
    """
    # 1. Explicit env override
    for key in ("DDG_PROXY", "SEARCH_PROXY"):
        val = os.environ.get(key, "").strip()
        if val:
            return _normalise_proxy(val)

    # 2. Standard env vars
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                "https_proxy", "http_proxy", "all_proxy"):
        val = os.environ.get(key, "").strip()
        if val:
            return _normalise_proxy(val)

    # 3. Windows registry
    if sys.platform == "win32":
        proxy = _detect_windows_proxy()
        if proxy:
            return proxy

    # 4. Probe common local proxy ports
    for port in (7897, 7890, 1080, 10809, 10808):
        if _probe_port("127.0.0.1", port):
            return f"http://127.0.0.1:{port}"

    return ""


def _normalise_proxy(val: str) -> str:
    """Ensure the proxy URL has a scheme."""
    val = val.strip()
    if not val:
        return ""
    if not val.startswith(("http://", "https://", "socks5://", "socks4://")):
        val = f"http://{val}"
    return val


def _detect_windows_proxy() -> str:
    """Read the Windows Internet Settings registry key."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return ""
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            if not server:
                return ""
            # ProxyServer can be "proto=host:port;proto2=host2:port2"
            # Take the first entry or the one with http=.
            if "=" in server:
                for part in server.split(";"):
                    if part.startswith("http="):
                        return _normalise_proxy(part.split("=", 1)[1])
                # Fall back to first entry
                return _normalise_proxy(server.split(";")[0].split("=", 1)[-1])
            return _normalise_proxy(server)
    except Exception:
        return ""


def _probe_port(host: str, port: int, timeout: float = 0.3) -> bool:
    """Quick TCP connect test to see if a proxy is listening."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


# ---------------------------------------------------------------------------
# DuckDuckGo backend
# ---------------------------------------------------------------------------

class DuckDuckGoClient(AsyncSearchClient):
    """Free DuckDuckGo HTML scrape — no key, no install.

    Requires a proxy in mainland China. Pass ``proxy`` explicitly or
    rely on :func:`detect_proxy` at construction time.
    """

    BASE_URL = "https://html.duckduckgo.com/html/"

    def __init__(
        self,
        *,
        proxy: str = "",
        timeout_sec: float = 8.0,
    ) -> None:
        self._proxy = proxy
        self._timeout = timeout_sec

    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        import httpx

        proxy = self._proxy or detect_proxy()
        client_kwargs: dict[str, Any] = {
            "timeout": self._timeout,
            "follow_redirects": True,
        }
        if proxy:
            client_kwargs["proxy"] = proxy

        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.post(
                self.BASE_URL,
                data={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            html = resp.text
        return self._parse_html(html)[:num_results]

    @staticmethod
    def _parse_html(html: str) -> list[SearchResult]:
        import re

        # Parse result blocks: each result is wrapped in a div with
        # class="result results_links ...". Inside each block there's
        # an <a class="result__a"> (title+link) and optionally a
        # <a class="result__snippet"> (summary text).
        results: list[SearchResult] = []

        # Extract all title/link pairs
        title_matches = list(re.finditer(
            r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.DOTALL,
        ))

        # Extract all snippets
        snippet_matches = list(re.finditer(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            html,
            flags=re.DOTALL,
        ))

        # Pair them by position (DuckDuckGo renders them in order)
        snippets: list[str] = []
        for sm in snippet_matches:
            raw = sm.group(1)
            # Decode HTML entities + strip tags
            clean = re.sub(r"<[^>]+>", "", raw).strip()
            clean = clean.replace("&#x27;", "'").replace("&amp;", "&")
            clean = clean.replace("&lt;", "<").replace("&gt;", ">")
            clean = clean.replace("&quot;", '"').replace("&#39;", "'")
            snippets.append(clean)

        for i, tm in enumerate(title_matches):
            link = tm.group(1)
            title = re.sub(r"<[^>]+>", "", tm.group(2)).strip()
            title = title.replace("&#x27;", "'").replace("&amp;", "&")
            if not link or not title:
                continue
            snippet = snippets[i] if i < len(snippets) else ""
            results.append(
                SearchResult(title=title, link=link, snippet=snippet, source="duckduck")
            )
        return results


# ---------------------------------------------------------------------------
# mmx (MiniMax CLI) backend
# ---------------------------------------------------------------------------

class MmxSearchClient(AsyncSearchClient):
    """mmx-cli async wrapper — MiniMax web search via the mmx CLI."""

    def __init__(
        self,
        *,
        binary: str = "mmx",
        api_key: str | None = None,
        region: str = "cn",
        proxy: str = "",
        timeout_sec: float = 30.0,
    ) -> None:
        self._binary = binary
        self._api_key = api_key
        self._region = region
        self._proxy = proxy
        self._timeout = timeout_sec

    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        """Invoke the mmx CLI as an async subprocess.

        If the binary is not found (``FileNotFoundError``), falls back
        to :class:`DuckDuckGoClient` so the pipeline degrades gracefully
        instead of crashing.
        """
        cmd = [
            self._binary,
            "search",
            "query",
            "--q",
            query,
            "--region",
            self._region,
            "--output",
            "json",
            "--non-interactive",
        ]
        if self._api_key:
            cmd.extend(["--api-key", self._api_key])

        env = os.environ.copy()
        if self._proxy:
            env["HTTPS_PROXY"] = self._proxy
            env["HTTP_PROXY"] = self._proxy

        # On Windows, mmx is installed as a .cmd wrapper (via npm).
        # create_subprocess_exec uses CreateProcess which cannot resolve
        # .cmd extensions, so we route through ``cmd /c``.
        if sys.platform == "win32":
            exec_cmd = ["cmd", "/c"] + cmd
        else:
            exec_cmd = cmd

        try:
            proc = await asyncio.create_subprocess_exec(
                *exec_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as e:
            logger.warning("mmx binary not found (%s), falling back to DuckDuckGo", e)
            ddg = DuckDuckGoClient(
                proxy=self._proxy,
                timeout_sec=8.0,
            )
            return await ddg.search(query, num_results=num_results)
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise
        if proc.returncode != 0:
            return []
        raw_results = self._parse_stdout(stdout)
        return [
            SearchResult(
                title=_clean_html(str(r.get("title", ""))),
                link=str(r.get("link", "")),
                snippet=_clean_html(str(r.get("snippet", ""))),
                source="mmx",
            )
            for r in raw_results[:num_results]
        ]

    @staticmethod
    def _parse_stdout(raw: bytes) -> list[dict[str, Any]]:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                # mmx search returns {"organic": [...], "base_resp": {...}}
                for key in ("organic", "results", "data"):
                    if key in data and isinstance(data[key], list):
                        return list(data[key])
            if isinstance(data, list):
                return list(data)
        except json.JSONDecodeError:
            pass
        return []


# ---------------------------------------------------------------------------
# Dual-mode orchestrator
# ---------------------------------------------------------------------------

def _clean_html(text: str) -> str:
    """Strip HTML tags and decode common entities from a string."""
    import re

    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", "", text).strip()
    # Decode common HTML entities
    clean = (
        clean.replace("&#x27;", "'")
        .replace("&#39;", "'")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&nbsp;", " ")
    )
    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


class DualSearchClient(AsyncSearchClient):
    """Adaptive search orchestrator — promotes whichever backend last worked.

    Behaviour summary
    -----------------

    * **Proxy detected** at construction → DDG, mmx, Bocha are all in
      the rotation. The chain *starts* with DDG (or whichever the
      learned preference says) and falls through to the rest.
    * **No proxy** → DDG is omitted (it requires a proxy in mainland
      China); the chain runs mmx first.
    * **Adaptive (``KG_SEARCH_ADAPTIVE=true``, default)** — every
      successful call hoists its backend to the front of the chain
      for the next query; every *raised* failure quarantines the
      backend for ``KG_SEARCH_QUARANTINE_SEC`` seconds so the next
      query skips it entirely. Each failure (and its error string)
      is appended to ``<KG_AGENT_MEMORY_DIR>/search_preference.json``
      for ops to review.
    * **Non-adaptive** — fixed chain in the original
      DDG → mmx → Bocha order, no learning, no quarantine. Useful
      for audits / reproducible demos.

    Each backend still gets its own per-call timeout so a slow primary
    doesn't eat the entire pipeline budget before the next tier runs.
    """

    #: Per-backend timeout (seconds). DDG must fail fast enough to
    #: leave room for the mmx fallback within the pipeline's global
    #: ``SEARCH_TIMEOUT_SEC``.
    DDG_TIMEOUT_SEC: float = 8.0
    MMX_TIMEOUT_SEC: float = 12.0
    BOCHA_TIMEOUT_SEC: float = 15.0

    def __init__(
        self,
        *,
        ddg: DuckDuckGoClient | None = None,
        mmx: MmxSearchClient | None = None,
        bocha: BochaSearchClient | None = None,
        proxy: str | None = None,
        adaptive: bool = True,
        preference_store: SearchPreferenceStore | None = None,
    ) -> None:
        # proxy=None → auto-detect; proxy="" → explicitly no proxy
        self._proxy = detect_proxy() if proxy is None else proxy
        self._ddg = ddg or DuckDuckGoClient(proxy=self._proxy, timeout_sec=self.DDG_TIMEOUT_SEC)
        self._mmx = mmx or MmxSearchClient(timeout_sec=self.MMX_TIMEOUT_SEC)
        # ``None`` here means "Bocha not configured" — chain skips it.
        self._bocha = bocha
        self._use_ddg = bool(self._proxy)
        # Adaptive learning is on by default; ops can disable via
        # ``KG_SEARCH_ADAPTIVE=false`` or by passing ``adaptive=False``
        # (used by tests).
        self._adaptive = adaptive
        self._pref = preference_store

    # ────────────────────────── Public surface ──────────────────────────

    @property
    def primary_source(self) -> str:
        """Human-readable name of the legacy primary backend.

        Reports the *static* primary (DDG if proxy, else mmx) — does
        not reflect runtime adaptations. Use :meth:`current_chain` to
        see the actually-active order.
        """
        return BACKEND_DDG if self._use_ddg else BACKEND_MMX

    @property
    def has_bocha(self) -> bool:
        """``True`` when a Bocha backend was injected at construction."""
        return self._bocha is not None

    @property
    def adaptive(self) -> bool:
        return self._adaptive

    def current_chain(self) -> list[str]:
        """Return the backend names that *will* be tried on the next
        call, in order.  Useful for ops / debug endpoints."""
        default = self._default_chain()
        if not self._adaptive or self._pref is None:
            return list(default)
        return self._pref.chain_for(default)

    def summary(self) -> dict[str, Any]:
        """Combine backend chain + preference-store snapshot for ops."""
        info: dict[str, Any] = {
            "adaptive": self._adaptive,
            "chain": self.current_chain(),
            "primary_source": self.primary_source,
        }
        if self._pref is not None:
            info["preference"] = self._pref.summary()
        return info

    # ────────────────────────── Search ──────────────────────────

    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        # Snapshot the available backends so a failed call mid-chain
        # can't see a backend that was quarantined by an earlier tier
        # in the same request.
        chain = self._ordered_chain()

        last_error: BaseException | None = None
        for source in chain:
            client, timeout = self._resolve(source)
            if client is None:
                # Backend removed from this run's chain (e.g. Bocha
                # not configured, or DDG missing without proxy).
                continue
            try:
                results = await asyncio.wait_for(
                    client.search(query, num_results=num_results),
                    timeout=timeout,
                )
            except (asyncio.TimeoutError, Exception) as e:
                last_error = e
                await self._mark_failure(source, e)
                # Keep going through the chain — don't return early.
                continue
            if results:
                await self._mark_success(source)
                return results
            # Empty result list: backend is reachable but didn't match.
            # Don't promote it, don't quarantine it — just try the next.
            logger.debug(
                "DualSearchClient: %s returned 0 results for %r, trying next",
                source, query,
            )

        logger.debug(
            "DualSearchClient: all backends exhausted for %r (last_error=%r)",
            query, last_error,
        )
        return []

    # ────────────────────────── Single-backend (no fallback) ──────────────────────────

    def has_backend(self, source: str) -> bool:
        """True when the named backend's client is actually wired up.

        Used by :func:`kg_set_search_channel` to reject a user-picked
        channel that has no working client (e.g. Bocha without an API
        key, DDG without a proxy) BEFORE we persist the preference.
        """
        if source == BACKEND_DDG:
            return self._ddg is not None and self._use_ddg
        if source == BACKEND_MMX:
            return self._mmx is not None
        if source == BACKEND_BOCHA:
            return self._bocha is not None
        return False

    async def search_one(
        self,
        source: str,
        query: str,
        *,
        num_results: int = 10,
    ) -> list[SearchResult]:
        """Single-backend search — no fallback chain, no preference, no quarantine.

        Use this when the caller has explicitly chosen a backend and
        should be the sole judge of what to do on failure —

        * Backend missing / not configured → return ``[]``
        * Backend raises / times out      → return ``[]`` (no fallback,
          no ``_mark_failure`` so the global quarantine stays clean
          and the auto-learned preference is unaffected)

        The per-backend timeout constants (``DDG_TIMEOUT_SEC`` /
        ``MMX_TIMEOUT_SEC`` / ``BOCHA_TIMEOUT_SEC``) still apply so a
        hung backend can't eat the whole pipeline budget.
        """
        # Reuse ``has_backend`` so the two methods agree on what
        # "configured" means (notably DDG's proxy check — see
        # ``has_backend`` for the rationale).
        if not self.has_backend(source):
            logger.debug(
                "DualSearchClient.search_one: backend %r unavailable "
                "(unconfigured or unknown name)",
                source,
            )
            return []
        client, timeout = self._resolve(source)
        try:
            results = await asyncio.wait_for(
                client.search(query, num_results=num_results),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "DualSearchClient.search_one: %s timed out after %.1fs for %r",
                source, timeout, query,
            )
            return []
        except Exception as e:  # pragma: no cover — defensive net
            logger.warning(
                "DualSearchClient.search_one: %s raised %r for %r",
                source, e, query,
            )
            return []
        return list(results)

    # ────────────────────────── Internals ──────────────────────────

    def _default_chain(self) -> tuple[str, ...]:
        """The static, adaptive-off chain based on what's injected."""
        chain: list[str] = []
        if self._ddg is not None and self._use_ddg:
            chain.append(BACKEND_DDG)
        if self._mmx is not None:
            chain.append(BACKEND_MMX)
        if self._bocha is not None:
            chain.append(BACKEND_BOCHA)
        return tuple(chain)

    def _ordered_chain(self) -> list[str]:
        """Apply the learned preference on top of the static chain."""
        default = self._default_chain()
        if not self._adaptive or self._pref is None:
            return list(default)
        return self._pref.chain_for(default)

    def _resolve(self, source: str) -> tuple[Any, float]:
        """Return ``(client, timeout)`` for ``source``."""
        if source == BACKEND_DDG:
            return self._ddg, self.DDG_TIMEOUT_SEC
        if source == BACKEND_MMX:
            return self._mmx, self.MMX_TIMEOUT_SEC
        if source == BACKEND_BOCHA:
            return self._bocha, self.BOCHA_TIMEOUT_SEC
        return None, 0.0

    async def _mark_success(self, source: str) -> None:
        if self._pref is None or not self._adaptive:
            return
        try:
            await self._pref.record_success(source)
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("DualSearchClient: preference.record_success failed: %s", e)

    async def _mark_failure(self, source: str, exc: BaseException) -> None:
        if self._pref is None or not self._adaptive:
            return
        try:
            await self._pref.record_failure(source, repr(exc))
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("DualSearchClient: preference.record_failure failed: %s", e)


# Backward-compatible alias
DuckDuckGoFallbackClient = DuckDuckGoClient


__all__ = [
    "MmxSearchClient",
    "DuckDuckGoClient",
    "DuckDuckGoFallbackClient",
    "DualSearchClient",
    "detect_proxy",
]
