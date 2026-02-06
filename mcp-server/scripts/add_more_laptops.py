#!/usr/bin/env python3
"""
Add more diverse laptops to PostgreSQL, then sync to Neo4j KG.

Adds 250+ laptops with:
- Linux (System76, Dell XPS Developer, Tuxedo, Framework)
- NVIDIA (RTX 3050-4090)
- AMD (Radeon RX 6700M-7900M)
- Dell, Mac, Lenovo, HP, ASUS, Acer, MSI
- 13.3", 14", 15.6", 16", 17.3"
- Prices $599-$3499
- User reviews (count, rating, comments)
- Stock (available_qty)
- Sources: Synthetic, BigCommerce, Shopify, WooCommerce, Generic

Redis: Products are NOT stored in Redis (session cache only). PostgreSQL is authoritative.

Run: python scripts/add_more_laptops.py
Then: python scripts/build_knowledge_graph_all.py
"""

import sys
import json
import random
import hashlib
import uuid
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

from app.database import SessionLocal
from app.models import Product, Price, Inventory


# === SYNTHETIC LAPTOP TEMPLATES ===

BRANDS = ["Dell", "Lenovo", "HP", "ASUS", "Acer", "MSI", "Apple", "Framework", "System76", "Razer", "LG", "Samsung", "Gigabyte", "Alienware"]
SCREEN_SIZES = [13.3, 14.0, 15.6, 16.0, 17.3]
SUBCATEGORIES = ["Gaming", "Work", "Creative", "School", "Ultrabook", "Linux"]
COLORS = ["Black", "Silver", "Space Gray", "Platinum", "Midnight", "Eclipse Gray", "Storm Gray", "White"]

CPUS = [
    ("Intel Core i9-13900H", "Intel", "High-end"),
    ("Intel Core i7-13700H", "Intel", "Upper mid"),
    ("Intel Core i5-13500H", "Intel", "Mid"),
    ("AMD Ryzen 9 7940HS", "AMD", "High-end"),
    ("AMD Ryzen 7 7840HS", "AMD", "Upper mid"),
    ("AMD Ryzen 5 7640HS", "AMD", "Mid"),
    ("Apple M3 Max", "Apple", "High-end"),
    ("Apple M3 Pro", "Apple", "Upper mid"),
    ("Apple M2", "Apple", "Mid"),
]

GPUS = [
    ("NVIDIA RTX 4090", "NVIDIA", 16),
    ("NVIDIA RTX 4080", "NVIDIA", 12),
    ("NVIDIA RTX 4070", "NVIDIA", 8),
    ("NVIDIA RTX 4060", "NVIDIA", 8),
    ("NVIDIA RTX 4050", "NVIDIA", 6),
    ("NVIDIA RTX 3060", "NVIDIA", 6),
    ("AMD Radeon RX 7900M", "AMD", 16),
    ("AMD Radeon RX 6800M", "AMD", 12),
    ("AMD Radeon RX 6700M", "AMD", 10),
    ("Apple M3 Max GPU", "Apple", 0),
    ("Integrated Graphics", None, 0),
]

RAM_OPTS = [8, 16, 32, 64]
STORAGE_OPTS = [256, 512, 1024, 2048]

# Linux-specific brands/models
LINUX_LAPTOPS = [
    {"brand": "System76", "name": "Lemur Pro", "os": "Pop!_OS"},
    {"brand": "System76", "name": "Oryx Pro", "os": "Pop!_OS"},
    {"brand": "Dell", "name": "XPS 13 Developer Edition", "os": "Ubuntu"},
    {"brand": "Dell", "name": "XPS 15 Developer Edition", "os": "Ubuntu"},
    {"brand": "Framework", "name": "Framework Laptop 16", "os": "Ubuntu"},
    {"brand": "Framework", "name": "Framework Laptop 13", "os": "Fedora"},
    {"brand": "Tuxedo", "name": "InfinityBook Pro 14", "os": "Ubuntu"},
    {"brand": "Lenovo", "name": "ThinkPad X1 Carbon (Linux)", "os": "Fedora"},
    {"brand": "HP", "name": "Dev One", "os": "Pop!_OS"},
]

