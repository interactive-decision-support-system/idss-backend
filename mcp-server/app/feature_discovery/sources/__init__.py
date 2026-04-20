"""Source adapters — each yields a list of UserQuery for a product_type.

Scope: third-party demand sources only. We deliberately do not read
from merchant-controlled surfaces (product-page Q&A, on-site reviews),
because those are filtered by what a retailer chose to host and would
leak merchant-side selection bias into what should be a neutral
estimate of shopper demand.

Candidate future adapters that fit this rule: YouTube comments on
review videos, specialized forums (head-fi.org, notebookreview.com,
r/BuyItForLife), Quora threads, Google Trends/autocomplete. Add them
behind this REGISTRY — the rest of the pipeline is adapter-agnostic.
"""

from __future__ import annotations

from typing import Callable

from app.feature_discovery.types import UserQuery

from app.feature_discovery.sources.reddit import harvest_reddit
from app.feature_discovery.sources.seed import harvest_seed

SourceFn = Callable[..., list[UserQuery]]

REGISTRY: dict[str, SourceFn] = {
    "reddit": harvest_reddit,
    "seed": harvest_seed,
}


def harvest(source: str, *, product_type: str, max_queries: int, **kwargs) -> list[UserQuery]:
    if source not in REGISTRY:
        raise ValueError(f"unknown source {source!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[source](product_type=product_type, max_queries=max_queries, **kwargs)
