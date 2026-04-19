"""Load Product rows from a per-merchant raw catalog into ProductInput batches.

Stays a thin SQL wrapper — agents shouldn't reach into ORM directly.

Per-merchant binding is *required and explicit*: callers pass the SQLAlchemy
``Product`` model that maps to ``merchants.products_<merchant_id>`` (typically
``agent.product_model`` or ``Catalog.for_merchant(mid).product_model``). The
previous version imported the module-level ``Product`` (bound to
``products_default``) and added a ``merchant_id`` filter, which silently read
from the default merchant's table for every other merchant. See issue Q1.
"""

from __future__ import annotations

from typing import Any, Iterator

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


def iter_products(
    db: Session,
    *,
    product_model: Any,
    limit: int | None = None,
    offset: int = 0,
) -> Iterator[ProductInput]:
    """Stream rows from the per-merchant catalog table.

    No ``merchant_id`` filter: ``product_model`` is already bound to
    ``merchants.products_<id>``, so every row in the table belongs to that
    merchant by construction. Filtering would silently return 0 rows on any
    merchant whose CSV import didn't populate the legacy ``merchant_id``
    column.
    """
    q = db.query(product_model).order_by(product_model.product_id)
    if offset:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(limit)
    for p in q.yield_per(100):
        yield _to_input(p)


def load_products(
    db: Session,
    *,
    product_model: Any,
    limit: int | None = None,
) -> list[ProductInput]:
    return list(iter_products(db, product_model=product_model, limit=limit))