# Review templates by use case
REVIEW_TEMPLATES = {
    "gaming": [
        {"rating": 5, "comment": "Runs AAA games at max settings. RTX performance is incredible!", "verified": True},
        {"rating": 4, "comment": "Great for gaming but gets warm. Cooling pad recommended.", "verified": True},
        {"rating": 5, "comment": "Display and keyboard are premium. Best gaming laptop I've owned.", "verified": True},
    ],
    "work": [
        {"rating": 5, "comment": "Perfect for remote work. Battery lasts all day.", "verified": True},
        {"rating": 4, "comment": "Smooth multitasking. Keyboard comfortable for long sessions.", "verified": True},
    ],
    "creative": [
        {"rating": 5, "comment": "Photo/video editing is buttery smooth. Color accuracy excellent.", "verified": True},
        {"rating": 4, "comment": "Handles Adobe Suite without breaking a sweat.", "verified": True},
    ],
    "school": [
        {"rating": 5, "comment": "Perfect for college. Light, portable, battery lasts through classes.", "verified": True},
        {"rating": 4, "comment": "Great for online learning and assignments.", "verified": True},
    ],
    "linux": [
        {"rating": 5, "comment": "Out-of-box Linux support. Everything works perfectly.", "verified": True},
        {"rating": 4, "comment": "Great developer machine. Driver support is solid.", "verified": True},
    ],
}


def generate_reviews(subcategory: str, count: Optional[int] = None) -> str:
    """Generate realistic reviews JSON with review_count and average_rating."""
    key = "linux" if subcategory == "Linux" else subcategory.lower()
    templates = REVIEW_TEMPLATES.get(key, REVIEW_TEMPLATES["work"])
    num = count or random.randint(3, 12)
    reviews_list = [random.choice(templates) for _ in range(min(num, 10))]
    avg = sum(r["rating"] for r in reviews_list) / len(reviews_list)
    return json.dumps({
        "reviews": reviews_list,
        "review_count": num,
        "average_rating": round(avg, 1),
    })


def generate_synthetic_laptops(count: int = 250) -> List[Dict[str, Any]]:
    """Generate diverse synthetic laptops."""
    laptops = []
    seen_names = set()

    for i in range(count):
        # 15% Linux laptops
        is_linux = random.random() < 0.15
        if is_linux:
            template = random.choice(LINUX_LAPTOPS)
            brand = template["brand"]
            base_name = template["name"]
            subcategory = "Linux"
            os_note = f" Pre-installed {template['os']}."
        else:
            brand = random.choice(BRANDS)
            subcategory = random.choice(SUBCATEGORIES)
            base_name = None
            os_note = ""

        screen = random.choice(SCREEN_SIZES)
        cpu, cpu_mfr, _ = random.choice(CPUS)
        ram = random.choice(RAM_OPTS)
        storage = random.choice(STORAGE_OPTS)

        # GPU based on subcategory - MUST match CPU (Apple GPU only with Apple CPU)
        if "Apple" in cpu or cpu_mfr == "Apple":
            gpu, gpu_vendor, vram = ("Apple M3 Max GPU", "Apple", 0) if "M3 Max" in cpu else ("Integrated Graphics", "Apple", 0)
        elif subcategory == "Gaming":
            gpu, gpu_vendor, vram = random.choice([g for g in GPUS if g[1] in ("NVIDIA", "AMD")])
        elif subcategory == "Linux":
            gpu, gpu_vendor, vram = random.choice([g for g in GPUS if g[1] in ("NVIDIA", "AMD", None)])
            if gpu_vendor is None:
                gpu_vendor = "Intel"
        else:
            # Non-Apple: only NVIDIA, AMD, or Intel integrated (never Apple GPU)
            gpu, gpu_vendor, vram = random.choice([g for g in GPUS if g[1] in ("NVIDIA", "AMD", None)])
            if gpu_vendor is None:
                gpu_vendor = "Intel"

        # Build name
        if base_name:
            name = f"{brand} {base_name} {screen}\""
        elif brand == "Apple":
            name = f"MacBook {'Pro' if subcategory == 'Creative' else 'Air'} {screen}\" {cpu.split()[1]}"
        else:
            suffix = random.choice(["", " Pro", " Plus", " Elite"])
            name = f"{brand} {subcategory} Laptop {screen}\"{suffix}"

        # Avoid duplicates
        if name in seen_names:
            name = f"{name} ({random.randint(1, 99)})"
        seen_names.add(name)

        # Price (reasonable)
        base = 600
        if "i9" in cpu or "Ryzen 9" in cpu or "M3 Max" in cpu:
            base += 600
        elif "i7" in cpu or "Ryzen 7" in cpu or "M3 Pro" in cpu:
            base += 350
        if gpu_vendor == "NVIDIA" and "40" in gpu:
            base += 400
        elif gpu_vendor == "AMD" and "79" in gpu:
            base += 350
        if ram >= 32:
            base += 150
        if storage >= 1024:
            base += 120
        price_cents = int((base + random.randint(-80, 200)) * 100)

        desc = f"{brand} {subcategory} laptop with {screen}\" display, {cpu} processor, {ram}GB RAM, {storage}GB SSD, and {gpu}.{os_note} Perfect for {subcategory.lower()} use."

        laptops.append({
            "product_id": f"prod-elec-{uuid.uuid4().hex[:20]}",
            "name": name,
            "description": desc,
            "category": "Electronics",
            "subcategory": subcategory,
            "product_type": "gaming_laptop" if subcategory == "Gaming" else "laptop",
            "brand": brand,
            "price_cents": price_cents,
            "available_qty": random.randint(5, 120),
            "color": random.choice(COLORS),
            "source": "Synthetic",
            "scraped_from_url": None,
            "gpu_vendor": gpu_vendor,
            "gpu_model": gpu if gpu_vendor else None,
            "reviews": generate_reviews(subcategory),
            "image_url": "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=800",
        })
    return laptops


