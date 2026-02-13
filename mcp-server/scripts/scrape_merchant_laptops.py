#!/usr/bin/env python3
"""
Scrape and seed laptops from merchant sites with complex requirements and rich attributes.

Merchants (per user request):
- System76 – Linux laptops; clear warranty/returns
- Framework – repairable laptops; explicit warranty + return policy
- Back Market – refurbished; emphasizes warranty/returns/shipping

Populates PostgreSQL with rich catalogs (images, core attributes, shipping/returns/warranty in description).
Sets kg_features for KG/search (good_for_linux, repairable, refurbished, etc.).
Run: python scripts/scrape_merchant_laptops.py
Then: python scripts/build_knowledge_graph.py (or build_knowledge_graph_all.py)
"""

import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product, Price, Inventory

# ---------------------------------------------------------------------------
# Seed data: rich catalogs with warranty/returns/shipping (guaranteed demo data)
# ---------------------------------------------------------------------------

SYSTEM76_POLICY = (
    " Warranty: 1-year limited warranty; 30-day return policy. "
    "Shipping: Free standard shipping in continental US; expedited available. "
    "Returns: 30-day return for refund; must be in like-new condition."
)
FRAMEWORK_POLICY = (
    " Warranty: 1-year limited warranty on Framework products. "
    "Return policy: 30-day return window; see framework.com/returns. "
    "Shipping: Free shipping on orders over threshold; international available."
)
BACKMARKET_POLICY = (
    " Warranty: 12-month Back Market limited warranty on refurbished devices. "
    "Returns: 30-day money-back guarantee. "
    "Shipping: Free shipping; delivery in 3–7 business days."
)

