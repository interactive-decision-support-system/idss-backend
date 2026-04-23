"""Unit tests for the Tavily websearch wrapper + WebSearchClient facade.

Mocks Tavily with a fake httpx-style client — no network.
"""

from __future__ import annotations

import json

import pytest

from app.enrichment.tools.websearch_client import (
    NullBackend,
    SearchResult,
    TavilyBackend,
    WebSearchClient,
)


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class _FakeHttp:
    def __init__(self, response: _Resp):
        self._resp = response
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, *, json=None, timeout=None):
        self.calls.append((url, json))
        return self._resp


def test_tavily_missing_api_key_returns_empty():
    http = _FakeHttp(_Resp(200, {"results": []}))
    backend = TavilyBackend(api_key=None, http_client=http)
    assert backend.search("laptop ram_gb") == []
    assert http.calls == []


def test_tavily_parses_results():
    http = _FakeHttp(
        _Resp(
            200,
            {
                "results": [
                    {
                        "url": "https://lenovo.com/x1",
                        "title": "X1",
                        "content": "Thinkpad X1 has 16 GB RAM",
                    },
                    {
                        "url": "https://example.com/other",
                        "title": "Other",
                        "content": "whatever",
                    },
                ]
            },
        )
    )
    backend = TavilyBackend(api_key="sk-test", http_client=http)
    results = backend.search("x1", max_results=5)
    assert len(results) == 2
    assert results[0].url == "https://lenovo.com/x1"
    assert results[0].domain == "lenovo.com"
    assert "Thinkpad" in results[0].snippet


def test_tavily_bad_status_returns_empty():
    http = _FakeHttp(_Resp(status_code=500, body={}))
    backend = TavilyBackend(api_key="sk", http_client=http)
    assert backend.search("x") == []


def test_tavily_respects_max_results():
    http = _FakeHttp(
        _Resp(
            200,
            {
                "results": [
                    {"url": f"https://lenovo.com/{i}", "title": "", "content": ""}
                    for i in range(10)
                ]
            },
        )
    )
    backend = TavilyBackend(api_key="sk", http_client=http)
    results = backend.search("x", max_results=3)
    assert len(results) == 3


def test_client_disabled_by_env(monkeypatch):
    http = _FakeHttp(
        _Resp(200, {"results": [{"url": "https://lenovo.com/x", "title": "", "content": ""}]})
    )
    backend = TavilyBackend(api_key="sk", http_client=http)
    monkeypatch.setenv("ENRICHMENT_DISABLE_WEBSEARCH", "1")
    client = WebSearchClient(backend=backend)
    assert client.search("x") == []
    # Backend was never hit.
    assert http.calls == []


def test_client_empty_query_returns_empty(monkeypatch):
    monkeypatch.delenv("ENRICHMENT_DISABLE_WEBSEARCH", raising=False)
    client = WebSearchClient(backend=NullBackend())
    assert client.search("") == []
    assert client.search("   ") == []
