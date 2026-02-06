#!/usr/bin/env python3
"""
Fix Product Metadata - Backfill missing images, sources, and metadata

This script fixes existing products by:
1. Adding image URLs for products without images
2. Backfilling source information
3. Adding missing metadata (colors, specs, etc.)
4. Ensuring all products have complete data

Run: python scripts/fix_product_metadata.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product, Price
from sqlalchemy import func

# Image URL sources for different product types
PRODUCT_IMAGES = {
    # Books
    "Dune": "https://m.media-amazon.com/images/I/81zN7udGRUL._AC_UF1000,1000_QL80_.jpg",
    "The Hobbit": "https://m.media-amazon.com/images/I/71jLBXtWJWL._AC_UF1000,1000_QL80_.jpg",
    "1984": "https://m.media-amazon.com/images/I/71kxa1-0mfL._AC_UF1000,1000_QL80_.jpg",
    "To Kill a Mockingbird": "https://m.media-amazon.com/images/I/81aY1lxk+9L._AC_UF1000,1000_QL80_.jpg",
    "The Great Gatsby": "https://m.media-amazon.com/images/I/71FTb9X6wsL._AC_UF1000,1000_QL80_.jpg",
    "Harry Potter and the Sorcerer's Stone": "https://m.media-amazon.com/images/I/71XqqKTZz7L._AC_UF1000,1000_QL80_.jpg",
    "The Catcher in the Rye": "https://m.media-amazon.com/images/I/8125BDk3l9L._AC_UF1000,1000_QL80_.jpg",
    "Pride and Prejudice": "https://m.media-amazon.com/images/I/71Q1tPupKjL._AC_UF1000,1000_QL80_.jpg",
    "The Lord of the Rings": "https://m.media-amazon.com/images/I/71jLBXtWJWL._AC_UF1000,1000_QL80_.jpg",
    
    # Laptops - MacBooks
    "MacBook": "https://store.storeimages.cdn-apple.com/4982/as-images.apple.com/is/macbook-air-midnight-select-20220606?wid=904&hei=840&fmt=jpeg&qlt=90&.v=1653084303665",
    "MacBook Air": "https://store.storeimages.cdn-apple.com/4982/as-images.apple.com/is/macbook-air-midnight-select-20220606?wid=904&hei=840&fmt=jpeg&qlt=90&.v=1653084303665",
    "MacBook Pro": "https://store.storeimages.cdn-apple.com/4982/as-images.apple.com/is/mbp-spacegray-select-202206?wid=904&hei=840&fmt=jpeg&qlt=90&.v=1653493200207",
    
    # Dell Laptops
    "Dell XPS": "https://i.dell.com/is/image/DellContent/content/dam/ss2/product-images/dell-client-products/notebooks/xps-notebooks/xps-15-9530/media-gallery/notebook-xps-15-9530-nt-blue-gallery-4.psd?fmt=png-alpha&pscan=auto&scl=1&hei=402&wid=619&qlt=100,1&resMode=sharp2&size=619,402&chrss=full",
    
    # HP Laptops
    "HP Spectre": "https://ssl-product-images.www8-hp.com/digmedialib/prodimg/lowres/c08278495.png",
    "HP Pavilion": "https://ssl-product-images.www8-hp.com/digmedialib/prodimg/lowres/c08278495.png",
    
    # Lenovo
    "Lenovo ThinkPad": "https://p3-ofp.static.pub/fes/cms/2023/08/07/kj3u8rgq5xhj3tvtlgzwj2w7n0ysq6880491.png",
    "Lenovo IdeaPad": "https://p3-ofp.static.pub/fes/cms/2023/08/07/kj3u8rgq5xhj3tvtlgzwj2w7n0ysq6880491.png",
    
    # ASUS
    "ASUS ROG": "https://dlcdnwebimgs.asus.com/gain/31E94DA4-8A1A-463D-8C5E-8320E3B10B4D/w800",
    "ASUS Vivobook": "https://dlcdnwebimgs.asus.com/gain/31E94DA4-8A1A-463D-8C5E-8320E3B10B4D/w800",
    
    # Gaming laptops generic
    "gaming laptop": "https://www.nvidia.com/content/dam/en-zz/Solutions/geforce/ada/rtx-40-series/geforce-ada-40-series-laptops-ogimage-1200x630.jpg",
}

# Default images by category
DEFAULT_IMAGES = {
    "Electronics": {
        "laptop": "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=800",
        "desktop": "https://images.unsplash.com/photo-1587202372634-32705e3bf49c?w=800",
        "phone": "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=800",
        "tablet": "https://images.unsplash.com/photo-1544244015-0df4b3ffc6b0?w=800",
        "monitor": "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?w=800",
        "default": "https://images.unsplash.com/photo-1498049794561-7780e7231661?w=800"
    },
    "Books": {
        "default": "https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=800"
    }
}


def get_image_url_for_product(product: Product) -> str:
    """Get appropriate image URL for a product."""
    # Check if product name matches any specific product images
    for name_pattern, url in PRODUCT_IMAGES.items():
        if name_pattern.lower() in product.name.lower():
            return url
    
    # Use default image based on category and type
    if product.category == "Electronics":
        product_type = (product.product_type or "").lower()
        if "laptop" in product_type or "laptop" in product.name.lower():
            return DEFAULT_IMAGES["Electronics"]["laptop"]
        elif "desktop" in product_type or "pc" in product.name.lower():
            return DEFAULT_IMAGES["Electronics"]["desktop"]
        elif "phone" in product_type or "iphone" in product.name.lower():
            return DEFAULT_IMAGES["Electronics"]["phone"]
        elif "tablet" in product_type or "ipad" in product.name.lower():
            return DEFAULT_IMAGES["Electronics"]["tablet"]
        elif "monitor" in product_type:
            return DEFAULT_IMAGES["Electronics"]["monitor"]
        else:
            return DEFAULT_IMAGES["Electronics"]["default"]
    
    elif product.category == "Books":
        return DEFAULT_IMAGES["Books"]["default"]
    
    return DEFAULT_IMAGES["Electronics"]["default"]


def fix_missing_images(db):
    """Add image URLs to products without images."""
    print("\n" + "="*80)
    print("FIXING MISSING IMAGES")
    print("="*80)
    
    # Find products without images
    products_without_images = db.query(Product).filter(
        (Product.image_url.is_(None)) | (Product.image_url == '')
    ).all()
    
    print(f"Found {len(products_without_images)} products without images")
    
    fixed = 0
    for product in products_without_images:
        image_url = get_image_url_for_product(product)
        product.image_url = image_url
        fixed += 1
        if fixed <= 5:  # Show first 5
            print(f"  {product.name[:50]}: {image_url[:60]}...")
    
    db.commit()
    print(f"\nFixed {fixed} products with images")
    return fixed


def fix_missing_source(db):
    """Backfill source field for products without it."""
    print("\n" + "="*80)
    print("FIXING MISSING SOURCE INFORMATION")
    print("="*80)
    
    # Find products without source
    products_without_source = db.query(Product).filter(
        (Product.source.is_(None)) | (Product.source == '')
    ).all()
    
    print(f"Found {len(products_without_source)} products without source")
    
    fixed = 0
    for product in products_without_source:
        # If it has scraped_from_url, extract domain as source
        if product.scraped_from_url:
            from urllib.parse import urlparse
            domain = urlparse(product.scraped_from_url).netloc
            product.source = domain.replace('www.', '').title()
        else:
            # Mark as seed data
            product.source = "Seed"
        
        fixed += 1
        if fixed <= 5:
            print(f"  {product.name[:50]}: source={product.source}")
    
    db.commit()
    print(f"\nFixed {fixed} products with source information")
    return fixed


def add_missing_colors(db):
    """Add colors to products that don't have them."""
    print("\n" + "="*80)
    print("ADDING MISSING COLORS")
    print("="*80)
    
    # Default colors for brands
    BRAND_COLORS = {
        "Apple": ["Space Gray", "Silver", "Midnight", "Starlight"],
        "Dell": ["Platinum Silver", "Black", "Rose Gold"],
        "HP": ["Natural Silver", "Jet Black", "Ceramic White"],
        "Lenovo": ["Black", "Gray", "Silver"],
        "ASUS": ["Eclipse Gray", "Off Black", "Silver Blue"],
        "MSI": ["Black", "Titanium Gray"],
        "Razer": ["Black"],
        "Samsung": ["Mystic Black", "Mystic Silver", "Mystic Bronze"],
    }
    
    # Find electronics without colors
    products_without_color = db.query(Product).filter(
        Product.category == 'Electronics',
        (Product.color.is_(None)) | (Product.color == '')
    ).all()
    
    print(f"Found {len(products_without_color)} electronics without colors")
    
    fixed = 0
    for product in products_without_color:
        # Get default color for brand
        brand_colors = BRAND_COLORS.get(product.brand, ["Black", "Silver", "Gray"])
        product.color = brand_colors[0]  # Use first default color
        
        fixed += 1
        if fixed <= 5:
            print(f"  {product.name[:50]}: color={product.color}")
    
    db.commit()
    print(f"\nAdded colors to {fixed} products")
    return fixed


