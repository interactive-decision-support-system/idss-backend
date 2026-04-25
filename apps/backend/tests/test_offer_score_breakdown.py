"""Unit test: MerchantAgent.search populates Offer.score_breakdown from
the KG scores returned in SearchResultsData.

Offline — no Neo4j, no Postgres. We stub search_products so we can assert
the MerchantAgent's plumbing in isolation (issue #52 §4.D, closing #34).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import asyncio
from unittest.mock import patch

import pytest

from merchant_agent.contract import StructuredQuery
from merchant_agent.merchant_agent import MerchantAgent
from merchant_agent.schemas import (
    ProductSummary,
    ResponseStatus,
    RequestTrace,
    SearchProductsResponse,
    SearchResultsData,
    VersionInfo,
)


def _make_summary(pid: str, price_cents: int = 10000) -> ProductSummary:
    return ProductSummary(
        product_id=pid,
        name=f"Product {pid}",
        price_cents=price_cents,
        currency="USD",
        available_qty=1,
    )


def _fake_response(products: List[ProductSummary], scores: Dict[str, Any]) -> SearchProductsResponse:
    from datetime import datetime, timezone
    return SearchProductsResponse(
        status=ResponseStatus.OK,
        data=SearchResultsData(
            products=products,
            total_count=len(products),
            next_cursor=None,
            scores=scores or None,
        ),
        constraints=[],
        trace=RequestTrace(
            request_id="unit-test",
            cache_hit=False,
            timings_ms={"total": 1.0},
            sources=["unit-test"],
        ),
        version=VersionInfo(
            catalog_version="test",
            updated_at=datetime.now(timezone.utc),
        ),
    )


@pytest.mark.asyncio
async def test_offer_score_breakdown_populated_when_kg_scores_present():
    """When the response carries per-product KG scores, every Offer must
    expose the per-term breakdown plus the raw total, and Offer.score
    should be the min-max normalized value."""
    products = [_make_summary("p1"), _make_summary("p2"), _make_summary("p3")]
    scores = {
        "p1": {
            "score": 4.2,
            "breakdown": {"soft": 3.0, "phrase": 1.0, "token": 0.0, "connectivity": 0.2},
        },
        "p2": {
            "score": 2.0,
            "breakdown": {"soft": 1.0, "phrase": 1.0, "token": 0.0, "connectivity": 0.0},
        },
        "p3": {
            "score": 0.5,
            "breakdown": {"soft": 0.5, "phrase": 0.0, "token": 0.0, "connectivity": 0.0},
        },
    }

    agent = MerchantAgent(merchant_id="default", domain="electronics")
    query = StructuredQuery(
        domain="electronics", hard_filters={}, soft_preferences={}, user_context={}, top_k=3,
    )

    async def fake_search(req, db):  # noqa: ARG001 (shapes match)
        return _fake_response(products, scores)

    with patch("merchant_agent.merchant_agent.search_products", side_effect=fake_search):
        offers = await agent.search(query, db=object())  # db unused in stub

    assert len(offers) == 3
    for offer in offers:
        breakdown = offer.score_breakdown
        assert "raw" in breakdown, (
            f"score_breakdown must carry the pre-normalized raw total; got {breakdown}"
        )
        for term in ("soft", "phrase", "token", "connectivity"):
            assert term in breakdown, (
                f"missing per-term key {term!r} in score_breakdown {breakdown}"
            )

    # Min-max normalization: raw range is [0.5, 4.2], so the highest-scoring
    # product maps to 1.0 and the lowest to 0.0.
    by_id = {o.product_id: o for o in offers}
    assert by_id["p1"].score == pytest.approx(1.0)
    assert by_id["p3"].score == pytest.approx(0.0)
    assert 0.0 < by_id["p2"].score < 1.0


@pytest.mark.asyncio
async def test_offer_score_degrades_gracefully_when_kg_scores_missing():
    """Pre-#52 behaviour: when the response carries no scores (KG offline,
    SQL-only hit), Offer.score falls back to the positional placeholder
    and score_breakdown is empty."""
    products = [_make_summary("a"), _make_summary("b")]
    agent = MerchantAgent(merchant_id="default", domain="electronics")
    query = StructuredQuery(
        domain="electronics", hard_filters={}, soft_preferences={}, user_context={}, top_k=2,
    )

    async def fake_search(req, db):  # noqa: ARG001
        return _fake_response(products, scores={})

    with patch("merchant_agent.merchant_agent.search_products", side_effect=fake_search):
        offers = await agent.search(query, db=object())

    assert [o.product_id for o in offers] == ["a", "b"]
    assert all(o.score_breakdown == {} for o in offers)
    # Positional: first is 1.0, last drops by 1/n.
    assert offers[0].score == pytest.approx(1.0)
    assert offers[1].score < offers[0].score
