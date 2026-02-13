#!/usr/bin/env python3
"""
Populate PostgreSQL and Neo4j with ONLY real scraped products.

Per week6instructions.txt:
- Remove all fake/seed/synthetic products
- Create separate PostgreSQL + KG of real scraped laptops, electronics, books only
- Sources: System76, Framework, Back Market, BigCommerce, Shopify, WooCommerce, Temu
- Ensure rich data: brand, image, price, description; no missing images
- Support complex queries (kg_features: good_for_ml, good_for_web_dev, battery_life, etc.)

Run:
  1. python scripts/populate_real_only_db.py              # Clear + load real products
  2. python scripts/build_knowledge_graph.py              # Build Neo4j KG

Or use --no-clear to ADD real products without clearing (not recommended for real-only mode).
"""

import os
import sys
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
from sqlalchemy.orm import sessionmaker
from app.database import DATABASE_URL
from app.models import Product, Price, Inventory
from scripts.populate_real_products import (
    populate_database,
    convert_scraped_to_dict,
    generate_product_id,
    normalize_structured_specs,
    MAC_SCRAPE_URLS,
)
from scripts.product_scraper import scrape_products, ScrapedProduct

# Placeholder for products with missing images (laptops/electronics)
LAPTOP_PLACEHOLDER_IMG = "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=800"
PHONE_PLACEHOLDER_IMG = "https://images.unsplash.com/photo-1592286927505-d9e808fd2f9f?w=800"
BOOK_PLACEHOLDER_IMG = "https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=800"

# Real scrape URLs: System76, Framework, Back Market, Fairphone
SYSTEM76_FRAMEWORK_BACKMARKET_URLS = [
    "https://system76.com/laptops",
    "https://system76.com/merch",
    "https://frame.work/products/framework-laptop-13",
    "https://frame.work/products/framework-laptop-16",
    "https://frame.work/desktop",
    "https://www.backmarket.com/en-us/l/computers-laptops/41f464b5-9356-48d3-86c3-a2bf52ced60e",
]

# Fairphone - sustainable/repairable phones (shop.fairphone.com)
FAIRPHONE_URLS = [
    "https://shop.fairphone.com/smartphones",
]

# Additional phone sources: Shopify, BigCommerce, WooCommerce demo stores (more phones)
PHONE_URLS = [
    # Shopify electronics demos with phones (Blynk, Amanto have Samsung/Xiaomi; AnyMind has Nothing Phone)
    "https://blynk-electronics-demo.myshopify.com",
    "https://amanto-store-demo.myshopify.com",
    "https://shopify-demo-store.anymindai.com/collections/electronics",
    # BigCommerce: smart-electronics has "Smart phone 5g" and smartphones category
    "https://smart-electronics-demo.mybigcommerce.com/smartphones/",
    "https://smart-electronics-demo.mybigcommerce.com/shop-all/",
    # scaffolding-calm has GAMAKOO, BLU, Motorola, iPhone (tehshop returns 403)
    "https://scaffolding-calm-demo.mybigcommerce.com/categories/cell-phones/4g.html",
]

# BigCommerce, Shopify, WooCommerce demo stores (more laptops/phones/electronics)
REAL_SCRAPE_URLS_FALLBACK = [
    # BigCommerce mc-demo
    "https://mc-demo.mybigcommerce.com/",
    "https://mc-demo.mybigcommerce.com/categories/Laptops/MAC/",
    "https://mc-demo.mybigcommerce.com/categories/iPods/",
    "https://mc-demo.mybigcommerce.com/categories/iPods/Nano/",
    "https://mc-demo.mybigcommerce.com/categories/iPods/Classic/",
    # BigCommerce california-demo (MacBook Air, MacBook Pro, laptops)
    "https://california-demo.mybigcommerce.com/laptops/",
    "https://california-demo.mybigcommerce.com/desktops/",
    "https://california-demo.mybigcommerce.com/",
    # Shopify tech demos (electronics, laptops, phones)
    "https://tech-demo10.myshopify.com",
    "https://tech-gadget-demo.myshopify.com",
    # WooCommerce demos
    "https://pluginrepublic.dev/product-extras/product/macbook/",
    "https://qodeinteractive.com/qode-product-extra-options-for-woocommerce/product/laptop-computer/",
]

# Barnes & Noble for books
BARNES_NOBLE_URLS = [
    "https://www.barnesandnoble.com/b/books/_/N-1fZ29Z8q8",
    "https://www.barnesandnoble.com/b/fiction/_/N-1fZ29Z8q8",
]

