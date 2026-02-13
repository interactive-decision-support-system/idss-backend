#!/usr/bin/env python3
"""
Build Complex Neo4j Knowledge Graph from PostgreSQL Data

Populates a rich, multi-dimensional knowledge graph with:
- Detailed laptop components (CPU, GPU, RAM, Storage, Display)
- Manufacturing and supply chain relationships
- Book metadata with authors, publishers, genres
- Literary connections and hierarchies
- User interactions and reviews
- Software compatibility
- Comparison relationships
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env from project root so NEO4J_* are set when run from mcp-server
try:
    from dotenv import load_dotenv
    _root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(_root / ".env")
    load_dotenv()
except Exception:
    pass

from app.database import SessionLocal
from app.models import Product, Price, Inventory
from app.neo4j_config import Neo4jConnection
from app.knowledge_graph import KnowledgeGraphBuilder
import json
import random
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
    "System76": {"country": "USA", "founded": 2007, "website": "system76.com"},
    "Framework": {"country": "USA", "founded": 2019, "website": "frame.work"},
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

SOFTWARE_CATALOG = [
    {"name": "Visual Studio Code", "category": "Development", "version": "1.85", "developer": "Microsoft", "license_type": "Free", "os_name": "Windows", "os_version": "11", "os_architecture": "x64"},
    {"name": "Adobe Photoshop", "category": "Creative", "version": "2024", "developer": "Adobe", "license_type": "Subscription", "os_name": "Windows", "os_version": "11", "os_architecture": "x64"},
    {"name": "AutoCAD", "category": "Design", "version": "2024", "developer": "Autodesk", "license_type": "Subscription", "os_name": "Windows", "os_version": "11", "os_architecture": "x64"},
    {"name": "Microsoft Office", "category": "Productivity", "version": "365", "developer": "Microsoft", "license_type": "Subscription", "os_name": "Windows", "os_version": "11", "os_architecture": "x64"},
    {"name": "Steam", "category": "Gaming", "version": "Latest", "developer": "Valve", "license_type": "Free", "os_name": "Windows", "os_version": "11", "os_architecture": "x64"},
]


def extract_laptop_specs(product: Product) -> dict:
    """Extract detailed laptop specifications from metadata."""
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
        "screen_size_inches": random.choice([13.3, 14.0, 15.6, 16.0, 17.3]),
        "refresh_rate_hz": random.choice([60, 120, 144, 165, 240]),
        
        # Manufacturer
        "manufacturer_country": LAPTOP_MANUFACTURERS.get(product.brand, {}).get("country", "Unknown"),
        "manufacturer_founded": LAPTOP_MANUFACTURERS.get(product.brand, {}).get("founded"),
        "manufacturer_website": LAPTOP_MANUFACTURERS.get(product.brand, {}).get("website"),
        
        # CPU
        "cpu_model": "Unknown CPU",
        "cpu_manufacturer": "Intel",
        "cpu_cores": 8,
        "cpu_threads": 16,
        "cpu_base_clock": 2.4,
        "cpu_boost_clock": 4.5,
        "cpu_tdp": 45,
        "cpu_generation": "12th Gen",
        "cpu_tier": "Mid-range",
        
        # GPU
        "gpu_model": None,
        "gpu_manufacturer": None,
        "gpu_vram": None,
        "gpu_memory_type": None,
        "gpu_tdp": None,
        "gpu_tier": None,
        "gpu_ray_tracing": False,
        
        # RAM
        "ram_capacity": 16,
        "ram_type": "DDR4",
        "ram_speed": 3200,
        "ram_channels": 2,
        "ram_expandable": True,
        
        # Storage
        "storage_capacity": 512,
        "storage_type": "NVMe SSD",
        "storage_interface": "PCIe 4.0",
        "storage_read_speed": 7000,
        "storage_write_speed": 5000,
        "storage_expandable": True,
        
        # Display
        "display_resolution": "1920x1080",
        "display_panel_type": "IPS",
        "display_brightness": 300,
        "display_color_gamut": "sRGB 100%",
        "display_touch": False,
    }
    
    # Parse metadata if available
    # In a real system, you'd extract this from product.metadata or description
    # For now, we'll generate realistic specs based on product name
    name_lower = product.name.lower()
    
    # CPU detection
    if "i9" in name_lower or "ryzen 9" in name_lower:
        specs["cpu_cores"] = 16
        specs["cpu_threads"] = 24
        specs["cpu_tier"] = "High-end"
        specs["cpu_model"] = "Intel Core i9-13900H" if "intel" in name_lower else "AMD Ryzen 9 7940HS"
    elif "i7" in name_lower or "ryzen 7" in name_lower:
        specs["cpu_cores"] = 12
        specs["cpu_threads"] = 16
        specs["cpu_tier"] = "Upper mid-range"
        specs["cpu_model"] = "Intel Core i7-13700H" if "intel" in name_lower else "AMD Ryzen 7 7735HS"
    elif "i5" in name_lower or "ryzen 5" in name_lower:
        specs["cpu_cores"] = 8
        specs["cpu_threads"] = 12
        specs["cpu_tier"] = "Mid-range"
        specs["cpu_model"] = "Intel Core i5-12500H" if "intel" in name_lower else "AMD Ryzen 5 7535HS"
    elif "m1" in name_lower or "m2" in name_lower or "m3" in name_lower:
        specs["cpu_manufacturer"] = "Apple"
        specs["cpu_model"] = "Apple M2" if "m2" in name_lower else ("Apple M3" if "m3" in name_lower else "Apple M1")
        specs["cpu_cores"] = 8
        specs["cpu_threads"] = 8
        specs["cpu_tier"] = "High-end"
    
    # GPU detection
    if "gaming" in name_lower or "rtx" in name_lower or "radeon" in name_lower:
        if "rtx 4090" in name_lower:
            specs.update({"gpu_model": "NVIDIA RTX 4090", "gpu_manufacturer": "NVIDIA", "gpu_vram": 16, "gpu_tier": "Ultra high-end", "gpu_ray_tracing": True})
        elif "rtx 4080" in name_lower:
            specs.update({"gpu_model": "NVIDIA RTX 4080", "gpu_manufacturer": "NVIDIA", "gpu_vram": 12, "gpu_tier": "High-end", "gpu_ray_tracing": True})
        elif "rtx 4070" in name_lower:
            specs.update({"gpu_model": "NVIDIA RTX 4070", "gpu_manufacturer": "NVIDIA", "gpu_vram": 8, "gpu_tier": "Upper mid-range", "gpu_ray_tracing": True})
        elif "rtx 4060" in name_lower:
            specs.update({"gpu_model": "NVIDIA RTX 4060", "gpu_manufacturer": "NVIDIA", "gpu_vram": 6, "gpu_tier": "Mid-range", "gpu_ray_tracing": True})
        else:
            specs.update({"gpu_model": "NVIDIA RTX 3060", "gpu_manufacturer": "NVIDIA", "gpu_vram": 6, "gpu_tier": "Mid-range", "gpu_ray_tracing": True})
        specs["gpu_memory_type"] = "GDDR6"
        specs["gpu_tdp"] = random.randint(80, 150)
    
    # RAM detection
    if "32gb" in name_lower:
        specs["ram_capacity"] = 32
    elif "64gb" in name_lower:
        specs["ram_capacity"] = 64
    elif "8gb" in name_lower:
        specs["ram_capacity"] = 8
    
    # Storage detection
    if "1tb" in name_lower:
        specs["storage_capacity"] = 1024
    elif "2tb" in name_lower:
        specs["storage_capacity"] = 2048
    elif "256gb" in name_lower:
        specs["storage_capacity"] = 256
    
    return specs


def extract_book_metadata(product: Product) -> dict:
    """Extract detailed book metadata."""
    name_parts = product.name.split(" by ")
    title = name_parts[0].strip()
    author = name_parts[1].strip() if len(name_parts) > 1 else "Unknown Author"
    
    # Sample author data (in production, this would come from a database or API)
    author_data = {
        "nationality": random.choice(["American", "British", "Canadian", "Australian", "International"]),
        "birth_year": random.randint(1940, 1985),
        "biography": f"{author} is a renowned author in their field.",
        "awards": []
    }
    
    # Determine publisher
    publisher_name = random.choice(list(BOOK_PUBLISHERS.keys()))
    publisher_info = BOOK_PUBLISHERS[publisher_name]
    
    metadata = {
        "product_id": product.product_id,
        "title": title,
        "name": product.name,
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
        
        # Author
        "author": author,
        "author_nationality": author_data["nationality"],
        "author_birth_year": author_data["birth_year"],
        "author_biography": author_data["biography"],
        "author_awards": author_data["awards"],
        
        # Publisher
        "publisher": publisher_name,
        "publisher_country": publisher_info["country"],
        "publisher_founded": publisher_info["founded"],
        "publisher_website": publisher_info["website"],
        
        # Genre
        "genre": product.subcategory or "Fiction",
        "genre_description": f"Books in the {product.subcategory or 'Fiction'} category",
        
        # Themes
        "themes": random.sample(["Identity", "Love", "Betrayal", "Justice", "Freedom", "Power", "Revenge", "Family", "Truth", "Redemption"], k=random.randint(2, 4)),
        
        # Series
        "series_name": None if random.random() > 0.3 else f"{title.split()[0]} Series",
        "series_position": random.randint(1, 5) if random.random() < 0.3 else None,
        "series_total_books": random.randint(3, 7) if random.random() < 0.3 else None,
    }
    
    return metadata


def main():
    """Build the complete knowledge graph."""
    import argparse
    parser = argparse.ArgumentParser(description="Build Neo4j knowledge graph from PostgreSQL")
    parser.add_argument("--clear", action="store_true", help="Clear Neo4j before building (use after populate_real_only_db)")
    args = parser.parse_args()

    print("="*80)
    print("BUILDING COMPLEX NEO4J KNOWLEDGE GRAPH")
    print("="*80)
    
    # Connect to databases
    print("\n1. Connecting to databases...")
    pg_db = SessionLocal()
    neo4j_conn = Neo4jConnection()
    
    if not neo4j_conn.verify_connectivity():
        print("[FAIL] Failed to connect to Neo4j. Please ensure Neo4j is running.")
        print("   Start Neo4j with: docker run -p 7687:7687 -p 7474:7474 neo4j")
        return
    
    print(" Connected to PostgreSQL and Neo4j")
    
    # Initialize graph builder
    builder = KnowledgeGraphBuilder(neo4j_conn)
    
    if args.clear:
        print("\n1b. Clearing existing Neo4j graph...")
        builder.clear_all_data()
    
    # Create indexes
    print("\n2. Creating indexes and constraints...")
    builder.create_indexes_and_constraints()
    
    # Get products from PostgreSQL
    print("\n3. Loading products from PostgreSQL...")
    all_products = pg_db.query(Product).all()
    # Laptops: explicit type or Electronics (real-only DB may have NULL product_type)
    laptops = [
        p for p in all_products
        if p.category == "Electronics"
        and (p.product_type in ["laptop", "gaming_laptop"] or p.product_type is None)
    ]
    books = [p for p in all_products if p.category == "Books"]
    
    print(f"   Found {len(laptops)} laptops and {len(books)} books")
    
    # Create genre hierarchy
    print("\n4. Creating genre hierarchy...")
    builder.create_genre_hierarchy(GENRE_HIERARCHY)
    print(f" Created {len(GENRE_HIERARCHY)} genres")
    
    # Process laptops
    print("\n5. Processing laptops...")
    laptop_ids = []
    for i, laptop in enumerate(laptops[:50], 1):  # Limit for demo
        try:
            # Get price
            price_obj = pg_db.query(Price).filter(Price.product_id == laptop.product_id).first()
            price = price_obj.price_cents / 100 if price_obj else 999.99
            
            # Extract specs
            laptop_data = extract_laptop_specs(laptop)
            laptop_data["price"] = price
            
            # Create node
            product_id = builder.create_laptop_node(laptop_data)
            laptop_ids.append(product_id)
            
            # Add software compatibility
            if i <= 10:  # Only for first 10 to keep it manageable
                software_compatible = random.sample(SOFTWARE_CATALOG, k=random.randint(2, 4))
                for sw in software_compatible:
                    sw["performance_rating"] = random.uniform(3.5, 5.0)
                builder.create_software_compatibility(product_id, software_compatible)
            
            if i % 10 == 0:
                print(f"   Processed {i}/{min(50, len(laptops))} laptops")
        except Exception as e:
            print(f"   [WARN]  Error processing {laptop.name}: {e}")
    
    print(f" Created {len(laptop_ids)} laptop nodes")
    
    # Process books
    print("\n6. Processing books...")
    book_ids = []
    for i, book in enumerate(books[:50], 1):  # Limit for demo
        try:
            # Get price
            price_obj = pg_db.query(Price).filter(Price.product_id == book.product_id).first()
            price = price_obj.price_cents / 100 if price_obj else 19.99
            
            # Extract metadata
            book_data = extract_book_metadata(book)
            book_data["price"] = price
            
            # Create node
            product_id = builder.create_book_node(book_data)
            book_ids.append(product_id)
            
            if i % 10 == 0:
                print(f"   Processed {i}/{min(50, len(books))} books")
        except Exception as e:
            print(f"   [WARN]  Error processing {book.name}: {e}")
    
    print(f" Created {len(book_ids)} book nodes")
    
    # Create reviews
    print("\n7. Creating reviews and user interactions...")
    review_count = 0
    for product_id in (laptop_ids + book_ids)[:30]:  # First 30 products
        num_reviews = random.randint(2, 5)
        for _ in range(num_reviews):
            sentiment_score = random.uniform(-1, 1)
            sentiment_label = "positive" if sentiment_score > 0.3 else ("negative" if sentiment_score < -0.3 else "neutral")
            
            review_data = {
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
            }
            builder.create_review_relationships(review_data)
            review_count += 1
    
    print(f" Created {review_count} reviews")
    
    # Create comparison relationships
    print("\n8. Creating product comparisons...")
    comparison_count = 0
    for i in range(min(20, len(laptop_ids))):
        if i + 1 < len(laptop_ids):
            builder.create_comparison_relationships(
                laptop_ids[i],
                laptop_ids[i + 1],
                "SIMILAR_TO",
                random.uniform(0.7, 0.95)
            )
            comparison_count += 1
    
    for i in range(min(15, len(book_ids))):
        if i + 1 < len(book_ids):
            builder.create_comparison_relationships(
                book_ids[i],
                book_ids[i + 1],
                "SIMILAR_TO",
                random.uniform(0.6, 0.9)
            )
            comparison_count += 1
    
    print(f" Created {comparison_count} comparison relationships")
    
    # Create literary connections
    print("\n9. Creating literary connections...")
    literary_count = 0
    for i in range(min(10, len(book_ids) - 1)):
        if random.random() < 0.3:
            builder.create_literary_connections(
                book_ids[i],
                book_ids[i + 1],
                random.choice(["SIMILAR_THEME", "INSPIRED_BY", "RECOMMENDED_WITH"]),
                "Connected by theme or style"
            )
            literary_count += 1
    
    print(f" Created {literary_count} literary connections")
    
    # Entity resolution: merge duplicate Authors, Manufacturers, Brands
    print("\n10. Running entity resolution...")
    try:
        merge_counts = builder.run_entity_resolution(similarity_threshold=0.88)
        if sum(merge_counts.values()) > 0:
            print(f"   Merged duplicates: {merge_counts}")
        else:
            print("   No duplicate entities found")
    except Exception as e:
        print(f"   [WARN] Entity resolution skipped: {e}")
    
    # Get statistics
    print("\n" + "="*80)
    print("KNOWLEDGE GRAPH STATISTICS")
    print("="*80)
    
    stats = builder.get_graph_statistics()
    print(f"\n Total Nodes: {stats['total_nodes']}")
    print(f" Total Relationships: {stats['total_relationships']}")
    print(f"\n Laptops: {stats['laptops']}")
    print(f" Books: {stats['books']}")
    print(f"  Authors: {stats['authors']}")
    print(f" Reviews: {stats['reviews']}")
    print(f"👥 Users: {stats['users']}")
    
    print("\n Node Types:")
    for item in stats['node_types'][:10]:
        print(f"   {item['label']}: {item['count']}")
    
    print("\n Relationship Types:")
    for item in stats['relationship_types'][:10]:
        print(f"   {item['type']}: {item['count']}")
    
    print("\n" + "="*80)
    print(" KNOWLEDGE GRAPH BUILD COMPLETE!")
    print("="*80)
    print("\nAccess Neo4j Browser at: http://localhost:7474")
    print("Username: neo4j")
    print("Password: (your password)")
    print("\nSample Cypher Queries:")
    print("  MATCH (l:Laptop)-[:HAS_CPU]->(cpu:CPU) RETURN l.name, cpu.model LIMIT 10")
    print("  MATCH (b:Book)-[:WRITTEN_BY]->(a:Author) RETURN b.title, a.name LIMIT 10")
    print("  MATCH (p:Product)<-[:REVIEWS]-(r:Review) RETURN p.name, avg(r.rating) AS avg_rating ORDER BY avg_rating DESC LIMIT 10")
    
    # Cleanup
    pg_db.close()
    neo4j_conn.close()


if __name__ == "__main__":
    main()
