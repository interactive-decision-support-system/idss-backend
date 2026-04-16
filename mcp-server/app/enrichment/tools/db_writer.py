"""UPSERT a StrategyOutput into merchants.products_enriched_default.

Mirrors CatalogNormalizer.batch_normalize's write path so behavior is
identical: pg_insert + on_conflict_do_update keyed by (product_id, strategy).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.enrichment.types import StrategyOutput
from app.models import ProductEnriched

logger = logging.getLogger(__name__)


def upsert_one(db: Session, output: StrategyOutput, *, dry_run: bool = False) -> None:
    if dry_run:
        logger.info(
            "enrichment_dry_run_write",
            extra={
                "strategy": output.strategy,
                "product_id": str(output.product_id),
                "keys": sorted(output.attributes.keys()),
            },
        )
        return

    now = datetime.now(timezone.utc)
    stmt = pg_insert(ProductEnriched).values(
        product_id=output.product_id,
        strategy=output.strategy,
        attributes=output.attributes,
        model=output.model,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["product_id", "strategy"],
        set_={
            "attributes": output.attributes,
            "model": output.model,
            "updated_at": now,
        },
    )
    db.execute(stmt)


def upsert_many(
    db: Session,
    outputs: Iterable[StrategyOutput],
    *,
    dry_run: bool = False,
) -> int:
    """UPSERT a batch and commit once at the end. Returns count written."""
    count = 0
    for output in outputs:
        upsert_one(db, output, dry_run=dry_run)
        count += 1
    if count and not dry_run:
        db.commit()
    return count
