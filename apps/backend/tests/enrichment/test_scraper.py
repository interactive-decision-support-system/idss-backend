"""Phase 1 scaffold: scraper allowlist + cache + log behavior, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from merchant_agent.enrichment.tools import scraper_client


class _Resp:
    def __init__(self, status: int = 200, text: str = "") -> None:
        self.status_code = status
        self.text = text


class _FakeHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.routes: dict[str, _Resp] = {}

    def add(self, url: str, resp: _Resp) -> None:
        self.routes[url] = resp

    def get(self, url: str, **kw):
        self.calls.append((url, kw))
        if url in self.routes:
            return self.routes[url]
        # default: empty robots.txt + missing pages
        if url.endswith("/robots.txt"):
            return _Resp(200, "")
        return _Resp(404, "")


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper_client, "_CACHE_ROOT", tmp_path / "scraped")
    monkeypatch.setattr(scraper_client, "_LOG_PATH", tmp_path / "scraper_log.jsonl")
    monkeypatch.setattr(scraper_client, "_PER_DOMAIN_MIN_INTERVAL", 0.0)
    scraper_client._DOMAIN_LAST_HIT.clear()
    scraper_client._ROBOTS_CACHE.clear()
    yield


def _write_allowlist(tmp_path: Path, mapping: dict[str, list[str]]) -> None:
    cfg = tmp_path / "scraper_sources.yaml"
    lines: list[str] = []
    for cat, doms in mapping.items():
        lines.append(f"{cat}:")
        for d in doms:
            lines.append(f"  - {d}")
    cfg.write_text("\n".join(lines), encoding="utf-8")


def test_blocks_url_outside_allowlist(monkeypatch, tmp_path):
    _write_allowlist(tmp_path, {"laptop": ["example-manufacturer.com"]})
    monkeypatch.setattr(scraper_client, "_config_path", lambda: tmp_path / "scraper_sources.yaml")
    http = _FakeHttp()
    client = scraper_client.ScraperClient(http_client=http)
    doc = client.fetch("https://random-domain.com/page", category="laptop")
    assert doc is None
    log_lines = (tmp_path / "scraper_log.jsonl").read_text().splitlines()
    assert json.loads(log_lines[-1])["status"] == "blocked_allowlist"


def test_fetches_when_allowlisted_and_caches(monkeypatch, tmp_path):
    _write_allowlist(tmp_path, {"laptop": ["example-manufacturer.com"]})
    monkeypatch.setattr(scraper_client, "_config_path", lambda: tmp_path / "scraper_sources.yaml")
    http = _FakeHttp()
    http.add("https://example-manufacturer.com/spec/x", _Resp(200, "<html>spec body</html>"))
    client = scraper_client.ScraperClient(http_client=http)

    doc = client.fetch("https://example-manufacturer.com/spec/x", category="laptop")
    assert doc is not None
    assert doc.from_cache is False
    assert "spec body" in doc.text

    # Second call hits cache, no second HTTP request to that URL.
    http2 = _FakeHttp()
    client2 = scraper_client.ScraperClient(http_client=http2)
    cached = client2.fetch("https://example-manufacturer.com/spec/x", category="laptop")
    assert cached is not None
    assert cached.from_cache is True
    assert "spec body" in cached.text


def test_blocks_when_robots_disallow(monkeypatch, tmp_path):
    _write_allowlist(tmp_path, {"laptop": ["example-manufacturer.com"]})
    monkeypatch.setattr(scraper_client, "_config_path", lambda: tmp_path / "scraper_sources.yaml")
    http = _FakeHttp()
    http.add(
        "https://example-manufacturer.com/robots.txt",
        _Resp(200, "User-agent: *\nDisallow: /spec/"),
    )
    client = scraper_client.ScraperClient(http_client=http)
    doc = client.fetch("https://example-manufacturer.com/spec/x", category="laptop")
    assert doc is None
    log_lines = (tmp_path / "scraper_log.jsonl").read_text().splitlines()
    assert json.loads(log_lines[-1])["status"] == "blocked_robots"


def test_subdomain_match(monkeypatch, tmp_path):
    _write_allowlist(tmp_path, {"laptop": ["example-manufacturer.com"]})
    monkeypatch.setattr(scraper_client, "_config_path", lambda: tmp_path / "scraper_sources.yaml")
    http = _FakeHttp()
    http.add("https://shop.example-manufacturer.com/p", _Resp(200, "ok"))
    client = scraper_client.ScraperClient(http_client=http)
    doc = client.fetch("https://shop.example-manufacturer.com/p", category="laptop")
    assert doc is not None