def add_missing_metadata(db):
    """Add missing metadata to products."""
    print("\n" + "="*80)
    print("ADDING MISSING METADATA")
    print("="*80)
    
    # Find gaming laptops without GPU info
    gaming_laptops = db.query(Product).filter(
        Product.category == 'Electronics',
        (Product.name.ilike('%gaming%')) | (Product.product_type == 'gaming_laptop'),
        (Product.gpu_vendor.is_(None)) | (Product.gpu_vendor == '')
    ).all()
    
    print(f"Found {len(gaming_laptops)} gaming laptops without GPU vendor")
    
    fixed = 0
    for product in gaming_laptops:
        # Infer GPU from name/description
        text = f"{product.name} {product.description or ''}".lower()
        
        if 'nvidia' in text or 'rtx' in text or 'gtx' in text or 'geforce' in text:
            product.gpu_vendor = 'NVIDIA'
            if 'rtx 4090' in text:
                product.gpu_model = 'RTX 4090'
            elif 'rtx 4080' in text:
                product.gpu_model = 'RTX 4080'
            elif 'rtx 4070' in text:
                product.gpu_model = 'RTX 4070'
            elif 'rtx 4060' in text:
                product.gpu_model = 'RTX 4060'
        elif 'amd' in text or 'radeon' in text:
            product.gpu_vendor = 'AMD'
            if 'rx 7900' in text:
                product.gpu_model = 'RX 7900 XTX'
            elif 'rx 6800' in text:
                product.gpu_model = 'RX 6800'
        
        if product.gpu_vendor:
            fixed += 1
            if fixed <= 5:
                print(f"  {product.name[:50]}: GPU={product.gpu_vendor} {product.gpu_model or ''}")
    
    db.commit()
    print(f"\nAdded GPU info to {fixed} gaming laptops")
    return fixed


