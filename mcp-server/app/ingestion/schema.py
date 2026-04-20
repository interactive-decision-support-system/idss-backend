"""
Per-merchant DDL helpers.

Each merchant gets a pair of tables inside the shared `merchants` schema:
    merchants.products_<id>           (raw catalog)
    merchants.products_enriched_<id>  (derived attributes per strategy)

Both are cloned from the archival reference pool via ``LIKE ... INCLUDING
ALL``. The FK from enriched → raw is re-added explicitly because LIKE does
not copy foreign keys.

Schema template source (migration 006):
  * Raw catalog column layout is cloned from ``merchants.raw_products_default``
    — the platform-operator archive, NOT a merchant. Using the archive as
    the template means no merchant's table is structurally load-bearing for
    new-merchant provisioning; every merchant, including 'default', is
    dropable without breaking anyone else.
  * Enriched column layout is cloned from ``merchants.products_enriched_default``.
    The default merchant's enriched table is used as a template here purely
    because its schema is fixed by migration 003; this is a column-shape
    dependency, not a data dependency, and the 'default' merchant has no
    runtime privilege from it.

Prerequisite: migrations 002, 003, and 006 must have run — the clone reads
from the template tables named above, and ``create_merchant_catalog`` raises
a bare Postgres "relation does not exist" if they aren't there yet.

All identifiers that interpolate the merchant_id are built via
``psycopg2.sql.Identifier`` after ``validate_merchant_id`` has matched the
merchant id against the slug regex in ``app.merchant_agent``. Merchant ids
reach this module from the registry, not from raw user input, but we still
validate defensively.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from psycopg2 import sql

from app.merchant_agent import validate_merchant_id

logger = logging.getLogger(__name__)

_SCHEMA = "merchants"
# Raw template: archival reference pool, not a merchant's table (migration 006).
# Using the archive as the template decouples new-merchant provisioning from
# any particular merchant's lifecycle.
_TEMPLATE_RAW = "raw_products_default"
# Enriched template: column-shape source only. No runtime privilege.
_TEMPLATE_ENRICHED = "products_enriched_default"


def _raw_table(merchant_id: str) -> str:
    return f"products_{merchant_id}"


def _enriched_table(merchant_id: str) -> str:
    return f"products_enriched_{merchant_id}"


def _fk_name(merchant_id: str) -> str:
    return f"fk_products_enriched_{merchant_id}_product_id"


def create_merchant_catalog(merchant_id: str, conn: Any) -> None:
    """Create ``merchants.products_<id>`` and ``merchants.products_enriched_<id>``.

    Clones both from the default merchant's tables via LIKE INCLUDING ALL and
    re-adds the enriched → raw FK (LIKE does not copy foreign keys). Idempotent
    — safe to re-run on an already-bootstrapped merchant.

    ``conn`` is a psycopg2 connection. Callers holding a SQLAlchemy engine can
    obtain one via ``engine.raw_connection()``.
    """
    merchant_id = validate_merchant_id(merchant_id)

    raw = sql.Identifier(_SCHEMA, _raw_table(merchant_id))
    enriched = sql.Identifier(_SCHEMA, _enriched_table(merchant_id))
    template_raw = sql.Identifier(_SCHEMA, _TEMPLATE_RAW)
    template_enriched = sql.Identifier(_SCHEMA, _TEMPLATE_ENRICHED)
    fk_name = _fk_name(merchant_id)

    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {schema}").format(
            schema=sql.Identifier(_SCHEMA),
        ))

        cur.execute(sql.SQL(
            "CREATE TABLE IF NOT EXISTS {raw} (LIKE {template} INCLUDING ALL)"
        ).format(raw=raw, template=template_raw))

        cur.execute(sql.SQL(
            "CREATE TABLE IF NOT EXISTS {enriched} (LIKE {template} INCLUDING ALL)"
        ).format(enriched=enriched, template=template_enriched))

        # LIKE INCLUDING ALL does not copy FK constraints. Re-add so dropping
        # a raw row cascades through enriched. ALTER TABLE ... ADD CONSTRAINT
        # IF NOT EXISTS is not available across all supported Postgres versions,
        # so probe pg_constraint first.
        cur.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = %s",
            (fk_name,),
        )
        if not cur.fetchone():
            cur.execute(sql.SQL(
                "ALTER TABLE {enriched} ADD CONSTRAINT {fk_name} "
                "FOREIGN KEY (product_id) REFERENCES {raw}(id) ON DELETE CASCADE"
            ).format(
                enriched=enriched,
                fk_name=sql.Identifier(fk_name),
                raw=raw,
            ))

    conn.commit()
    logger.info("created_merchant_catalog merchant_id=%s", merchant_id)


def drop_merchant_catalog(merchant_id: str, conn: Any, *, _force: bool = False) -> None:
    """Drop both per-merchant tables. Gated on ``ALLOW_MERCHANT_DROP=1``.

    ``_force=True`` bypasses the env gate — reserved for in-request cleanup
    of a half-provisioned merchant whose tables were just created in the
    same turn. Not for external callers.
    """
    if not _force and os.environ.get("ALLOW_MERCHANT_DROP", "") != "1":
        raise PermissionError(
            "drop_merchant_catalog disabled. Set ALLOW_MERCHANT_DROP=1 to enable."
        )
    merchant_id = validate_merchant_id(merchant_id)
    # No 'default'-specific guard: since migration 006 the clone template is
    # merchants.raw_products_default (the archive), not this merchant's table.
    # 'default' is dropable like any other merchant; ALLOW_MERCHANT_DROP is
    # the sole safety mechanism. If 'default' is dropped, it can be rebuilt
    # by re-running migration 006 (which resamples from raw).

    raw = sql.Identifier(_SCHEMA, _raw_table(merchant_id))
    enriched = sql.Identifier(_SCHEMA, _enriched_table(merchant_id))
    with conn.cursor() as cur:
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {t} CASCADE").format(t=enriched))
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {t} CASCADE").format(t=raw))
    conn.commit()
    logger.info("dropped_merchant_catalog merchant_id=%s", merchant_id)
