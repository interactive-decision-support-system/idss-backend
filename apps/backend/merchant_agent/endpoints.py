"""Merchant product search endpoint helpers.

This is the narrowed replacement for the legacy all-purpose MCP endpoint
module. It keeps the retrieval contract that ``MerchantAgent`` depends on
without carrying chat, vehicle, OpenClaw, UCP, or ACP routes into the new repo.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from merchant_agent.models import make_product_model
from merchant_agent.schemas import (
    ProductSummary,
    RequestTrace,
    ResponseStatus,
    SearchProductsRequest,
    SearchProductsResponse,
    SearchResultsData,
    VersionInfo,
)


_CARTS: dict[str, Any] = {}


def create_trace(
    request_id: str,
    cache_hit: bool,
    timings_ms: dict[str, float],
    sources: list[str],
    metadata: dict[str, Any] | None = None,
) -> RequestTrace:
    return RequestTrace(
        request_id=request_id,
        cache_hit=cache_hit,
        timings_ms=timings_ms,
        sources=sources,
        metadata=metadata,
    )


def create_version_info() -> VersionInfo:
    now = datetime.now(timezone.utc)
    return VersionInfo(
        catalog_version="merchant-agent-seed",
        updated_at=now,
        db_version=None,
        snapshot_version=None,
        kg_version=None,
    )


def _list_filter(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if v is not None and v != ""]
    return [value]


def _price_cents(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(float(value) * 100)
    except (TypeError, ValueError):
        return 0


def _summary_from_product(product: Any) -> ProductSummary:
    attrs = product.attributes if isinstance(product.attributes, dict) else {}
    return ProductSummary(
        product_id=str(product.product_id),
        name=product.name or "",
        price_cents=_price_cents(product.price_value),
        currency="USD",
        category=product.category,
        brand=product.brand,
        available_qty=int(product.inventory or 0),
        source=product.source,
        color=attrs.get("color"),
        image_url=product.image_url,
        scraped_from_url=product.link,
        product_type=product.product_type,
        metadata=attrs,
        shipping=product.delivery_promise,
        return_policy=product.return_policy,
        warranty=product.warranty,
        promotion_info=product.promotions_discounts,
        reason=None,
    )


async def search_products(
    request: SearchProductsRequest,
    db: Session,
) -> SearchProductsResponse:
    """Search one merchant catalog with simple SQL filters.

    ``MerchantAgent.search`` provides the higher-level ranking contract. This
    helper intentionally stays small: it resolves the merchant table, applies
    hard filters, performs lightweight text matching, and returns product
    summaries. KG/vector enrichment can be reintroduced behind this contract
    after the standalone repo lands.
    """

    request_id = str(uuid.uuid4())
    started = time.time()
    filters = dict(request.filters or {})
    merchant_id = filters.get("merchant_id") or "default"
    Product = make_product_model(merchant_id)

    query = db.query(Product)

    if category := filters.get("category"):
        query = query.filter(Product.category == category)

    product_types = _list_filter(filters.get("product_type"))
    if product_types:
        query = query.filter(Product.product_type.in_(product_types))

    brands = _list_filter(filters.get("brand"))
    if brands:
        query = query.filter(Product.brand.in_(brands))

    if filters.get("price_min_cents") is not None:
        query = query.filter(Product.price_value >= int(filters["price_min_cents"]) / 100)
    if filters.get("price_max_cents") is not None:
        query = query.filter(Product.price_value <= int(filters["price_max_cents"]) / 100)
    elif filters.get("price_max") is not None:
        query = query.filter(Product.price_value <= float(filters["price_max"]))

    if filters.get("in_stock") is True:
        query = query.filter(Product.inventory.is_(None) | (Product.inventory > 0))

    text_query = (request.query or "").strip()
    if text_query:
        needle = f"%{text_query}%"
        query = query.filter(
            or_(
                Product.name.ilike(needle),
                Product.brand.ilike(needle),
                Product.category.ilike(needle),
                Product.product_type.ilike(needle),
            )
        )

    rows = query.limit(request.limit).all()
    products = [_summary_from_product(row) for row in rows]
    elapsed_ms = round((time.time() - started) * 1000, 1)

    return SearchProductsResponse(
        status=ResponseStatus.OK,
        data=SearchResultsData(
            products=products,
            total_count=len(products),
            next_cursor=None,
            scores=None,
        ),
        constraints=[],
        trace=create_trace(
            request_id=request_id,
            cache_hit=False,
            timings_ms={"total": elapsed_ms},
            sources=["postgres"],
            metadata={"merchant_id": merchant_id},
        ),
        version=create_version_info(),
    )