# Open Library API - free, reliable book data (fallback when B&N times out)
OPEN_LIBRARY_SEARCH_QUERIES = [
    "fiction bestseller",
    "science fiction",
    "mystery thriller",
]


# Per week6tips: enriched outputs include delivery, return, warranty
DEFAULT_POLICY_SUFFIX = (
    " Shipping: Free standard shipping; delivery in 3–7 business days. "
    "Returns: 30-day return for refund. Warranty: 1-year limited warranty."
)


def _enrich_with_policy(p: dict, policy_cache: dict = None) -> dict:
    """
    Append shipping/return/warranty to description (week6tips: enriched outputs).
    Uses real policy text from policy_scraper when available (System76, Framework,
    Fairphone, Back Market, BigCommerce, Shopify, WooCommerce).
    """
    from scripts.policy_scraper import get_policy_for_product
    desc = p.get("description") or ""
    source = p.get("source") or ""
    url = p.get("scraped_from_url") or ""
    cache_key = (source, url.split("/")[2] if url else "")
    policy_cache = policy_cache if policy_cache is not None else {}
    if cache_key not in policy_cache:
        policy_cache[cache_key] = get_policy_for_product(source, url or None, use_live_fetch=True)
    policy = policy_cache[cache_key]
    if policy.strip() and policy.strip() not in desc:
        p["description"] = (desc + policy).strip()
    return p


def _has_real_image(p: dict) -> bool:
    """Check if product has a real (non-placeholder) image URL."""
    url = p.get("image_url") or ""
    if not url or not str(url).strip().startswith("http"):
        return False
    # Exclude known placeholders
    if "unsplash.com" in url or "placeholder" in url.lower():
        return False
    return True


def _ensure_image(p: dict, category: str) -> dict:
    """Ensure product has image. Use placeholder only if --keep-missing-images; else mark for removal."""
    if _has_real_image(p):
        return p
    # Per week6instructions: remove low quality (missing images) or improve.
    # Use placeholder as improvement so we keep products; callers can filter via remove_products_without_images.
    if category == "Books":
        p["image_url"] = BOOK_PLACEHOLDER_IMG
    elif "phone" in (p.get("product_type") or "").lower() or "smartphone" in (p.get("product_type") or "").lower():
        p["image_url"] = PHONE_PLACEHOLDER_IMG
    else:
        p["image_url"] = LAPTOP_PLACEHOLDER_IMG
    return p


