#!/usr/bin/env python3
"""Seed a mock laptop catalog into a merchant by sampling real rows from the
archival reference pool (``merchants.raw_products_default``).

Rows are copied verbatim — real ``title``, ``brand``, ``attributes``, ``link``,
etc. — so no product is a frankenstein of mixed feature fields. Retrieval is
deterministic (``ORDER BY id``), not randomized, so a given ``--count`` always
yields the same prefix of the filtered pool.

Usage:
  python scripts/seed_mock_laptops.py --merchant mocklaptops --count 100

Re-seed:
  ALLOW_MERCHANT_DROP=1 python scripts/seed_mock_laptops.py \
      --merchant mocklaptops --reseed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from psycopg2 import sql

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "mcp-server"))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass


_SOURCE_SCHEMA = "merchants"
_SOURCE_TABLE = "raw_products_default"


def _maybe_drop_existing(merchant_id: str, engine) -> None:
    from app.ingestion.schema import drop_merchant_catalog
    raw_conn = engine.raw_connection()
    try:
        drop_merchant_catalog(merchant_id, raw_conn)
    finally:
        raw_conn.close()


def _copy_rows(
    *,
    merchant_id: str,
    target_table: str,
    product_type: str,
    count: int,
    engine,
) -> int:
    """Copy ``count`` rows of ``product_type`` from raw_products_default into
    the merchant's raw table, rewriting ``merchant_id`` to the target.

    ``target_table`` is the fully-qualified name from ``agent.catalog_table()``
    (e.g. ``merchants.products_mocklaptops``). Source and target share the same
    schema because both were cloned via ``LIKE INCLUDING ALL`` from the same
    template, so ``INSERT ... SELECT *`` is safe.
    """
    schema_name, table_name = target_table.split(".", 1)
    target = sql.Identifier(schema_name, table_name)
    source = sql.Identifier(_SOURCE_SCHEMA, _SOURCE_TABLE)

    insert_stmt = sql.SQL(
        "INSERT INTO {target} "
        "SELECT * FROM {source} "
        "WHERE product_type = %s "
        "ORDER BY id "
        "LIMIT %s"
    ).format(target=target, source=source)

    update_stmt = sql.SQL(
        "UPDATE {target} SET merchant_id = %s"
    ).format(target=target)

    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            cur.execute(insert_stmt, (product_type, count))
            inserted = cur.rowcount
            cur.execute(update_stmt, (merchant_id,))
        raw_conn.commit()
    finally:
        raw_conn.close()
    return inserted


def main() -> int:
    p = argparse.ArgumentParser(
        description="Seed a mock laptop catalog by sampling real rows from raw_products_default."
    )
    p.add_argument("--merchant", default="mocklaptops",
                   help="Merchant slug. Must match [a-z][a-z0-9_]{1,31}.")
    p.add_argument("--count", type=int, default=100,
                   help="Number of rows to copy (default 100).")
    p.add_argument("--product-type", default="laptop",
                   help="product_type filter applied to raw_products_default (default 'laptop').")
    p.add_argument("--reseed", action="store_true",
                   help="Drop the merchant catalog first (requires ALLOW_MERCHANT_DROP=1).")
    p.add_argument("--domain", default="electronics")
    p.add_argument("--strategy", default="normalizer_v1")
    p.add_argument("--kg-strategy", default="default_v1")
    args = p.parse_args()

    from app.database import SessionLocal
    from app.ingestion.schema import create_merchant_catalog
    from app.merchant_agent import (
        MerchantAgent,
        upsert_registry_row,
        validate_merchant_id,
    )

    merchant_id = validate_merchant_id(args.merchant)

    session = SessionLocal()
    engine = session.get_bind()
    session.close()

    if args.reseed:
        if os.environ.get("ALLOW_MERCHANT_DROP") != "1":
            print("--reseed requires ALLOW_MERCHANT_DROP=1", file=sys.stderr)
            return 2
        _maybe_drop_existing(merchant_id, engine)

    raw_conn = engine.raw_connection()
    try:
        create_merchant_catalog(merchant_id, raw_conn)
    finally:
        raw_conn.close()

    agent = MerchantAgent(
        merchant_id=merchant_id,
        domain=args.domain,
        strategy=args.strategy,
        kg_strategy=args.kg_strategy,
    )
    Product = agent.product_model

    db = SessionLocal()
    try:
        existing = db.query(Product).limit(1).count()
    finally:
        db.close()
    if existing:
        print(
            f"merchant {merchant_id!r} already has rows; pass --reseed "
            f"with ALLOW_MERCHANT_DROP=1 to wipe and re-insert.",
            file=sys.stderr,
        )
        return 2

    inserted = _copy_rows(
        merchant_id=merchant_id,
        target_table=agent.catalog_table(),
        product_type=args.product_type,
        count=args.count,
        engine=engine,
    )

    if inserted == 0:
        print(
            f"no rows matched product_type={args.product_type!r} in "
            f"{_SOURCE_SCHEMA}.{_SOURCE_TABLE}; merchant provisioned but empty.",
            file=sys.stderr,
        )

    upsert_registry_row(
        merchant_id=merchant_id,
        domain=args.domain,
        strategy=args.strategy,
        kg_strategy=args.kg_strategy,
    )

    print(json.dumps({
        "merchant_id": merchant_id,
        "domain": args.domain,
        "strategy": args.strategy,
        "kg_strategy": args.kg_strategy,
        "catalog_table": agent.catalog_table(),
        "enriched_table": agent.enriched_table(),
        "source_table": f"{_SOURCE_SCHEMA}.{_SOURCE_TABLE}",
        "product_type": args.product_type,
        "requested": args.count,
        "inserted": inserted,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
