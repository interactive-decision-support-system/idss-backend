"""scraper_v1 (scraper-v2 rewrite, issue #118) — gated websearch + crawl.

Covers:
  - no-gap early return
  - no-API-key early return
  - allowlist block
  - robots block (cascades through ScraperClient)
  - conflict (parser wins vs scraper — verified through composer in composer tests)
  - citation shape validation (URL must come from fetched pages)
  - cost-cap enforcement (max 2 searches + 3 fetches + 1 LLM call)

All tests use an in-memory fake websearch backend and the existing
``_FakeHttp`` crawler mock. No network.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.enrichment import registry
from app.enrichment.agents.web_scraper import WebScraperAgent
from app.enrichment.tools import scraper_client
from app.enrichment.tools.llm_client import LLMResponse
from app.enrichment.tools.scraper_client import ScraperClient
from app.enrichment.tools.websearch_client import (
    NullBackend,
    SearchResult,
    WebSearchClient,
)
from app.enrichment.types import ProductInput


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status=200, text=""):
        self.status_code = status
        self.text = text


class _FakeHttp:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls: list[str] = []

    def get(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return self.routes.get(url, _Resp(200, ""))
        return self.routes.get(url, _Resp(404, ""))


class _FakeSearchBackend:
    """Backend that returns scripted results keyed by query substring."""

    def __init__(self, script: dict[str, list[SearchResult]] | None = None):
        self.script = script or {}
        self.calls: list[str] = []

    def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        self.calls.append(query)
        for key, results in self.script.items():
            if key in query:
                return results[:max_results]
        return []


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def complete(self, **kw):
        self.calls.append(kw)
        return LLMResponse(
            text="",
            model=kw.get("model") or "gpt-5-mini",
            input_tokens=10,
            output_tokens=10,
            cost_usd=0.0001,
            latency_ms=1,
            parsed_json=self.payload,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    registry._reset_for_tests()
    registry.register(WebScraperAgent)
    monkeypatch.setattr(scraper_client, "_CACHE_ROOT", tmp_path / "scraped")
    monkeypatch.setattr(scraper_client, "_LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(scraper_client, "_PER_DOMAIN_MIN_INTERVAL", 0.0)
    cfg = tmp_path / "scraper_sources.yaml"
    cfg.write_text(
        "laptop:\n  - lenovo.com\n  - dell.com\n  - hp.com\n", encoding="utf-8"
    )
    monkeypatch.setattr(scraper_client, "_config_path", lambda: cfg)
    scraper_client._DOMAIN_LAST_HIT.clear()
    scraper_client._ROBOTS_CACHE.clear()
    # Ensure the API-key gate doesn't short-circuit the injected-backend path.
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.delenv("ENRICHMENT_DISABLE_WEBSEARCH", raising=False)
    yield
    registry._reset_for_tests()


def _product(title="ThinkPad X1 Carbon", brand="Lenovo"):
    return ProductInput(
        product_id=uuid4(),
        title=title,
        brand=brand,
        category="Electronics",
    )


def _agent(websearch_backend=None, http=None, llm_payload=None, llm=None):
    backend = websearch_backend if websearch_backend is not None else _FakeSearchBackend()
    websearch = WebSearchClient(backend=backend)
    scraper = ScraperClient(http_client=http or _FakeHttp())
    if llm is None and llm_payload is not None:
        llm = _FakeLLM(llm_payload)
    elif llm is None:
        llm = _FakeLLM({"extracted": {}})
    return WebScraperAgent(scraper=scraper, websearch=websearch, llm=llm)


# ---------------------------------------------------------------------------
# Early-return paths
# ---------------------------------------------------------------------------


def test_no_missing_fields_early_returns_empty():
    agent = _agent()
    result = agent.run(
        _product(),
        context={"taxonomy": {"product_type": "laptop"}, "missing_fields": []},
    )
    assert result.success is True
    attrs = result.output.attributes
    assert attrs["scraped_specs"] == {}
    assert attrs["scraped_sources"] == []
    assert attrs["scraped_reviews"] == []
    assert attrs["scraped_qna"] == []
    assert attrs["scraped_category"] == "laptop"
    assert result.output.notes == "no_missing_fields"


def test_missing_api_key_with_default_backend_early_returns(monkeypatch):
    # Don't inject a fake backend — use the default Tavily backend with no key.
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    agent = WebScraperAgent()
    result = agent.run(
        _product(),
        context={
            "taxonomy": {"product_type": "laptop"},
            "missing_fields": ["ram_gb"],
        },
    )
    assert result.success is True
    assert result.output.attributes["scraped_specs"] == {}
    assert result.output.notes == "websearch_no_api_key"


def test_disable_env_var_short_circuits(monkeypatch):
    monkeypatch.setenv("ENRICHMENT_DISABLE_WEBSEARCH", "1")
    agent = _agent()
    result = agent.run(
        _product(),
        context={
            "taxonomy": {"product_type": "laptop"},
            "missing_fields": ["ram_gb"],
        },
    )
    assert result.success is True
    assert result.output.notes == "websearch_disabled"
    assert result.output.attributes["scraped_specs"] == {}


# ---------------------------------------------------------------------------
# Allowlist and robots gates
# ---------------------------------------------------------------------------


def test_allowlist_blocks_offsite_results():
    """Search returns a hit on a non-allowlisted domain → agent skips it."""
    backend = _FakeSearchBackend(
        {
            "ram_gb": [
                SearchResult(
                    url="https://randomblog.com/thinkpad",
                    title="ThinkPad review",
                    snippet="16GB RAM",
                    domain="randomblog.com",
                )
            ]
        }
    )
    http = _FakeHttp({})  # should never be called
    agent = _agent(websearch_backend=backend, http=http, llm_payload={"extracted": {}})
    result = agent.run(
        _product(),
        context={
            "taxonomy": {"product_type": "laptop"},
            "missing_fields": ["ram_gb"],
        },
    )
    assert result.success is True
    assert result.output.attributes["scraped_sources"] == []
    # The non-robots HTTP calls list should be empty — we never reached fetch.
    non_robots = [u for u in http.calls if not u.endswith("/robots.txt")]
    assert non_robots == []


def test_robots_disallow_skips_page():
    url = "https://lenovo.com/thinkpad"
    http = _FakeHttp(
        {
            "https://lenovo.com/robots.txt": _Resp(
                200, "User-agent: *\nDisallow: /thinkpad"
            ),
            url: _Resp(200, "16GB RAM 512GB SSD"),
        }
    )
    backend = _FakeSearchBackend(
        {
            "ram_gb": [
                SearchResult(url=url, title="ThinkPad", snippet="", domain="lenovo.com"),
            ]
        }
    )
    agent = _agent(websearch_backend=backend, http=http, llm_payload={"extracted": {}})
    result = agent.run(
        _product(),
        context={
            "taxonomy": {"product_type": "laptop"},
            "missing_fields": ["ram_gb"],
        },
    )
    assert result.success is True
    # Blocked by robots → no source recorded, no LLM call fired.
    assert result.output.attributes["scraped_sources"] == []


# ---------------------------------------------------------------------------
# Citation shape validation — URL must come from fetched pages
# ---------------------------------------------------------------------------


def test_citation_shape_rejects_fabricated_url():
    url = "https://lenovo.com/x1"
    http = _FakeHttp({url: _Resp(200, "ThinkPad X1 with 16 GB RAM")})
    backend = _FakeSearchBackend(
        {
            "ram_gb": [
                SearchResult(url=url, title="X1", snippet="16 GB RAM", domain="lenovo.com")
            ]
        }
    )
    # LLM tries to cite a URL we never fetched — should be dropped.
    llm_payload = {
        "extracted": {
            "ram_gb": {
                "value": 16,
                "source_url": "https://fabricated.com/page",
                "snippet": "hallucinated",
            }
        }
    }
    agent = _agent(websearch_backend=backend, http=http, llm_payload=llm_payload)
    result = agent.run(
        _product(),
        context={
            "taxonomy": {"product_type": "laptop"},
            "missing_fields": ["ram_gb"],
        },
    )
    assert result.success is True
    # Fabricated URL rejected → no spec lands.
    assert result.output.attributes["scraped_specs"] == {}
    # We DID reach the allowlisted page; source should be recorded.
    assert len(result.output.attributes["scraped_sources"]) == 1
    assert result.output.attributes["scraped_sources"][0]["url"] == url


def test_citation_shape_accepts_valid_url():
    url = "https://lenovo.com/x1"
    http = _FakeHttp({url: _Resp(200, "ThinkPad X1 16 GB RAM")})
    backend = _FakeSearchBackend(
        {
            "ram_gb": [
                SearchResult(url=url, title="X1", snippet="16 GB RAM", domain="lenovo.com"),
            ]
        }
    )
    llm_payload = {
        "extracted": {
            "ram_gb": {
                "value": 16,
                "source_url": url,
                "snippet": "ThinkPad X1 16 GB RAM",
            }
        }
    }
    agent = _agent(websearch_backend=backend, http=http, llm_payload=llm_payload)
    result = agent.run(
        _product(),
        context={
            "taxonomy": {"product_type": "laptop"},
            "missing_fields": ["ram_gb"],
        },
    )
    assert result.success is True
    specs = result.output.attributes["scraped_specs"]
    assert "ram_gb" in specs
    entry = specs["ram_gb"]
    assert entry["value"] == 16
    assert entry["source_url"] == url
    assert entry["source_domain"] == "lenovo.com"
    assert entry["snippet"] == "ThinkPad X1 16 GB RAM"
    assert "extracted_at" in entry


# ---------------------------------------------------------------------------
# Cost cap enforcement
# ---------------------------------------------------------------------------


def test_cost_caps_limit_searches_and_fetches():
    """Budget: 2 searches + 3 fetches + 1 LLM call. Many missing fields
    shouldn't drive spend above those caps."""
    routes = {
        f"https://lenovo.com/page{i}": _Resp(200, f"spec page {i}") for i in range(10)
    }
    http = _FakeHttp(routes)
    # Every query returns 5 URLs from the allowlisted domain.
    results = [
        SearchResult(
            url=f"https://lenovo.com/page{i}",
            title=f"Page {i}",
            snippet="",
            domain="lenovo.com",
        )
        for i in range(5)
    ]
    # Same script matches any missing field substring.
    backend = _FakeSearchBackend({"": results})

    llm = _FakeLLM({"extracted": {}})
    agent = _agent(websearch_backend=backend, http=http, llm=llm)
    context = {
        "taxonomy": {"product_type": "laptop"},
        "missing_fields": [
            "ram_gb",
            "storage_gb",
            "cpu_model",
            "gpu_model",
            "display_size_in",
            "refresh_rate_hz",
            "weight_kg",
            "battery_life_hours",
            "os",
        ],
    }
    result = agent.run(_product(), context=context)
    assert result.success is True
    budget = context.get("_scraper_budget") or {}
    assert budget["search_calls"] <= 2
    assert budget["fetch_calls"] <= 3
    assert budget["llm_calls"] <= 1
    # LLM was called exactly once because at least one page fetch succeeded.
    assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# LLM-failure resilience