# System76 laptop models from system76.com/laptops (real catalog as of 2025)
SYSTEM76_LAPTOPS = [
    {"name": "System76 Lemur Pro 14\"", "description": "Ultraportable Linux laptop. 14th Gen Intel Core Ultra, up to 14hr battery, 8TB NVMe, 56GB DDR5." + SYSTEM76_POLICY, "price_cents": 159900, "subcategory": "Linux", "image_url": "https://cdn11.bigcommerce.com/s-pywjnxrcr2/images/stencil/original/image-manager/lemp13-hero-min.png", "scraped_from_url": "https://system76.com/laptops/lemp13/configure", "specs": "Intel Core Ultra, 16GB RAM, 512GB NVMe", "kg_features": {"good_for_linux": True, "good_for_web_dev": True, "battery_life_hours": 14}},
    {"name": "System76 Darter Pro 14\" or 16\"", "description": "Best screen real estate. Up to 16-core Intel Core Ultra, 9hr battery, 8TB NVMe, 96GB DDR5." + SYSTEM76_POLICY, "price_cents": 159900, "subcategory": "Linux", "image_url": "https://cdn11.bigcommerce.com/s-pywjnxrcr2/images/stencil/original/image-manager/darp11-product-14-1280pop-min.png", "scraped_from_url": "https://system76.com/laptops/darp11/configure", "specs": "Intel Core Ultra, 32GB RAM, 1TB SSD", "kg_features": {"good_for_linux": True, "good_for_web_dev": True, "good_for_creative": True, "battery_life_hours": 9}},
    {"name": "System76 Pangolin 16\"", "description": "Best integrated graphics. AMD Ryzen 9 8945HS, 6hr battery, 16TB NVMe, 96GB DDR5, 2K display." + SYSTEM76_POLICY, "price_cents": 169900, "subcategory": "Linux", "image_url": "https://cdn11.bigcommerce.com/s-pywjnxrcr2/images/stencil/original/image-manager/pang15-quarter-right-pop-heror-min.png", "scraped_from_url": "https://system76.com/laptops/pang15/configure", "specs": "AMD Ryzen 9, 16GB RAM, 512GB NVMe", "kg_features": {"good_for_linux": True, "good_for_web_dev": True, "battery_life_hours": 6}},
    {"name": "System76 Gazelle 15\"", "description": "Fast, affordable graphics. Intel Core 7, NVIDIA RTX 5050, 8TB NVMe, 64GB DDR5, 144Hz display." + SYSTEM76_POLICY, "price_cents": 189900, "subcategory": "Linux", "image_url": "https://cdn11.bigcommerce.com/s-pywjnxrcr2/images/stencil/original/image-manager/product-gaze20-qrtr-1280-grey-popnobootc.jpg", "scraped_from_url": "https://system76.com/laptops/gaze20/configure", "specs": "Intel Core 7, RTX 5050, 16GB RAM, 512GB SSD", "kg_features": {"good_for_linux": True, "good_for_gaming": True, "good_for_ml": True, "battery_life_hours": 6}},
    {"name": "System76 Oryx Pro 16\"", "description": "Premium performance. AMD Ryzen AI 9 HX 370, NVIDIA RTX 5070, 8TB NVMe, 96GB DDR5, 2K 240Hz." + SYSTEM76_POLICY, "price_cents": 269900, "subcategory": "Linux", "image_url": "https://cdn11.bigcommerce.com/s-pywjnxrcr2/images/stencil/original/image-manager/product-oryp13-qrtr-1280-pop-grey-min.jpg", "scraped_from_url": "https://system76.com/laptops/oryp13/configure", "specs": "AMD Ryzen AI 9, RTX 5070, 32GB RAM, 1TB SSD", "kg_features": {"good_for_linux": True, "good_for_gaming": True, "good_for_ml": True, "good_for_creative": True, "battery_life_hours": 6}},
    {"name": "System76 Adder WS 15\" or 17\"", "description": "Affordable high-end performance. Intel Core Ultra 9, RTX 5050/5060/5070, 12TB NVMe, 96GB DDR5." + SYSTEM76_POLICY, "price_cents": 219900, "subcategory": "Linux", "image_url": "https://cdn11.bigcommerce.com/s-pywjnxrcr2/images/stencil/original/image-manager/addw5-15-1280-gray-pop-min.jpg", "scraped_from_url": "https://system76.com/laptops/addw5/configure", "specs": "Intel Core Ultra 9, RTX 5060, 32GB RAM, 1TB SSD", "kg_features": {"good_for_linux": True, "good_for_ml": True, "good_for_gaming": True, "good_for_creative": True, "battery_life_hours": 5}},
    {"name": "System76 Serval WS 16\"", "description": "Premium performance. Intel Core Ultra 9, RTX 5070 Ti, 16TB NVMe, 96GB DDR5, 2K 240Hz." + SYSTEM76_POLICY, "price_cents": 319900, "subcategory": "Linux", "image_url": "https://cdn11.bigcommerce.com/s-pywjnxrcr2/images/stencil/original/image-manager/servw14-grey-right-pop-min.jpg", "scraped_from_url": "https://system76.com/laptops/serw14/configure", "specs": "Intel Core Ultra 9, RTX 5070 Ti, 32GB RAM, 1TB SSD", "kg_features": {"good_for_linux": True, "good_for_ml": True, "good_for_gaming": True, "good_for_creative": True, "battery_life_hours": 5}},
    {"name": "System76 Bonobo WS 18\"", "description": "Most powerful. Intel Core Ultra 9, RTX 5080/5090, 20TB NVMe, 192GB DDR5, 2K/4K display." + SYSTEM76_POLICY, "price_cents": 419900, "subcategory": "Linux", "image_url": "https://cdn11.bigcommerce.com/s-pywjnxrcr2/images/stencil/original/image-manager/bonw16-right-pop-grey.jpg", "scraped_from_url": "https://system76.com/laptops/bonw16/configure", "specs": "Intel Core Ultra 9, RTX 5090, 64GB RAM, 2TB SSD", "kg_features": {"good_for_linux": True, "good_for_ml": True, "good_for_gaming": True, "good_for_creative": True, "battery_life_hours": 4}},
]

