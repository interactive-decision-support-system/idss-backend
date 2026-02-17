#!/usr/bin/env python3
"""
Merge Supabase remote products (24,150) + scraped laptops CSV (73) into local PostgreSQL.

Maps Supabase schema → local schema:
  Supabase.id          → products.product_id
  Supabase.title       → products.name
  Supabase.price       → prices.price_cents (dollars → cents)
  Supabase.imageurl    → products.image_url
  Supabase.link        → products.scraped_from_url
  Supabase.attributes  → products.kg_features
  Supabase.rating      → products.reviews (JSON string)
  Supabase.inventory   → inventory.available_qty
  Supabase.ref_id      → products.source_product_id

Run: python scripts/merge_supabase_data.py [--skip-supabase] [--skip-csv] [--skip-redis] [--skip-kg]
"""

import argparse
import csv
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    _root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(_root / ".env")
    load_dotenv()
except Exception:
    pass

from sqlalchemy import create_engine, text
from app.database import SessionLocal, engine as local_engine
from app.models import Product, Price, Inventory


# Supabase connection (pooler)
SUPABASE_URL = (
    "postgresql://postgres.laandfflkmhvuflzcgmn:1X9soqDaP7yYe1K8"
    "@aws-0-us-west-2.pooler.supabase.com:6543/postgres"
)

CSV_PATH = Path(__file__).parent.parent / "scraped_laptops_73_supabase.csv"


def _make_product_id(source: str, ref_id: str) -> str:
    """Deterministic product ID from source + ref_id."""
    return f"supa-{ref_id}" if ref_id else f"supa-{uuid.uuid4().hex[:16]}"


def _normalize_category(cat: str) -> str:
    """Normalize category casing."""
    if not cat:
        return "Electronics"
    lower = cat.lower()
    if lower == "electronics":
        return "Electronics"
    if lower == "books":
        return "Books"
    return cat.title()


def _build_reviews_json(rating, rating_count) -> str:
    """Build reviews text from rating + count."""
    if rating is None and rating_count is None:
        return None
    parts = []
    if rating is not None and str(rating).strip():
        try:
            parts.append(f"Average rating: {float(rating):.1f}/5")
        except (ValueError, TypeError):
            pass
    if rating_count is not None and str(rating_count).strip():
        try:
            if int(float(rating_count)) > 0:
                parts.append(f"({int(float(rating_count))} reviews)")
        except (ValueError, TypeError):
            pass
    return " ".join(parts) if parts else None


def pull_supabase_products() -> int:
    """Pull all products from Supabase and insert into local Postgres."""
    print("\n" + "=" * 80)
    print("1. PULLING PRODUCTS FROM SUPABASE")
    print("=" * 80)

    try:
        supa_engine = create_engine(SUPABASE_URL, connect_args={"connect_timeout": 15})
        supa_engine.connect().close()
        print("   Connected to Supabase OK")
    except Exception as e:
        print(f"   [ERROR] Cannot connect to Supabase: {e}")
        return 0

    db = SessionLocal()
    inserted = 0
    skipped = 0

    try:
        # Get existing product_ids to avoid duplicates
        existing_ids = set(
            r[0] for r in db.execute(text("SELECT product_id FROM products")).fetchall()
        )
        existing_refs = set(
            r[0] for r in db.execute(text("SELECT source_product_id FROM products WHERE source_product_id IS NOT NULL")).fetchall()
        )
        print(f"   Existing local products: {len(existing_ids)}")

        with supa_engine.connect() as supa_conn:
            total = supa_conn.execute(text("SELECT count(*) FROM products")).scalar()
            print(f"   Supabase products: {total}")

            # Pull in batches of 500
            batch_size = 500
            for offset in range(0, total, batch_size):
                rows = supa_conn.execute(
                    text(f"SELECT * FROM products ORDER BY id OFFSET {offset} LIMIT {batch_size}")
                ).fetchall()

                for row in rows:
                    ref_id = row.ref_id or str(row.id)

                    # Skip if already imported
                    if ref_id in existing_refs:
                        skipped += 1
                        continue

                    product_id = _make_product_id(row.source or "supabase", ref_id)
                    if product_id in existing_ids:
                        skipped += 1
                        continue

                    category = _normalize_category(row.category)
                    name = row.title or f"{row.brand or ''} {row.model or ''}".strip() or "Unknown Product"

                    # Build description from series + model + attributes
                    desc_parts = []
                    if row.series:
                        desc_parts.append(f"Series: {row.series}")
                    if row.model:
                        desc_parts.append(f"Model: {row.model}")
                    if row.attributes and isinstance(row.attributes, dict):
                        for k, v in row.attributes.items():
                            if v is not None and v != "" and v is not False:
                                desc_parts.append(f"{k}: {v}")
                    description = ". ".join(desc_parts) if desc_parts else name

                    # Determine GPU info from attributes
                    gpu_vendor = None
                    gpu_model = None
                    attrs = row.attributes or {}
                    if isinstance(attrs, dict):
                        gpu = attrs.get("gpu") or attrs.get("gpu_model") or ""
                        if gpu:
                            gpu_model = str(gpu)
                            gpu_lower = gpu.lower()
                            if "nvidia" in gpu_lower or "geforce" in gpu_lower or "rtx" in gpu_lower or "gtx" in gpu_lower:
                                gpu_vendor = "NVIDIA"
                            elif "amd" in gpu_lower or "radeon" in gpu_lower:
                                gpu_vendor = "AMD"
                            elif "intel" in gpu_lower:
                                gpu_vendor = "Intel"

                    product = Product(
                        product_id=product_id,
                        name=name[:500],
                        description=description[:2000],
                        category=category,
                        brand=row.brand,
                        source=row.source or "supabase",
                        scraped_from_url=row.link,
                        image_url=row.imageurl,
                        product_type=row.product_type,
                        gpu_vendor=gpu_vendor,
                        gpu_model=gpu_model,
                        source_product_id=ref_id,
                        reviews=_build_reviews_json(row.rating, row.rating_count),
                        kg_features=attrs if isinstance(attrs, dict) else None,
                    )
                    db.add(product)

                    # Price
                    price_cents = int(float(row.price) * 100) if row.price else 0
                    db.add(Price(
                        product_id=product_id,
                        price_cents=price_cents,
                        currency="USD",
                    ))

                    # Inventory
                    available = int(row.inventory) if row.inventory else 10
                    db.add(Inventory(
                        product_id=product_id,
                        available_qty=available,
                        reserved_qty=0,
                    ))

                    existing_ids.add(product_id)
                    existing_refs.add(ref_id)
                    inserted += 1

                db.commit()
                print(f"   Progress: {min(offset + batch_size, total)}/{total} processed, {inserted} inserted, {skipped} skipped")

        print(f"\n   Supabase import done: {inserted} inserted, {skipped} skipped")
        return inserted

    except Exception as e:
        db.rollback()
        print(f"   [ERROR] Supabase import failed: {e}")
        import traceback
        traceback.print_exc()
        return 0
    finally:
        db.close()


