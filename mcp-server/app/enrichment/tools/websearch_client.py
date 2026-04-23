"""Websearch client used by the scraper_v2 gap-filler agent.

Issue #118: the scraper agent needs to find candidate source URLs for
product-spec gaps the parser/specialist couldn't close from raw input.
Tavily is the default provider; Brave / Google-CSE can be swapped in by
implementing the ``WebSearchBackend`` protocol and injecting it.

Design notes:
  - The backend is a tiny protocol so tests can inject a fake without
    touching the network.
  - Tavily uses HTTP POST; we hit it with ``httpx`` but fall back to an
    inert no-op client when ``httpx`` isn't installed (keeps module
    import-safe in minimal environments).
  - ``ENRICHMENT_DISABLE_WEBSEARCH=1`` short-circuits every call to
    return ``[]``. The scraper agent also respects this; belt-and-braces
    so nothing reaches the network in CI or offline runs.
  - Missing ``TAVILY_API_KEY`` is NOT a crash — the agent detects that
    earlier (and early-returns empty outputs). The client still logs a
    warning on every invocation in case it's instantiated directly.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_DEFAULT_TIMEOUT_SECONDS = 10


@dataclass
class SearchResult:
    """One hit from a websearch backend. Domain is derived from url."""

    url: str
    title: str
    snippet: str
    domain: str


class WebSearchBackend(Protocol):
    """Implement this to swap providers (Brave, Google-CSE, ...).

    Contract: return at most ``max_results`` results; never raise on
    transient errors — log and return ``[]``.
    """

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        ...


def _domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001 - defensive
        return ""


class TavilyBackend:
    """Tavily Search API wrapper. Thin: no caching, no retry — the caller
    (ScraperClient) already caches, and transient errors fall back to
    empty results."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        http_client: Any | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key or os.getenv("TAVILY_API_KEY")
        self._timeout = timeout_seconds
        if http_client is not None:
            self._http = http_client
            return
        try:
            import httpx

            self._http = httpx.Client(timeout=timeout_seconds)
        except ImportError:
            self._http = None

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        if not self._api_key:
            logger.warning("tavily_search_missing_api_key")
            return []
        if self._http is None:
            logger.warning("tavily_search_no_http_client")
            return []
        payload = {
            "api_key": self._api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }
        try:
            resp = self._http.post(_TAVILY_ENDPOINT, json=payload, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 - never crash the run
            logger.warning("tavily_search_failed: %s", exc)
            return []
        if getattr(resp, "status_code", 0) != 200:
            logger.warning(
                "tavily_search_bad_status: %s", getattr(resp, "status_code", None)
            )
            return []
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("tavily_search_bad_json: %s", exc)
            return []
        results: list[SearchResult] = []
        for item in data.get("results", []) or []:
            url = str(item.get("url") or "")
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=str(item.get("title") or ""),
                    snippet=str(item.get("content") or ""),
                    domain=_domain_of(url),
                )
            )
        return results[:max_results]


class NullBackend:
    """Inert backend used when websearch is explicitly disabled."""

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        return []


class WebSearchClient:
    """Facade the scraper agent uses. Wraps a backend; honors
    ``ENRICHMENT_DISABLE_WEBSEARCH=1`` before delegating."""

    def __init__(self, backend: WebSearchBackend | None = None) -> None:
        self._backend = backend or TavilyBackend()

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        if os.getenv("ENRICHMENT_DISABLE_WEBSEARCH") == "1":
            return []
        if not query or not query.strip():
            return []
        return self._backend.search(query, max_results=max_results)
