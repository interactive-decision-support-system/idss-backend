#!/usr/bin/env python3
"""
Bootstrap a new merchant from a CSV file.

Thin CLI over ``MerchantAgent.from_csv``. Creates per-merchant tables in the
``merchants`` schema, loads the CSV, runs the normalizer, and registers the
agent in the in-memory registry (the registry is process-local — see issue
#42 for the UI counterpart that will hold a durable registration).

Usage:
  python scripts/bootstrap_merchant.py \\
      --csv fixture.csv \\
      --merchant acme \\
      --domain electronics \\
      --product-type laptop

Re-ingest:
  The loader raises MerchantAlreadyBootstrapped if the target table has
  rows. To re-ingest the same merchant, drop the catalog first:

      ALLOW_MERCHANT_DROP=1 python -c "
      import os
      from sqlalchemy import create_engine
      from merchant_agent.ingestion.schema import drop_merchant_catalog
      eng = create_engine(os.environ['DATABASE_URL'])
      conn = eng.raw_connection()
      try: drop_merchant_catalog('<merchant-id>', conn)
      finally: conn.close()
      "

  then re-run this script.
"""

import argparse
import json
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
    parser = argparse.ArgumentParser(description="Bootstrap a merchant from a CSV.")
    parser.add_argument("--csv", required=True, help="Path to the CSV file.")
    parser.add_argument("--merchant", dest="merchant_id", required=True,
                        help="Merchant slug. Must match [a-z][a-z0-9_]{1,31}.")
    parser.add_argument("--domain", required=True,
                        help="Domain label the agent claims, e.g. 'electronics'.")
    parser.add_argument("--product-type", required=True,
                        help="Default product_type for rows missing the column.")
    parser.add_argument("--source", default=None,
                        help="Source label for rows. Defaults to 'csv:<merchant>'.")
    parser.add_argument("--strategy", default="normalizer_v1",
                        help="Enrichment strategy label (default normalizer_v1).")
    parser.add_argument("--normalize-limit", type=int, default=1000,
                        help="Max products to enrich synchronously (default 1000).")
    parser.add_argument("--skip-enrichment", action="store_true",
                        help="Create tables and load CSV but skip LLM enrichment.")
    args = parser.parse_args()

    from merchant_agent.merchant_agent import MerchantAgent

    agent = MerchantAgent.from_csv(
        args.csv,
        merchant_id=args.merchant_id,
        domain=args.domain,
        product_type=args.product_type,
        strategy=args.strategy,
        source=args.source,
        normalize_limit=args.normalize_limit,
        skip_enrichment=args.skip_enrichment,
    )

    print(json.dumps({
        "merchant_id": agent.merchant_id,
        "domain": agent.domain,
        "catalog_table": agent.catalog_table(),
        "enriched_table": agent.enriched_table(),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