FRAMEWORK_LAPTOPS = [
    {
        "name": "Framework Laptop 13 (Intel)",
        "description": "Repairable, upgradeable 13.5\" laptop. Modular design; replace RAM, storage, ports. Intel Core Ultra."
        + FRAMEWORK_POLICY,
        "price_cents": 104900,
        "subcategory": "Work",
        "image_url": "https://frame.work/cdn/shop/files/...",
        "scraped_from_url": "https://frame.work/marketplace/laptops",
        "specs": "Intel Core Ultra 7, 16GB RAM, 256GB SSD",
        "kg_features": {"good_for_web_dev": True, "repairable": True, "battery_life_hours": 10},
    },
    {
        "name": "Framework Laptop 16",
        "description": "Modular 16\" laptop with optional discrete GPU. Repairable and upgradeable; warranty and return policy on site."
        + FRAMEWORK_POLICY,
        "price_cents": 139900,
        "subcategory": "Creative",
        "image_url": "https://frame.work/cdn/shop/files/...",
        "scraped_from_url": "https://frame.work/marketplace/laptops",
        "specs": "AMD Ryzen 9, 32GB RAM, 512GB SSD, GPU expansion",
        "kg_features": {"good_for_web_dev": True, "good_for_creative": True, "repairable": True, "battery_life_hours": 8},
    },
]

BACKMARKET_LAPTOPS = [
    {
        "name": "Refurbished MacBook Pro 14\" M3 (Back Market)",
        "description": "Certified refurbished MacBook Pro 14\" with Apple M3. Inspected and graded; full Back Market warranty."
        + BACKMARKET_POLICY,
        "price_cents": 129900,
        "subcategory": "Creative",
        "brand": "Apple",
        "image_url": "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?w=800",
        "scraped_from_url": "https://www.backmarket.com/",
        "specs": "Apple M3, 18GB RAM, 512GB SSD",
        "kg_features": {"refurbished": True, "good_for_creative": True, "battery_life_hours": 12},
    },
    {
        "name": "Refurbished Dell XPS 15 (Back Market)",
        "description": "Refurbished Dell XPS 15. 12-month Back Market warranty, 30-day returns, free shipping."
        + BACKMARKET_POLICY,
        "price_cents": 89900,
        "subcategory": "Work",
        "brand": "Dell",
        "image_url": "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=800",
        "scraped_from_url": "https://www.backmarket.com/",
        "specs": "Intel i7, 16GB RAM, 512GB SSD",
        "kg_features": {"refurbished": True, "good_for_web_dev": True, "battery_life_hours": 8},
    },
    {
        "name": "Refurbished Lenovo ThinkPad X1 Carbon (Back Market)",
        "description": "Certified refurbished ThinkPad X1 Carbon. Business-grade; Back Market warranty and returns."
        + BACKMARKET_POLICY,
        "price_cents": 74900,
        "subcategory": "Work",
        "brand": "Lenovo",
        "image_url": "https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?w=800",
        "scraped_from_url": "https://www.backmarket.com/",
        "specs": "Intel i5, 8GB RAM, 256GB SSD",
        "kg_features": {"refurbished": True, "good_for_web_dev": True, "battery_life_hours": 10},
    },
]


def _reviews_for(subcategory: str, source: str) -> str:
    """Generate minimal reviews JSON for merchant laptops."""
    templates = [
        {"rating": 5, "comment": "Exactly as described. Warranty and return policy were clear.", "verified": True},
        {"rating": 4, "comment": "Great machine. Shipping was fast.", "verified": True},
        {"rating": 5, "comment": "Refurbished like new. Very happy.", "verified": True},
    ]
    if "Linux" in subcategory or "System76" in source:
        templates.append({"rating": 5, "comment": "Out-of-box Linux. Everything works.", "verified": True})
    if "Framework" in source:
        templates.append({"rating": 5, "comment": "Repairability is a game-changer.", "verified": True})
    n = random.randint(2, 5)
    chosen = random.sample(templates, min(n, len(templates)))
    avg = sum(c["rating"] for c in chosen) / len(chosen)
    return json.dumps({"reviews": chosen, "review_count": n, "average_rating": round(avg, 1)})


