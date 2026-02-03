
import sys
import os
import uuid
from dotenv import load_dotenv

# Setup environment
load_dotenv()

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../"))

from app.database import SessionLocal, engine
from app.models import Product, Price, Inventory, Base

def seed_laptops():
    db = SessionLocal()
    
    # Check if we already have laptops
    if db.query(Product).filter(Product.category == "Electronics").count() > 0:
        print("Laptops already exist. Skipping seed.")
        return

    print("Seeding sample laptops...")

    laptops = [
        {
            "name": "Alienware m16 Gaming Laptop",
            "description": "High-performance gaming laptop. Processor: Intel Core i9-13900HK, RAM: 32GB DDR5, Storage: 1TB SSD, Graphics: NVIDIA GeForce RTX 4080. 16-inch QHD+ 240Hz Display.",
            "price": 249999, # $2499.99
            "brand": "Alienware",
            "gpu_vendor": "NVIDIA",
            "gpu_model": "RTX 4080",
            "tags": ["gaming", "high-performance"]
        },
        {
            "name": "MacBook Pro 16",
            "description": "Processor: Apple M3 Max, RAM: 36GB Unified, Storage: 1TB SSD. Liquid Retina XDR display. Perfect for creative professionals.",
            "price": 349900,
            "brand": "Apple",
            "gpu_vendor": "Apple",
            "gpu_model": "M3 Max",
            "tags": ["productivity", "creative"]
        },
        {
            "name": "ASUS ROG Zephyrus G14",
            "description": "Ultra-portable gaming. Processor: AMD Ryzen 9 7940HS, RAM: 16GB, Storage: 512GB SSD, Graphics: NVIDIA GeForce RTX 4060.",
            "price": 159999,
            "brand": "ASUS",
            "gpu_vendor": "NVIDIA",
            "gpu_model": "RTX 4060",
            "tags": ["gaming", "portable"]
        }
    ]

    for lap in laptops:
        p_id = f"prod-laptop-{uuid.uuid4().hex[:8]}"
        
        product = Product(
            product_id=p_id,
            name=lap["name"],
            description=lap["description"],
            category="Electronics",
            brand=lap["brand"],
            gpu_vendor=lap["gpu_vendor"],
            gpu_model=lap["gpu_model"],
            tags=lap["tags"],
            image_url=None # Test placeholder logic
        )
        
        price = Price(
            product_id=p_id,
            price_cents=lap["price"],
            currency="USD"
        )
        
        inv = Inventory(
            product_id=p_id,
            available_qty=10
        )
        
        db.add(product)
        db.add(price)
        db.add(inv)
        
    db.commit()
    print(f"Seeded {len(laptops)} laptops successfully.")
    db.close()

if __name__ == "__main__":
    seed_laptops()
