#!/usr/bin/env python3
"""
Update Redis cache and Neo4j Knowledge Graph with latest products from PostgreSQL.

Syncs:
1. Redis (MCP cache: prod_summary, price, inventory) - warm cache for fast lookups
2. Neo4j Knowledge Graph - rebuild from PostgreSQL products

PostgreSQL is the source of truth. Run populate_real_only_db.py and
scrape_merchant_laptops.py first if you need to refresh product data.

Run: python scripts/update_redis_and_kg.py [--skip-redis] [--skip-kg]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    _root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(_root / ".env")
    load_dotenv()
except Exception:
    pass

from app.database import SessionLocal
from app.models import Product, Price, Inventory
from app.cache import cache_client


def warm_redis_cache() -> bool:
    """Warm Redis MCP cache with all products from PostgreSQL."""
    print("\n" + "=" * 80)
    print("1. REDIS CACHE (MCP format: prod_summary, price, inventory)")
    print("=" * 80)

    if not cache_client.ping():
        print("\n[WARN] Redis not reachable. Start with: redis-server")
        return False

    db = SessionLocal()
    try:
        products = db.query(Product).all()
        print(f"\n   Loading {len(products)} products from PostgreSQL...")

        cached = 0
        for product in products:
            try:
                # Product summary (matches get_product response shape)
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
                    cache_client.set_price(
                        product.product_id,
                        {"price_cents": price_obj.price_cents, "currency": price_obj.currency or "USD"},
                    )

                # Inventory
                inv_obj = db.query(Inventory).filter(Inventory.product_id == product.product_id).first()
                if inv_obj:
                    cache_client.set_inventory(
                        product.product_id,
                        {
                            "available_qty": inv_obj.available_qty,
                            "reserved_qty": inv_obj.reserved_qty,
                        },
                    )

                cached += 1
                if cached % 20 == 0:
                    print(f"   Progress: {cached}/{len(products)} cached...")
            except Exception as e:
                print(f"   [WARN] {product.product_id}: {e}")
                continue

        print(f"\n   Redis cache warmed: {cached} products")
        return True
    finally:
        db.close()


def build_neo4j_kg() -> bool:
    """Build Neo4j knowledge graph from PostgreSQL."""
    print("\n" + "=" * 80)
    print("2. NEO4J KNOWLEDGE GRAPH")
    print("=" * 80)

    try:
        import subprocess
        script_path = Path(__file__).parent / "build_knowledge_graph_all.py"
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(Path(__file__).parent.parent),
            capture_output=False,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"\n[WARN] Neo4j build failed: {e}")
        print("   Ensure Neo4j is running and NEO4J_URI/USER/PASSWORD are set in .env")
        return False


def main():
    parser = argparse.ArgumentParser(description="Update Redis and Neo4j KG from PostgreSQL")
    parser.add_argument("--skip-redis", action="store_true", help="Skip Redis cache warmup")
    parser.add_argument("--skip-kg", action="store_true", help="Skip Neo4j KG build")
    args = parser.parse_args()

    print("=" * 80)
    print("UPDATE REDIS + NEO4J KG FROM POSTGRESQL")
    print("=" * 80)

    start = time.time()
    results = {}

    if not args.skip_redis:
        results["redis"] = warm_redis_cache()
    else:
        print("\n[SKIP] Redis cache warmup")
        results["redis"] = True

    if not args.skip_kg:
        results["neo4j"] = build_neo4j_kg()
    else:
        print("\n[SKIP] Neo4j KG build")
        results["neo4j"] = True

    elapsed = time.time() - start
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for name, ok in results.items():
        print(f"   {name.upper():<10} {'OK' if ok else 'FAILED'}")
    print(f"\n   Time: {elapsed:.1f}s")
    print("=" * 80)

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
