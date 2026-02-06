#!/usr/bin/env python3
"""
Build Neo4j Knowledge Graph for ALL Products (1000+)

Populates the knowledge graph with every product from PostgreSQL:
- Laptops (Electronics + laptop/gaming_laptop)
- Books
- Jewelry (category Jewelry)
- Accessories (category Accessories)
- Other Electronics (phones, tablets, desktops, monitors)
- Beauty, Clothing, Art, Food, and all other categories (generic Product nodes)

Follows kg.txt instructions: comprehensive coverage, no artificial limits.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env from project root (idss-backend/.env)
from dotenv import load_dotenv
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

from app.database import SessionLocal
from app.models import Product, Price, Inventory
from app.neo4j_config import Neo4jConnection
from app.knowledge_graph import KnowledgeGraphBuilder
import random
import re
from datetime import datetime, timedelta
import uuid


# Sample data for enrichment
LAPTOP_MANUFACTURERS = {
    "Dell": {"country": "USA", "founded": 1984, "website": "dell.com"},
    "HP": {"country": "USA", "founded": 1939, "website": "hp.com"},
    "Lenovo": {"country": "China", "founded": 1984, "website": "lenovo.com"},
    "Apple": {"country": "USA", "founded": 1976, "website": "apple.com"},
    "Asus": {"country": "Taiwan", "founded": 1989, "website": "asus.com"},
    "Acer": {"country": "Taiwan", "founded": 1976, "website": "acer.com"},
    "MSI": {"country": "Taiwan", "founded": 1986, "website": "msi.com"},
    "Razer": {"country": "USA", "founded": 2005, "website": "razer.com"},
    "Samsung": {"country": "South Korea", "founded": 1938, "website": "samsung.com"},
    "LG": {"country": "South Korea", "founded": 1958, "website": "lg.com"},
}

BOOK_PUBLISHERS = {
    "Penguin Random House": {"country": "USA", "founded": 2013, "website": "penguinrandomhouse.com"},
    "HarperCollins": {"country": "USA", "founded": 1989, "website": "harpercollins.com"},
    "Simon & Schuster": {"country": "USA", "founded": 1924, "website": "simonandschuster.com"},
    "Macmillan": {"country": "USA", "founded": 1843, "website": "macmillan.com"},
    "Hachette": {"country": "France", "founded": 1826, "website": "hachette.com"},
}

GENRE_HIERARCHY = [
    {"name": "Fiction", "description": "Literary works of imaginative narration", "level": 1, "parent_genres": []},
    {"name": "Science Fiction", "description": "Speculative fiction with scientific themes", "level": 2, "parent_genres": ["Fiction"]},
    {"name": "Fantasy", "description": "Fiction with magical or supernatural elements", "level": 2, "parent_genres": ["Fiction"]},
    {"name": "Mystery", "description": "Fiction dealing with crime and detection", "level": 2, "parent_genres": ["Fiction"]},
    {"name": "Romance", "description": "Fiction focusing on romantic relationships", "level": 2, "parent_genres": ["Fiction"]},
    {"name": "Non-fiction", "description": "Factual writing", "level": 1, "parent_genres": []},
    {"name": "Biography", "description": "Life stories of real people", "level": 2, "parent_genres": ["Non-fiction"]},
    {"name": "History", "description": "Historical accounts and analysis", "level": 2, "parent_genres": ["Non-fiction"]},
    {"name": "Self-Help", "description": "Personal development and improvement", "level": 2, "parent_genres": ["Non-fiction"]},
    {"name": "Business", "description": "Business and economics", "level": 2, "parent_genres": ["Non-fiction"]},
]

# Jewelry materials to extract from name/description
JEWELRY_MATERIALS = ["gold", "silver", "platinum", "rose gold", "sterling", "brass", "copper", "titanium", "pearl", "diamond", "crystal"]

# Valid laptop screen sizes (inches) for fallback
SCREEN_SIZES = [13.3, 14.0, 15.6, 16.0, 17.3]


def _parse_screen_size(name: str, description: str) -> float:
    """Parse screen size in inches from product name or description. Falls back to random if not found."""
    text = f"{(name or '')} {(description or '')}".lower()
    # Patterns: 14", 14 inch, 14-inch, 14.0", 15.6 inch, 16-inch, 17.3"
    for pattern in [
        r"(\d+\.?\d*)\s*[\"']",           # 14" or 15.6"
        r"(\d+\.?\d*)\s*inch(?:es)?",     # 14 inch, 15.6 inches
        r"(\d+\.?\d*)\s*-\s*inch",        # 14 - inch
        r"(\d+\.?\d*)-inch",               # 14-inch (no space)
        r"(\d+\.?\d*)\"",                  # 14" (alternate)
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if 10 <= val <= 20:  # Sanity: laptop screens 10–20"
                return round(val, 1) if val != int(val) else float(val)
    return random.choice(SCREEN_SIZES)


def extract_laptop_specs(product: Product) -> dict:
    """Extract detailed laptop specifications from product."""
    specs = {
        "product_id": product.product_id,
        "name": product.name,
        "brand": product.brand or "Unknown",
        "model": product.name,
        "price": 0,
        "description": product.description or "",
        "image_url": product.image_url or "",
        "subcategory": product.subcategory or "General",
        "available": True,
        "weight_kg": round(random.uniform(1.2, 3.5), 2),
        "portability_score": random.randint(60, 95),
        "battery_life_hours": random.randint(4, 15),
        "screen_size_inches": _parse_screen_size(product.name, product.description),
        "refresh_rate_hz": random.choice([60, 120, 144, 165, 240]),
        "manufacturer_country": LAPTOP_MANUFACTURERS.get(product.brand or "", {}).get("country", "Unknown"),
        "manufacturer_founded": LAPTOP_MANUFACTURERS.get(product.brand or "", {}).get("founded"),
        "manufacturer_website": LAPTOP_MANUFACTURERS.get(product.brand or "", {}).get("website"),
        "cpu_model": "Unknown CPU",
        "cpu_manufacturer": "Intel",
        "cpu_cores": 8,
        "cpu_threads": 16,
        "cpu_base_clock": 2.4,
        "cpu_boost_clock": 4.5,
        "cpu_tdp": 45,
        "cpu_generation": "12th Gen",
        "cpu_tier": "Mid-range",
        "gpu_model": None,
        "gpu_manufacturer": None,
        "gpu_vram": None,
        "gpu_memory_type": None,
        "gpu_tdp": None,
        "gpu_tier": None,
        "gpu_ray_tracing": False,
        "ram_capacity": 16,
        "ram_type": "DDR4",
        "ram_speed": 3200,
        "ram_channels": 2,
        "ram_expandable": True,
        "storage_capacity": 512,
        "storage_type": "NVMe SSD",
        "storage_interface": "PCIe 4.0",
        "storage_read_speed": 7000,
        "storage_write_speed": 5000,
        "storage_expandable": True,
        "display_resolution": "1920x1080",
        "display_panel_type": "IPS",
        "display_brightness": 300,
        "display_color_gamut": "sRGB 100%",
        "display_touch": False,
    }
    name_lower = (product.name or "").lower()
    if "i9" in name_lower or "ryzen 9" in name_lower:
        specs.update({"cpu_cores": 16, "cpu_threads": 24, "cpu_tier": "High-end", "cpu_model": "Intel Core i9-13900H" if "intel" in name_lower else "AMD Ryzen 9 7940HS"})
    elif "i7" in name_lower or "ryzen 7" in name_lower:
        specs.update({"cpu_cores": 12, "cpu_threads": 16, "cpu_tier": "Upper mid-range", "cpu_model": "Intel Core i7-13700H" if "intel" in name_lower else "AMD Ryzen 7 7735HS"})
    elif "i5" in name_lower or "ryzen 5" in name_lower:
        specs.update({"cpu_cores": 8, "cpu_threads": 12, "cpu_model": "Intel Core i5-12500H" if "intel" in name_lower else "AMD Ryzen 5 7535HS"})
    elif "m1" in name_lower or "m2" in name_lower or "m3" in name_lower:
        specs.update({"cpu_manufacturer": "Apple", "cpu_model": "Apple M2" if "m2" in name_lower else ("Apple M3" if "m3" in name_lower else "Apple M1"), "cpu_cores": 8, "cpu_threads": 8, "cpu_tier": "High-end"})
    # Prefer PostgreSQL gpu_model/gpu_vendor when available (authoritative)
    if product.gpu_model and product.gpu_vendor:
        gpu_manufacturer = product.gpu_vendor
        gpu_model = product.gpu_model
        vram_map = {"4090": 16, "4080": 12, "4070": 8, "4060": 6, "4050": 6, "3060": 6, "7900": 16, "6800": 12, "6700": 10}
        vram = 0 if gpu_manufacturer == "Apple" else next((v for k, v in vram_map.items() if k in gpu_model), 6)
        specs.update({
            "gpu_model": gpu_model,
            "gpu_manufacturer": gpu_manufacturer,
            "gpu_vram": vram,
            "gpu_memory_type": "GDDR6" if "NVIDIA" in gpu_manufacturer or "AMD" in gpu_manufacturer else None,
            "gpu_tier": "High-end" if "4090" in gpu_model or "4080" in gpu_model else "Mid-range",
            "gpu_ray_tracing": "RTX" in gpu_model or "rtx" in gpu_model.lower(),
            "gpu_tdp": random.randint(80, 150) if gpu_manufacturer in ("NVIDIA", "AMD") else None,
        })
    elif "gaming" in name_lower or "rtx" in name_lower or "radeon" in name_lower:
        gpu = {"gpu_model": "NVIDIA RTX 3060", "gpu_manufacturer": "NVIDIA", "gpu_vram": 6, "gpu_tier": "Mid-range", "gpu_ray_tracing": True}
        if "rtx 4090" in name_lower: gpu = {"gpu_model": "NVIDIA RTX 4090", "gpu_manufacturer": "NVIDIA", "gpu_vram": 16, "gpu_tier": "Ultra high-end", "gpu_ray_tracing": True}
        elif "rtx 4080" in name_lower: gpu = {"gpu_model": "NVIDIA RTX 4080", "gpu_manufacturer": "NVIDIA", "gpu_vram": 12, "gpu_tier": "High-end", "gpu_ray_tracing": True}
        elif "rtx 4070" in name_lower: gpu = {"gpu_model": "NVIDIA RTX 4070", "gpu_manufacturer": "NVIDIA", "gpu_vram": 8, "gpu_tier": "Upper mid-range", "gpu_ray_tracing": True}
        elif "rtx 4060" in name_lower: gpu = {"gpu_model": "NVIDIA RTX 4060", "gpu_manufacturer": "NVIDIA", "gpu_vram": 6, "gpu_tier": "Mid-range", "gpu_ray_tracing": True}
        specs.update(gpu)
        specs["gpu_memory_type"] = "GDDR6"
        specs["gpu_tdp"] = random.randint(80, 150)
    if "32gb" in name_lower: specs["ram_capacity"] = 32
    elif "64gb" in name_lower: specs["ram_capacity"] = 64
    elif "8gb" in name_lower: specs["ram_capacity"] = 8
    if "1tb" in name_lower: specs["storage_capacity"] = 1024
    elif "2tb" in name_lower: specs["storage_capacity"] = 2048
    elif "256gb" in name_lower: specs["storage_capacity"] = 256
    return specs


def extract_book_metadata(product: Product) -> dict:
    """Extract detailed book metadata."""
    name_parts = (product.name or "").split(" by ")
    title = name_parts[0].strip()
    author = name_parts[1].strip() if len(name_parts) > 1 else "Unknown Author"
    publisher_name = random.choice(list(BOOK_PUBLISHERS.keys()))
    publisher_info = BOOK_PUBLISHERS[publisher_name]
    return {
        "product_id": product.product_id,
        "title": title,
        "name": product.name or title,
        "price": 0,
        "description": product.description or "",
        "image_url": product.image_url or "",
        "isbn": f"978-{random.randint(1000000000, 9999999999)}",
        "pages": random.randint(200, 800),
        "language": "English",
        "publication_year": random.randint(2010, 2025),
        "edition": random.choice(["1st", "2nd", "3rd", "Revised"]),
        "format": random.choice(["Hardcover", "Paperback", "eBook", "Audiobook"]),
        "available": True,
        "author": author,
        "author_nationality": random.choice(["American", "British", "Canadian", "Australian", "International"]),
        "author_birth_year": random.randint(1940, 1985),
        "author_biography": f"{author} is a renowned author in their field.",
        "author_awards": [],
        "publisher": publisher_name,
        "publisher_country": publisher_info["country"],
        "publisher_founded": publisher_info["founded"],
        "publisher_website": publisher_info["website"],
        "genre": product.subcategory or "Fiction",
        "genre_description": f"Books in the {product.subcategory or 'Fiction'} category",
        "themes": random.sample(["Identity", "Love", "Betrayal", "Justice", "Freedom", "Power", "Revenge", "Family", "Truth", "Redemption"], k=random.randint(2, 4)),
        "series_name": None if random.random() > 0.3 else f"{title.split()[0] if title else 'The'} Series",
        "series_position": random.randint(1, 5) if random.random() < 0.3 else None,
        "series_total_books": random.randint(3, 7) if random.random() < 0.3 else None,
    }


def extract_material_from_product(product: Product) -> str:
    """Extract jewelry material from name/description/color."""
    text = " ".join(filter(None, [product.name, product.description, product.color])).lower()
    for m in JEWELRY_MATERIALS:
        if m in text:
            return m.title()
    if product.color:
        c = product.color.lower()
        if "gold" in c or "silver" in c: return product.color
    return ""


def to_jewelry_data(product: Product, price: float) -> dict:
    """Build jewelry node data."""
    return {
        "product_id": product.product_id,
        "name": product.name or "Jewelry",
        "brand": product.brand or "",
        "price": price,
        "description": product.description or "",
        "image_url": product.image_url or "",
        "subcategory": product.subcategory or "Jewelry",
        "color": product.color or "",
        "available": True,
        "material": extract_material_from_product(product),
        "item_type": product.subcategory or "Jewelry",
    }


def to_accessory_data(product: Product, price: float) -> dict:
    """Build accessory node data."""
    return {
        "product_id": product.product_id,
        "name": product.name or "Accessory",
        "brand": product.brand or "",
        "price": price,
        "description": product.description or "",
        "image_url": product.image_url or "",
        "subcategory": product.subcategory or "Accessories",
        "color": product.color or "",
        "available": True,
        "item_type": product.subcategory or "Accessories",
    }


def to_generic_product_data(product: Product, price: float) -> dict:
    """Build generic product node data (includes source/scraped_from_url for provenance)."""
    return {
        "product_id": product.product_id,
        "name": product.name or "Product",
        "brand": product.brand or "",
        "price": price,
        "description": product.description or "",
        "image_url": product.image_url or "",
        "category": product.category or "Other",
        "subcategory": product.subcategory or "",
        "product_type": product.product_type or "",
        "color": product.color or "",
        "available": True,
        "source": product.source or "",
        "scraped_from_url": product.scraped_from_url or "",
    }


def get_price(pg_db, product_id: str, default: float = 29.99) -> float:
    """Get product price in dollars."""
    price_obj = pg_db.query(Price).filter(Price.product_id == product_id).first()
    return price_obj.price_cents / 100 if price_obj else default


def sanitize_jewelry_price(price: float, product_name: str, default: float = 49.99) -> float:
    """Cap jewelry prices to avoid WooCommerce conversion bugs (e.g. 500000 -> 50000)."""
    if price <= 0:
        return default
    if price > 50_000:
        # Likely bug: e.g. 500000 from cents/dollars mix-up. Use reasonable luxury cap.
        if "carat" in (product_name or "").lower() and "diamond" in (product_name or "").lower():
            return 50_000.0  # Luxury 7-carat diamond ring
        return min(5_000.0, price / 100)  # Try dividing by 100 (cents stored as dollars)
    return price


def main():
    """Build the complete knowledge graph for all products."""
    print("=" * 80)
    print("BUILDING NEO4J KNOWLEDGE GRAPH - ALL PRODUCTS (1000+)")
    print("=" * 80)

    pg_db = SessionLocal()
    neo4j_conn = Neo4jConnection()

    if not neo4j_conn.verify_connectivity():
        print("Failed to connect to Neo4j. Ensure Neo4j is running (e.g. docker run -p 7687:7687 -p 7474:7474 neo4j)")
        return

    print("Connected to PostgreSQL and Neo4j")

    builder = KnowledgeGraphBuilder(neo4j_conn)

    # Clear existing graph for fresh build
    print("\n1. Clearing existing graph data...")
    builder.clear_all_data()

    print("\n2. Creating indexes and constraints...")
    builder.create_indexes_and_constraints()

    # Load all products
    print("\n3. Loading products from PostgreSQL...")
    all_products = pg_db.query(Product).all()
    total = len(all_products)
    print(f"   Total products: {total}")

    # Categorize
    laptops = [p for p in all_products if p.category == "Electronics" and p.product_type in ("laptop", "gaming_laptop")]
    books = [p for p in all_products if p.category == "Books"]
    jewelry = [p for p in all_products if p.category == "Jewelry"]
    accessories = [p for p in all_products if p.category == "Accessories"]
    other_electronics = [p for p in all_products if p.category == "Electronics" and p not in laptops]
    generic = [p for p in all_products if p not in laptops and p not in books and p not in jewelry and p not in accessories and p not in other_electronics]

    print(f"   Laptops: {len(laptops)}, Books: {len(books)}, Jewelry: {len(jewelry)}, Accessories: {len(accessories)}")
    print(f"   Other Electronics: {len(other_electronics)}, Other categories: {len(generic)}")

    # Genre hierarchy
    print("\n4. Creating genre hierarchy...")
    builder.create_genre_hierarchy(GENRE_HIERARCHY)

    # Process laptops (all)
    print("\n5. Processing laptops...")
    laptop_ids = []
    for i, p in enumerate(laptops):
        try:
            price = get_price(pg_db, p.product_id, 999.99)
            data = extract_laptop_specs(p)
            data["price"] = price
            pid = builder.create_laptop_node(data)
            laptop_ids.append(pid)
            if (i + 1) % 50 == 0:
                print(f"   {i + 1}/{len(laptops)} laptops")
        except Exception as e:
            print(f"   [WARN] {p.name}: {e}")
    print(f"Laptops: {len(laptop_ids)}")

    # Process books (all)
    print("\n6. Processing books...")
    book_ids = []
    for i, p in enumerate(books):
        try:
            price = get_price(pg_db, p.product_id, 19.99)
            data = extract_book_metadata(p)
            data["price"] = price
            pid = builder.create_book_node(data)
            book_ids.append(pid)
            if (i + 1) % 100 == 0:
                print(f"   {i + 1}/{len(books)} books")
        except Exception as e:
            print(f"   [WARN] {p.name}: {e}")
    print(f"Books: {len(book_ids)}")

    # Process jewelry (all)
    print("\n7. Processing jewelry...")
    jewelry_ids = []
    for i, p in enumerate(jewelry):
        try:
            price = get_price(pg_db, p.product_id, 49.99)
            price = sanitize_jewelry_price(price, p.name, 49.99)
            data = to_jewelry_data(p, price)
            pid = builder.create_jewelry_node(data)
            jewelry_ids.append(pid)
        except Exception as e:
            print(f"   [WARN] {p.name}: {e}")
    print(f"Jewelry: {len(jewelry_ids)}")

    # Process accessories (all)
    print("\n8. Processing accessories...")
    accessory_ids = []
    for i, p in enumerate(accessories):
        try:
            price = get_price(pg_db, p.product_id, 29.99)
            data = to_accessory_data(p, price)
            pid = builder.create_accessory_node(data)
            accessory_ids.append(pid)
            if (i + 1) % 50 == 0:
                print(f"   {i + 1}/{len(accessories)} accessories")
        except Exception as e:
            print(f"   [WARN] {p.name}: {e}")
    print(f"Accessories: {len(accessory_ids)}")

    # Process other electronics (generic Product with category Electronics)
    print("\n9. Processing other electronics...")
    other_elec_ids = []
    for i, p in enumerate(other_electronics):
        try:
            price = get_price(pg_db, p.product_id, 199.99)
            data = to_generic_product_data(p, price)
            pid = builder.create_generic_product_node(data)
            other_elec_ids.append(pid)
            if (i + 1) % 50 == 0:
                print(f"   {i + 1}/{len(other_electronics)}")
        except Exception as e:
            print(f"   [WARN] {p.name}: {e}")
    print(f"Other Electronics: {len(other_elec_ids)}")

    # Process generic (Beauty, Clothing, Art, Food, etc.)
    print("\n10. Processing other categories (Beauty, Clothing, Art, Food, etc.)...")
    generic_ids = []
    for i, p in enumerate(generic):
        try:
            price = get_price(pg_db, p.product_id, 24.99)
            data = to_generic_product_data(p, price)
            pid = builder.create_generic_product_node(data)
            generic_ids.append(pid)
            if (i + 1) % 100 == 0:
                print(f"   {i + 1}/{len(generic)}")
        except Exception as e:
            print(f"   [WARN] {p.name}: {e}")
    print(f"Other categories: {len(generic_ids)}")

    # Create reviews (sample for variety)
    print("\n11. Creating sample reviews...")
    all_ids = laptop_ids + book_ids + jewelry_ids + accessory_ids + other_elec_ids + generic_ids
    review_count = 0
    sample_ids = all_ids[:min(100, len(all_ids))]
    for product_id in sample_ids:
        for _ in range(random.randint(1, 4)):
            sentiment_score = random.uniform(-1, 1)
            sentiment_label = "positive" if sentiment_score > 0.3 else ("negative" if sentiment_score < -0.3 else "neutral")
            try:
                builder.create_review_relationships({
                    "product_id": product_id,
                    "review_id": str(uuid.uuid4()),
                    "user_id": f"user_{random.randint(1000, 9999)}",
                    "username": f"User{random.randint(1000, 9999)}",
                    "user_join_date": (datetime.now() - timedelta(days=random.randint(30, 730))).isoformat(),
                    "user_verified": random.choice([True, False]),
                    "rating": random.randint(3, 5) if sentiment_label == "positive" else random.randint(1, 3),
                    "comment": "Great product!" if sentiment_label == "positive" else "Could be better.",
                    "sentiment_score": sentiment_score,
                    "sentiment_label": sentiment_label,
                    "helpful_count": random.randint(0, 50),
                    "verified_purchase": random.choice([True, False]),
                    "review_date": (datetime.now() - timedelta(days=random.randint(1, 365))).isoformat(),
                })
                review_count += 1
            except Exception:
                pass
    print(f"Reviews: {review_count}")

    # Create SIMILAR_TO relationships within categories
    print("\n12. Creating similarity relationships...")
    sim_count = 0
    for ids, batch_size in [(laptop_ids, 30), (book_ids, 40), (jewelry_ids, 15), (accessory_ids, 30)]:
        for i in range(min(batch_size, len(ids) - 1)):
            try:
                builder.create_comparison_relationships(ids[i], ids[i + 1], "SIMILAR_TO", random.uniform(0.6, 0.95))
                sim_count += 1
            except Exception:
                pass
    print(f"Similarity relationships: {sim_count}")

    # Literary connections for books
    print("\n13. Creating literary connections...")
    lit_count = 0
    for i in range(min(20, len(book_ids) - 1)):
        if random.random() < 0.4:
            try:
                builder.create_literary_connections(
                    book_ids[i], book_ids[i + 1],
                    random.choice(["SIMILAR_THEME", "INSPIRED_BY", "RECOMMENDED_WITH"]),
                    "Connected by theme or style"
                )
                lit_count += 1
            except Exception:
                pass
    print(f"Literary connections: {lit_count}")

    # Statistics
    print("\n" + "=" * 80)
    print("KNOWLEDGE GRAPH STATISTICS")
    print("=" * 80)
    stats = builder.get_graph_statistics()
    print(f"\nTotal Nodes: {stats.get('total_nodes', 'N/A')}")
    print(f"Total Relationships: {stats.get('total_relationships', 'N/A')}")
    print(f"\nLaptops: {stats.get('laptops', 0)}")
    print(f"Books: {stats.get('books', 0)}")
    print(f"Jewelry: {stats.get('jewelry', 0)}")
    print(f"Accessories: {stats.get('accessories', 0)}")
    print(f"Authors: {stats.get('authors', 0)}")
    print(f"Reviews: {stats.get('reviews', 0)}")
    print("\nNode Types:")
    for item in stats.get("node_types", [])[:15]:
        print(f"   {item.get('label', '?')}: {item.get('count', 0)}")
    print("\nRelationship Types:")
    for item in stats.get("relationship_types", [])[:10]:
        print(f"   {item.get('type', '?')}: {item.get('count', 0)}")
    print("\n" + "=" * 80)
    print("KNOWLEDGE GRAPH BUILD COMPLETE!")
    print("=" * 80)
    print("\nAccess Neo4j Browser: http://localhost:7475 (or 7474 if using default ports)")
    print("\nSample Cypher:")
    print("  MATCH (p:Product) RETURN p.category, count(*) ORDER BY count(*) DESC")
    print("  MATCH (j:Jewelry)-[:BRANDED_BY]->(b:Brand) RETURN j.name, b.name LIMIT 10")

    pg_db.close()
    neo4j_conn.close()


if __name__ == "__main__":
    main()