def build_seed_products() -> List[Dict[str, Any]]:
    """Build product dicts for System76, Framework, Back Market from seed data."""
    out: List[Dict[str, Any]] = []
    for item in SYSTEM76_LAPTOPS:
        sid = f"system76:{item['scraped_from_url']}"
        pid = "prod-elec-" + hashlib.sha256(sid.encode()).hexdigest()[:16]
        out.append({
            "product_id": pid,
            "name": item["name"],
            "description": item["description"],
            "category": "Electronics",
            "subcategory": item["subcategory"],
            "product_type": "laptop",
            "brand": "System76",
            "price_cents": item["price_cents"],
            "available_qty": random.randint(10, 50),
            "source": "System76",
            "scraped_from_url": item["scraped_from_url"],
            "source_product_id": sid,
            "image_url": item.get("image_url"),
            "reviews": _reviews_for(item["subcategory"], "System76"),
            "kg_features": item.get("kg_features"),
        })
    for item in FRAMEWORK_LAPTOPS:
        sid = f"framework:{item['name']}"
        pid = "prod-elec-" + hashlib.sha256(sid.encode()).hexdigest()[:16]
        out.append({
            "product_id": pid,
            "name": item["name"],
            "description": item["description"],
            "category": "Electronics",
            "subcategory": item["subcategory"],
            "product_type": "laptop",
            "brand": "Framework",
            "price_cents": item["price_cents"],
            "available_qty": random.randint(5, 30),
            "source": "Framework",
            "scraped_from_url": item["scraped_from_url"],
            "source_product_id": f"framework:{hashlib.sha256(sid.encode()).hexdigest()[:12]}",
            "image_url": item.get("image_url"),
            "reviews": _reviews_for(item["subcategory"], "Framework"),
            "kg_features": item.get("kg_features"),
        })
    for item in BACKMARKET_LAPTOPS:
        sid = f"backmarket:{item['name']}"
        pid = "prod-elec-" + hashlib.sha256(sid.encode()).hexdigest()[:16]
        out.append({
            "product_id": pid,
            "name": item["name"],
            "description": item["description"],
            "category": "Electronics",
            "subcategory": item["subcategory"],
            "product_type": "laptop",
            "brand": item.get("brand", "Various"),
            "price_cents": item["price_cents"],
            "available_qty": random.randint(5, 25),
            "source": "Back Market",
            "scraped_from_url": item["scraped_from_url"],
            "source_product_id": f"backmarket:{hashlib.sha256(sid.encode()).hexdigest()[:12]}",
            "image_url": item.get("image_url"),
            "reviews": _reviews_for(item["subcategory"], "Back Market"),
            "kg_features": item.get("kg_features"),
        })
    return out


# ---------------------------------------------------------------------------
# Optional live scraping (HTML; may be blocked or JS-rendered)
# ---------------------------------------------------------------------------

def _parse_price(price_str: str) -> Optional[int]:
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d.]", "", price_str.strip())
    try:
        return int(float(cleaned) * 100)
    except ValueError:
        return None


def try_scrape_system76(max_products: int = 10) -> List[Dict[str, Any]]:
    """Try to scrape System76 laptops page. Returns empty if blocked or no HTML products."""
    products: List[Dict[str, Any]] = []
    try:
        url = "https://system76.com/laptops"
        r = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        )
        if r.status_code != 200 or len(r.text) < 5000:
            return products
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select('a[href*="/laptops/"]')[:max_products]:
            href = a.get("href", "")
            if "/laptops" == href or "/laptops" not in href:
                continue
            name_el = a.select_one("h2, h3, .title, .name, [class*='product']")
            name = (name_el.get_text(strip=True) if name_el else "") or a.get_text(strip=True)[:80]
            if not name or len(name) < 3:
                continue
            full_url = href if href.startswith("http") else f"https://system76.com{href}"
            sid = f"system76:scrape:{full_url}"
            pid = "prod-elec-" + hashlib.sha256(sid.encode()).hexdigest()[:16]
            products.append({
                "product_id": pid,
                "name": name,
                "description": f"System76 Linux laptop. Pre-installed Pop!_OS.{SYSTEM76_POLICY}",
                "category": "Electronics",
                "subcategory": "Linux",
                "product_type": "laptop",
                "brand": "System76",
                "price_cents": 149900,
                "available_qty": 15,
                "source": "System76",
                "scraped_from_url": full_url,
                "source_product_id": sid,
                "reviews": _reviews_for("Linux", "System76"),
                "kg_features": {"good_for_linux": True, "good_for_web_dev": True, "battery_life_hours": 10},
            })
        time.sleep(1)
    except Exception as e:
        print(f"  [WARN] System76 scrape: {e}")
    return products