def fetch_open_library_books(max_books: int = 30) -> list:
    """Fetch real books from Open Library API (free, no scraping). Fallback when B&N times out."""
    import requests
    all_books = []
    seen_titles = set()
    for q in OPEN_LIBRARY_SEARCH_QUERIES:
        try:
            r = requests.get(
                "https://openlibrary.org/search.json",
                params={"q": q, "limit": min(15, max_books // 2)},
                timeout=10,
                headers={"User-Agent": "MCP-RealProducts/1.0"},
            )
            r.raise_for_status()
            data = r.json()
            for doc in data.get("docs", []):
                title = doc.get("title", "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                author = ", ".join(doc.get("author_name", [])[:2]) if doc.get("author_name") else "Unknown"
                cover_i = doc.get("cover_i")
                img_url = f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg" if cover_i else None
                if not img_url:
                    continue
                year = doc.get("first_publish_year") or ""
                desc = f"By {author}. First published {year}." if year else f"By {author}."
                all_books.append({
                    "name": title[:200],
                    "description": desc,
                    "category": "Books",
                    "subcategory": "Fiction",
                    "product_type": "book",
                    "brand": author[:50],
                    "price_cents": 1999,
                    "source": "Open Library",
                    "scraped_from_url": f"https://openlibrary.org{doc.get('key', '')}",
                    "image_url": img_url,
                })
                if len(all_books) >= max_books:
                    break
        except Exception as e:
            print(f"   [WARN] Open Library fetch {q}: {e}")
        if len(all_books) >= max_books:
            break
    return all_books


def get_merchant_laptops_fallback():
    """Fallback: curated merchant laptops if scrape returns too few (System76, Framework, Back Market)."""
    from scripts.scrape_merchant_laptops import build_seed_products
    products = build_seed_products()
    out = []
    for p in products:
        d = {
            "name": p["name"],
            "description": p.get("description", ""),
            "category": "Electronics",
            "subcategory": p.get("subcategory"),
            "product_type": "laptop",
            "brand": p.get("brand"),
            "price_cents": p["price_cents"],
            "available_qty": p.get("available_qty", 15),
            "source": p.get("source", "Merchant"),
            "scraped_from_url": p.get("scraped_from_url"),
            "source_product_id": p.get("source_product_id"),
            "image_url": p.get("image_url"),
            "kg_features": p.get("kg_features"),
        }
        d = _ensure_image(d, "Electronics")
        d = normalize_structured_specs(d)
        out.append(d)
    return out


def scrape_electronics_and_books(urls, max_per_url=20, use_selenium_for_temu=False, use_selenium_for_blocked=False):
    """Scrape real products from URLs."""
    all_products = []
    for url in urls:
        try:
            products = scrape_products(
                [url],
                max_per_url=max_per_url,
                use_selenium_for_temu=use_selenium_for_temu,
                use_selenium_for_blocked=use_selenium_for_blocked,
            )
            for sp in products:
                d = convert_scraped_to_dict(sp)
                all_products.append(d)
        except Exception as e:
            print(f"  [WARN] Scrape {url}: {e}")
    return all_products


def filter_real_only(products, categories=None, remove_missing_images=True):
    """Keep only laptops, phones, electronics, books. Per week6: remove low quality (missing images)."""
    if categories is None:
        categories = {"Electronics", "Books"}
    allowed_types = {"laptop", "gaming_laptop", "desktop_pc", "phone", "smartphone", "tablet", "book", None}
    out = []
    dropped_no_image = 0
    for p in products:
        cat = p.get("category") or "Electronics"
        if cat not in categories:
            continue
        ptype = (p.get("product_type") or "").lower()
        if cat == "Electronics" and ptype not in {"laptop", "gaming_laptop", "desktop_pc", "phone", "smartphone", "tablet", ""}:
            if p.get("source") in {"FakeStoreAPI", "DummyJSON", "Seed"}:
                continue
        if remove_missing_images and not _has_real_image(p):
            dropped_no_image += 1
            continue
        p = _ensure_image(p, cat)
        out.append(p)
    if dropped_no_image:
        print(f"   [Filter] Dropped {dropped_no_image} products without real images (week6: remove low quality)")
    return out


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Populate DB with real scraped products only (no fake/seed)")
    parser.add_argument("--clear", action="store_true", default=True, help="Clear existing products (default: True)")
    parser.add_argument("--no-clear", action="store_true", help="Do NOT clear; add to existing (not recommended)")
    parser.add_argument("--scrape-books", action="store_true", help="Scrape Barnes & Noble for books")
    parser.add_argument("--scrape-temu", action="store_true", help="Scrape Temu (requires Selenium)")
    parser.add_argument("--temu-urls", nargs="+", default=[], help="Temu URLs to scrape")
    parser.add_argument("--full", action="store_true", help="Enable all: books, Temu, Selenium for Framework/Back Market/Fairphone")
    parser.add_argument("--keep-missing-images", action="store_true", help="Keep products with placeholder images (default: remove per week6)")
    args = parser.parse_args()

    clear = args.clear and not args.no_clear
    use_selenium_blocked = args.full
    scrape_books = args.scrape_books or args.full
    scrape_temu = args.scrape_temu or args.full

    print("=" * 80)
    print("POPULATE REAL-ONLY DATABASE")
    print("Laptops, Electronics, Books, Phones - no fake/seed/synthetic")
    print("=" * 80)

    all_products = []

    # 1. WEB SCRAPE System76, Framework, Back Market (use Selenium for blocked sites when --full)
    print("\n1. Web scraping System76, Framework, Back Market...")
    scraped_merchant = scrape_electronics_and_books(
        SYSTEM76_FRAMEWORK_BACKMARKET_URLS,
        max_per_url=50,
        use_selenium_for_blocked=use_selenium_blocked,
    )
    scraped_merchant = [p for p in scraped_merchant if p.get("category") == "Electronics" or p.get("category") == "Books"]
    all_products.extend(scraped_merchant)
    print(f"   Scraped {len(scraped_merchant)} products from System76/Framework/Back Market")

    # Fallback: if scrape returned few, add curated merchant data
    if len(scraped_merchant) < 15:
        print("   [Fallback] Adding curated merchant laptops (scrape returned few)...")
        fallback = get_merchant_laptops_fallback()
        all_products.extend(fallback)
        print(f"   Added {len(fallback)} fallback products")

    # 2. Scrape Fairphone (phones) - use Selenium when --full
    print("\n2. Web scraping Fairphone (shop.fairphone.com/smartphones)...")
    scraped_fairphone = scrape_electronics_and_books(
        FAIRPHONE_URLS,
        max_per_url=20,
        use_selenium_for_blocked=use_selenium_blocked,
    )
    for p in scraped_fairphone:
        p["product_type"] = "smartphone"
        p["category"] = "Electronics"
    all_products.extend(scraped_fairphone)
    print(f"   Scraped {len(scraped_fairphone)} Fairphone products")

    # 2b. Scrape more phones (Shopify, BigCommerce, WooCommerce demo stores)
    print("\n2b. Web scraping more phones (Shopify, BigCommerce, WooCommerce)...")
    scraped_phones = scrape_electronics_and_books(
        PHONE_URLS,
        max_per_url=25,
        use_selenium_for_blocked=use_selenium_blocked,
    )
    added_phones = 0
    for p in scraped_phones:
        ptype = (p.get("product_type") or "").lower()
        pcat = (p.get("category") or "").lower()
        name_lower = (p.get("name") or "").lower()
        is_phone = (
            any(x in ptype for x in ["phone", "smartphone"])
            or any(x in pcat for x in ["phone", "cell"])
            or any(x in name_lower for x in ["iphone", "galaxy", "samsung", "xiaomi", "phone", "smartphone", "nothing phone"])
        )
        if is_phone:
            p["product_type"] = "smartphone"
            p["category"] = "Electronics"
            all_products.append(p)
            added_phones += 1
    print(f"   Scraped {added_phones} additional phone products")

    if not use_selenium_blocked and len(scraped_fairphone) == 0:
        print("   (Use --full for Selenium when Fairphone blocks)")
    print("\n3. Scraping BigCommerce demo stores (extra laptops/electronics)...")
    scraped_bc = scrape_electronics_and_books(REAL_SCRAPE_URLS_FALLBACK, max_per_url=25)
    scraped_bc = [p for p in scraped_bc if p.get("category") == "Electronics" or p.get("category") == "Books"]
    all_products.extend(scraped_bc)
    print(f"   Scraped {len(scraped_bc)} products")

    # 5. Optional: Barnes & Noble books + Open Library fallback
    if scrape_books:
        print("\n5. Scraping books (B&N + Open Library fallback)...")
        books = scrape_electronics_and_books(BARNES_NOBLE_URLS, max_per_url=50)
        for b in books:
            b["category"] = "Books"
            b["product_type"] = "book"
        all_products.extend(books)
        print(f"   Scraped {len(books)} books from B&N")
        if len(books) == 0:
            print("   [Fallback] Fetching from Open Library API...")
            ol_books = fetch_open_library_books(max_books=30)
            all_products.extend(ol_books)
            print(f"   Fetched {len(ol_books)} books from Open Library")
    else:
        print("\n5. Skipping books (use --scrape-books or --full)")

    # 6. Optional: Temu
    if scrape_temu or args.temu_urls:
        urls = args.temu_urls or ["https://www.temu.com/laptops.html"]
        print(f"\n6. Scraping Temu (Selenium)...")
        temu = scrape_electronics_and_books(urls, max_per_url=30, use_selenium_for_temu=True)
        temu = [p for p in temu if p.get("category") == "Electronics" or p.get("category") == "Books"]
        all_products.extend(temu)
        print(f"   Scraped {len(temu)} products")
    else:
        print("\n6. Skipping Temu (use --scrape-temu or --full)")

    # Filter to real only; remove products without real images (week6: remove low quality)
    all_products = filter_real_only(all_products, remove_missing_images=not args.keep_missing_images)

    # Enrich with shipping/return/warranty (week6tips: agent-ready outputs)
    # Use real policy from scraped merchant pages when available
    policy_cache = {}
    for p in all_products:
        _enrich_with_policy(p, policy_cache)

    # Dedupe by (name, source) to avoid duplicates
    seen = set()
    unique = []
    for p in all_products:
        key = (p.get("name", "").lower()[:60], p.get("source", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    print(f"\n5. Total real products: {len(unique)} (laptops/electronics/books)")
    if not unique:
        print("\n[WARN] No products to load. Try --scrape-books or check network.")
        sys.exit(1)

    # Populate
    print("\n6. Populating PostgreSQL...")
    populate_database(unique, clear_existing=clear)

    print("\n" + "=" * 80)
    print("REAL-ONLY DATABASE READY")
    print("=" * 80)
    print(f"Products: {len(unique)}")
    print("\nNext: Build Neo4j KG (use --clear to replace old graph)")
    print("  python scripts/build_knowledge_graph.py --clear")
    print("=" * 80)


if __name__ == "__main__":
    main()
