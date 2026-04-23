"""scraper_v1 — gated websearch + crawl gap-filler (issue #118).

Runs post-specialist / pre-composer in the fixed pipeline. Reads
``ctx['missing_fields']`` (populated by the runner) and, for each
missing field:

  1. queries a websearch backend for ``{brand} {title} {field}``
  2. filters results to the per-category allowlist
  3. fetches up to N pages via the existing ScraperClient
     (robots.txt, 24h cache, per-domain rate limit)
  4. asks the LLM to extract values from excerpts WITH citations

Cost caps per product:
  - 2 websearch calls
  - 3 page fetches
  - 1 LLM extraction call

Emission schema (kept flat so composer flattens one level as with
parsed_specs):

    scraped_specs: {
        <field>: {
            "value": <scalar>,
            "source_url": "https://...",
            "source_domain": "lenovo.com",
            "snippet": "...",
            "extracted_at": "ISO-8601",
        }
    }
    scraped_sources: [
        {"url": ..., "domain": ..., "query": ..., "fetched_at": ...},
    ]
    scraped_at: ISO-8601
    scraped_category: "laptop"
    scraped_reviews: [] / scraped_qna: []   # out of scope per #118

Gated behaviors:
  - no TAVILY_API_KEY  → early-return empty outputs + warn
  - ENRICHMENT_DISABLE_WEBSEARCH=1 → same
  - missing_fields == [] → early-return empty outputs
  - category has no gap list → same (handled upstream via empty
    missing_fields)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.enrichment import registry
from app.enrichment.base import BaseEnrichmentAgent
from app.enrichment.tools.llm_client import LLMClient, default_model
from app.enrichment.tools.scraper_client import ScraperClient, is_allowed
from app.enrichment.tools.websearch_client import WebSearchClient
from app.enrichment.types import ProductInput, StrategyOutput


logger = logging.getLogger(__name__)


# Cost caps per product — tuned to keep per-product spend predictable.
# See issue #118 "Per-product cost caps".
_MAX_SEARCH_CALLS = 2
_MAX_PAGE_FETCHES = 3
_MAX_LLM_CALLS = 1

# Truncate each page's extracted text before handing it to the LLM so one
# fetched page can't blow the token budget. Multiple pages share this per
# page (not in total).
_MAX_PAGE_CHARS_FOR_LLM = 4000
# Upper bound on search results per query.
_MAX_SEARCH_RESULTS = 5
# LLM extraction token budget — generous enough for 10 fields × cited
# shape, small enough to stay within gpt-5-mini per-call pricing.
_MAX_COMPLETION_TOKENS = 2000


# Per-category gap fields. Mirrors the set the runner consults when
# computing ctx['missing_fields']. Duplicated here rather than imported
# from the runner so the agent stays usable standalone in tests.
_GAP_FIELDS: dict[str, tuple[str, ...]] = {
    "laptop": (
        "ram_gb",
        "storage_gb",
        "cpu_model",
        "gpu_model",
        "display_size_in",
        "refresh_rate_hz",
        "weight_kg",
        "battery_life_hours",
        "os",
    ),
}


# Alternate spellings a gap field may appear under in parser output or raw
# attributes. Checked by the runner's gap detection so we don't queue a
# scrape for a field the parser already filled under a different key
# (e.g. parser emits `screen_size_in`, gap list uses `display_size_in`).
# Issue #115 rec #4 tracks normalizing this upstream; until then, this map
# prevents the scraper from burning cost on redundant fetches.
_GAP_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "display_size_in": (
        "screen_size_in",
        "screen_size_inch",
        "screen_size_inches",
        "screen_size",
    ),
    "weight_kg": ("weight_lbs", "weight_g", "weight_grams", "weight"),
    "cpu_model": ("cpu",),
    "gpu_model": ("gpu",),
    "refresh_rate_hz": ("refresh_rate", "display_refresh_rate"),
}


def gap_fields_for(category: str) -> tuple[str, ...]:
    """Public accessor so the runner doesn't re-define this list."""
    if not category:
        return ()
    return _GAP_FIELDS.get(category.lower(), ())


def aliases_for(gap_field: str) -> tuple[str, ...]:
    """Alternate key spellings a gap field may already appear under."""
    return _GAP_FIELD_ALIASES.get(gap_field, ())