def scrape_bigcommerce_laptops() -> List[Dict[str, Any]]:
    """Scrape laptops from BigCommerce mc-demo."""
    urls = [
        "https://mc-demo.mybigcommerce.com/categories/Laptops/",
        "https://mc-demo.mybigcommerce.com/categories/Laptops/MAC/",
    ]
    products = []
    try:
        from scripts.product_scraper import BigCommerceScraper
        scraper = BigCommerceScraper(rate_limit_delay=1.0)
        for url in urls:
            scraped = scraper.scrape(url, max_products=15)
            for s in scraped:
                if "laptop" in (s.name or "").lower() or "mac" in (s.name or "").lower():
                    h = hashlib.sha256(f"BigCommerce|{s.name}|{s.source_url}".encode()).hexdigest()[:20]
                    products.append({
                        "product_id": f"prod-elec-{h}",
                        "name": s.name,
                        "description": s.description or s.name,
                        "category": "Electronics",
                        "subcategory": "General",
                        "product_type": "laptop",
                        "brand": None,
                        "price_cents": s.price_cents,
                        "available_qty": random.randint(10, 50),
                        "source": "BigCommerce",
                        "scraped_from_url": s.source_url,
                        "source_product_id": f"bigcommerce:{s.source_url}",
                        "reviews": generate_reviews("work"),
                        "image_url": getattr(s, "image_url", None),
                    })
    except Exception as e:
        print(f"  [WARN] BigCommerce scrape: {e}")
    return products


def scrape_shopify_laptops() -> List[Dict[str, Any]]:
    """Fetch laptops from Shopify stores (comptechdirect, TechMarket demos, etc.)."""
    stores = [
        {"domain": "comptechdirect.com", "name": "CompTech Direct"},
        {"domain": "techmarket-demo1.myshopify.com", "name": "TechMarket Demo 1"},
        {"domain": "techmarket-demo4.myshopify.com", "name": "TechMarket Demo 4"},
    ]
    products = []
    for store in stores:
        try:
            url = f"https://{store['domain']}/products.json?limit=50"
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            data = r.json()
            for p in data.get("products", [])[:25]:
                pt = (p.get("product_type") or "").lower()
                title = (p.get("title") or "").lower()
                laptop_keywords = ["laptop", "notebook", "thinkpad", "ultrabook", "zenbook", "macbook", "mac book", "computer"]
                if any(k in pt or k in title for k in laptop_keywords):
                    v = p.get("variants", [{}])[0]
                    price_cents = int(float(v.get("price", 0) or 0) * 100)
                    if price_cents <= 0:
                        continue
                    img = (p.get("images") or [{}])[0].get("src", "") if p.get("images") else ""
                    sid = f"shopify:{store['domain']}:{p.get('id')}"
                    products.append({
                        "product_id": f"prod-elec-{hashlib.sha256(sid.encode()).hexdigest()[:20]}",
                        "name": p.get("title", "Laptop"),
                        "description": (p.get("body_html") or "")[:500] or p.get("title"),
                        "category": "Electronics",
                        "subcategory": "Work",
                        "product_type": "laptop",
                        "brand": next((v.get("title") for v in p.get("variants", []) if v.get("title") != "Default Title"), None) or "Generic",
                        "price_cents": price_cents,
                        "available_qty": v.get("inventory_quantity") or random.randint(5, 30),
                        "source": "Shopify",
                        "scraped_from_url": f"https://{store['domain']}/products/{p.get('handle')}",
                        "source_product_id": sid,
                        "reviews": generate_reviews("work"),
                        "image_url": img,
                    })
        except Exception as e:
            print(f"  [WARN] Shopify {store['domain']}: {e}")
    return products