def import_csv_laptops() -> int:
    """Import scraped laptops from CSV into local Postgres."""
    print("\n" + "=" * 80)
    print("2. IMPORTING SCRAPED LAPTOPS FROM CSV")
    print("=" * 80)

    if not CSV_PATH.exists():
        print(f"   [WARN] CSV not found: {CSV_PATH}")
        return 0

    db = SessionLocal()
    inserted = 0
    skipped = 0

    try:
        existing_refs = set(
            r[0] for r in db.execute(text("SELECT source_product_id FROM products WHERE source_product_id IS NOT NULL")).fetchall()
        )

        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ref_id = row.get("ref_id", "")
                if ref_id in existing_refs:
                    skipped += 1
                    continue

                product_id = f"csv-{ref_id}" if ref_id else f"csv-{uuid.uuid4().hex[:16]}"
                name = row.get("title", "").strip() or f"{row.get('brand', '')} {row.get('model', '')}".strip()
                if not name:
                    continue

                # Parse attributes JSON
                attrs_str = row.get("attributes", "")
                attrs = {}
                if attrs_str:
                    try:
                        attrs = json.loads(attrs_str)
                    except (json.JSONDecodeError, TypeError):
                        pass

                # Build description
                desc_parts = []
                if row.get("series"):
                    desc_parts.append(f"Series: {row['series']}")
                if row.get("model"):
                    desc_parts.append(f"Model: {row['model']}")
                for k, v in attrs.items():
                    if v is not None and v != "" and v is not False:
                        desc_parts.append(f"{k}: {v}")
                description = ". ".join(desc_parts) if desc_parts else name

                product = Product(
                    product_id=product_id,
                    name=name[:500],
                    description=description[:2000],
                    category=_normalize_category(row.get("category", "Electronics")),
                    brand=row.get("brand"),
                    source=row.get("source", "csv_scraped"),
                    scraped_from_url=row.get("link"),
                    image_url=row.get("imageurl"),
                    product_type=row.get("product_type", "laptop"),
                    source_product_id=ref_id,
                    reviews=_build_reviews_json(row.get("rating"), row.get("rating_count")),
                    kg_features=attrs if attrs else None,
                )
                db.add(product)

                price_cents = int(float(row.get("price", 0)) * 100) if row.get("price") else 0
                db.add(Price(product_id=product_id, price_cents=price_cents, currency="USD"))

                inv = int(row.get("inventory", 10)) if row.get("inventory") else 10
                db.add(Inventory(product_id=product_id, available_qty=inv, reserved_qty=0))

                existing_refs.add(ref_id)
                inserted += 1

        db.commit()
        print(f"   CSV import done: {inserted} inserted, {skipped} skipped (already exist)")
        return inserted

    except Exception as e:
        db.rollback()
        print(f"   [ERROR] CSV import failed: {e}")
        import traceback
        traceback.print_exc()
        return 0
    finally:
        db.close()


