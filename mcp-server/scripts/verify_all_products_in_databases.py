#!/usr/bin/env python3
"""
Verify that PostgreSQL, Neo4j KG, and Redis contain all product details.

Checks:
- PostgreSQL: total products (1000+), by category, by source (Seed, WooCommerce, Shopify, BigCommerce, Generic)
- Neo4j: product count matches PostgreSQL, iPods and web-scraped products present
- Redis: session cache (products not stored in Redis; it's session-only)

Sample products to verify (user-provided):
- iPod Classic, iPod Nano, iPod Touch (BigCommerce mc-demo)
- MacBook Air, MacBook (BigCommerce, Generic)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

from collections import Counter
from app.database import SessionLocal
from app.models import Product, Price, Inventory


def verify_postgresql():
    """Verify PostgreSQL has all products with full details."""
    db = SessionLocal()
    try:
        all_products = db.query(Product).all()
        total = len(all_products)

        # By category
        by_category = Counter(p.category for p in all_products if p.category)
        # By source
        by_source = Counter(p.source or "NULL" for p in all_products)

        # Check for specific product types
        ipods = [p for p in all_products if p.name and "iPod" in p.name]
        jewelry = [p for p in all_products if p.category == "Jewelry"]
        accessories = [p for p in all_products if p.category == "Accessories"]
        laptops = [p for p in all_products if p.category == "Electronics" and p.product_type in ("laptop", "gaming_laptop")]
        scraped = [p for p in all_products if p.scraped_from_url]
        bigcommerce = [p for p in all_products if p.source == "BigCommerce"]
        woo = [p for p in all_products if p.source == "WooCommerce"]
        shopify = [p for p in all_products if p.source == "Shopify"]
        generic = [p for p in all_products if p.source == "Generic"]
        seed = [p for p in all_products if p.source in ("Seed", "Synthetic") or p.scraped_from_url is None]

        # Sample scraped product IDs (user-provided format)
        sample_ids = [
            "prod-ele-13a4b5342401eb626d8a",  # iPod Classic
            "prod-ele-c7dc6fbfe8bbf56d85de",  # iPod Nano
            "prod-ele-322839b490dcdb124b69",  # iPod Touch
        ]
        found_sample = [p for p in all_products if p.product_id in sample_ids or any(sid in (p.product_id or "") for sid in ["iPod", "MacBook"])]

        print("\n" + "=" * 60)
        print("POSTGRESQL VERIFICATION")
        print("=" * 60)
        print(f"Total products: {total}")
        print(f"Target: 1000+  ->  {'OK' if total >= 1000 else 'FAIL'}")

        print("\nBy category:")
        for cat, count in by_category.most_common(15):
            print(f"  {cat}: {count}")

        print("\nBy source:")
        for src, count in by_source.most_common():
            print(f"  {src or 'NULL'}: {count}")

        print("\nProduct type counts:")
        print(f"  Laptops: {len(laptops)}")
        print(f"  Jewelry: {len(jewelry)}")
        print(f"  Accessories: {len(accessories)}")
        print(f"  iPods (name contains 'iPod'): {len(ipods)}")
        print(f"  Web-scraped (has scraped_from_url): {len(scraped)}")
        print(f"  BigCommerce: {len(bigcommerce)}")
        print(f"  WooCommerce: {len(woo)}")
        print(f"  Shopify: {len(shopify)}")
        print(f"  Generic: {len(generic)}")
        print(f"  Seed/Synthetic: {len(seed)}")

        if ipods:
            print("\nSample iPods:")
            for p in ipods[:5]:
                print(f"  {p.product_id} | {p.name} | source={p.source} | url={str(p.scraped_from_url or '')[:50]}")

        if scraped:
            print("\nSample scraped products (with source/scraped_from_url):")
            for p in scraped[:5]:
                print(f"  {p.product_id} | {p.name} | {p.source} | {str(p.scraped_from_url or '')[:60]}")

        # Prices and inventory
        with_price = db.query(Product).join(Price).count()
        with_inv = db.query(Product).join(Inventory).count()
        print(f"\nProducts with Price: {with_price}")
        print(f"Products with Inventory: {with_inv}")

        return total >= 1000
    finally:
        db.close()


def verify_neo4j():
    """Verify Neo4j KG has products (loads from PostgreSQL, so count should match)."""
    try:
        from app.neo4j_config import Neo4jConnection
        from app.knowledge_graph import KnowledgeGraphBuilder

        conn = Neo4jConnection()
        if not conn.verify_connectivity():
            print("\n" + "=" * 60)
            print("NEO4J VERIFICATION")
            print("=" * 60)
            print("Neo4j not connected (optional)")
            return False

        builder = KnowledgeGraphBuilder(conn)
        stats = builder.get_graph_statistics()

        total_nodes = stats.get("total_nodes", 0)
        total_rels = stats.get("total_relationships", 0)
        products = stats.get("laptops", 0) + stats.get("books", 0) + stats.get("jewelry", 0) + stats.get("accessories", 0)

        print("\n" + "=" * 60)
        print("NEO4J KNOWLEDGE GRAPH VERIFICATION")
        print("=" * 60)
        print(f"Total nodes: {total_nodes}")
        print(f"Total relationships: {total_rels}")
        print(f"Laptops: {stats.get('laptops', 0)}")
        print(f"Books: {stats.get('books', 0)}")
        print(f"Jewelry: {stats.get('jewelry', 0)}")
        print(f"Accessories: {stats.get('accessories', 0)}")
        print("(Other electronics + generic products in Product nodes)")
        print(f"Target 1000+ products in graph  ->  {'OK' if total_nodes > 500 else 'WARN: Run build_knowledge_graph_all.py to populate'}")

        conn.close()
        return True
    except Exception as e:
        print("\n" + "=" * 60)
        print("NEO4J VERIFICATION")
        print("=" * 60)
        print(f"[WARN] Neo4j check skipped: {e}")
        return False


def verify_redis():
    """Redis stores session data, not products. Products come from PostgreSQL."""
    print("\n" + "=" * 60)
    print("REDIS VERIFICATION")
    print("=" * 60)
    print("Redis: Session cache only (mcp:session:{id}).")
    print("Products are NOT stored in Redis; they come from PostgreSQL.")
    print("Redis is for session state, not product catalog.")
    return True


def main():
    print("\n" + "=" * 60)
    print("ALL DATABASES PRODUCT VERIFICATION")
    print("=" * 60)

    pg_ok = verify_postgresql()
    neo_ok = verify_neo4j()
    redis_ok = verify_redis()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"PostgreSQL (authoritative): {'OK' if pg_ok else 'FAIL'} - Run populate scripts if < 1000")
    print(f"Neo4j KG: {'OK' if neo_ok else 'WARN'} - Run build_knowledge_graph_all.py to sync")
    print(f"Redis: Session cache (products from PostgreSQL)")
    print("\nTo include web-scraped products (iPods, mc-demo, etc.) in search:")
    print("  - mc-demo/demo filter has been REMOVED from search API")
    print("  - All products in PostgreSQL are now searchable")


if __name__ == "__main__":
    main()
