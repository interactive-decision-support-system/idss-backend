#!/usr/bin/env python3
"""
Populate PostgreSQL database with real scraped products from Temu, WooCommerce, etc.

This script scrapes product data from various sources and populates the MCP e-commerce database.
"""

import os
import sys
import json
import random
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.database import DATABASE_URL, get_db

# Import scraper
from scripts.product_scraper import scrape_products, ScrapedProduct

# Sample real product data (in production, this would come from actual scraping)
# For now, we'll use realistic product data that mimics what would be scraped
REAL_PRODUCTS = [
    # Electronics - Laptops (every laptop has color for detail view and filtering)
    {
        "name": "MacBook Pro 16-inch M3 Max",
        "description": "Apple MacBook Pro 16-inch with M3 Max chip, 36GB unified memory, 1TB SSD. Perfect for professional work, video editing, and development.",
        "category": "Electronics",
        "brand": "Apple",
        "price_cents": 349999,
        "available_qty": 12,
        "color": "Space Gray",
        "source": "Seed",
    },
    {
        "name": "Dell XPS 15 OLED",
        "description": "Dell XPS 15 laptop with 15.6-inch OLED display, Intel Core i7, 32GB RAM, 1TB SSD. Excellent for creative professionals.",
        "category": "Electronics",
        "brand": "Dell",
        "price_cents": 249999,
        "available_qty": 8,
        "color": "Platinum Silver",
        "source": "Seed",
    },
    {
        "name": "HP Spectre x360 14",
        "description": "HP Spectre x360 14-inch 2-in-1 laptop, Intel Core i7, 16GB RAM, 512GB SSD. Versatile convertible design.",
        "category": "Electronics",
        "brand": "HP",
        "price_cents": 129999,
        "available_qty": 15,
        "color": "Natural Silver",
        "source": "Seed",
    },
    {
        "name": "Lenovo ThinkPad X1 Carbon",
        "description": "Lenovo ThinkPad X1 Carbon Gen 11, Intel Core i7, 16GB RAM, 512GB SSD. Business-class durability and performance.",
        "category": "Electronics",
        "brand": "Lenovo",
        "price_cents": 189999,
        "available_qty": 10,
        "color": "Black",
        "source": "Seed",
    },
    {
        "name": "ASUS ROG Zephyrus G14",
        "description": "ASUS ROG Zephyrus G14 gaming laptop, AMD Ryzen 9, NVIDIA RTX 4060, 16GB RAM, 1TB SSD. Powerful gaming performance.",
        "category": "Electronics",
        "brand": "ASUS",
        "price_cents": 159999,
        "available_qty": 6,
        "color": "Eclipse Gray",
        "source": "Seed",
        "product_type": "gaming_laptop",
        "gpu_vendor": "NVIDIA",
        "gpu_model": "RTX 4060",
        "tags": ["gaming"],
    },
    {
        "name": "CyberPowerPC Gamer Xtreme",
        "description": "Gaming desktop PC with Intel Core i7-13700F, NVIDIA RTX 4070, 16GB RAM, 1TB SSD. Prebuilt gaming PC.",
        "category": "Electronics",
        "brand": "CyberPowerPC",
        "price_cents": 149999,
        "available_qty": 4,
        "color": "Black",
        "source": "Seed",
        "product_type": "desktop_pc",
        "gpu_vendor": "NVIDIA",
        "gpu_model": "RTX 4070",
        "tags": ["gaming"],
    },
    {
        "name": "iBuyPower Trace MR",
        "description": "Gaming desktop with AMD Ryzen 7, NVIDIA RTX 4060 Ti, 32GB RAM, 1TB NVMe. Desktop PC for gaming.",
        "category": "Electronics",
        "brand": "iBuyPower",
        "price_cents": 129999,
        "available_qty": 5,
        "color": "Black",
        "source": "Seed",
        "product_type": "desktop_pc",
        "gpu_vendor": "NVIDIA",
        "gpu_model": "RTX 4060 Ti",
        "tags": ["gaming"],
    },
    # Electronics - Phones
    {
        "name": "iPhone 15 Pro Max",
        "description": "Apple iPhone 15 Pro Max, 256GB, Titanium. Latest flagship with A17 Pro chip and advanced camera system.",
        "category": "Electronics",
        "brand": "Apple",
        "price_cents": 119999,
        "available_qty": 25,
        "color": "Natural Titanium",
        "source": "Seed",
    },
    {
        "name": "Samsung Galaxy S24 Ultra",
        "description": "Samsung Galaxy S24 Ultra, 256GB, Phantom Black. Flagship Android phone with S Pen and advanced AI features.",
        "category": "Electronics",
        "brand": "Samsung",
        "price_cents": 109999,
        "available_qty": 18,
        "color": "Phantom Black",
        "source": "Seed",
    },
    {
        "name": "Google Pixel 8 Pro",
        "description": "Google Pixel 8 Pro, 128GB, Obsidian. Pure Android experience with exceptional camera and AI capabilities.",
        "category": "Electronics",
        "brand": "Google",
        "price_cents": 89999,
        "available_qty": 20,
        "color": "Obsidian",
        "source": "Seed",
    },
    
    # Books
    {
        "name": "The Seven Husbands of Evelyn Hugo",
        "description": "A captivating novel by Taylor Jenkins Reid about a reclusive Hollywood icon who finally decides to tell her story.",
        "category": "Books",
        "subcategory": "Fiction",
        "brand": "Atria Books",
        "price_cents": 1699,
        "available_qty": 50,
    },
    {
        "name": "Atomic Habits",
        "description": "An Easy & Proven Way to Build Good Habits & Break Bad Ones by James Clear. Bestselling self-help book.",
        "category": "Books",
        "subcategory": "Self-Help",
        "brand": "Avery",
        "price_cents": 1799,
        "available_qty": 75,
    },
    {
        "name": "Project Hail Mary",
        "description": "A science fiction novel by Andy Weir, author of The Martian. Thrilling space adventure.",
        "category": "Books",
        "subcategory": "Science Fiction",
        "brand": "Ballantine Books",
        "price_cents": 1899,
        "available_qty": 40,
    },
    {
        "name": "The Midnight Library",
        "description": "A novel by Matt Haig about a library between life and death where every book provides a chance to try another life.",
        "category": "Books",
        "subcategory": "Fiction",
        "brand": "Viking",
        "price_cents": 1599,
        "available_qty": 60,
    },
    {
        "name": "Educated",
        "description": "A memoir by Tara Westover about growing up in a survivalist Mormon family and her journey to education.",
        "category": "Books",
        "subcategory": "Memoir",
        "brand": "Random House",
        "price_cents": 1699,
        "available_qty": 45,
    },
    
    # Home & Kitchen
    {
        "name": "Instant Pot Duo Plus 9-in-1",
        "description": "9-in-1 electric pressure cooker, slow cooker, rice cooker, steamer, sauté pan, yogurt maker, and more.",
        "category": "Home & Kitchen",
        "brand": "Instant Pot",
        "price_cents": 9999,
        "available_qty": 30,
    },
    {
        "name": "Ninja Foodi 8-in-1",
        "description": "8-in-1 indoor grill and air fryer. Grill, air fry, roast, bake, broil, and dehydrate all in one appliance.",
        "category": "Home & Kitchen",
        "brand": "Ninja",
        "price_cents": 19999,
        "available_qty": 15,
    },
    {
        "name": "Dyson V15 Detect Vacuum",
        "description": "Cordless vacuum cleaner with laser dust detection, powerful suction, and advanced filtration.",
        "category": "Home & Kitchen",
        "brand": "Dyson",
        "price_cents": 74999,
        "available_qty": 12,
    },
    
    # Sports & Outdoors
    {
        "name": "Nike Air Zoom Pegasus 40",
        "description": "Responsive running shoes with excellent cushioning for daily training and long runs.",
        "category": "Sports",
        "brand": "Nike",
        "price_cents": 12999,
        "available_qty": 100,
    },
    {
        "name": "Adidas Ultraboost 22",
        "description": "Premium running shoes with Boost midsole technology for maximum energy return.",
        "category": "Sports",
        "brand": "Adidas",
        "price_cents": 17999,
        "available_qty": 80,
    },
    {
        "name": "YETI Rambler 30oz Tumbler",
        "description": "Insulated stainless steel tumbler keeps drinks cold for hours. Perfect for outdoor adventures.",
        "category": "Sports",
        "brand": "YETI",
        "price_cents": 4499,
        "available_qty": 200,
    },
    
    # Electronics - Accessories
    {
        "name": "AirPods Pro 2",
        "description": "Apple AirPods Pro with Active Noise Cancellation, Spatial Audio, and MagSafe charging case.",
        "category": "Electronics",
        "brand": "Apple",
        "price_cents": 24999,
        "available_qty": 75,
        "color": "White",
        "source": "Seed",
    },
    {
        "name": "Sony WH-1000XM5 Headphones",
        "description": "Premium noise-canceling wireless headphones with exceptional sound quality and battery life.",
        "category": "Electronics",
        "brand": "Sony",
        "price_cents": 39999,
        "available_qty": 25,
        "color": "Black",
        "source": "Seed",
    },
    {
        "name": "Logitech MX Master 3S Mouse",
        "description": "Wireless ergonomic mouse with precision tracking, customizable buttons, and multi-device connectivity.",
        "category": "Electronics",
        "brand": "Logitech",
        "price_cents": 9999,
        "available_qty": 150,
        "color": "Graphite",
        "source": "Seed",
    },
]

