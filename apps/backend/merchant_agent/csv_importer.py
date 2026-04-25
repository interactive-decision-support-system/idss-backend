"""
CSV → Supabase Product Importer
================================
Reads a product CSV file, maps columns to ``ProductSchema``, optionally
enriches missing fields via LLM (description, reviews), and upserts rows
into the Supabase ``products`` table.

Typical usage (cameras example):

    python -m merchant_agent.csv_importer \\
        --file data/cameras.csv \\
        --product-type camera \\
        --source "csv-cameras-2026" \\
        --enrich \\
        --dry-run

    # Remove --dry-run to write to Supabase.

Column name aliases
-------------------
The importer normalises common header variations automatically.  For
anything unusual, pass ``--col-map 'my_price_col=price'``.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column name aliases
# Keyed by the canonical ProductSchema field; values are alternative CSV
# headers that map to that field (case-insensitive).
# ---------------------------------------------------------------------------

_COLUMN_ALIASES: Dict[str, List[str]] = {
    "title":              ["name", "product_name", "product_title", "item_name", "model_name"],
    "price":              ["price_usd", "retail_price", "msrp", "cost", "sale_price"],
    "brand":              ["manufacturer", "make", "vendor", "oem"],
    "image_url":          ["image", "img", "thumbnail", "photo", "picture_url"],
    "rating":             ["stars", "avg_rating", "average_rating", "score"],
    "rating_count":       ["reviews", "num_reviews", "review_count", "ratings_count", "num_ratings"],
    "link":               ["url", "product_url", "product_link", "merchant_url"],
    "ref_id":             ["sku", "asin", "product_id", "item_id", "model_number"],
    "description":        ["desc", "product_description", "details", "overview", "about"],
    "color":              ["colour"],
    "weight_lbs":         ["weight"],
    # Electronics
    "cpu":                ["processor", "chip", "chipset"],
    "gpu":                ["graphics", "graphics_card", "video_card"],
    "ram_gb":             ["ram", "memory", "memory_gb"],
    "storage_gb":         ["storage", "disk", "disk_gb", "hdd", "ssd", "capacity_gb"],
    "storage_type":       ["disk_type", "drive_type"],
    "screen_size":        ["display", "display_size", "display_inches", "screen", "diagonal"],
    "resolution":         ["display_resolution", "screen_resolution"],
    "refresh_rate_hz":    ["refresh_rate", "hz", "refresh"],
    "battery_life_hours": ["battery", "battery_life", "battery_hours"],
    "os":                 ["operating_system", "platform"],
    # Camera
    "megapixels":         ["mp", "resolution_mp", "sensor_mp"],
    "sensor_type":        ["sensor", "sensor_format"],
    "lens_mount":         ["mount", "lens_system"],
    "video_resolution":   ["video", "max_video", "4k", "video_quality"],
    "image_stabilization": ["ois", "ibis", "stabilization", "is"],
    "weather_sealed":     ["weather_sealing", "sealed", "weatherproof", "weather_resistance"],
    "burst_fps":          ["fps", "continuous_fps", "max_burst"],
    # Book
    "genre":              ["category", "book_genre", "type"],
    "format":             ["book_format", "edition", "media"],
    "author":             ["authors", "writer", "written_by"],
    "isbn":               ["isbn13", "isbn10", "barcode"],
    "pages":              ["page_count", "num_pages"],
    "publisher":          ["publishing_house"],
    # Vehicle
    "body_style":         ["vehicle_type", "car_type", "style"],
    "fuel_type":          ["fuel", "engine_type", "powertrain"],
    "mileage":            ["miles", "odometer", "km"],
    "year":               ["model_year", "vehicle_year"],
    "transmission":       ["gearbox", "trans"],
    "drivetrain":         ["drive", "awd", "4wd", "fwd", "rwd"],
    "engine":             ["engine_size", "displacement"],
}

# Reverse mapping: lowercase alias → canonical field name
_ALIAS_LOOKUP: Dict[str, str] = {}
for _canonical, _aliases in _COLUMN_ALIASES.items():
    _ALIAS_LOOKUP[_canonical] = _canonical
    for _a in _aliases:
        _ALIAS_LOOKUP[_a.lower()] = _canonical


def _map_header(header: str) -> str:
    """Map a CSV column name to the canonical ProductSchema field name."""
    return _ALIAS_LOOKUP.get(header.strip().lower(), header.strip().lower())


def _coerce_value(field: str, raw: str) -> Any:
    """Convert a raw string from CSV into the right Python type."""
    raw = raw.strip()
    if not raw:
        return None

    _INT_FIELDS = {"ram_gb", "storage_gb", "refresh_rate_hz", "battery_life_hours",
                   "rating_count", "pages", "mileage", "burst_fps"}
    _FLOAT_FIELDS = {"price", "rating", "weight_lbs", "weight_kg",
                     "screen_size", "megapixels", "release_year", "year"}
    _BOOL_FIELDS = {"image_stabilization", "weather_sealed"}

    if field in _BOOL_FIELDS:
        return raw.lower() in {"yes", "true", "1", "y"}
    if field in _INT_FIELDS:
        try:
            return int(float(raw.replace(",", "").replace("$", "")))
        except ValueError:
            return None
    if field in _FLOAT_FIELDS:
        try:
            return float(raw.replace(",", "").replace("$", ""))
        except ValueError:
            return None
    return raw


# ---------------------------------------------------------------------------
# LLM enrichment
# ---------------------------------------------------------------------------

_ENRICH_SYSTEM = (
    "You are a product catalog editor. Given product information, "
    "return a JSON object with two fields:\n"
    '  "description": 1-2 sentences (max 40 words), feature-focused, no marketing hype.\n'
    '  "reviews": list of exactly 3 short review snippets (1 sentence each) '
    "that a real customer might leave — honest, varied in sentiment.\n"
    "Output ONLY valid JSON, no extra text."
)


def _enrich_product(client, title: str, brand: Optional[str], attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Call the LLM to generate description + reviews for a product.

    Returns a dict with keys ``description`` (str) and ``reviews`` (List[str]).
    Returns empty dict on failure.
    """
    spec_preview = {k: v for k, v in attrs.items()
                    if k not in {"description", "reviews", "normalized_description"} and v}
    context = f"Product: {title}"
    if brand:
        context += f"\nBrand: {brand}"
    if spec_preview:
        context += f"\nSpecs: {json.dumps(spec_preview, ensure_ascii=False)[:400]}"
    existing_desc = attrs.get("description")
    if existing_desc:
        context += f"\nExisting description (improve this): {existing_desc[:200]}"

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _ENRICH_SYSTEM},
                {"role": "user", "content": context},
            ],
            max_tokens=200,
            temperature=0.5,
        )
        result = json.loads(resp.choices[0].message.content.strip())
        return {
            "description": result.get("description") or "",
            "reviews": [str(r) for r in result.get("reviews", [])],
        }
    except Exception as exc:
        logger.warning("llm_enrich_failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# CSV → ProductSchema row parser
# ---------------------------------------------------------------------------

def parse_csv(
    filepath: str,
    *,
    product_type: str,
    source: str = "csv-import",
    col_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Read a CSV file and return a list of raw field dicts (not yet validated).

    Args:
        filepath:     Path to the CSV file.
        product_type: Default product type if the CSV has no ``product_type`` column.
        source:       Source label written to the ``source`` DB column.
        col_map:      Extra column overrides, e.g. ``{"my_price": "price"}``.
    """
    col_map = {k.lower(): v for k, v in (col_map or {}).items()}
    rows: List[Dict[str, Any]] = []

    with open(filepath, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for raw_row in reader:
            parsed: Dict[str, Any] = {"product_type": product_type, "source": source}

            for header, raw_val in raw_row.items():
                canonical = col_map.get(header.strip().lower()) or _map_header(header)
                coerced = _coerce_value(canonical, raw_val)
                if coerced is None:
                    continue
                if canonical in parsed:
                    # Known field — overwrite only if we have a value
                    parsed[canonical] = coerced
                elif canonical in {
                    "title", "price", "brand", "image_url", "rating", "rating_count",
                    "link", "ref_id", "description", "color", "weight_lbs", "weight_kg",
                    "dimensions", "release_year",
                    "cpu", "gpu", "ram_gb", "storage_gb", "storage_type",
                    "screen_size", "resolution", "refresh_rate_hz", "battery_life_hours", "os",
                    "megapixels", "sensor_type", "lens_mount", "video_resolution",
                    "image_stabilization", "weather_sealed", "burst_fps",
                    "genre", "format", "author", "isbn", "pages", "publisher",
                    "body_style", "fuel_type", "mileage", "year", "transmission",
                    "drivetrain", "engine",
                }:
                    parsed[canonical] = coerced
                else:
                    # Unknown column → extra_attributes
                    parsed.setdefault("extra_attributes", {})[canonical] = coerced

            rows.append(parsed)

    return rows


# ---------------------------------------------------------------------------
# Main importer
# ---------------------------------------------------------------------------

def import_csv(
    filepath: str,
    *,
    product_type: str,
    source: str = "csv-import",
    col_map: Optional[Dict[str, str]] = None,
    enrich: bool = False,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> Dict[str, int]:
    """Import products from a CSV file into Supabase.

    Args:
        filepath:     Path to the CSV file.
        product_type: Default product_type for all rows.
        source:       Source label for the ``source`` column.
        col_map:      Extra column name overrides.
        enrich:       If True, call LLM to fill missing description + reviews.
        dry_run:      If True, print rows but do not write to DB.
        limit:        Process at most this many rows (useful for testing).

    Returns:
        {"inserted": N, "failed": N, "enriched": N}
    """
    from merchant_agent.product_schema import ProductSchema

    raw_rows = parse_csv(filepath, product_type=product_type, source=source, col_map=col_map)
    if limit:
        raw_rows = raw_rows[:limit]

    openai_client = None
    if enrich:
        try:
            from openai import OpenAI
            openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        except ImportError:
            logger.warning("openai not installed — enrichment disabled")
            enrich = False

    db = None
    if not dry_run:
        from dotenv import load_dotenv as _ld
        _ld()
        from merchant_agent.database import SessionLocal
        db = SessionLocal()

    inserted = failed = enriched = 0

    try:
        for raw in raw_rows:
            # --- Validate against schema ---
            try:
                schema = ProductSchema(**raw)
            except Exception as exc:
                logger.warning("schema_validation_failed: %s — row: %s", exc, raw.get("title", "?"))
                failed += 1
                continue

            # --- LLM enrichment ---
            if enrich and openai_client:
                needs_desc = not schema.description
                needs_reviews = not schema.reviews
                if needs_desc or needs_reviews:
                    enriched_data = _enrich_product(
                        openai_client,
                        schema.title,
                        schema.brand,
                        schema.to_attributes_dict(),
                    )
                    if needs_desc and enriched_data.get("description"):
                        schema.description = enriched_data["description"]
                    if needs_reviews and enriched_data.get("reviews"):
                        schema.reviews = enriched_data["reviews"]
                    if enriched_data:
                        enriched += 1

            # --- Build DB row ---
            row = schema.to_product_row()

            if dry_run:
                print(json.dumps({k: v for k, v in row.items() if k != "attributes"}, indent=2))
                print(f"  attributes keys: {list(row['attributes'].keys())}")
                inserted += 1
                continue

            # --- Upsert to Supabase ---
            try:
                from sqlalchemy import text
                db.execute(
                    text("""
                        INSERT INTO products (id, title, category, product_type, brand, price,
                                             imageurl, rating, rating_count, source, link,
                                             ref_id, attributes)
                        VALUES (:id, :title, :category, :product_type, :brand, :price,
                                :imageurl, :rating, :rating_count, :source, :link,
                                :ref_id, :attributes::jsonb)
                        ON CONFLICT (id) DO UPDATE SET
                            title        = EXCLUDED.title,
                            brand        = EXCLUDED.brand,
                            price        = EXCLUDED.price,
                            imageurl     = EXCLUDED.imageurl,
                            rating       = EXCLUDED.rating,
                            rating_count = EXCLUDED.rating_count,
                            attributes   = EXCLUDED.attributes,
                            updated_at   = NOW()
                    """),
                    {**row, "attributes": json.dumps(row["attributes"])},
                )
                inserted += 1
            except Exception as exc:
                logger.error("db_insert_failed for %s: %s", row.get("title"), exc)
                failed += 1

        if not dry_run and db and inserted > 0:
            db.commit()
            logger.info("csv_import_done: inserted=%d failed=%d enriched=%d", inserted, failed, enriched)

    finally:
        if db:
            db.close()

    return {"inserted": inserted, "failed": failed, "enriched": enriched}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_col_map(pairs: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for pair in pairs:
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Import products from a CSV file into Supabase.")
    parser.add_argument("--file", required=True, help="Path to the CSV file")
    parser.add_argument("--product-type", required=True,
                        help="Product type: laptop | camera | phone | book | vehicle | …")
    parser.add_argument("--source", default="csv-import",
                        help="Source label written to the source column (default: csv-import)")
    parser.add_argument("--col-map", nargs="*", default=[],
                        metavar="CSV_COL=FIELD",
                        help="Extra column name mappings, e.g. my_price_col=price")
    parser.add_argument("--enrich", action="store_true",
                        help="Use LLM (gpt-4o-mini) to fill missing description + reviews")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print rows but do not write to Supabase")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N rows (for testing)")
    args = parser.parse_args()

    result = import_csv(
        args.file,
        product_type=args.product_type,
        source=args.source,
        col_map=_parse_col_map(args.col_map),
        enrich=args.enrich,
        dry_run=args.dry_run,
        limit=args.limit,
    )
    print(f"\nResult: {result}")
