"""
Read helpers for products_enriched.

Raw `products` rows are the golden source and never mutated. Enrichment writes
land in `products_enriched` keyed by (product_id, strategy). Callers pick which
strategy they want and merge enriched attributes on top of raw at query time.

Typical usage:

    raw = db.query(Product).filter_by(product_id=pid).one()
    enriched = fetch_enriched(db, pid, strategy="normalizer_v1")
    merged = merge_with_raw(raw.attributes or {}, enriched)
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import ProductEnriched


def fetch_enriched(
    db: Session,
    product_id: UUID,
    strategy: str,
) -> Dict[str, Any]:
    """Return the enriched attributes for one product under one strategy.

    Returns an empty dict if no row exists — callers can blindly merge.
    """
    row = (
        db.query(ProductEnriched)
        .filter(
            ProductEnriched.product_id == product_id,
            ProductEnriched.strategy == strategy,
        )
        .one_or_none()
    )
    return dict(row.attributes) if row and row.attributes else {}


def hydrate_batch(
    db: Session,
    product_ids: Iterable[UUID],
    strategy: str,
) -> Dict[UUID, Dict[str, Any]]:
    """Batch-fetch enriched attributes for many products under one strategy.

    Missing products simply don't appear in the result dict.
    """
    ids: List[UUID] = list(product_ids)
    if not ids:
        return {}
    rows = (
        db.query(ProductEnriched)
        .filter(
            ProductEnriched.product_id.in_(ids),
            ProductEnriched.strategy == strategy,
        )
        .all()
    )
    return {r.product_id: (dict(r.attributes) if r.attributes else {}) for r in rows}


def merge_with_raw(
    raw_attributes: Dict[str, Any],
    enriched_attributes: Dict[str, Any],
) -> Dict[str, Any]:
    """Shallow-merge raw + enriched. Enriched keys win where present."""
    return {**(raw_attributes or {}), **(enriched_attributes or {})}