# ---------------------------------------------------------------------------


def test_llm_failure_falls_back_to_empty_specs():
    url = "https://lenovo.com/x1"
    http = _FakeHttp({url: _Resp(200, "spec body")})
    backend = _FakeSearchBackend(
        {
            "ram_gb": [
                SearchResult(url=url, title="X1", snippet="", domain="lenovo.com")
            ]
        }
    )

    class _BoomLLM:
        def complete(self, **kw):
            raise RuntimeError("llm exploded")

    agent = _agent(websearch_backend=backend, http=http, llm=_BoomLLM())
    result = agent.run(
        _product(),
        context={
            "taxonomy": {"product_type": "laptop"},
            "missing_fields": ["ram_gb"],
        },
    )
    assert result.success is True
    # No specs extracted, but the source we fetched was recorded.
    assert result.output.attributes["scraped_specs"] == {}
    assert len(result.output.attributes["scraped_sources"]) == 1


# ---------------------------------------------------------------------------
# Null backend (disabled explicitly via WebSearchClient)
# ---------------------------------------------------------------------------


def test_null_backend_returns_no_sources():
    agent = _agent(websearch_backend=NullBackend(), http=_FakeHttp())
    result = agent.run(
        _product(),
        context={
            "taxonomy": {"product_type": "laptop"},
            "missing_fields": ["ram_gb"],
        },
    )
    assert result.success is True
    # No results from search → no fetches → no specs.
    assert result.output.attributes["scraped_sources"] == []
    assert result.output.attributes["scraped_specs"] == {}