_SYSTEM = (
    "You extract product specifications from excerpts of web pages. For each "
    "field the user asks about, look through the provided excerpts and return a "
    "single grounded value WITH its source URL and a short snippet (<=200 "
    "chars) from the excerpt that proves the value.\n"
    "\n"
    "Return JSON with a single key ``extracted`` — an object mapping each "
    "field name the user asked about (or a subset) to:\n"
    "  {\n"
    '    "value": <scalar: number | string>,\n'
    '    "source_url": "<url that appeared in the user prompt>",\n'
    '    "snippet": "<<=200 chars copied from the excerpt>"\n'
    "  }\n"
    "\n"
    "Rules:\n"
    "  - Never invent values. If no excerpt mentions the field clearly, OMIT "
    "the field from the output.\n"
    "  - source_url MUST be one of the URLs shown in the user prompt — do not "
    "fabricate.\n"
    "  - snippet MUST be copied verbatim from that URL's excerpt (<=200 chars).\n"
    "  - Numeric fields (ram_gb, storage_gb, weight_kg, ...) must be numbers, "
    "not strings; use the unit the field name implies.\n"
    "  - If the same field has conflicting values across sources, pick the "
    "one on the most authoritative domain (manufacturer > retailer)."
)


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
    DEFAULT_MODEL = "gpt-5-mini"

    def __init__(
        self,
        *,
        scraper: ScraperClient | None = None,
        websearch: WebSearchClient | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        super().__init__()
        self._scraper = scraper or ScraperClient()
        self._websearch = websearch or WebSearchClient()
        self._llm = llm or LLMClient()

    def _invoke(self, product: ProductInput, context: dict[str, Any]) -> StrategyOutput:
        now_iso = datetime.now(timezone.utc).isoformat()
        category = _category_from_context(context, product)
        missing_fields = _coerce_missing_fields(context.get("missing_fields"))

        empty_payload: dict[str, Any] = {
            "scraped_specs": {},
            "scraped_reviews": [],
            "scraped_qna": [],
            "scraped_sources": [],
            "scraped_at": now_iso,
            "scraped_category": category,
        }

        # Gate 1: no gaps to fill → no work to do.
        if not missing_fields:
            return StrategyOutput(
                product_id=product.product_id,
                strategy=self.STRATEGY,
                model=None,
                attributes=empty_payload,
                notes="no_missing_fields",
            )

        # Gate 2: websearch disabled or unconfigured → no fetches.
        if os.getenv("ENRICHMENT_DISABLE_WEBSEARCH") == "1":
            logger.info("scraper_v1_disabled_by_env")
            return StrategyOutput(
                product_id=product.product_id,
                strategy=self.STRATEGY,
                model=None,
                attributes=empty_payload,
                notes="websearch_disabled",
            )
        if not os.getenv("TAVILY_API_KEY") and _using_default_websearch(self._websearch):
            logger.warning("scraper_v1_no_api_key_skipping")
            return StrategyOutput(
                product_id=product.product_id,
                strategy=self.STRATEGY,
                model=None,
                attributes=empty_payload,
                notes="websearch_no_api_key",
            )

        # --- budgeted search + fetch loop ---
        search_calls = 0
        fetch_calls = 0
        scraped_sources: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        # Page excerpts keyed by URL, used as LLM input.
        page_excerpts: dict[str, str] = {}

        brand = product.brand or ""
        title = product.title or ""

        for field in missing_fields:
            if search_calls >= _MAX_SEARCH_CALLS or fetch_calls >= _MAX_PAGE_FETCHES:
                break
            query = _build_query(brand, title, field)
            if not query:
                continue
            search_calls += 1
            results = self._websearch.search(query, max_results=_MAX_SEARCH_RESULTS)
            for sr in results:
                if fetch_calls >= _MAX_PAGE_FETCHES:
                    break
                if sr.url in seen_urls:
                    continue
                # Allowlist check happens inside ScraperClient.fetch too,
                # but skipping early avoids burning the fetch-call counter
                # on guaranteed-blocked domains.
                if not is_allowed(category, sr.url):
                    logger.debug(
                        "scraper_v1_blocked_domain",
                        extra={"domain": sr.domain, "category": category},
                    )
                    continue
                fetch_calls += 1
                doc = self._scraper.fetch(sr.url, category=category)
                if doc is None or doc.status_code != 200 or not doc.text:
                    continue
                seen_urls.add(sr.url)
                page_excerpts[sr.url] = doc.text[:_MAX_PAGE_CHARS_FOR_LLM]
                scraped_sources.append(
                    {
                        "url": sr.url,
                        "domain": sr.domain,
                        "query": query,
                        "fetched_at": doc.fetched_at,
                    }
                )

        # Gate 3: nothing usable fetched → return empty specs but record
        # the sources we at least attempted (there won't be any here since
        # we only append on success; left for symmetry).
        if not page_excerpts:
            return StrategyOutput(
                product_id=product.product_id,
                strategy=self.STRATEGY,
                model=None,
                attributes={
                    **empty_payload,
                    "scraped_sources": scraped_sources,
                },
                notes="no_pages_fetched",
            )

        # --- single LLM extraction call with citations ---
        llm_calls = 0
        scraped_specs: dict[str, Any] = {}
        resp = None  # noqa: F841 - bound below; keeps None on failure branch
        if llm_calls < _MAX_LLM_CALLS:
            llm_calls += 1
            try:
                resp = self._llm.complete(
                    system=_SYSTEM,
                    user=_format_extraction_prompt(missing_fields, page_excerpts),
                    model=context.get("model") or default_model(),
                    json_mode=True,
                    max_tokens=_MAX_COMPLETION_TOKENS,
                    temperature=0.0,
                )
                context["_last_cost_usd"] = resp.cost_usd
                data = resp.parsed_json or {}
                scraped_specs = _coerce_extracted(
                    data.get("extracted"),
                    allowed_fields=set(missing_fields),
                    allowed_urls=set(page_excerpts.keys()),
                    source_domain_by_url={
                        src["url"]: src["domain"] for src in scraped_sources
                    },
                    extracted_at=now_iso,
                )
            except Exception as exc:  # noqa: BLE001 - one LLM hiccup shouldn't kill the agent
                logger.warning("scraper_v1_llm_failed: %s", exc)
                scraped_specs = {}

        attrs = {
            "scraped_specs": scraped_specs,
            "scraped_reviews": [],
            "scraped_qna": [],
            "scraped_sources": scraped_sources,
            "scraped_at": now_iso,
            "scraped_category": category,
        }
        # Surface budget usage via context so tests / metrics can assert.
        context.setdefault("_scraper_budget", {}).update(
            {
                "search_calls": search_calls,
                "fetch_calls": fetch_calls,
                "llm_calls": llm_calls,
                "missing_fields": list(missing_fields),
                "filled_fields": sorted(scraped_specs.keys()),
            }
        )
        return StrategyOutput(
            product_id=product.product_id,
            strategy=self.STRATEGY,
            model=resp.model if resp is not None else None,
            attributes=attrs,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _using_default_websearch(client: WebSearchClient) -> bool:
    """True if the client was constructed with the default (Tavily) backend —
    which is the only path that needs TAVILY_API_KEY. Tests injecting a
    fake backend should bypass the API-key gate.
    """
    backend = getattr(client, "_backend", None)
    # TavilyBackend is the only default. Name-check avoids an import cycle
    # risk and keeps the gate permissive: anything that isn't literally
    # TavilyBackend is treated as injected (tests).
    return backend.__class__.__name__ == "TavilyBackend"


def _coerce_missing_fields(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, str) and v]


def _category_from_context(context: dict[str, Any], p: ProductInput) -> str:
    tax = context.get("taxonomy") or {}
    if isinstance(tax, dict):
        pt = tax.get("product_type")
        if pt:
            return str(pt)
    return p.category or "unknown"


def _build_query(brand: str, title: str, field: str) -> str:
    # Tavily accepts freeform. A short, spec-oriented query beats a long
    # marketing one — ``brand title field`` is rarely noisier than the
    # full title alone, and it's what spec-sheet lookups look like on
    # Google too.
    parts = [p.strip() for p in (brand, title, field) if p and p.strip()]
    return " ".join(parts)


def _format_extraction_prompt(
    missing_fields: list[str],
    page_excerpts: dict[str, str],
) -> str:
    # Give the LLM each page as (url, excerpt) so it can cite by URL.
    sources_payload = [
        {"url": url, "excerpt": text} for url, text in page_excerpts.items()
    ]
    payload = {
        "fields_to_extract": missing_fields,
        "sources": sources_payload,
    }
    return (
        "Extract the requested product spec fields from the excerpts below. "
        "Cite each value's source URL and snippet.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _coerce_extracted(
    value: Any,
    *,
    allowed_fields: set[str],
    allowed_urls: set[str],
    source_domain_by_url: dict[str, str],
    extracted_at: str,
) -> dict[str, Any]:
    """Validate the LLM's extracted object. Drops entries whose source_url
    doesn't match one we fetched (no fabrication), whose field isn't in
    the asked-for set, or whose value is missing.

    Fills in ``source_domain`` and ``extracted_at`` ourselves so the LLM
    can't lie about them. Snippet is truncated to 200 chars.
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for raw_field, entry in value.items():
        field = str(raw_field)
        if field not in allowed_fields:
            continue
        if not isinstance(entry, dict):
            continue
        url = entry.get("source_url")
        if not isinstance(url, str) or url not in allowed_urls:
            logger.debug("scraper_v1_rejected_bad_url", extra={"field": field, "url": url})
            continue
        val = entry.get("value")
        if val is None or (isinstance(val, str) and not val.strip()):
            continue
        snippet = entry.get("snippet")
        if not isinstance(snippet, str):
            snippet = ""
        out[field] = {
            "value": val,
            "source_url": url,
            "source_domain": source_domain_by_url.get(url, _domain_of(url)),
            "snippet": snippet[:200],
            "extracted_at": extracted_at,
        }
    return out


def _domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