# ---------------------------------------------------------------------------
# Upsert to PostgreSQL (Product, Price, Inventory, kg_features)
# ---------------------------------------------------------------------------

def upsert_products(products: List[Dict[str, Any]]) -> int:
    """Upsert products to PostgreSQL. Uses product_id as key; updates existing."""
    db = SessionLocal()
    added = 0
    try:
        for p in products:
            pid = p["product_id"]
            existing = db.query(Product).filter(Product.product_id == pid).first()
            if existing:
                existing.name = p["name"]
                existing.description = p.get("description", "") or existing.description
                existing.subcategory = p.get("subcategory") or existing.subcategory
                existing.brand = p.get("brand") or existing.brand
                existing.image_url = p.get("image_url") or existing.image_url
                existing.source = p.get("source", "Seed")
                existing.scraped_from_url = p.get("scraped_from_url")
                existing.source_product_id = p.get("source_product_id")
                existing.reviews = p.get("reviews") or existing.reviews
                if p.get("kg_features") is not None:
                    existing.kg_features = p["kg_features"]
                db.add(existing)
            else:
                prod = Product(
                    product_id=pid,
                    name=p["name"],
                    description=p.get("description", ""),
                    category=p.get("category", "Electronics"),
                    subcategory=p.get("subcategory"),
                    brand=p.get("brand"),
                    product_type=p.get("product_type", "laptop"),
                    gpu_vendor=p.get("gpu_vendor"),
                    gpu_model=p.get("gpu_model"),
                    color=p.get("color"),
                    source=p.get("source", "Seed"),
                    scraped_from_url=p.get("scraped_from_url"),
                    source_product_id=p.get("source_product_id"),
                    reviews=p.get("reviews"),
                    image_url=p.get("image_url"),
                    kg_features=p.get("kg_features"),
                )
                db.add(prod)
            db.flush()
            price_row = db.query(Price).filter(Price.product_id == pid).first()
            if price_row:
                price_row.price_cents = p["price_cents"]
                price_row.currency = p.get("currency", "USD")
            else:
                db.add(Price(product_id=pid, price_cents=p["price_cents"], currency=p.get("currency", "USD")))
            inv_row = db.query(Inventory).filter(Inventory.product_id == pid).first()
            if inv_row:
                inv_row.available_qty = p.get("available_qty", 10)
            else:
                db.add(Inventory(product_id=pid, available_qty=p.get("available_qty", 10), reserved_qty=0))
            added += 1
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
    return added


def main():
    print("=" * 70)
    print("MERCHANT LAPTOPS: System76 | Framework | Back Market")
    print("=" * 70)

    all_products = build_seed_products()
    print(f"\n1. Seed data: {len(all_products)} products (System76, Framework, Back Market)")

    # Optional: try live scrape System76
    try_scrape = os.environ.get("SCRAPE_MERCHANT_LAPTOPS", "0") == "1"
    if try_scrape:
        print("\n2. Attempting live scrape (System76)...")
        scraped = try_scrape_system76(max_products=6)
        for s in scraped:
            if not any(p["product_id"] == s["product_id"] for p in all_products):
                all_products.append(s)
        print(f"   Scraped {len(scraped)} additional products")
    else:
        print("\n2. Skipping live scrape (set SCRAPE_MERCHANT_LAPTOPS=1 to enable)")

    print("\n3. Upserting to PostgreSQL...")
    n = upsert_products(all_products)
    print(f"   Upserted {n} products")

    db = SessionLocal()
    try:
        merchant_count = db.query(Product).filter(
            Product.category == "Electronics",
            Product.source.in_(["System76", "Framework", "Back Market"]),
        ).count()
        laptop_total = db.query(Product).filter(
            Product.category == "Electronics",
            Product.product_type.in_(["laptop", "gaming_laptop"]),
        ).count()
    finally:
        db.close()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Merchant laptops (System76/Framework/Back Market): {merchant_count}")
    print(f"Total laptops in DB: {laptop_total}")
    print("\nNext: Rebuild knowledge graph to include these in Neo4j")
    print("  python scripts/build_knowledge_graph.py")
    print("  or  python scripts/build_knowledge_graph_all.py")


if __name__ == "__main__":
    main()