# Real Mac models and prices (from Apple.com, Jan 2025). Use for --scrape-macs fallback or seed.
# source=Seed, scraped_from_url=None. color set where applicable.
REAL_MACS = [
    {"name": "MacBook Air 13\" M3", "description": "13.6-inch Liquid Retina, M3 chip, 8GB RAM, 256GB SSD. Apple MacBook Air.", "category": "Electronics", "brand": "Apple", "price_cents": 109900, "available_qty": 25, "color": "Silver", "source": "Seed"},
    {"name": "MacBook Air 13\" M3 16GB 512GB", "description": "13.6-inch MacBook Air M3, 16GB unified memory, 512GB SSD.", "category": "Electronics", "brand": "Apple", "price_cents": 139900, "available_qty": 15, "color": "Space Gray", "source": "Seed"},
    {"name": "MacBook Air 15\" M3", "description": "15.3-inch Liquid Retina, M3 chip, 8GB RAM, 256GB SSD.", "category": "Electronics", "brand": "Apple", "price_cents": 129900, "available_qty": 18, "color": "Midnight", "source": "Seed"},
    {"name": "MacBook Pro 14\" M3", "description": "14.2-inch Liquid Retina XDR, M3 chip, 8GB RAM, 512GB SSD.", "category": "Electronics", "brand": "Apple", "price_cents": 159900, "available_qty": 12, "color": "Space Black", "source": "Seed"},
    {"name": "MacBook Pro 14\" M3 Pro", "description": "14.2-inch MacBook Pro, M3 Pro, 18GB RAM, 512GB SSD.", "category": "Electronics", "brand": "Apple", "price_cents": 199900, "available_qty": 10, "color": "Silver", "source": "Seed"},
    {"name": "MacBook Pro 16\" M3 Pro", "description": "16.2-inch Liquid Retina XDR, M3 Pro, 18GB RAM, 512GB SSD.", "category": "Electronics", "brand": "Apple", "price_cents": 249900, "available_qty": 8, "color": "Space Gray", "source": "Seed"},
    {"name": "MacBook Pro 16\" M3 Max", "description": "16.2-inch MacBook Pro, M3 Max, 36GB RAM, 1TB SSD.", "category": "Electronics", "brand": "Apple", "price_cents": 349900, "available_qty": 6, "color": "Space Black", "source": "Seed"},
    {"name": "MacBook Pro 14\" M3 Max", "description": "14.2-inch MacBook Pro, M3 Max, 36GB RAM, 1TB SSD.", "category": "Electronics", "brand": "Apple", "price_cents": 319900, "available_qty": 5, "color": "Space Gray", "source": "Seed"},
]

