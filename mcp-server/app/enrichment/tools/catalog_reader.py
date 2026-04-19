"""Load Product rows from a per-merchant raw catalog into ProductInput batches.

Stays a thin SQL wrapper — agents shouldn't reach into ORM directly.

Per-merchant binding: callers pass the SQLAlchemy ``Product`` model that maps
to ``merchants.products_<merchant_id>`` (typically ``agent.product_model``,
which lives on the per-merchant catalog). The previous version imported the
module-level ``Product`` (bound to ``products_default``) and added a
``merchant_id`` filter, which silently read from the default merchant's
table for every other merchant — a leftover from the pre-multi-merchant
era. See issue Q1.
"""

from __future__ import annotations

from typing import Any, Iterator, Optional

from sqlalchemy.orm import Session

from app.enrichment.types import ProductInput


def _to_input(p: Any) -> ProductInput:
    return ProductInput(
        product_id=p.product_id,
        title=p.name,
        category=p.category,
        brand=p.brand,
        description=(p.attributes or {}).get("description") if p.attributes else None,
        price=p.price_value,
        raw_attributes=dict(p.attributes) if p.attributes else {},
    )


def _resolve_model(merchant_id: str, product_model: Optional[Any]) -> Any:
    """Return the per-merchant ORM model, defaulting via the factory.

    ``product_model`` is the preferred entry point — pass
    ``agent.product_model`` and the catalog binding is explicit. The
    ``merchant_id`` fallback exists so existing callers (the enrichment
    runner) can be updated incrementally without breaking the call signature.
    """
    if product_model is not None:
        return product_model
    # Local import to avoid an import-time cycle: app.models imports from
    # app.merchant_agent (validate_merchant_id), which transitively imports
    # the enrichment package via app.endpoints in some configurations.
    from app.models import make_product_model

    return make_product_model(merchant_id)


def iter_products(
    db: Session,
    *,
    merchant_id: str = "default",
    product_model: Optional[Any] = None,
    limit: int | None = None,
    offset: int = 0,
) -> Iterator[ProductInput]:
    Product = _resolve_model(merchant_id, product_model)
    # No merchant_id filter: the per-merchant model is already bound to
    # merchants.products_<id>, so every row in the table belongs to this
    # merchant by construction. Filtering would silently return 0 rows on
    # any merchant whose CSV import didn't populate the legacy
    # merchant_id column.
    q = db.query(Product).order_by(Product.product_id)
    if offset:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(limit)
    for p in q.yield_per(100):
        yield _to_input(p)


def load_products(
    db: Session,
    *,
    merchant_id: str = "default",
    product_model: Optional[Any] = None,
    limit: int | None = None,
) -> list[ProductInput]:
    return list(
        iter_products(
            db,
            merchant_id=merchant_id,
            product_model=product_model,
            limit=limit,
        )
    )
