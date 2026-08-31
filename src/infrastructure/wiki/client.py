"""Async Wikipedia REST summary client — proxy-aware, call-time detection."""

from __future__ import annotations

import urllib.parse

import httpx


def _detect_proxy() -> str:
    """Detect an available HTTP proxy at call time.

    Checks env vars and common local proxy ports.  Returns the proxy URL
    (e.g. ``http://127.0.0.1:7897``) or an empty string.

    This is an inline clone of :func:`~src.infrastructure.search.mmx.detect_proxy`
    so the wiki client stays free of package-level import chains.
    """
    import os
    import socket
    import sys

    for key in (
        "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
        "https_proxy", "http_proxy", "all_proxy",
    ):
        val = os.environ.get(key, "").strip()
        if val:
            if not val.startswith(("http://", "https://", "socks5://")):
                val = f"http://{val}"
            return val

    for port in (7897, 7890, 1080, 10809, 10808):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return f"http://127.0.0.1:{port}"
        except OSError:
            pass

    return ""


class AsyncWikiClient:
    """Async Wikipedia summary lookup with timeout + graceful failure.

    Proxy detection is deferred to each ``lookup()`` call so the client
    adapts when the proxy appears or disappears between invocations.

    Parameters
    ----------
    language:
        Wikipedia language sub-domain (default ``zh``).
    timeout_sec:
        Per-request timeout in seconds.
    user_agent:
        ``User-Agent`` header sent with every request.
    proxy:
        Explicit proxy URL (e.g. ``http://127.0.0.1:7897``).  When left
        empty the client auto-detects a proxy on every ``lookup()`` call.
    """

    def __init__(
        self,
        *,
        language: str = "zh",
        timeout_sec: float = 5.0,
        user_agent: str = "kg-engine/1.0 (https://example.com)",
        proxy: str = "",
    ) -> None:
        self._lang = language
        self._timeout = timeout_sec
        self._ua = user_agent
        self._proxy = proxy

    async def lookup(self, title: str) -> str:
        """Return the first paragraph of the article, or empty string on failure.

        Detects an available HTTP proxy at call time (unless an explicit
        proxy was supplied at construction).  Requests that time out or
        fail for any reason return an empty string silently.
        """
        if not title:
            return ""

        proxy = self._proxy or _detect_proxy()
        kwargs: dict = {
            "timeout": httpx.Timeout(self._timeout),
            "trust_env": True,
        }
        if proxy:
            kwargs["proxy"] = proxy

        url = (
            f"https://{self._lang}.wikipedia.org/api/rest_v1/page/summary/"
            + urllib.parse.quote(title)
        )
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": self._ua, "Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return ""
        return data.get("extract", "") or ""


__all__ = ["AsyncWikiClient"]