# Working demo URLs for real scraped products (BigCommerce, PluginRepublic).
# WooCommerce: use your own store URLs; many demos restrict API. Store API fallback tries /wp-json/wc/store/v1/products.
MAC_SCRAPE_URLS = [
    "https://mc-demo.mybigcommerce.com/",
    "https://mc-demo.mybigcommerce.com/categories/Laptops/MAC/",
    "https://mc-demo.mybigcommerce.com/categories/iPods/",
    "https://pluginrepublic.dev/product-extras/product/macbook/",
]

def generate_product_id(category: str, brand: str, name: str, index: int) -> str:
    """Generate a unique product ID."""
    # Clean brand and name for ID (handle None from scraped data)
    brand_clean = (brand or "Unknown").replace(" ", "-").replace("&", "and").upper()[:10]
    name_clean = (name or "product").replace(" ", "-").replace("&", "and").upper()[:15]
    cat = (category or "Electronics")[:3].lower()
    return f"prod-{cat}-{brand_clean}-{name_clean}-{index:03d}"


def stable_product_id_for_source(source: str, source_product_id: str, category: str = "Electronics") -> str:
    """
    Derive a stable product_id from (source, source_product_id) so the same external product
    always maps to the same row, avoiding ux_products_source_source_product_id UniqueViolation
    when the same URL is scraped from multiple category pages.
    """
    raw = f"{source}|{source_product_id}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:20]
    cat = (category or "Electronics")[:3].lower()
    return f"prod-{cat}-{h}"


