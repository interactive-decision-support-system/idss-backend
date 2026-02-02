"""
Seed diverse products into Postgres database.

Creates sample products across multiple categories:
- Electronics
- Vehicles
- Travel
- Real Estate
- Clothing
- Books
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.database import SessionLocal, engine, Base
from app.models import Product, Price, Inventory
from datetime import datetime


def seed_products():
    """Seed diverse products into the database."""
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    # Check if already seeded
    existing = db.query(Product).first()
    if existing:
        print("[WARN] Database already has products. Clearing and reseeding...")
        db.query(Inventory).delete()
        db.query(Price).delete()
        db.query(Product).delete()
        db.commit()
    
    products_data = [
        # Electronics
        {
            "product_id": "ELEC-001",
            "name": "Dell XPS 15 Laptop",
            "description": "15.6\" 4K OLED touchscreen, Intel Core i7, 16GB RAM, 512GB SSD",
            "category": "electronics",
            "brand": "Dell",
            "price_cents": 179999,
            "stock": 15
        },
        {
            "product_id": "ELEC-002",
            "name": "iPhone 15 Pro",
            "description": "256GB, Titanium, A17 Pro chip, Pro camera system",
            "category": "electronics",
            "brand": "Apple",
            "price_cents": 119999,
            "stock": 25
        },
        {
            "product_id": "ELEC-003",
            "name": "Samsung 65\" 4K Smart TV",
            "description": "QLED, HDR10+, 120Hz, Alexa built-in",
            "category": "electronics",
            "brand": "Samsung",
            "price_cents": 89999,
            "stock": 8
        },
        {
            "product_id": "ELEC-004",
            "name": "Sony WH-1000XM5 Headphones",
            "description": "Noise cancelling, 30hr battery, premium sound",
            "category": "electronics",
            "brand": "Sony",
            "price_cents": 39999,
            "stock": 30
        },
        {
            "product_id": "ELEC-005",
            "name": "iPad Pro 12.9\"",
            "description": "M2 chip, 256GB, WiFi + Cellular, Magic Keyboard compatible",
            "category": "electronics",
            "brand": "Apple",
            "price_cents": 129999,
            "stock": 12
        },
        
        # Vehicles (simulated - would normally come from IDSS)
        {
            "product_id": "VEH-001",
            "name": "2024 Tesla Model 3",
            "description": "Long Range AWD, Autopilot, 358 mile range, Pearl White",
            "category": "vehicles",
            "brand": "Tesla",
            "price_cents": 4499900,
            "stock": 3
        },
        {
            "product_id": "VEH-002",
            "name": "2024 Honda Accord Sport",
            "description": "1.5T, CVT, 192 HP, Modern Steel Metallic, 30k miles",
            "category": "vehicles",
            "brand": "Honda",
            "price_cents": 3199900,
            "stock": 5
        },
        {
            "product_id": "VEH-003",
            "name": "2023 Toyota Camry XLE",
            "description": "2.5L 4-cyl, 8-speed automatic, Silver, 15k miles",
            "category": "vehicles",
            "brand": "Toyota",
            "price_cents": 2899900,
            "stock": 4
        },
        
        # Travel (flights, hotels, packages)
        {
            "product_id": "TRAVEL-001",
            "name": "Round-trip Flight SFO → NYC",
            "description": "Direct flight, Economy, Depart Jan 25, Return Jan 30",
            "category": "travel",
            "brand": "United Airlines",
            "price_cents": 45900,
            "stock": 50
        },
        {
            "product_id": "TRAVEL-002",
            "name": "Hilton San Francisco",
            "description": "Deluxe King Room, 3 nights, Union Square location",
            "category": "travel",
            "brand": "Hilton",
            "price_cents": 89900,
            "stock": 20
        },
        {
            "product_id": "TRAVEL-003",
            "name": "Hawaii Vacation Package",
            "description": "7 nights in Maui, 4-star resort, round-trip flights included",
            "category": "travel",
            "brand": "Hawaiian Airlines",
            "price_cents": 249900,
            "stock": 10
        },
        
        # Real Estate (simulated - would normally come from Real Estate backend)
        {
            "product_id": "PROP-001",
            "name": "Modern Condo in Downtown SF",
            "description": "2 bed, 2 bath, 1200 sqft, city views, updated kitchen",
            "category": "real_estate",
            "brand": "Zillow",
            "price_cents": 95000000,  # $950,000
            "stock": 1
        },
        {
            "product_id": "PROP-002",
            "name": "Suburban Home in Palo Alto",
            "description": "4 bed, 3 bath, 2500 sqft, large yard, near schools",
            "category": "real_estate",
            "brand": "Redfin",
            "price_cents": 285000000,  # $2.85M
            "stock": 1
        },
        {
            "product_id": "PROP-003",
            "name": "Apartment in Mission District",
            "description": "1 bed, 1 bath, 750 sqft, renovated, close to BART",
            "category": "real_estate",
            "brand": "Apartments.com",
            "price_cents": 65000000,  # $650,000
            "stock": 1
        },
        
        # Clothing
        {
            "product_id": "CLOTH-001",
            "name": "Nike Air Max 270",
            "description": "Men's running shoes, size 10, Black/White colorway",
            "category": "clothing",
            "brand": "Nike",
            "price_cents": 15999,
            "stock": 25
        },
        {
            "product_id": "CLOTH-002",
            "name": "Levi's 501 Original Jeans",
            "description": "Classic straight fit, medium wash, size 32x32",
            "category": "clothing",
            "brand": "Levi's",
            "price_cents": 6999,
            "stock": 40
        },
        {
            "product_id": "CLOTH-003",
            "name": "Patagonia Down Jacket",
            "description": "Men's insulated parka, Navy Blue, size M",
            "category": "clothing",
            "brand": "Patagonia",
            "price_cents": 29999,
            "stock": 15
        },
        
        # Books
        {
            "product_id": "BOOK-001",
            "name": "Artificial Intelligence: A Modern Approach",
            "description": "4th Edition, Russell & Norvig, comprehensive AI textbook",
            "category": "books",
            "brand": "Pearson",
            "price_cents": 12999,
            "stock": 50
        },
        {
            "product_id": "BOOK-002",
            "name": "The Pragmatic Programmer",
            "description": "20th Anniversary Edition, essential software engineering guide",
            "category": "books",
            "brand": "Addison-Wesley",
            "price_cents": 4999,
            "stock": 35
        },
        {
            "product_id": "BOOK-003",
            "name": "Designing Data-Intensive Applications",
            "description": "Martin Kleppmann, distributed systems and databases",
            "category": "books",
            "brand": "O'Reilly",
            "price_cents": 5999,
            "stock": 30
        },
    ]
    
    print(f"\nSeeding {len(products_data)} diverse products...")
    
    for data in products_data:
        # Create product
        product = Product(
            product_id=data["product_id"],
            name=data["name"],
            description=data["description"],
            category=data["category"],
            brand=data["brand"],
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(product)
        db.flush()
        
        # Add price
        price = Price(
            product_id=data["product_id"],
            price_cents=data["price_cents"],
            currency="USD"
        )
        db.add(price)
        
        # Add inventory
        inventory = Inventory(
            product_id=data["product_id"],
            available_qty=data["stock"],
            reserved_qty=0
        )
        db.add(inventory)
        
        print(f"  [OK] {data['category']:15s} | {data['product_id']:12s} | {data['name']}")
    
    db.commit()
    
    # Verify
    total = db.query(Product).count()
    print(f"\n[OK] Successfully seeded {total} products!")
    
    # Show category breakdown
    print("\nCategory Breakdown:")
    from sqlalchemy import func
    categories = db.query(
        Product.category,
        func.count(Product.product_id).label('count')
    ).group_by(Product.category).all()
    
    for category, count in categories:
        print(f"  • {category:15s}: {count} products")
    
    db.close()


if __name__ == "__main__":
    seed_products()
