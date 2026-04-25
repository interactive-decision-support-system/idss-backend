#!/usr/bin/env python3
"""
CLI wrapper around the enrichment runner.

Examples:
  python scripts/run_enrichment.py --mode fixed --limit 5
  python scripts/run_enrichment.py --mode orchestrated --limit 50 --eval-output runs/eval.json
  python scripts/run_enrichment.py --mode fixed --strategies parser_v1,soft_tagger_v1 --dry-run
  python scripts/run_enrichment.py --ab-eval --limit 25 --eval-output runs/ab.json

Defaults to merchant_id='default' if --merchant is omitted. Reads
merchants.products_<merchant> and writes merchants.products_enriched_<merchant>.
The merchant must already be registered in merchants.registry.
"""

from __future__ import annotations

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


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run multi-agent catalog enrichment.")
    p.add_argument("--mode", choices=["fixed", "orchestrated"], default="fixed")
    p.add_argument("--limit", type=int, default=10, help="Max products to process.")
    p.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip the first N products (ordered by product_id). Useful for batching.",
    )
    p.add_argument(
        "--merchant",
        default=_V1_MERCHANT_ID,
        help=f"Merchant slug to enrich (default {_V1_MERCHANT_ID!r}). "
             "Must be registered in merchants.registry.",
    )
    p.add_argument(
        "--strategies",
        type=str,
        default=None,
        help="Comma-separated subset of strategies to run.",
    )
    p.add_argument("--dry-run", action="store_true", help="Don't UPSERT into products_enriched.")
    p.add_argument("--audit", action="store_true", help="Write a validator_v1 audit row per product.")
    p.add_argument(
        "--eval-output",
        type=str,
        default=None,
        help="Path to write the full RunResult JSON (summary + assessment + schema).",
    )
    p.add_argument(
        "--ab-eval",
        action="store_true",
        help="Run BOTH modes on the same product set and emit a comparison.",
    )
    return p.parse_args()


_V1_MERCHANT_ID = "default"


def main() -> int:
    args = _parse_args()

    # Importing the package side-effect-registers all strategies.
    from merchant_agent.database import SessionLocal
    from merchant_agent.enrichment import agents  # noqa: F401 - register strategies
    from merchant_agent.enrichment.orchestration.runner import (
        RunResult,
        run_enrichment,
        serialize_full,
        write_assessment_artifact,
    )

    strategies_filter = (
        [s.strip() for s in args.strategies.split(",") if s.strip()]
        if args.strategies
        else None
    )

    db = SessionLocal()
    try:
        if args.ab_eval:
            return _run_ab_eval(db, args, strategies_filter)

        result = run_enrichment(
            db,
            mode=args.mode,
            merchant_id=args.merchant,
            limit=args.limit,
            offset=args.offset,
            strategies_filter=strategies_filter,
            dry_run=args.dry_run,
            audit=args.audit,
        )
    finally:
        db.close()

    print(json.dumps(result.summary.to_dict(), indent=2, default=str))

    if args.eval_output:
        out = Path(args.eval_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(serialize_full(result), encoding="utf-8")
        print(f"\neval written to {out}")

    return 0


def _run_ab_eval(db, args, strategies_filter) -> int:
    from merchant_agent.enrichment.orchestration.runner import (
        RunResult,
        run_enrichment,
        serialize_full,
    )

    fixed_result = run_enrichment(
        db,
        mode="fixed",
        merchant_id=args.merchant,
        limit=args.limit,
        strategies_filter=strategies_filter,
        dry_run=True,  # eval mode never writes
        audit=False,
    )
    orch_result = run_enrichment(
        db,
        mode="orchestrated",
        merchant_id=args.merchant,
        limit=args.limit,
        strategies_filter=strategies_filter,
        dry_run=True,
        audit=False,
    )
    comparison = {
        "products_processed": args.limit,
        "fixed": fixed_result.summary.to_dict(),
        "orchestrated": orch_result.summary.to_dict(),
        "delta": {
            "cost_usd": round(
                orch_result.summary.total_cost_usd - fixed_result.summary.total_cost_usd, 6
            ),
            "latency_ms": orch_result.summary.total_latency_ms - fixed_result.summary.total_latency_ms,
            "fixed_avg_keys": (
                sum(fixed_result.summary.keys_filled_per_product)
                / len(fixed_result.summary.keys_filled_per_product)
                if fixed_result.summary.keys_filled_per_product
                else 0.0
            ),
            "orchestrated_avg_keys": (
                sum(orch_result.summary.keys_filled_per_product)
                / len(orch_result.summary.keys_filled_per_product)
                if orch_result.summary.keys_filled_per_product
                else 0.0
            ),
        },
    }
    print(json.dumps(comparison, indent=2, default=str))
    if args.eval_output:
        out = Path(args.eval_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(comparison, indent=2, default=str), encoding="utf-8")
        print(f"\nA/B eval written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
