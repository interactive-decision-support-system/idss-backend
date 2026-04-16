#!/usr/bin/env python3
"""
CLI wrapper around CatalogNormalizer.

Runs description normalization and writes to products_enriched under
strategy='normalizer_v1'. Raw products table is never mutated.

Usage:
  python scripts/run_normalizer.py                 # up to 200 products
  python scripts/run_normalizer.py --dry-run       # print, no DB writes
  python scripts/run_normalizer.py --limit 50
  python scripts/run_normalizer.py --force         # re-normalize existing rows (UPSERT)
"""

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "mcp-server"))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize product catalog via LLM (writes to products_enriched).")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing.")
    parser.add_argument("--limit", type=int, default=200, help="Max products to process (default 200).")
    parser.add_argument("--force", action="store_true", help="Re-normalize products that already have a normalizer_v1 row.")
    args = parser.parse_args()

    from app.catalog_ingestion import CatalogNormalizer, STRATEGY
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        normalizer = CatalogNormalizer()
        result = normalizer.batch_normalize(
            db,
            limit=args.limit,
            dry_run=args.dry_run,
            force=args.force,
        )
    finally:
        db.close()

    print(
        f"\nstrategy={STRATEGY}  "
        f"normalized={result['normalized']}  "
        f"skipped={result['skipped']}  "
        f"failed={result['failed']}"
    )
    if args.dry_run:
        print("Dry-run mode — no DB writes were made.")
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