def scrape_woocommerce_laptops() -> List[Dict[str, Any]]:
    """Scrape laptops from WooCommerce demo stores (pluginrepublic, electronics-shop, etc.)."""
    urls = [
        "https://pluginrepublic.dev/product-extras/product/macbook/",
        "https://pluginrepublic.dev/product-extras/product/build-your-own-computer/",
        "https://demo.glthemes.com/electronics-shop/",
        "https://qodeinteractive.com/qode-product-extra-options-for-woocommerce/product/laptop-computer/",
    ]
    products = []
    try:
        from scripts.product_scraper import GenericEcommerceScraper
        scraper = GenericEcommerceScraper(rate_limit_delay=1.5)
        for url in urls:
            scraped = scraper.scrape(url, max_products=5)
            for s in scraped:
                name_lower = (s.name or "").lower()
                if any(k in name_lower for k in ["laptop", "macbook", "computer", "acer", "asus", "dell"]):
                    h = hashlib.sha256(f"WooCommerce|{s.name}|{s.source_url}".encode()).hexdigest()[:20]
                    products.append({
                        "product_id": f"prod-elec-{h}",
                        "name": s.name,
                        "description": s.description or s.name,
                        "category": "Electronics",
                        "subcategory": "General",
                        "product_type": "laptop",
                        "brand": None,
                        "price_cents": s.price_cents,
                        "available_qty": random.randint(8, 40),
                        "source": "WooCommerce",
                        "scraped_from_url": s.source_url,
                        "source_product_id": f"woocommerce:{s.source_url}",
                        "reviews": generate_reviews("work"),
                        "image_url": getattr(s, "image_url", None),
                    })
    except Exception as e:
        print(f"  [WARN] WooCommerce scrape: {e}")
    return products


def save_to_postgres(products: List[Dict[str, Any]]) -> int:
    """Save products to PostgreSQL with Price and Inventory."""
    db = SessionLocal()
    added = 0
    try:
        for p in products:
            # Only skip if we have a scraped product with same source_product_id
            if p.get("source_product_id"):
                existing = db.query(Product).filter(Product.source_product_id == p["source_product_id"]).first()
            else:
                # Synthetic: skip if same name+source already exists (avoid duplicates on re-run)
                existing = db.query(Product).filter(
                    Product.name == p["name"],
                    Product.source == p.get("source", "Synthetic")
                ).first()
            if existing:
                continue

            prod = Product(
                product_id=p["product_id"],
                name=p["name"],
                description=p.get("description", ""),
                category=p["category"],
                subcategory=p.get("subcategory"),
                brand=p.get("brand"),
                product_type=p.get("product_type", "laptop"),
                gpu_vendor=p.get("gpu_vendor"),
                gpu_model=p.get("gpu_model"),
                color=p.get("color"),
                source=p.get("source", "Synthetic"),
                scraped_from_url=p.get("scraped_from_url"),
                source_product_id=p.get("source_product_id"),
                reviews=p.get("reviews"),
                image_url=p.get("image_url"),
            )
            db.add(prod)
            db.flush()

            db.add(Price(product_id=prod.product_id, price_cents=p["price_cents"], currency="USD"))
            db.add(Inventory(product_id=prod.product_id, available_qty=p.get("available_qty", 25), reserved_qty=0))
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
    print("ADD MORE LAPTOPS - PostgreSQL + Neo4j KG")
    print("=" * 70)

    all_products = []

    # 1. Synthetic laptops
    print("\n1. Generating 250 synthetic laptops...")
    synthetic = generate_synthetic_laptops(250)
    all_products.extend(synthetic)
    print(f"   Generated {len(synthetic)} synthetic laptops")

    # 2. BigCommerce scrape
    print("\n2. Scraping BigCommerce mc-demo...")
    bc = scrape_bigcommerce_laptops()
    all_products.extend(bc)
    print(f"   Scraped {len(bc)} from BigCommerce")

    # 3. Shopify scrape
    print("\n3. Fetching Shopify laptop stores...")
    shopify = scrape_shopify_laptops()
    all_products.extend(shopify)
    print(f"   Fetched {len(shopify)} from Shopify")

    # 4. WooCommerce scrape
    print("\n4. Scraping WooCommerce laptop demos...")
    wc = scrape_woocommerce_laptops()
    all_products.extend(wc)
    print(f"   Scraped {len(wc)} from WooCommerce")

    # 5. Save to PostgreSQL
    print("\n5. Saving to PostgreSQL...")
    added = save_to_postgres(all_products)
    print(f"   Added {added} new laptops")

    # 6. Summary
    db = SessionLocal()
    laptop_count = db.query(Product).filter(
        Product.category == "Electronics",
        Product.product_type.in_(["laptop", "gaming_laptop"])
    ).count()
    db.close()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total laptops in DB: {laptop_count}")
    print("\nNext step: Rebuild Neo4j knowledge graph")
    print("  cd mcp-server && python scripts/build_knowledge_graph_all.py")
    print("\nNote: Redis stores session data only. Products come from PostgreSQL.")


if __name__ == "__main__":
    main()
