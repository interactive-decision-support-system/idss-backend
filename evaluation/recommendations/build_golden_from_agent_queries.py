#!/usr/bin/env python3
"""
Build golden_dataset.json from agent_response laptop queries (testing/query_data_enriched.csv).

Each row becomes one golden item: query_id (laptop_{id}), user_query (shortened_query),
expected_ucp (mapped from extracted_criteria), ground_truth_product_ids (empty).
Run populate_ground_truth afterward to fill ground truths.

Usage:
  python -m evaluation.recommendations.build_golden_from_agent_queries [--csv path] [--golden path]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_THIS_DIR = Path(__file__).resolve().parent

DEFAULT_CSV = _REPO_ROOT / "testing" / "query_data_enriched.csv"
DEFAULT_GOLDEN = _THIS_DIR / "golden_dataset.json"


def _parse_bool(s) -> bool | None:
    if s is None:
        return None
    if isinstance(s, bool):
        return s
    t = str(s).strip().lower()
    if t in ("true", "1", "yes"):
        return True
    if t in ("false", "0", "no", "null"):
        return False
    return None


def _derive_use_case(criteria: dict, ucp: dict) -> str | None:
    """Derive use_case for _soft_preferences from criteria or from good_for_* in ucp."""
    use_case = criteria.get("use_case") if criteria else None
    if isinstance(use_case, str) and use_case.strip():
        return use_case.strip()
    if ucp.get("good_for_gaming"):
        return "gaming"
    if ucp.get("good_for_ml"):
        return "ml"
    if ucp.get("good_for_creative"):
        return "creative"
    if ucp.get("good_for_web_dev"):
        return "web_dev"
    category = (criteria or {}).get("category") or ""
    if isinstance(category, str) and "school" in category.lower():
        return "school"
    if isinstance(category, str) and "office" in category.lower():
        return "business"
    return "general"


def criteria_to_expected_ucp(criteria: dict) -> dict:
    """Map extracted_criteria JSON to expected_ucp (hard keys for scoring) and _soft_preferences."""
    ucp = {
        "category": "Electronics",
        "product_type": "laptop",
    }
    if not criteria:
        ucp["_soft_preferences"] = {"use_case": "general"}
        return ucp

    # price -> price_max_cents
    price = criteria.get("price")
    if isinstance(price, (int, float)) and price > 0:
        ucp["price_max_cents"] = int(round(price * 100))

    # brand
    brand = criteria.get("brand")
    if isinstance(brand, str) and brand.strip():
        ucp["brand"] = brand.strip()

    # attributes
    attrs = criteria.get("attributes") or {}
    if isinstance(attrs, dict):
        ram = attrs.get("ram_gb")
        if ram is not None:
            try:
                ucp["min_ram_gb"] = int(float(ram))
            except (TypeError, ValueError):
                pass
        screen = attrs.get("screen_size")
        if screen is not None:
            try:
                s = float(screen)
                ucp["min_screen_size"] = max(0, s - 0.5)
                ucp["max_screen_size"] = s + 0.5
            except (TypeError, ValueError):
                pass
        # Some rows have good_for_* inside attributes
        for key in ("good_for_gaming", "good_for_ml", "good_for_creative", "good_for_web_dev"):
            val = attrs.get(key) or criteria.get(key)
            b = _parse_bool(val)
            if b is True:
                ucp[key] = True

    # top-level good_for_*
    for key in ("good_for_gaming", "good_for_ml", "good_for_creative", "good_for_web_dev"):
        if key in ucp:
            continue
        val = criteria.get(key)
        b = _parse_bool(val)
        if b is True:
            ucp[key] = True

    # Non-hard constraints: _soft_preferences (use_case, liked_features for ranking / agent alignment)
    use_case = _derive_use_case(criteria, ucp)
    soft: dict = {"use_case": use_case}
    features = criteria.get("features") or criteria.get("liked_features")
    if features is not None:
        soft["liked_features"] = [features] if isinstance(features, str) else (features if isinstance(features, list) else [])
    ucp["_soft_preferences"] = soft

    return ucp


def load_golden_items_from_csv(csv_path: Path) -> list[dict]:
    """Load laptop test cases from CSV and build golden items with expected_ucp."""
    items = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            user_query = (row.get("shortened_query") or row.get("original_query") or "").strip()
            if not user_query:
                continue
            query_id = f"laptop_{row.get('id', len(items) + 1)}"
            raw_criteria = row.get("extracted_criteria") or "{}"
            try:
                criteria = json.loads(raw_criteria) if isinstance(raw_criteria, str) else raw_criteria
            except (json.JSONDecodeError, TypeError):
                criteria = {}
            expected_ucp = criteria_to_expected_ucp(criteria)
            items.append({
                "query_id": query_id,
                "user_query": user_query,
                "expected_ucp": expected_ucp,
                "ground_truth_product_ids": [],
            })
    return items


def _derive_use_case_from_expected_ucp(expected_ucp: dict) -> str:
    """Derive use_case for _soft_preferences from existing expected_ucp (good_for_* only)."""
    if expected_ucp.get("good_for_gaming"):
        return "gaming"
    if expected_ucp.get("good_for_ml"):
        return "ml"
    if expected_ucp.get("good_for_creative"):
        return "creative"
    if expected_ucp.get("good_for_web_dev"):
        return "web_dev"
    return "general"


def enrich_golden_with_soft_preferences(golden_path: Path) -> int:
    """Add _soft_preferences to each expected_ucp in an existing golden_dataset.json (in place)."""
    with open(golden_path, "r", encoding="utf-8") as f:
        items = json.load(f)
    for item in items:
        ucp = item.get("expected_ucp") or {}
        existing = ucp.get("_soft_preferences")
        use_case = _derive_use_case_from_expected_ucp(ucp) if not existing else existing.get("use_case", "general")
        liked = (existing or {}).get("liked_features")
        if liked is None:
            liked = []
        ucp["_soft_preferences"] = {"use_case": use_case, "liked_features": liked if isinstance(liked, list) else [liked] if liked else []}
    with open(golden_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)
    print(f"Enriched {len(items)} items with _soft_preferences")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build golden_dataset.json from agent_response laptop CSV")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Path to query_data_enriched.csv")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN, help="Output golden_dataset.json path")
    parser.add_argument(
        "--enrich-existing",
        action="store_true",
        help="Add _soft_preferences to each expected_ucp in existing golden file (in place)",
    )
    args = parser.parse_args()

    if args.enrich_existing:
        if not args.golden.is_file():
            print(f"Error: golden file not found: {args.golden}", file=sys.stderr)
            return 1
        return enrich_golden_with_soft_preferences(args.golden)

    if not args.csv.is_file():
        print(f"Error: CSV not found: {args.csv}", file=sys.stderr)
        return 1

    items = load_golden_items_from_csv(args.csv)
    if not items:
        print("Error: no rows loaded from CSV", file=sys.stderr)
        return 1

    with open(args.golden, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)

    print(f"Wrote {len(items)} items to {args.golden}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
