"""
CSV → per-merchant Postgres loader.

Reads a product CSV and inserts into ``merchants.products_<merchant_id>`` via
the merchant's ORM model. Column parsing and type coercion are delegated to
``app.csv_importer.parse_csv`` so we don't fork alias/coercion logic.

CSV column contract
-------------------
Required (validated by ProductSchema):
    title, product_type (or --product-type fallback), price

Optional top-level columns (become ORM attributes):
    id (UUID — auto-generated if missing), brand, image_url/imageurl,
    rating, rating_count, source, link, ref_id

Optional spec columns (go into ``attributes`` JSONB):
    description, color, weight_*, dimensions, release_year,
    cpu, gpu, ram_gb, storage_gb, screen_size, resolution, battery_life_hours,
    os, megapixels, sensor_type, genre, format, author, pages, publisher,
    body_style, fuel_type, mileage, year, transmission, drivetrain, engine.

Any unrecognised column lands in ``attributes`` via
``ProductSchema.extra_attributes``.

Reserved keys — CSV must NOT carry these (produced by enrichment):
    normalized_description, normalized_at

These are the keys written by ``app.catalog_ingestion`` (strategy
``normalizer_v1``) — the source of truth lives there. We strip with a
warning so a typo in one row doesn't torpedo an entire load. The
disjoint-keys invariant asserted by
``enriched_reader.combine_raw_and_enriched`` depends on this. When a
new enrichment strategy lands that writes additional keys, extend
``RESERVED_ENRICHMENT_KEYS``.

Failure mode
------------
Best-effort. Rows that fail ProductSchema validation or collide on product_id
are logged and skipped — the load returns a summary dict with counts so the
operator can inspect rejects. ``commit`` happens once at the end; if commit
fails the partial batch rolls back.

Re-ingest
---------
This loader does not upsert. We probe the target table up front and raise
``MerchantAlreadyBootstrapped`` if rows already exist, pointing the operator
at ``ingestion.schema.drop_merchant_catalog`` (gated on
``ALLOW_MERCHANT_DROP=1``) as the escape hatch. Failing fast before the
parse runs beats letting the insert hit a Postgres ``UniqueViolation`` at
commit time with a stack-trace message that doesn't name the fix.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional, Set

from sqlalchemy.orm import Session

from app.csv_importer import parse_csv
from app.product_schema import ProductSchema

logger = logging.getLogger(__name__)


class MerchantAlreadyBootstrapped(RuntimeError):
    """Raised when the target merchant table is non-empty at load time."""


# Keys produced by enrichment strategies. If the raw CSV carries any of
# these, ``combine_raw_and_enriched`` would raise at read time because raw
# and enriched must own disjoint key sets. Keep this list in sync with the
# output shape of each strategy in ``app.catalog_ingestion`` (today only
# ``normalizer_v1`` — writes ``normalized_description`` and
# ``normalized_at``). When a new strategy lands, extend this set before
# merging so the disjoint-keys invariant can't be silently violated by a
# CSV row.
RESERVED_ENRICHMENT_KEYS: Set[str] = {
    "normalized_description",
    "normalized_at",
}

# CSV headers that map to the Postgres primary key. Not aliased by
# csv_importer (which maps product_id/sku/asin → ref_id), so we handle
# them here.
_ID_HEADERS: Set[str] = {"id", "uuid", "product_uuid"}


def _strip_reserved_keys(raw: Dict[str, Any], merchant_id: str) -> int:
    """Remove reserved-enrichment keys from a parsed row. Returns count removed."""
    stripped = 0
    extras = raw.get("extra_attributes")
    if isinstance(extras, dict):
        for k in list(extras.keys()):
            if k in RESERVED_ENRICHMENT_KEYS:
                logger.warning(
                    "csv_stripped_reserved_key merchant=%s key=%s title=%s",
                    merchant_id, k, raw.get("title"),
                )
                extras.pop(k)
                stripped += 1
    for k in list(raw.keys()):
        if k in RESERVED_ENRICHMENT_KEYS:
            logger.warning(
                "csv_stripped_reserved_key merchant=%s key=%s title=%s",
                merchant_id, k, raw.get("title"),
            )
            raw.pop(k)
            stripped += 1
    return stripped


def _extract_id(raw: Dict[str, Any]) -> Optional[str]:
    """Pull a CSV-supplied UUID out of parsed row (bucketed into extra_attributes)."""
    extras = raw.get("extra_attributes") or {}
    for header in _ID_HEADERS:
        if header in extras:
            return str(extras.pop(header))
    return None


def load_csv_into_merchant(
    filepath: str,
    *,
    db: Session,
    product_model,
    merchant_id: str,
    product_type: str,
    source: str,
    col_map: Optional[Dict[str, str]] = None,
) -> Dict[str, int]:
    """Parse ``filepath`` and insert rows into ``product_model``'s table.

    Returns a summary ``{"parsed", "inserted", "rejected", "reserved_stripped"}``.

    Raises ``MerchantAlreadyBootstrapped`` if the target table already has
    rows — calling the loader twice on the same merchant is a user error.
    The operator should
    ``ALLOW_MERCHANT_DROP=1 drop_merchant_catalog(merchant_id, conn)``
    and re-bootstrap.
    """
    existing = db.query(product_model).limit(1).count()
    if existing:
        raise MerchantAlreadyBootstrapped(
            f"merchant {merchant_id!r} already has rows in "
            f"{product_model.__table__.schema}.{product_model.__tablename__}; "
            "drop the catalog first via "
            "`ALLOW_MERCHANT_DROP=1 python -c \"from app.ingestion.schema "
            "import drop_merchant_catalog\"` and re-run bootstrap."
        )

    raw_rows = parse_csv(
        filepath,
        product_type=product_type,
        source=source,
        col_map=col_map,
    )

    inserted = 0
    rejected = 0
    reserved_stripped = 0
    seen_ids: Set[str] = set()

    for raw in raw_rows:
        reserved_stripped += _strip_reserved_keys(raw, merchant_id)

        pid_raw = _extract_id(raw) or str(uuid.uuid4())
        try:
            pid = uuid.UUID(str(pid_raw))
        except (ValueError, TypeError):
            logger.warning("csv_invalid_uuid merchant=%s id=%s", merchant_id, pid_raw)
            rejected += 1
            continue

        pid_str = str(pid)
        if pid_str in seen_ids:
            logger.warning(
                "csv_duplicate_product_id merchant=%s id=%s — skipped",
                merchant_id, pid_str,
            )
            rejected += 1
            continue
        seen_ids.add(pid_str)

        try:
            schema = ProductSchema(**raw)
        except Exception as exc:
            logger.warning(
                "csv_schema_validation_failed merchant=%s title=%s err=%s",
                merchant_id, raw.get("title"), exc,
            )
            rejected += 1
            continue

        row = schema.to_product_row(product_id=pid_str)
        # `ProductSchema.to_product_row` returns category capitalized
        # (e.g. "Electronics") from its product_type→category map. The search
        # path filters with a case-sensitive equality check, so we lowercase
        # here to match the agent's default-domain filter (e.g. "electronics").
        # Temporary workaround — follow-up issue #45 moves the normalisation
        # into ProductSchema / the search filter so every caller benefits.
        category_norm = (row["category"] or "").strip().lower() or None
        instance = product_model(
            product_id=pid,
            name=row["title"],
            category=category_norm,
            product_type=row["product_type"],
            brand=row["brand"],
            price_value=row["price"],
            image_url=row["imageurl"],
            rating=row["rating"],
            rating_count=row["rating_count"],
            source=row["source"],
            link=row["link"],
            ref_id=row["ref_id"],
            attributes=row["attributes"],
            merchant_id=merchant_id,
        )
        db.add(instance)
        inserted += 1

    db.commit()

    summary = {
        "parsed": len(raw_rows),
        "inserted": inserted,
        "rejected": rejected,
        "reserved_stripped": reserved_stripped,
    }
    logger.info("csv_load_complete merchant=%s %s", merchant_id, summary)
    return summary