def normalize_structured_specs(product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract product_type, gpu_vendor, tags from name+description so hard filters work.
    Run after building product dict; mutates and returns product.
    """
    name = (product.get("name") or "").lower()
    desc = (product.get("description") or "").lower()
    text = f"{name} {desc}"
    if product.get("product_type") is None:
        if "gaming" in text and ("pc" in text or "desktop" in text or "computer" in text):
            product["product_type"] = "desktop_pc"
        elif "gaming" in text and ("laptop" in text or "notebook" in text):
            product["product_type"] = "gaming_laptop"
        elif "laptop" in text or "notebook" in text or "macbook" in text or "thinkpad" in text:
            product["product_type"] = "laptop"
        elif "desktop" in text or "workstation" in text or "tower" in text:
            product["product_type"] = "desktop_pc"
        elif product.get("category") == "Books":
            product["product_type"] = "book"
    if product.get("gpu_vendor") is None:
        if "rtx" in text or "geforce" in text or "nvidia" in text:
            product["gpu_vendor"] = "NVIDIA"
        elif "radeon" in text or "rx " in text or "amd" in text and ("gpu" in text or "graphics" in text):
            product["gpu_vendor"] = "AMD"
        elif "apple" in text and ("m1" in text or "m2" in text or "m3" in text or "m4" in text):
            product["gpu_vendor"] = "Apple"
    if product.get("tags") is None and "gaming" in text:
        product["tags"] = ["gaming"]
    return product


def convert_scraped_to_dict(scraped: ScrapedProduct) -> Dict[str, Any]:
    """Convert ScrapedProduct to dictionary format."""
    d = {
        "name": scraped.name,
        "description": scraped.description,
        "category": scraped.category,
        "subcategory": scraped.subcategory,
        "brand": scraped.brand,
        "price_cents": scraped.price_cents,
        "available_qty": scraped.available_qty,
        "source": scraped.source,
        "color": getattr(scraped, "color", None),
        "scraped_from_url": scraped.source_url or getattr(scraped, "scraped_from_url", None),
        "image_url": getattr(scraped, "image_url", None),
        "source_product_id": getattr(scraped, "source_product_id", None) or scraped.source_url,
    }
    return normalize_structured_specs(d)


def populate_database(products: List[Dict[str, Any]], clear_existing: bool = False):
    """
    Populate PostgreSQL database with products.
    
    Args:
        products: List of product dictionaries
        clear_existing: If True, clear existing products before inserting
    """
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        if clear_existing:
            print("Clearing existing products...")
            db.execute(text("DELETE FROM cart_items"))
            db.execute(text("DELETE FROM inventory"))
            db.execute(text("DELETE FROM prices"))
            db.execute(text("DELETE FROM products"))
            db.commit()
            print("[OK] Cleared existing products")
        
        print(f"\nInserting {len(products)} products...")
        
        for idx, product in enumerate(products):
            product = normalize_structured_specs(dict(product))
            source = product.get("source") or "Seed"
            source_product_id = product.get("source_product_id")
            if source and source_product_id:
                # Include index so multiple products from same page (e.g. Temu category) get distinct IDs
                unique_key = f"{source_product_id}|{idx}"
                product_id = stable_product_id_for_source(
                    source, unique_key, product.get("category", "Electronics")
                )
            else:
                product_id = generate_product_id(
                    product.get("category", "Electronics"),
                    product.get("brand") or "Unknown",
                    product.get("name", "product"),
                    idx,
                )
            # tags as list for PostgreSQL array
            tags = product.get("tags")
            tags_sql = tags if isinstance(tags, list) else ([tags] if tags else None)
            insert_params = {
                "product_id": product_id,
                "name": product["name"],
                "description": product.get("description", ""),
                "category": product["category"],
                "subcategory": product.get("subcategory"),
                "brand": product.get("brand"),
                "source": product.get("source") or "Seed",
                "color": product.get("color"),
                "scraped_from_url": product.get("scraped_from_url"),
                "product_type": product.get("product_type"),
                "gpu_vendor": product.get("gpu_vendor"),
                "gpu_model": product.get("gpu_model"),
                "tags": tags_sql,
                "image_url": product.get("image_url"),
                "source_product_id": product.get("source_product_id"),
            }
            try:
                db.execute(
                    text("""
                        INSERT INTO products (product_id, name, description, category, subcategory, brand, source, color, scraped_from_url,
                            product_type, gpu_vendor, gpu_model, tags, image_url, source_product_id)
                        VALUES (:product_id, :name, :description, :category, :subcategory, :brand, :source, :color, :scraped_from_url,
                            :product_type, :gpu_vendor, :gpu_model, :tags, :image_url, :source_product_id)
                        ON CONFLICT (product_id) DO UPDATE SET
                            name = EXCLUDED.name,
                            description = EXCLUDED.description,
                            category = EXCLUDED.category,
                            subcategory = EXCLUDED.subcategory,
                            brand = EXCLUDED.brand,
                            source = EXCLUDED.source,
                            color = EXCLUDED.color,
                            scraped_from_url = EXCLUDED.scraped_from_url,
                            product_type = EXCLUDED.product_type,
                            gpu_vendor = EXCLUDED.gpu_vendor,
                            gpu_model = EXCLUDED.gpu_model,
                            tags = EXCLUDED.tags,
                            image_url = EXCLUDED.image_url,
                            source_product_id = EXCLUDED.source_product_id,
                            updated_at = CURRENT_TIMESTAMP
                    """),
                    insert_params,
                )
            except Exception as e:
                db.rollback()
                if "product_type" in str(e) or "gpu_vendor" in str(e) or "column" in str(e).lower():
                    # Fallback: table lacks new columns; run psql mcp_ecommerce -f scripts/add_structured_specs.sql
                    db.execute(
                        text("""
                            INSERT INTO products (product_id, name, description, category, subcategory, brand, source, color, scraped_from_url)
                            VALUES (:product_id, :name, :description, :category, :subcategory, :brand, :source, :color, :scraped_from_url)
                            ON CONFLICT (product_id) DO UPDATE SET
                                name = EXCLUDED.name,
                                description = EXCLUDED.description,
                                category = EXCLUDED.category,
                                subcategory = EXCLUDED.subcategory,
                                brand = EXCLUDED.brand,
                                source = EXCLUDED.source,
                                color = EXCLUDED.color,
                                scraped_from_url = EXCLUDED.scraped_from_url,
                                updated_at = CURRENT_TIMESTAMP
                        """),
                        {k: insert_params[k] for k in ("product_id", "name", "description", "category", "subcategory", "brand", "source", "color", "scraped_from_url")},
                    )
                    if idx == 0:
                        print("  (Table missing product_type/gpu_vendor columns; run: psql mcp_ecommerce -f scripts/add_structured_specs.sql)")
                else:
                    raise
            
            # Insert price
            db.execute(
                text("""
                    INSERT INTO prices (product_id, price_cents, currency)
                    VALUES (:product_id, :price_cents, 'USD')
                    ON CONFLICT (product_id) DO UPDATE SET
                        price_cents = EXCLUDED.price_cents,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {
                    "product_id": product_id,
                    "price_cents": product["price_cents"],
                }
            )
            
            # Insert inventory
            db.execute(
                text("""
                    INSERT INTO inventory (product_id, available_qty, reserved_qty)
                    VALUES (:product_id, :available_qty, 0)
                    ON CONFLICT (product_id) DO UPDATE SET
                        available_qty = EXCLUDED.available_qty,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {
                    "product_id": product_id,
                    "available_qty": product.get("available_qty", 0),
                }
            )
            
            if (idx + 1) % 10 == 0:
                print(f"  Inserted {idx + 1}/{len(products)} products...")
        
        db.commit()
        print(f"\n[OK] Successfully inserted {len(products)} products!")
        
        # Verify counts
        product_count = db.execute(text("SELECT COUNT(*) FROM products")).scalar()
        price_count = db.execute(text("SELECT COUNT(*) FROM prices")).scalar()
        inventory_count = db.execute(text("SELECT COUNT(*) FROM inventory")).scalar()
        
        print(f"\nDatabase Summary:")
        print(f"  Products: {product_count}")
        print(f"  Prices: {price_count}")
        print(f"  Inventory records: {inventory_count}")
        
    except Exception as e:
        db.rollback()
        print(f"[FAIL] Error populating database: {e}")
        raise
    finally:
        db.close()


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Populate database with real products")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing products before inserting"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Limit number of products to insert (for testing)"
    )
    parser.add_argument(
        "--scrape",
        action="store_true",
        help="Scrape products from URLs instead of using sample data"
    )
    parser.add_argument(
        "--urls",
        nargs="+",
        default=[],
        help="URLs to scrape products from (e.g., https://example.com/products)"
    )
    parser.add_argument(
        "--max-per-url",
        type=int,
        default=20,
        help="Maximum products to scrape per URL (default: 20)"
    )
    parser.add_argument(
        "--scrape-macs",
        action="store_true",
        help="Scrape Macs/laptops from demo URLs; merge with REAL_MACS unless --scrape-only",
    )
    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="Use only scraped products; no seed/REAL_MACS. Use with --scrape or --scrape-macs.",
    )

    args = parser.parse_args()

    if args.scrape_macs:
        print("=" * 60)
        print("Scraping Real Macs / Laptops")
        print("=" * 60)
        urls = args.urls if args.urls else MAC_SCRAPE_URLS
        print(f"URLs: {urls}")
        # Auto-detect if any URLs are Temu and use Selenium
        has_temu = any('temu.com' in url.lower() or 'temu.' in url.lower() for url in urls)
        use_selenium = has_temu  # Auto-use Selenium for Temu
        
        if use_selenium:
            print("Detected Temu URL(s) - will use Selenium for JavaScript rendering")
        
        scraped = scrape_products(urls, max_per_url=args.max_per_url, use_selenium_for_temu=use_selenium)
        products = [convert_scraped_to_dict(p) for p in scraped]
        if not args.scrape_only:
            seen = {p["name"].lower() for p in products}
            for m in REAL_MACS:
                if m["name"].lower() not in seen:
                    products.append(m)
                    seen.add(m["name"].lower())
            # Also add all REAL_PRODUCTS Electronics (laptops, desktops, etc.) so every laptop has color in DB
            for p in REAL_PRODUCTS:
                if p.get("category") == "Electronics" and p.get("name", "").lower() not in seen:
                    products.append(dict(p))
                    seen.add(p["name"].lower())
        if args.count:
            products = products[: args.count]
        print(f"Total products (scraped only): {len(products)}" if args.scrape_only else f"Total products (scraped + seed): {len(products)}")
    elif args.scrape and args.urls:
        print("=" * 60)
        print("Scraping Products from URLs")
        print("=" * 60)
        print(f"URLs: {args.urls}")
        print(f"Max per URL: {args.max_per_url}")
        print("=" * 60)
        # Auto-detect if any URLs are Temu and use Selenium
        has_temu = any('temu.com' in url.lower() or 'temu.' in url.lower() for url in args.urls)
        use_selenium = has_temu  # Auto-use Selenium for Temu
        
        if use_selenium:
            print("Detected Temu URL(s) - will use Selenium for JavaScript rendering")
        
        scraped_products = scrape_products(args.urls, max_per_url=args.max_per_url, use_selenium_for_temu=use_selenium)
        products = [convert_scraped_to_dict(p) for p in scraped_products]
        if args.count:
            products = products[: args.count]
        print(f"\nScraped {len(products)} products total")
    else:
        if args.scrape_only:
            print("--scrape-only requires --scrape --urls <...> or --scrape-macs")
            sys.exit(1)
        # Include REAL_MACS so "black mac laptop" finds Midnight/Space Black MacBooks
        products = REAL_PRODUCTS + REAL_MACS
        if args.count:
            products = products[: args.count]
        print("=" * 60)
        print("Populating MCP E-commerce Database with Sample Products")
        print("=" * 60)
        print(f"Database: {DATABASE_URL}")
        print(f"Products to insert: {len(products)}")
        print(f"Clear existing: {args.clear}")
        print("=" * 60)
        print("Tip: --scrape-macs --scrape-only --clear for scraped-only; --scrape --urls <URL1> <URL2> for custom URLs")
        print("=" * 60)
    
    populate_database(products, clear_existing=args.clear)
    
    print("\n[OK] Done!")
    print("   Products will appear on the website when the MCP server uses the same DATABASE_URL.")
    print("   Run from idss-mcp/mcp-server: python scripts/check_db_products.py to verify.")


if __name__ == "__main__":
    main()
