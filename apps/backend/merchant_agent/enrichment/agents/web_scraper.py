"""scraper_v1 — augment a product with content from an allowlisted external page.

v1 ships narrow: takes a hint URL from raw_attributes['merchant_product_url']
or product.link, fetches it through ScraperClient (allowlist + robots.txt +
24h cache), records the page text under scraped_specs (truncated). The
follow-up PR's heavier scraping (manufacturer reviews, Q&A, per-category
domain ranking) consumes the same scraped_reviews/scraped_qna keys, which
are intentionally present in OUTPUT_KEYS from day one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from merchant_agent.enrichment import registry
from merchant_agent.enrichment.base import BaseEnrichmentAgent
from merchant_agent.enrichment.tools.scraper_client import ScraperClient
from merchant_agent.enrichment.types import ProductInput, StrategyOutput


_MAX_TEXT_CHARS = 8000


@registry.register
class WebScraperAgent(BaseEnrichmentAgent):
    STRATEGY = "scraper_v1"
    OUTPUT_KEYS = frozenset(
        {
            "scraped_specs",
            "scraped_reviews",
            "scraped_qna",
            "scraped_sources",
            "scraped_at",
            "scraped_category",
        }
    )
    DEFAULT_MODEL = None  # no LLM call in v1

    def __init__(self, scraper: ScraperClient | None = None) -> None:
        super().__init__()
        self._scraper = scraper or ScraperClient()

    def _invoke(self, product: ProductInput, context: dict[str, Any]) -> StrategyOutput:
        url = _pick_url(product)
        product_type = _category_from_context(context, product)

        sources: list[dict[str, str]] = []
        scraped_specs: dict[str, Any] = {}

        if url:
            doc = self._scraper.fetch(url, category=product_type)
            if doc is not None and doc.status_code == 200 and doc.text:
                sources.append(
                    {
                        "url": doc.url,
                        "domain": doc.domain,
                        "fetched_at": doc.fetched_at,
                        "from_cache": str(doc.from_cache).lower(),
                    }
                )
                scraped_specs["raw_text_excerpt"] = doc.text[:_MAX_TEXT_CHARS]

        attrs = {
            "scraped_specs": scraped_specs,
            "scraped_reviews": [],  # v1 leaves these empty; follow-up populates
            "scraped_qna": [],
            "scraped_sources": sources,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "scraped_category": product_type,
        }
        return StrategyOutput(
            product_id=product.product_id,
            strategy=self.STRATEGY,
            model=None,
            attributes=attrs,
        )


def _pick_url(p: ProductInput) -> str | None:
    # Prefer the top-level catalog column wired through ProductInput.link.
    # Fall back to raw_attributes keys for backward compat with rows that
    # carry the URL only in the JSONB blob.
    raw = p.raw_attributes or {}
    candidate = (
        p.link
        or raw.get("merchant_product_url")
        or raw.get("link")
        or raw.get("url")
    )
    if not isinstance(candidate, str):
        return None
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        return None
    return candidate


def _category_from_context(context: dict[str, Any], p: ProductInput) -> str:
    tax = context.get("taxonomy") or {}
    if isinstance(tax, dict):
        pt = tax.get("product_type")
        if pt:
            return str(pt)
    return p.category or "unknown"
