#!/usr/bin/env python3
"""
CLI wrapper around CatalogNormalizer.

Runs description normalization and writes to products_enriched under
strategy='normalizer_v1'. Raw products table is never mutated.

Usage:
  python scripts/run_normalizer.py                           # default merchant, up to 200
  python scripts/run_normalizer.py --dry-run
  python scripts/run_normalizer.py --limit 50
  python scripts/run_normalizer.py --force                   # UPSERT existing rows
  python scripts/run_normalizer.py --merchant-id acme        # scope to one merchant
"""

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize product catalog via LLM (writes to products_enriched).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing.")
    parser.add_argument("--limit", type=int, default=200, help="Max products to process (default 200).")
    parser.add_argument(
        "--force", action="store_true",
        help="Re-normalize products that already have a row under this strategy.",
    )
    parser.add_argument(
        "--merchant-id", default="default",
        help="Merchant to normalize. Defaults to 'default'.",
    )
    parser.add_argument(
        "--strategy", default=None,
        help="Strategy label to write. Defaults to normalizer_v1.",
    )
    args = parser.parse_args()

    from merchant_agent.catalog_ingestion import CatalogNormalizer, STRATEGY
    from merchant_agent.database import SessionLocal
    from merchant_agent.models import make_enriched_model, make_product_model

    strategy = args.strategy or STRATEGY
    product_model = make_product_model(args.merchant_id)
    enriched_model = make_enriched_model(args.merchant_id)

    db = SessionLocal()
    try:
        normalizer = CatalogNormalizer()
        result = normalizer.batch_normalize(
            db,
            limit=args.limit,
            dry_run=args.dry_run,
            force=args.force,
            product_model=product_model,
            enriched_model=enriched_model,
            strategy=strategy,
        )
    finally:
        db.close()

    print(
        f"\nmerchant={args.merchant_id}  strategy={strategy}  "
        f"normalized={result['normalized']}  "
        f"skipped={result['skipped']}  "
        f"failed={result['failed']}"
    )
    if args.dry_run:
        print("Dry-run mode — no DB writes were made.")
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
