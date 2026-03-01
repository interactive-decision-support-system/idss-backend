#!/usr/bin/env python3
"""
Catalog Ingestion / Normalization Script
========================================
Reads raw scraped products from Supabase, uses GPT-4o-mini to normalize titles
and generate one-sentence descriptions, then writes back into the attributes JSONB.

No DB migration required — normalized data is stored in:
  attributes.normalized_title        (str, ≤ 80 chars)
  attributes.normalized_description  (str, 1 sentence)
  attributes.normalized_at           (ISO timestamp string)

Idempotent: skips products where attributes.normalized_at already exists.

Usage:
  python scripts/normalize_catalog.py               # normalize up to 200 products
  python scripts/normalize_catalog.py --dry-run     # print normalized output, no DB writes
  python scripts/normalize_catalog.py --limit 50    # process at most 50 products
  python scripts/normalize_catalog.py --reset       # re-normalize ALL (ignore normalized_at)

Cost: ~$0.001 per product with GPT-4o-mini.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
from pathlib import Path
# Allow running from repo root or scripts/ dir
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass  # dotenv optional — use real env vars

DATABASE_URL: Optional[str] = os.environ.get("DATABASE_URL")
OPENAI_API_KEY: Optional[str] = os.environ.get("OPENAI_API_KEY")

if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL not set. Add it to .env or export it.")
if not OPENAI_API_KEY:
    sys.exit("ERROR: OPENAI_API_KEY not set.")

# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------
import psycopg2
import psycopg2.extras

def _connect():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

# ---------------------------------------------------------------------------
# OpenAI normalization
# ---------------------------------------------------------------------------
from openai import OpenAI

_CLIENT = OpenAI(api_key=OPENAI_API_KEY)

_SYSTEM_PROMPT = """\
You are a product catalog editor. Given a raw scraped product title and optional description,
return a JSON object with exactly two keys:
  "normalized_title": a clean, canonical product title (max 80 chars) in the form
     "Brand Model, Key Spec, Key Spec"  e.g. "HP Pavilion 15, i5-1235U, 16GB, 512GB SSD"
  "normalized_description": one concise sentence (max 120 chars) describing what the product is
     and who it is for.  e.g. "A mid-range laptop suited for everyday work and school tasks."

Rules:
- Drop filler words, part numbers, and redundant specs from the title.
- If brand is obvious from the title, keep it as the first word.
- Never fabricate specs that are not in the original text.
- Output raw JSON only — no markdown fences, no extra keys.
"""

def _normalize_product(title: str, description: str | None, attributes: dict | None) -> dict:
    """Call GPT-4o-mini to normalize a product title + description."""
    # Build context string
    attr_summary = ""
    if attributes:
        useful = {k: v for k, v in attributes.items()
                  if k in ("ram_gb", "storage_gb", "storage_type", "cpu",
                           "screen_size", "battery_life_hours", "os")
                  and v is not None}
        if useful:
            attr_summary = "  Specs: " + ", ".join(f"{k}={v}" for k, v in useful.items())

    user_content = f"Title: {title}\n"
    if description:
        user_content += f"Description (first 300 chars): {description[:300]}\n"
    if attr_summary:
        user_content += attr_summary

    resp = _CLIENT.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_tokens=120,
        temperature=0.1,
    )
    raw = (resp.choices[0].message.content or "").strip()
    try:
        result = json.loads(raw)
        # Enforce length limits
        result["normalized_title"] = str(result.get("normalized_title", title))[:80]
        result["normalized_description"] = str(result.get("normalized_description", ""))[:120]
        return result
    except (json.JSONDecodeError, KeyError):
        # Fallback: keep original title, no description
        return {
            "normalized_title": title[:80],
            "normalized_description": "",
        }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Normalize product catalog via LLM")
    parser.add_argument("--dry-run", action="store_true", help="Print results but do not write to DB")
    parser.add_argument("--limit", type=int, default=200, help="Max products to process (default 200)")
    parser.add_argument("--reset", action="store_true", help="Re-normalize all products, even already-normalized ones")
    args = parser.parse_args()

    conn = _connect()
    cur = conn.cursor()

    # Fetch products that need normalization
    if args.reset:
        where_clause = "TRUE"
    else:
        where_clause = "attributes->>'normalized_at' IS NULL"

    cur.execute(
        f"""
        SELECT id, title, attributes
        FROM products
        WHERE {where_clause}
        ORDER BY id
        LIMIT %s
        """,
        (args.limit,),
    )
    rows = cur.fetchall()
    print(f"Found {len(rows)} products to normalize (dry_run={args.dry_run}, reset={args.reset})")

    # Edge case: nothing to do
    if not rows:
        print("Nothing to normalize. All products already have normalized_at set.")
        conn.close()
        return

    ok = 0
    errors = 0
    for i, row in enumerate(rows):
        product_id = str(row["id"])
        raw_title = row["title"] or ""
        attrs = row["attributes"] or {}
        description = attrs.get("description") or attrs.get("product_description") or ""

        try:
            normalized = _normalize_product(raw_title, description, attrs)
        except Exception as exc:
            print(f"  [{i+1}/{len(rows)}] ERROR {product_id[:8]}…: {exc}")
            errors += 1
            time.sleep(0.5)
            continue

        normalized["normalized_at"] = datetime.now(timezone.utc).isoformat()

        print(
            f"  [{i+1}/{len(rows)}] {product_id[:8]}…\n"
            f"    RAW:  {raw_title[:80]}\n"
            f"    NORM: {normalized['normalized_title']}\n"
            f"    DESC: {normalized['normalized_description']}"
        )

        if not args.dry_run:
            # Merge into existing attributes JSONB using PostgreSQL || operator
            cur.execute(
                """
                UPDATE products
                SET attributes = COALESCE(attributes, '{}'::jsonb) || %s::jsonb
                WHERE id = %s
                """,
                (json.dumps(normalized), product_id),
            )
            conn.commit()

        ok += 1
        time.sleep(0.1)  # ~10 products/sec — well within OpenAI rate limits

    conn.close()
    print(f"\nDone. Normalized: {ok}, Errors: {errors}")
    if args.dry_run:
        print("Dry-run mode — no DB writes were made.")


if __name__ == "__main__":
    main()
