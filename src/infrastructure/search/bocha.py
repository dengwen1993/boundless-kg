"""Bocha AI Search (博查搜索) async backend.

Endpoint: ``POST https://api.bochaai.com/v1/web-search``
Auth:     ``Authorization: Bearer <API_KEY>``

Request body (``application/json``):

    {
      "query":     "<natural language query>",
      "summary":   true | false,            # optional — request AI summary
      "count":     <int 1..50>,              # optional — number of results
      "freshness": "oneDay" | "oneWeek" |   # optional — time filter
                   "oneMonth" | "oneYear" |
                   "noLimit"
    }

Response shape (truncated to the fields we read):

    {
      "_type": "SearchResponse",
      "queryContext": {"originalQuery": "..."},
      "webPages": {
        "webSearchUrl": "...",
        "totalEstimatedMatches": <int>,
        "value": [
          {
            "id":            "...",
            "name":          "<title>",
            "url":           "<link>",
            "siteName":      "...",
            "siteIcon":      "...",
            "snippet":       "<short summary>",
            "summary":       "<AI-generated longer summary>",
            "datePublished": "2024-07-22T00:00:00+08:00"
          },
          ...
        ]
      }
    }

The client maps each ``webPages.value[]`` item to the project's
``SearchResult`` dataclass so callers (``ResourceService``, ``NoteService``,
``@tool`` adapters) get a uniform shape regardless of which backend was
used. Unknown / missing fields degrade gracefully — ``title`` falls back
to ``"(" + url + ")"`` so downstream code never indexes on an empty
string.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .base import AsyncSearchClient, SearchResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bocha constants
# ---------------------------------------------------------------------------

#: Default Bocha web-search endpoint.
DEFAULT_ENDPOINT = "https://api.bochaai.com/v1/web-search"

#: Default ``count`` when the caller doesn't specify one. Bocha's docs
#: accept 1..50; 10 mirrors DuckDuckGo / mmx defaults in this project.
DEFAULT_COUNT = 10

#: Maximum ``count`` we will ever send — Bocha rejects larger payloads.
MAX_COUNT = 50

#: Recognised ``freshness`` values. Mirrors Bocha's documented enum.
VALID_FRESHNESS = frozenset({"oneDay", "oneWeek", "oneMonth", "oneYear", "noLimit"})


class BochaSearchClient(AsyncSearchClient):
    """Async Bocha AI Search backend.

    Implements the same :class:`AsyncSearchClient` protocol as
    :class:`DuckDuckGoClient` / :class:`MmxSearchClient` so the rest of
    the project (DualSearchClient, ResourceService, the LangChain
    ``@tool`` adapter) can swap it in without changes.

    Authentication is via ``Authorization: Bearer <BOCHA_API_KEY>``.
    The key MUST come from configuration (settings / env) — never
    hardcoded — per ENGINEERING_PLAN §1.1.
    """

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        proxy: str = "",
        timeout_sec: float = 15.0,
        default_count: int = DEFAULT_COUNT,
        include_summary: bool = True,
    ) -> None:
        if not api_key:
            # Surface a loud error rather than letting the request go
            # out with an empty Bearer token (which Bocha silently
            # rejects with a 401 that looks like a network failure).
            raise ValueError(
                "BochaSearchClient requires a non-empty api_key. "
                "Set BOCHA_API_KEY in .env (see ENGINEERING_PLAN §1.1)."
            )
        self._api_key = api_key
        self._endpoint = endpoint
        self._proxy = proxy
        self._timeout = timeout_sec
        self._default_count = max(1, min(int(default_count), MAX_COUNT))
        self._include_summary = include_summary

    async def search(
        self, query: str, *, num_results: int = DEFAULT_COUNT
    ) -> list[SearchResult]:
        """Call Bocha web-search and return up to ``num_results`` items."""
        import httpx

        if not query or not query.strip():
            return []

        # Clamp the request count to Bocha's documented range.
        count = max(1, min(int(num_results or self._default_count), MAX_COUNT))

        payload: dict[str, Any] = {"query": query, "count": count}
        if self._include_summary:
            payload["summary"] = True

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        client_kwargs: dict[str, Any] = {
            "timeout": self._timeout,
            "headers": headers,
        }
        if self._proxy:
            client_kwargs["proxy"] = self._proxy

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.post(self._endpoint, json=payload)
        except httpx.HTTPError as e:
            # Network / TLS / timeout — degrade to empty list so the
            # DualSearchClient fallback chain can try the next backend.
            log.warning("Bocha search network error: %s", e)
            return []

        # Bocha returns 4xx with a JSON body like
        # ``{"code": ..., "message": ...}``. Treat any non-2xx as an
        # empty result for graceful degradation.
        if resp.status_code >= 400:
            log.warning(
                "Bocha search HTTP %s: %s",
                resp.status_code,
                resp.text[:200] if resp.text else "<empty body>",
            )
            return []

        # Bocha serves Chinese-language responses in GBK / GB18030 bytes
        # while advertising ``Content-Type: application/json`` (no
        # charset). If we let httpx decode via UTF-8 the titles come
        # back as ``������`` mojibake. Decode raw bytes with GB18030
        # (a superset of GBK that handles every modern Chinese char)
        # before parsing JSON.
        raw = resp.content
        text = self._decode_chinese(raw)
        try:
            data = json.loads(text)
        except (ValueError, TypeError) as e:
            log.warning(
                "Bocha search returned non-JSON body (len=%d): %s",
                len(raw), e,
            )
            return []

        return self._parse_response(data, limit=count)

    @staticmethod
    def _decode_chinese(raw: bytes) -> str:
        """Decode ``raw`` as GB18030 with a UTF-8 fast-path.

        Order:
          1. UTF-8 — handles ``webSearchUrl``, ASCII metadata, and any
             future deployment that switches to UTF-8.
          2. GB18030 — the actual encoding Bocha uses today for
             Chinese titles / snippets.
        """
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("gb18030", errors="replace")

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(data: Any, *, limit: int) -> list[SearchResult]:
        """Map a Bocha JSON envelope to ``SearchResult`` instances.

        Robust against:
          * nested ``{"code", "data"}`` wrapper (the real Bocha
            envelope — actual SearchResponse lives under ``data.data``)
          * flat ``{"webPages": ...}`` (older / mocked responses)
          * missing ``webPages`` (e.g. error envelope)
          * missing per-item keys (``summary`` is AI-generated and may
            be empty even when ``summary=true`` was requested)
          * non-dict items in the ``value`` array
        """
        if not isinstance(data, dict):
            return []
        # Unwrap ``{"code":200, "data":{...}}`` if present — the real
        # SearchResponse lives at ``data["data"]``. Falls through
        # unchanged when no wrapper is present (e.g. tests / older API).
        if isinstance(data.get("data"), dict):
            data = data["data"]
        # When the wrapper carries a non-2xx ``code`` field, treat the
        # response as an error envelope and return no results. Bocha
        # sometimes still returns HTTP 200 with ``{"code":401,...}``
        # for auth failures.
        code = data.get("code")
        if isinstance(code, int) and code != 200:
            log.warning(
                "Bocha search envelope code=%s msg=%s",
                code, data.get("msg") or data.get("message"),
            )
            return []

        web_pages = data.get("webPages") or {}
        if not isinstance(web_pages, dict):
            return []
        values = web_pages.get("value") or []
        if not isinstance(values, list):
            return []

        results: list[SearchResult] = []
        for raw in values:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("name") or "").strip()
            url = str(raw.get("url") or "").strip()
            if not url:
                # Bocha guarantees ``url`` on every result, but if it's
                # missing we have nothing actionable — skip.
                continue
            if not title:
                # Fallback so downstream code never indexes on "".
                title = f"({url})"

            snippet = str(raw.get("snippet") or "").strip()
            summary = str(raw.get("summary") or "").strip()
            # Prefer the AI summary when it's meaningfully longer than
            # the raw snippet — note-generation pipelines (see
            # src/domain/note/generator.py) read this as evidence text.
            evidence = summary if len(summary) > len(snippet) else snippet

            results.append(
                SearchResult(
                    title=title,
                    link=url,
                    snippet=evidence,
                    source="bocha",
                )
            )
            if len(results) >= limit:
                break
        return results


__all__ = [
    "BochaSearchClient",
    "DEFAULT_ENDPOINT",
    "DEFAULT_COUNT",
    "MAX_COUNT",
    "VALID_FRESHNESS",
]