def fix_jewelry_prices(db):
    """Fix jewelry prices that are clearly wrong (e.g. WooCommerce conversion bug: 500000)."""
    print("\n" + "="*80)
    print("FIXING JEWELRY PRICES")
    print("="*80)

    jewelry_products = db.query(Product).filter(Product.category == "Jewelry").all()
    fixed = 0
    for p in jewelry_products:
        price_row = db.query(Price).filter(Price.product_id == p.product_id).first()
        if not price_row:
            continue
        dollars = price_row.price_cents / 100
        if dollars > 50_000:
            # Likely bug: e.g. 500000 from cents/dollars mix-up
            if "carat" in (p.name or "").lower() and "diamond" in (p.name or "").lower():
                new_cents = 5_000_000  # $50,000 for luxury diamond
            else:
                new_cents = min(500_000, price_row.price_cents // 100)  # Try /100, cap $5,000
            price_row.price_cents = int(new_cents)
            fixed += 1
            print(f"  {p.name[:50]}: ${dollars:,.0f} -> ${price_row.price_cents/100:,.0f}")

    if fixed:
        db.commit()
    print(f"\nFixed {fixed} jewelry prices")
    return fixed


def generate_report(db):
    """Generate final report on database completeness."""
    print("\n" + "="*80)
    print("FINAL DATABASE STATUS")
    print("="*80)
    
    total = db.query(Product).count()
    with_images = db.query(Product).filter(
        Product.image_url.isnot(None), Product.image_url != ''
    ).count()
    with_source = db.query(Product).filter(
        Product.source.isnot(None), Product.source != ''
    ).count()
    with_color = db.query(Product).filter(
        Product.color.isnot(None), Product.color != ''
    ).count()
    
    print(f"Total Products: {total}")
    print(f"  With images: {with_images}/{total} ({with_images/total*100:.1f}%)")
    print(f"  With source: {with_source}/{total} ({with_source/total*100:.1f}%)")
    print(f"  With colors: {with_color}/{total} ({with_color/total*100:.1f}%)")
    
    # Count by category
    electronics = db.query(Product).filter(Product.category == 'Electronics').count()
    books = db.query(Product).filter(Product.category == 'Books').count()
    
    print(f"\nBy Category:")
    print(f"  Electronics: {electronics}")
    print(f"  Books: {books}")
    
    return {
        "total": total,
        "with_images": with_images,
        "with_source": with_source,
        "with_color": with_color,
        "electronics": electronics,
        "books": books
    }


def main():
    print("="*80)
    print("PRODUCT METADATA FIX TOOL")
    print("Backfilling missing images, sources, colors, and metadata")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        # Run fixes
        images_fixed = fix_missing_images(db)
        source_fixed = fix_missing_source(db)
        colors_fixed = add_missing_colors(db)
        metadata_fixed = add_missing_metadata(db)
        jewelry_prices_fixed = fix_jewelry_prices(db)
        
        # Generate report
        report = generate_report(db)
        
        # Summary
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)
        print(f"Images fixed: {images_fixed}")
        print(f"Sources backfilled: {source_fixed}")
        print(f"Colors added: {colors_fixed}")
        print(f"Metadata enhanced: {metadata_fixed}")
        print(f"Jewelry prices fixed: {jewelry_prices_fixed}")
        print(f"\nTotal products: {report['total']}")
        print(f"Completeness: {report['with_images']/report['total']*100:.1f}%")
        print("="*80)
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
