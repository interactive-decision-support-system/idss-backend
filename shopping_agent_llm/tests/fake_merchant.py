"""
In-process fake of the merchant agent.

Smoke tests should not require a running MCP backend. This fake mimics
`POST /merchant/search` by returning canned Offers from a small catalog
filtered by the StructuredQuery's hard filters. It also records every
query it was asked to serve, so tests can assert on the contract shape
the agent sends.
"""

from __future__ import annotations

from typing import Any, Dict, List

from shopping_agent_llm.contract import Offer, ProductSummary, StructuredQuery


CATALOG: List[Dict[str, Any]] = [
    {
        "product_id": "lap-1",
        "name": "Apple MacBook Air 13\" M3",
        "price_cents": 129900,
        "brand": "Apple",
        "category": "laptops",
        "color": "Silver",
    },
    {
        "product_id": "lap-2",
        "name": "Dell XPS 13",
        "price_cents": 119900,
        "brand": "Dell",
        "category": "laptops",
        "color": "Graphite",
    },
    {
        "product_id": "lap-3",
        "name": "Lenovo ThinkPad X1 Carbon",
        "price_cents": 159900,
        "brand": "Lenovo",
        "category": "laptops",
        "color": "Black",
    },
    {
        "product_id": "shoe-1",
        "name": "Nike Pegasus 41",
        "price_cents": 13000,
        "brand": "Nike",
        "category": "running_shoes",
        "color": "Red",
    },
    {
        "product_id": "shoe-2",
        "name": "Adidas Ultraboost 24",
        "price_cents": 19000,
        "brand": "Adidas",
        "category": "running_shoes",
        "color": "White",
    },
]


class FakeMerchant:
    def __init__(self) -> None:
        self.queries: List[StructuredQuery] = []

    def search(self, query: StructuredQuery) -> List[Offer]:
        self.queries.append(query)
        rows = list(CATALOG)

        domain = query.domain.lower().strip()
        if domain:
            rows = [
                r
                for r in rows
                if domain in r.get("category", "").lower()
                or domain.rstrip("s") in r.get("category", "").lower()
            ]

        cap = query.hard_filters.get("price_max_cents")
        if cap is not None:
            rows = [r for r in rows if r["price_cents"] <= cap]
        floor = query.hard_filters.get("price_min_cents")
        if floor is not None:
            rows = [r for r in rows if r["price_cents"] >= floor]

        cat = query.hard_filters.get("category")
        if cat:
            rows = [r for r in rows if cat.lower() in r.get("category", "").lower()]

        excluded = set(query.user_context.get("exclude_ids") or [])
        if excluded:
            rows = [r for r in rows if r["product_id"] not in excluded]

        offers: List[Offer] = []
        for i, r in enumerate(rows[: query.top_k]):
            offers.append(
                Offer(
                    merchant_id="fake",
                    product_id=r["product_id"],
                    score=1.0 - (i * 0.1),
                    score_breakdown={},
                    product=ProductSummary(
                        product_id=r["product_id"],
                        name=r["name"],
                        price_cents=r["price_cents"],
                        available_qty=5,
                        category=r.get("category"),
                        brand=r.get("brand"),
                        color=r.get("color"),
                    ),
                    rationale="",
                )
            )
        return offers
