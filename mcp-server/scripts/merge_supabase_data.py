#!/usr/bin/env python3
"""
Merge Supabase remote products (24,150) + scraped laptops CSV (73) into local PostgreSQL.
Updated to support the Single-Table Schema and UUID identifiers.
"""

import argparse
import csv
import json
import sys
import time
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    _root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(_root / ".env")
    load_dotenv()
except Exception:
    pass

from sqlalchemy import create_engine, text
from app.database import SessionLocal, DATABASE_URL
from app.models import Product  # Price and Inventory are now stubs in models.py

# Supabase connection (pooler)
SUPABASE_URL = (
    "postgresql://postgres.laandfflkmhvuflzcgmn:1X9soqDaP7yYe1K8"
    "@aws-0-us-west-2.pooler.supabase.com:6543/postgres"
)

# CSV Path - Adjusted to look in the mcp-server root
CSV_PATH = Path(__file__).parent.parent / "scraped_laptops_73_supabase.csv"

def _normalize_category(cat: str) -> str:
    if not cat: return "Electronics"
    lower = cat.lower()
    return "Electronics" if lower == "electronics" else "Books" if lower == "books" else cat.title()

def pull_supabase_products() -> int:
    """Pull all products from Supabase and insert into local Postgres."""
    print("\n" + "=" * 80)
    print("1. PULLING PRODUCTS FROM SUPABASE (REFACTORED)")
    print("=" * 80)

    try:
        supa_engine = create_engine(SUPABASE_URL, connect_args={"connect_timeout": 15})
        print("   Connected to Supabase OK")
    except Exception as e:
        print(f"   [ERROR] Cannot connect to Supabase: {e}")
        return 0

    db = SessionLocal()
    inserted = 0
    skipped = 0

    try:
        # FIX: Query 'id' instead of 'product_id'
        existing_ids = set(
            str(r[0]) for r in db.execute(text("SELECT id FROM products")).fetchall()
        )
        print(f"   Existing local products: {len(existing_ids)}")

        with supa_engine.connect() as supa_conn:
            total = supa_conn.execute(text("SELECT count(*) FROM products")).scalar()
            print(f"   Supabase total products: {total}")

            batch_size = 500
            for offset in range(0, total, batch_size):
                # FIX: Map Supabase columns to the single-table model
                rows = supa_conn.execute(
                    text(f"SELECT * FROM products ORDER BY id OFFSET {offset} LIMIT {batch_size}")
                ).fetchall()

                for row in rows:
                    supa_id = str(row.id)

                    if supa_id in existing_ids:
                        skipped += 1
                        continue

                    # Directly map the Supabase row to our new Product model
                    product = Product(
                        product_id=supa_id, # Maps to 'id' in the DB
                        name=row.title or "Unknown Product",
                        price_value=row.price,
                        brand=row.brand,
                        category=_normalize_category(row.category),
                        product_type=row.product_type,
                        image_url=row.imageurl,
                        source=row.source or "supabase",
                        attributes=row.attributes, # Essential for the @properties
                        series=row.series,
                        model=row.model,
                        link=row.link,
                        rating=row.rating,
                        rating_count=row.rating_count,
                        inventory=row.inventory,
                        ref_id=row.ref_id
                    )
                    db.add(product)
                    existing_ids.add(supa_id)
                    inserted += 1

                db.commit()
                print(f"   Progress: {min(offset + batch_size, total)}/{total} processed...")

        return inserted

    except Exception as e:
        db.rollback()
        print(f"   [ERROR] Supabase import failed: {e}")
        return 0
    finally:
        db.close()

def import_csv_laptops() -> int:
    """Import scraped laptops from CSV into the new attributes-based schema."""
    print("\n" + "=" * 80)
    print("2. IMPORTING SCRAPED LAPTOPS FROM CSV")
    print("=" * 80)

    if not CSV_PATH.exists():
        print(f"   [WARN] CSV not found: {CSV_PATH}")
        return 0

    db = SessionLocal()
    inserted = 0

    try:
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Generate a UUID if ref_id is missing to satisfy the new PK requirement
                supa_id = str(uuid.uuid4())
                
                # Parse attributes if they exist in CSV, otherwise build from columns
                attrs = {}
                if row.get("attributes"):
                    try:
                        attrs = json.loads(row["attributes"])
                    except:
                        pass
                
                # Add legacy columns into the JSONB attributes for compatibility
                attrs.update({
                    "gpu_vendor": row.get("gpu_vendor"),
                    "gpu_model": row.get("gpu_model"),
                    "description": row.get("description")
                })

                product = Product(
                    product_id=supa_id,
                    name=row.get("title", "Unknown CSV Laptop"),
                    price_value=float(row.get("price", 0)) if row.get("price") else 0,
                    category="Electronics",
                    brand=row.get("brand"),
                    product_type="laptop",
                    source="csv_scraped",
                    image_url=row.get("imageurl"),
                    attributes=attrs,
                    inventory=int(row.get("inventory", 10))
                )
                db.add(product)
                inserted += 1

        db.commit()
        print(f"   CSV import done: {inserted} inserted")
        return inserted
    except Exception as e:
        db.rollback()
        print(f"   [ERROR] CSV import failed: {e}")
        return 0
    finally:
        db.close()

def sync_redis() -> bool:
    print("\n" + "=" * 80)
    print("3. SYNCING REDIS")
    print("=" * 80)
    # Simple clear for demo purposes; production logic should be in app.cache
    try:
        from app.cache import cache_client
        cache_client.invalidate_search_cache()
        print("   Search cache invalidated.")
        return True
    except:
        print("   [WARN] Redis sync skipped (check connection)")
        return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-supabase", action="store_true")
    parser.add_argument("--skip-csv", action="store_true")
    args = parser.parse_args()

    print("=" * 80)
    print("FIXED MERGE SCRIPT: SUPABASE SINGLE-TABLE ALIGNMENT")
    print("=" * 80)

    start = time.time()
    
    if not args.skip_supabase:
        pull_supabase_products()
    
    if not args.skip_csv:
        import_csv_laptops()

    sync_redis()

    elapsed = time.time() - start
    print(f"\nMerge completed in {elapsed:.1f}s")

if __name__ == "__main__":
    main()