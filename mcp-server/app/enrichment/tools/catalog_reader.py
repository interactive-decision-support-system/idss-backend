"""Load Product rows from merchants.products_default into ProductInput batches.

Stays a thin SQL wrapper — agents shouldn't reach into ORM directly.
"""

from __future__ import annotations

from typing import Iterator

from sqlalchemy.orm import Session

from app.enrichment.types import ProductInput
from app.models import Product


def _to_input(p: Product) -> ProductInput:
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
    merchant_id: str = "default",
    limit: int | None = None,
    offset: int = 0,
) -> Iterator[ProductInput]:
    q = db.query(Product).filter(Product.merchant_id == merchant_id).order_by(Product.product_id)
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
    limit: int | None = None,
) -> list[ProductInput]:
    return list(iter_products(db, merchant_id=merchant_id, limit=limit))