def sync_redis() -> bool:
    """Warm Redis cache + rebuild brand/category indexes."""
    print("\n" + "=" * 80)
    print("3. SYNCING REDIS")
    print("=" * 80)

    try:
        from app.cache import cache_client
        if not cache_client.ping():
            print("   [WARN] Redis not available")
            return False

        # Clear old indexes
        import redis as redis_mod
        r = cache_client.client
        old_cats = r.keys("category:*")
        old_brands = r.keys("brand:*")
        if old_cats:
            r.delete(*old_cats)
        if old_brands:
            r.delete(*old_brands)
        cache_client.invalidate_search_cache()
        print("   Cleared old indexes and search cache")

        db = SessionLocal()
        products = db.query(Product).all()
        print(f"   Loading {len(products)} products into Redis...")

        cached = 0
        for product in products:
            try:
                # Product summary
                summary = {
                    "product_id": product.product_id,
                    "name": product.name,
                    "description": product.description,
                    "category": product.category,
                    "brand": product.brand,
                    "source": getattr(product, "source", None),
                    "color": getattr(product, "color", None),
                    "scraped_from_url": getattr(product, "scraped_from_url", None),
                    "reviews": getattr(product, "reviews", None),
                    "created_at": product.created_at.isoformat() if product.created_at else None,
                    "updated_at": product.updated_at.isoformat() if product.updated_at else None,
                }
                cache_client.set_product_summary(product.product_id, summary)

                # Price
                price_obj = db.query(Price).filter(Price.product_id == product.product_id).first()
                if price_obj:
                    cache_client.set_price(product.product_id, {
                        "price_cents": price_obj.price_cents,
                        "currency": price_obj.currency or "USD",
                    })

                # Inventory
                inv_obj = db.query(Inventory).filter(Inventory.product_id == product.product_id).first()
                if inv_obj:
                    cache_client.set_inventory(product.product_id, {
                        "available_qty": inv_obj.available_qty,
                        "reserved_qty": inv_obj.reserved_qty,
                    })

                # Brand/category indexes (non-namespaced, for set intersection)
                if product.category:
                    r.sadd(f"category:{product.category}", product.product_id)
                if product.brand:
                    r.sadd(f"brand:{product.brand}", product.product_id)

                cached += 1
                if cached % 500 == 0:
                    print(f"   Progress: {cached}/{len(products)}...")
            except Exception as e:
                continue

        db.close()

        cats = len(r.keys("category:*"))
        brands = len(r.keys("brand:*"))
        print(f"\n   Redis sync done: {cached} products cached")
        print(f"   Category indexes: {cats}, Brand indexes: {brands}")
        return True

    except Exception as e:
        print(f"   [ERROR] Redis sync failed: {e}")
        return False


def sync_knowledge_graph() -> bool:
    """Rebuild Neo4j KG from local Postgres."""
    print("\n" + "=" * 80)
    print("4. SYNCING KNOWLEDGE GRAPH")
    print("=" * 80)

    try:
        import subprocess
        script_path = Path(__file__).parent / "build_knowledge_graph_all.py"
        if not script_path.exists():
            print(f"   [WARN] KG build script not found: {script_path}")
            return False
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(Path(__file__).parent.parent),
            capture_output=False,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"   [ERROR] KG sync failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Merge Supabase + CSV data into local databases")
    parser.add_argument("--skip-supabase", action="store_true")
    parser.add_argument("--skip-csv", action="store_true")
    parser.add_argument("--skip-redis", action="store_true")
    parser.add_argument("--skip-kg", action="store_true")
    args = parser.parse_args()

    print("=" * 80)
    print("MERGE SUPABASE + CSV DATA INTO LOCAL POSTGRES / REDIS / KG")
    print("=" * 80)

    start = time.time()
    results = {}

    if not args.skip_supabase:
        results["supabase"] = pull_supabase_products()
    else:
        print("\n[SKIP] Supabase pull")

    if not args.skip_csv:
        results["csv"] = import_csv_laptops()
    else:
        print("\n[SKIP] CSV import")

    if not args.skip_redis:
        results["redis"] = sync_redis()
    else:
        print("\n[SKIP] Redis sync")

    if not args.skip_kg:
        results["kg"] = sync_knowledge_graph()
    else:
        print("\n[SKIP] KG sync")

    elapsed = time.time() - start

    # Final count
    db = SessionLocal()
    final_count = db.execute(text("SELECT count(*) FROM products")).scalar()
    cats = db.execute(text("SELECT category, count(*) FROM products GROUP BY category ORDER BY count(*) DESC")).fetchall()
    db.close()

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for name, val in results.items():
        status = val if isinstance(val, bool) else f"{val} imported"
        print(f"   {name.upper():<12} {status}")
    print(f"\n   Final product count: {final_count}")
    for c in cats:
        print(f"     {c[0]}: {c[1]}")
    print(f"   Time: {elapsed:.1f}s")
    print("=" * 80)


if __name__ == "__main__":
    sys.exit(main() or 0)
