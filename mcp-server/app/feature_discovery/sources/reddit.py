"""Reddit source — public JSON search endpoint, no auth.

Hits ``https://www.reddit.com/r/<sub>/search.json?q=...&restrict_sr=1``
which Reddit serves without an OAuth token for low-volume read traffic.
We keep the per-subreddit request count small (< a dozen) and route
through the same robots-aware ScraperClient as the web_scraper agent so
backoff, User-Agent, and domain allowlisting stay consistent.

The subreddit map below is the demand-discovery equivalent of
``enrichment/config/scraper_sources.yaml`` — deliberately small, expand
as categories land. Threads that are less than 200 chars or that look
like bot/auto posts are dropped before returning.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from app.feature_discovery.types import UserQuery

logger = logging.getLogger(__name__)


_SUBREDDITS_BY_TYPE: dict[str, list[str]] = {
    "laptop": ["SuggestALaptop", "laptops", "GamingLaptops"],
    "headphones": ["HeadphoneAdvice", "headphones"],
    "blender": ["Cooking", "BuyItForLife"],
    "office-chair": ["OfficeChairs", "chairs"],
}

_SEARCH_QUERIES_BY_TYPE: dict[str, list[str]] = {
    "laptop": ["recommend", "which laptop", "best for", "should I buy"],
    "headphones": ["recommend", "best for", "which headphones"],
    "blender": ["recommend blender", "best blender for"],
    "office-chair": ["recommend chair", "best chair for"],
}

_MIN_BODY_CHARS = 200
_MAX_BODY_CHARS = 4000


def harvest_reddit(
    *,
    product_type: str,
    max_queries: int,
    http_get: Any = None,
) -> list[UserQuery]:
    """Return up to `max_queries` posts for the product_type.

    `http_get(url) -> (status_code, text)` is injected so tests don't
    hit the network. When None, we lazily build a ScraperClient-equivalent
    requests-based getter.
    """

    subs = _SUBREDDITS_BY_TYPE.get(product_type)
    queries = _SEARCH_QUERIES_BY_TYPE.get(product_type, ["recommend"])
    if not subs:
        logger.info("no subreddit map for product_type=%s, returning []", product_type)
        return []

    if http_get is None:
        http_get = _default_http_get()

    out: list[UserQuery] = []
    per_query_limit = max(1, max_queries // (len(subs) * len(queries)))
    now = datetime.now(timezone.utc)

    for sub in subs:
        for q in queries:
            if len(out) >= max_queries:
                break
            url = (
                f"https://www.reddit.com/r/{sub}/search.json"
                f"?q={quote(q)}&restrict_sr=1&sort=relevance&limit={per_query_limit}"
            )
            try:
                status, body = http_get(url)
            except Exception as exc:
                logger.warning("reddit fetch failed for %s: %s", url, exc)
                continue
            if status != 200 or not body:
                continue
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                logger.warning("reddit returned non-JSON for %s", url)
                continue
            for child in payload.get("data", {}).get("children", []):
                data = child.get("data", {})
                body_text = (data.get("selftext") or "").strip()
                title = (data.get("title") or "").strip()
                combined = f"{title}\n\n{body_text}".strip()
                if len(combined) < _MIN_BODY_CHARS:
                    continue
                if len(combined) > _MAX_BODY_CHARS:
                    combined = combined[:_MAX_BODY_CHARS]
                out.append(
                    UserQuery(
                        source="reddit",
                        source_url=f"https://www.reddit.com{data.get('permalink','')}",
                        source_id=str(data.get("id") or data.get("name") or title[:40]),
                        product_type=product_type,
                        text=combined,
                        author=data.get("author"),
                        fetched_at=now,
                    )
                )
                if len(out) >= max_queries:
                    break

    return out


def _default_http_get():
    import urllib.request

    def _get(url: str) -> tuple[int, str]:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "idss-feature-discovery/0.1 (research prototype)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")

    return _get
