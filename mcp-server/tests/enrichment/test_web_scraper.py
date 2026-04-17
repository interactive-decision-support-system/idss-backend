"""Phase 2: scraper_v1 — produces ScrapedDoc-shaped output, leaves reviews/qna empty in v1."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.enrichment import registry
from app.enrichment.agents.web_scraper import WebScraperAgent
from app.enrichment.tools import scraper_client
from app.enrichment.tools.scraper_client import ScraperClient
from app.enrichment.types import ProductInput


class _Resp:
    def __init__(self, status=200, text=""):
        self.status_code = status
        self.text = text


class _FakeHttp:
    def __init__(self, routes=None):
        self.routes = routes or {}

    def get(self, url, **kw):
        if url.endswith("/robots.txt"):
            return _Resp(200, "")
        return self.routes.get(url, _Resp(404, ""))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    registry._reset_for_tests()
    registry.register(WebScraperAgent)
    monkeypatch.setattr(scraper_client, "_CACHE_ROOT", tmp_path / "scraped")
    monkeypatch.setattr(scraper_client, "_LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(scraper_client, "_PER_DOMAIN_MIN_INTERVAL", 0.0)
    cfg = tmp_path / "scraper_sources.yaml"
    cfg.write_text("laptop:\n  - example-manufacturer.com\n", encoding="utf-8")
    monkeypatch.setattr(scraper_client, "_config_path", lambda: cfg)
    scraper_client._DOMAIN_LAST_HIT.clear()
    scraper_client._ROBOTS_CACHE.clear()
    yield
    registry._reset_for_tests()


def _product(url="https://example-manufacturer.com/spec/x"):
    return ProductInput(
        product_id=uuid4(),
        title="ThinkPad",
        category="Electronics",
        raw_attributes={"merchant_product_url": url},
    )


def test_scraper_emits_all_declared_keys():
    http = _FakeHttp({"https://example-manufacturer.com/spec/x": _Resp(200, "<html>spec</html>")})
    agent = WebScraperAgent(scraper=ScraperClient(http_client=http))
    result = agent.run(_product(), context={"taxonomy": {"product_type": "laptop"}})
    assert result.success is True
    attrs = result.output.attributes
    assert set(attrs.keys()) == {
        "scraped_specs",
        "scraped_reviews",
        "scraped_qna",
        "scraped_sources",
        "scraped_at",
        "scraped_category",
    }
    assert attrs["scraped_reviews"] == []
    assert attrs["scraped_qna"] == []
    assert attrs["scraped_sources"]
    assert attrs["scraped_specs"]["raw_text_excerpt"].startswith("<html>")


def test_scraper_with_no_url_returns_empty_payload():
    agent = WebScraperAgent(scraper=ScraperClient(http_client=_FakeHttp()))
    result = agent.run(
        ProductInput(product_id=uuid4(), title="x", category="Electronics"),
        context={"taxonomy": {"product_type": "laptop"}},
    )
    assert result.success is True
    assert result.output.attributes["scraped_sources"] == []
    assert result.output.attributes["scraped_specs"] == {}


def test_scraper_blocked_url_yields_empty_sources():
    agent = WebScraperAgent(scraper=ScraperClient(http_client=_FakeHttp()))
    result = agent.run(
        _product("https://not-allowed.com/p"),
        context={"taxonomy": {"product_type": "laptop"}},
    )
    assert result.success is True
    assert result.output.attributes["scraped_sources"] == []
