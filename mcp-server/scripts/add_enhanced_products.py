#!/usr/bin/env python3
"""
Add Enhanced Products with Complete Metadata

Adds diverse products with:
- Complete specifications
- User reviews
- High-quality images
- Detailed descriptions
- Proper categorization

Run: python scripts/add_enhanced_products.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product, Price, Inventory
import uuid
import json


ENHANCED_PRODUCTS = [
    # Premium Laptops with Complete Specs
    {
        "name": "Microsoft Surface Laptop 5",
        "description": "Microsoft Surface Laptop 5 with 13.5-inch PixelSense touchscreen, Intel Core i7-1255U, 16GB RAM, 512GB SSD. Perfect blend of portability and performance with all-day battery life.",
        "category": "Electronics",
        "brand": "Microsoft",
        "price_cents": 129999,
        "available_qty": 15,
        "color": "Platinum",
        "source": "Seed",
        "product_type": "laptop",
        "image_url": "https://img-prod-cms-rt-microsoft-com.akamaized.net/cms/api/am/imageFileData/RE50NDs?ver=1729&q=90&m=6&h=600&w=1000&b=%23FFFFFFFF&f=jpg&o=f&p=140&aim=true",
        "subcategory": "Work",
        "metadata": {
            "cpu": "Intel Core i7-1255U",
            "ram": "16GB",
            "storage": "512GB SSD",
            "display": "13.5-inch PixelSense (2256x1504)",
            "battery": "Up to 17 hours",
            "weight": "2.80 lbs",
            "ports": ["USB-C", "USB-A", "Surface Connect"]
        },
        "tags": ["productivity", "touchscreen", "premium"],
        "reviews": json.dumps([
            {"rating": 5, "comment": "Perfect for work. Great keyboard and battery life.", "author": "Sarah M."},
            {"rating": 4, "comment": "Beautiful design, but pricey.", "author": "Mike K."}
        ])
    },
    {
        "name": "Razer Blade 15 Advanced",
        "description": "Razer Blade 15 gaming laptop with 15.6-inch QHD 240Hz display, Intel Core i9-13900H, NVIDIA RTX 4080, 32GB DDR5 RAM, 1TB SSD. Ultimate gaming performance in a sleek aluminum chassis.",
        "category": "Electronics",
        "brand": "Razer",
        "price_cents": 319999,
        "available_qty": 5,
        "color": "Black",
        "source": "Seed",
        "product_type": "gaming_laptop",
        "gpu_vendor": "NVIDIA",
        "gpu_model": "RTX 4080",
        "image_url": "https://assets3.razerzone.com/yBjn8E3cG84jtMn8nCHQo5uDAm4=/1500x1000/https%3A%2F%2Fhybrismediaprod.blob.core.windows.net%2Fsys-master-phoenix-images-container%2Fh4a%2Fh13%2F9681130471454%2Fblade15-advanced-2023-render-03.png",
        "subcategory": "Gaming",
        "metadata": {
            "cpu": "Intel Core i9-13900H",
            "ram": "32GB DDR5",
            "storage": "1TB NVMe SSD",
            "display": "15.6-inch QHD 240Hz",
            "gpu": "NVIDIA RTX 4080 12GB",
            "weight": "4.63 lbs",
            "cooling": "Vapor Chamber Cooling"
        },
        "tags": ["gaming", "high-performance", "premium", "rgb"],
        "reviews": json.dumps([
            {"rating": 5, "comment": "Best gaming laptop I've owned. Runs everything maxed out.", "author": "GamePro42"},
            {"rating": 5, "comment": "Expensive but worth every penny for the build quality.", "author": "TechEnthusiast"},
            {"rating": 4, "comment": "Great performance, runs a bit hot under load.", "author": "PCGamer123"}
        ])
    },
    {
        "name": "Acer Swift 3 OLED",
        "description": "Acer Swift 3 with stunning 14-inch 2.8K OLED display, AMD Ryzen 7 7730U, 16GB LPDDR4X RAM, 512GB SSD. Thin, light, and perfect for students and professionals on a budget.",
        "category": "Electronics",
        "brand": "Acer",
        "price_cents": 79999,
        "available_qty": 20,
        "color": "Silver",
        "source": "Seed",
        "product_type": "laptop",
        "image_url": "https://static.acer.com/up/Resource/Acer/Laptops/Swift_3/Images/20220523/acer-swift-3-sf314-43-hero-01.png",
        "subcategory": "School",
        "metadata": {
            "cpu": "AMD Ryzen 7 7730U",
            "ram": "16GB LPDDR4X",
            "storage": "512GB PCIe SSD",
            "display": "14-inch 2.8K OLED (2880x1800)",
            "battery": "Up to 10 hours",
            "weight": "2.76 lbs",
            "ports": ["USB-C", "USB 3.2", "HDMI 2.1"]
        },
        "tags": ["budget", "oled", "portable", "student"],
        "reviews": json.dumps([
            {"rating": 5, "comment": "Amazing value for money. OLED screen is gorgeous!", "author": "Student2024"},
            {"rating": 4, "comment": "Great laptop for the price. Perfect for school work.", "author": "CollegeLife"}
        ])
    },
    
    # More Books with Reviews and Metadata
    {
        "name": "The Martian by Andy Weir",
        "description": "A gripping tale of survival on Mars. Astronaut Mark Watney is stranded alone on the Red Planet and must use his ingenuity to survive. Bestselling science fiction thriller.",
        "category": "Books",
        "brand": "Andy Weir",
        "price_cents": 1699,
        "available_qty": 50,
        "source": "Seed",
        "product_type": "book",
        "subcategory": "Sci-Fi",
        "image_url": "https://m.media-amazon.com/images/I/71Gb50C-NxL._AC_UF1000,1000_QL80_.jpg",
        "metadata": {
            "author": "Andy Weir",
            "pages": 369,
            "publisher": "Crown",
            "isbn": "978-0553418026",
            "language": "English",
            "publication_year": 2014
        },
        "tags": ["sci-fi", "survival", "bestseller", "space"],
        "reviews": json.dumps([
            {"rating": 5, "comment": "Absolutely brilliant! Couldn't put it down.", "author": "SciFiFan"},
            {"rating": 5, "comment": "Perfect blend of science and humor. Highly recommend!", "author": "BookLover2024"},
            {"rating": 4, "comment": "Great read, very engaging and scientifically accurate.", "author": "NASAEngineer"}
        ])
    },
    {
        "name": "Educated by Tara Westover",
        "description": "A powerful memoir about a woman who, kept out of school, leaves her survivalist family and goes on to earn a PhD from Cambridge University. An inspiring story of self-invention.",
        "category": "Books",
        "brand": "Tara Westover",
        "price_cents": 1899,
        "available_qty": 40,
        "source": "Seed",
        "product_type": "book",
        "subcategory": "Biography",
        "image_url": "https://m.media-amazon.com/images/I/71-4MkLN5jL._AC_UF1000,1000_QL80_.jpg",
        "metadata": {
            "author": "Tara Westover",
            "pages": 334,
            "publisher": "Random House",
            "isbn": "978-0399590504",
            "language": "English",
            "publication_year": 2018
        },
        "tags": ["memoir", "education", "inspiration", "nonfiction"],
        "reviews": json.dumps([
            {"rating": 5, "comment": "Life-changing book. A must-read!", "author": "ReadingAddict"},
            {"rating": 5, "comment": "Incredibly powerful and moving story.", "author": "BookClubMom"}
        ])
    },
    {
        "name": "Project Hail Mary by Andy Weir",
        "description": "From the author of The Martian comes another thrilling space adventure. Ryland Grace wakes up on a spaceship with no memory, and must save humanity from extinction.",
        "category": "Books",
        "brand": "Andy Weir",
        "price_cents": 1999,
        "available_qty": 35,
        "source": "Seed",
        "product_type": "book",
        "subcategory": "Sci-Fi",
        "image_url": "https://m.media-amazon.com/images/I/81uHbVfcVsL._AC_UF1000,1000_QL80_.jpg",
        "metadata": {
            "author": "Andy Weir",
            "pages": 476,
            "publisher": "Ballantine Books",
            "isbn": "978-0593135204",
            "language": "English",
            "publication_year": 2021
        },
        "tags": ["sci-fi", "space", "bestseller", "adventure"],
        "reviews": json.dumps([
            {"rating": 5, "comment": "Even better than The Martian! Absolutely loved it.", "author": "AndyWeirFan"},
            {"rating": 5, "comment": "Emotional, funny, and scientifically fascinating.", "author": "BookNerd99"}
        ])
    },
    
    # Gaming Desktops and Accessories
    {
        "name": "Alienware Aurora R15 Gaming Desktop",
        "description": "Alienware Aurora R15 gaming desktop with Intel Core i9-13900KF, NVIDIA RTX 4090 24GB, 64GB DDR5 RAM, 2TB NVMe SSD + 2TB HDD. Extreme gaming performance with Legend 2.0 design and advanced cooling.",
        "category": "Electronics",
        "brand": "Alienware",
        "price_cents": 449999,
        "available_qty": 3,
        "color": "Dark Side of the Moon",
        "source": "Seed",
        "product_type": "desktop_pc",
        "gpu_vendor": "NVIDIA",
        "gpu_model": "RTX 4090",
        "image_url": "https://i.dell.com/is/image/DellContent/content/dam/ss2/product-images/dell-client-products/desktops/alienware-desktops/alienware-aurora-r15/media-gallery/desktop-alienware-aurora-r15-dark-gallery-1.psd?fmt=png-alpha&pscan=auto&scl=1&hei=402&wid=573&qlt=100,1&resMode=sharp2&size=573,402&chrss=full",
        "subcategory": "Gaming",
        "metadata": {
            "cpu": "Intel Core i9-13900KF",
            "ram": "64GB DDR5",
            "storage": "2TB NVMe SSD + 2TB HDD",
            "gpu": "NVIDIA RTX 4090 24GB",
            "cooling": "Liquid Cooling",
            "psu": "1350W",
            "case": "Alienware Legend 2.0"
        },
        "tags": ["gaming", "desktop", "high-end", "rgb", "overclocking"],
        "reviews": json.dumps([
            {"rating": 5, "comment": "Ultimate gaming machine. Worth every penny!", "author": "ProGamer"},
            {"rating": 5, "comment": "Handles 4K gaming like a beast. Amazing build quality.", "author": "HardcoreGamer"}
        ])
    },
    {
        "name": "LG UltraGear 27\" 4K Gaming Monitor",
        "description": "LG UltraGear 27-inch 4K UHD (3840x2160) gaming monitor with 144Hz refresh rate, 1ms response time, NVIDIA G-SYNC, HDR400, and 98% DCI-P3 color gamut.",
        "category": "Electronics",
        "brand": "LG",
        "price_cents": 69999,
        "available_qty": 12,
        "color": "Black",
        "source": "Seed",
        "product_type": "monitor",
        "image_url": "https://www.lg.com/us/images/monitors/md07530838/gallery/medium01.jpg",
        "subcategory": "Gaming",
        "metadata": {
            "size": "27 inches",
            "resolution": "3840x2160 (4K UHD)",
            "refresh_rate": "144Hz",
            "response_time": "1ms (GtG)",
            "panel_type": "Nano IPS",
            "hdr": "VESA DisplayHDR 400",
            "ports": ["DisplayPort 1.4", "HDMI 2.1 x2", "USB Hub"]
        },
        "tags": ["gaming", "4k", "monitor", "high-refresh"],
        "reviews": json.dumps([
            {"rating": 5, "comment": "Perfect gaming monitor. Colors are vibrant!", "author": "MonitorReviewer"},
            {"rating": 4, "comment": "Great for both gaming and content creation.", "author": "CreativeGamer"}
        ])
    },
    
    # More Diverse Laptops
    {
        "name": "Framework Laptop 13",
        "description": "Framework Laptop 13 - the world's most repairable laptop. Customize, upgrade, and repair with easy-to-replace modular components. Intel Core i7-1370P, 32GB DDR4, 1TB NVMe SSD.",
        "category": "Electronics",
        "brand": "Framework",
        "price_cents": 179999,
        "available_qty": 10,
        "color": "Gray",
        "source": "Seed",
        "product_type": "laptop",
        "image_url": "https://cdn.shopify.com/s/files/1/0570/9866/2524/files/Framework_Laptop_13_Hero_1024x1024.png?v=1690847842",
        "subcategory": "Creative",
        "metadata": {
            "cpu": "Intel Core i7-1370P",
            "ram": "32GB DDR4",
            "storage": "1TB NVMe SSD",
            "display": "13.5-inch 2256x1504",
            "modular": True,
            "repairable": True,
            "weight": "2.87 lbs"
        },
        "tags": ["modular", "repairable", "sustainable", "customizable"],
        "reviews": json.dumps([
            {"rating": 5, "comment": "Love the repairability. Easy to upgrade!", "author": "TechSustainable"},
            {"rating": 5, "comment": "Finally, a laptop I can actually repair myself.", "author": "RightToRepair"}
        ])
    },
    {
        "name": "Samsung Galaxy Book3 Ultra",
        "description": "Samsung Galaxy Book3 Ultra with stunning 16-inch Dynamic AMOLED 2X 120Hz display, Intel Core i9-13900H, NVIDIA RTX 4070, 32GB LPDDR5, 1TB SSD. Premium creator laptop.",
        "category": "Electronics",
        "brand": "Samsung",
        "price_cents": 249999,
        "available_qty": 8,
        "color": "Graphite",
        "source": "Seed",
        "product_type": "laptop",
        "gpu_vendor": "NVIDIA",
        "gpu_model": "RTX 4070",
        "image_url": "https://image-us.samsung.com/SamsungUS/home/computing/galaxy-books/gallery/MB-GalaxyBook3Ultra-Gray-Gallery-01-1600x1200.jpg",
        "subcategory": "Creative",
        "metadata": {
            "cpu": "Intel Core i9-13900H",
            "ram": "32GB LPDDR5",
            "storage": "1TB NVMe SSD",
            "display": "16-inch Dynamic AMOLED 2X (2880x1800) 120Hz",
            "gpu": "NVIDIA RTX 4070 8GB",
            "weight": "3.90 lbs"
        },
        "tags": ["creator", "amoled", "high-performance", "120hz"],
        "reviews": json.dumps([
            {"rating": 5, "comment": "Best screen I've ever seen on a laptop.", "author": "PhotoEditor"},
            {"rating": 5, "comment": "Perfect for video editing and creative work.", "author": "VideoCreator"}
        ])
    },
]


def add_products(db, products):
    """Add products to database."""
    added = 0
    skipped = 0
    
    for product_data in products:
        # Check if product already exists
        existing = db.query(Product).filter(Product.name == product_data["name"]).first()
        
        if existing:
            print(f"  ⏭ Skipped (exists): {product_data['name']}")
            skipped += 1
            continue
        
        # Generate product_id
        category_prefix = "elec" if product_data["category"] == "Electronics" else "book"
        product_id = f"prod-{category_prefix}-{uuid.uuid4().hex[:16]}"
        
        # Extract price and inventory data
        price_cents = product_data.pop("price_cents", 0)
        available_qty = product_data.pop("available_qty", 0)
        
        # Convert metadata dict to JSON string if it's a dict
        if "metadata" in product_data and isinstance(product_data["metadata"], dict):
            product_data["metadata"] = json.dumps(product_data["metadata"])
        
        # Create product
        product = Product(
            product_id=product_id,
            **product_data
        )
        db.add(product)
        
        # Create price record
        price = Price(
            product_id=product_id,
            price_cents=price_cents,
            currency="USD"
        )
        db.add(price)
        
        # Create inventory record
        inventory = Inventory(
            product_id=product_id,
            available_qty=available_qty
        )
        db.add(inventory)
        
        added += 1
        print(f"   Added: {product_data['name']}")
    
    db.commit()
    return added, skipped


def main():
    print("="*80)
    print("ADDING ENHANCED PRODUCTS")
    print("Products with complete metadata, images, and reviews")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        print(f"\nAdding {len(ENHANCED_PRODUCTS)} enhanced products...\n")
        added, skipped = add_products(db, ENHANCED_PRODUCTS)
        
        # Final stats
        total = db.query(Product).count()
        electronics = db.query(Product).filter(Product.category == 'Electronics').count()
        books = db.query(Product).filter(Product.category == 'Books').count()
        
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)
        print(f" Added: {added} new products")
        print(f"⏭ Skipped: {skipped} (already exist)")
        print(f"\nTotal Database:")
        print(f"   Total products: {total}")
        print(f"   Electronics: {electronics}")
        print(f"   Books: {books}")
        print("="*80)
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
