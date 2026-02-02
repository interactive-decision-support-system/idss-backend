#!/usr/bin/env python3
"""
Verify scraped products: DB has source/scraped_from_url, API returns them, UI config shows them.
Run from mcp-server: python scripts/verify_scraped_products.py
"""

import sys
import asyncio
from pathlib import Path

# Add parent so we can import app
sys.path.insert(0, str(Path(__file__).parent.parent))

def check_db():
    """Verify products table has source and scraped_from_url populated."""
    from app.database import SessionLocal
    from app.models import Product
    db = SessionLocal()
    try:
        total = db.query(Product).count()
        with_source = db.query(Product).filter(Product.source.isnot(None), Product.source != "").count()
        with_url = db.query(Product).filter(Product.scraped_from_url.isnot(None), Product.scraped_from_url != "").count()
        print(f"[DB] Products: {total}, with source: {with_source}, with scraped_from_url: {with_url}")
        if total == 0:
            print("  -> Run: python scripts/populate_real_products.py --scrape-macs --scrape-only --max-per-url 30 --clear")
            return False
        if with_source < total or with_url < total:
            print("  -> Some rows missing source/scraped_from_url (seed data may not have them)")
        else:
            print("  -> OK: All products have source and scraped_from_url")
        # Sample
        for p in db.query(Product).limit(2):
            print(f"  Sample: {p.name} | source={p.source} | scraped_from_url={(p.scraped_from_url or '')[:50]}...")
        return True
    finally:
        db.close()


def check_api():
    """Verify search-products and get-product return source and scraped_from_url."""
    from app.database import SessionLocal
    from app.models import Product
    from app.schemas import SearchProductsRequest, GetProductRequest
    from app.endpoints import search_products, get_product

    db = SessionLocal()
    try:
        # Get a product_id from DB to test get-product
        sample = db.query(Product).filter(Product.source.isnot(None)).first()
        if not sample:
            print("[API] No products with source in DB - run scraper first")
            return False

        # Test get-product (always returns full detail including source/scraped_from_url)
        get_req = GetProductRequest(product_id=sample.product_id)
        get_r = get_product(get_req, db)  # sync function
        if get_r.status.value != "OK" or not get_r.data:
            print("[API GetProduct] Failed or no data")
            return False
        d = get_r.data
        has_s = getattr(d, "source", None) is not None
        has_u = getattr(d, "scraped_from_url", None) is not None
        print(f"[API GetProduct] {d.name} | source={getattr(d, 'source', None)} | scraped_from_url={(getattr(d, 'scraped_from_url', None) or '')[:50]}...")
        if not (has_s and has_u):
            print("  -> WARNING: GetProduct response missing source or scraped_from_url")
            return False
        print("  -> OK: API returns source and scraped_from_url")

        # Optionally test search (may return 0 if follow-up required)
        req = SearchProductsRequest(query="MacBook", limit=5, filters={"category": "Electronics"})
        r = asyncio.run(search_products(req, db))  # search_products is async
        products = r.data.products or []
        print(f"[API Search] Status={r.status}, products={len(products)}")
        if products:
            for p in products[:1]:
                print(f"  Sample: {p.name} | source={getattr(p, 'source', None)} | scraped_from_url={getattr(p, 'scraped_from_url', None)}")
        else:
            print("  (Search may return 0 if follow-up question required or brand filter excludes all)")
        return True
    finally:
        db.close()


def check_ui_config():
    """Verify frontend config includes source and scraped_from_url in card fields."""
    frontend_config = Path(__file__).parent.parent.parent / "src" / "config" / "domain-config.ts"
    if not frontend_config.exists():
        print("[UI Config] domain-config.ts not found (expected in idss-mcp/src/config/)")
        return False
    text = frontend_config.read_text()
    has_source = "key: 'source'" in text or "key: \"source\"" in text
    has_scraped = "scraped_from_url" in text
    print(f"[UI Config] recommendationCardFields has 'source': {has_source}, 'scraped_from_url': {has_scraped}")
    if has_source and has_scraped:
        print("  -> OK: Cards will show Source and Scraped from when present")
    return has_source and has_scraped


def main():
    print("=== Scraped products verification ===\n")
    ok_db = check_db()
    print()
    ok_ui = check_ui_config()
    print()
    ok_api = check_api()
    print()
    if ok_db and ok_ui and ok_api:
        print("All checks passed. Scraped products have source/scraped_from_url and UI will show them.")
    else:
        print("Some checks failed. See above.")
    return 0 if (ok_db and ok_ui and ok_api) else 1


if __name__ == "__main__":
    sys.exit(main())
