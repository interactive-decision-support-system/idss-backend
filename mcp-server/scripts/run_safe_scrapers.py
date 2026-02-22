#!/usr/bin/env python3
"""
Safe Product Scraper Runner

Scrapes products from scraper-friendly sources:
1. BigCommerce Demo Store (publicly accessible)
2. WooCommerce Demo Sites
3. JSON APIs (no scraping needed)
4. Open product databases

This script safely adds real products without violating Terms of Service.

Run: python scripts/run_safe_scrapers.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
import json
from typing import List, Dict, Any
from app.database import SessionLocal
from app.models import Product, Price, Inventory
import uuid


# Safe, public product sources
SAFE_SOURCES = {
    "bigcommerce_demo": {
        "name": "BigCommerce Demo Store",
        "api_url": "https://api.bigcommerce.com/stores/{store_hash}/v3/catalog/products",
        "public": True,
        "note": "Public demo store - safe to scrape"
    },
    "fakestoreapi": {
        "name": "Fake Store API",
        "api_url": "https://fakestoreapi.com/products",
        "public": True,
        "note": "Free fake e-commerce API for testing"
    },
    "dummyjson": {
        "name": "DummyJSON",
        "api_url": "https://dummyjson.com/products",
        "public": True,
        "note": "Free fake REST API with products"
    }
}


def scrape_fakestoreapi() -> List[Dict[str, Any]]:
    """
    Scrape products from FakeStoreAPI (free, public API).
    
    Returns:
        List of product dictionaries
    """
    print("\n" + "="*80)
    print("SCRAPING: FakeStoreAPI (Free Public API)")
    print("="*80)
    
    try:
        url = "https://fakestoreapi.com/products"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        products = response.json()
        print(f" Fetched {len(products)} products from FakeStoreAPI")
        
        # Convert to our format
        converted = []
        for p in products:
            # Determine category
            category_map = {
                "electronics": "Electronics",
                "men's clothing": "Clothing",
                "women's clothing": "Clothing",
                "jewelery": "Jewelry"
            }
            
            category = category_map.get(p.get("category", "").lower(), "Electronics")
            
            product = {
                "name": p.get("title", "Unknown Product"),
                "description": p.get("description", ""),
                "category": category,
                "price_cents": int(float(p.get("price", 0)) * 100),
                "available_qty": 50,  # Assume good stock
                "image_url": p.get("image", ""),
                "source": "FakeStoreAPI",
                "source_product_id": str(p.get("id", "")),
                "reviews": json.dumps([{
                    "rating": p.get("rating", {}).get("rate", 0),
                    "comment": f"Rated {p.get('rating', {}).get('rate', 0)}/5 by {p.get('rating', {}).get('count', 0)} users",
                    "author": "Aggregate"
                }])
            }
            
            converted.append(product)
        
        return converted
        
    except Exception as e:
        print(f"[FAIL] Error scraping FakeStoreAPI: {e}")
        return []


def scrape_dummyjson() -> List[Dict[str, Any]]:
    """
    Scrape products from DummyJSON (free, public API).
    
    Returns:
        List of product dictionaries
    """
    print("\n" + "="*80)
    print("SCRAPING: DummyJSON (Free Public API)")
    print("="*80)
    
    try:
        url = "https://dummyjson.com/products?limit=100"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        products = data.get("products", [])
        print(f" Fetched {len(products)} products from DummyJSON")
        
        # Convert to our format
        converted = []
        for p in products:
            # Map categories
            category_map = {
                "smartphones": "Electronics",
                "laptops": "Electronics",
                "fragrances": "Beauty",
                "skincare": "Beauty",
                "groceries": "Groceries",
                "home-decoration": "Home",
                "furniture": "Home",
                "tops": "Clothing",
                "womens-dresses": "Clothing",
                "womens-shoes": "Shoes",
                "mens-shirts": "Clothing",
                "mens-shoes": "Shoes",
                "mens-watches": "Accessories",
                "womens-watches": "Accessories",
                "womens-bags": "Accessories",
                "womens-jewellery": "Jewelry",
                "sunglasses": "Accessories",
                "automotive": "Automotive",
                "motorcycle": "Automotive",
                "lighting": "Home"
            }
            
            raw_category = p.get("category", "").lower()
            category = category_map.get(raw_category, "Electronics")
            
            # Get product type
            product_type = None
            if category == "Electronics":
                if "laptop" in raw_category:
                    product_type = "laptop"
                elif "phone" in raw_category or "smartphone" in raw_category:
                    product_type = "phone"
            
            # Get brand
            brand = p.get("brand", "Generic")
            
            # Get first image
            images = p.get("images", [])
            image_url = images[0] if images else p.get("thumbnail", "")
            
            product = {
                "name": p.get("title", "Unknown Product"),
                "description": p.get("description", ""),
                "category": category,
                "brand": brand,
                "price_cents": int(float(p.get("price", 0)) * 100),
                "available_qty": p.get("stock", 50),
                "image_url": image_url,
                "source": "DummyJSON",
                "source_product_id": str(p.get("id", "")),
                "product_type": product_type,
                "metadata": json.dumps({
                    "rating": p.get("rating", 0),
                    "discount_percentage": p.get("discountPercentage", 0),
                    "sku": p.get("sku", ""),
                    "weight": p.get("weight", 0),
                    "dimensions": p.get("dimensions", {})
                }),
                "tags": p.get("tags", []) if isinstance(p.get("tags"), list) else []
            }
            
            converted.append(product)
        
        return converted
        
    except Exception as e:
        print(f"[FAIL] Error scraping DummyJSON: {e}")
        return []


def add_products_to_database(products: List[Dict[str, Any]], db):
    """
    Add scraped products to database.
    
    Args:
        products: List of product dictionaries
        db: Database session
    
    Returns:
        Tuple of (added, skipped, updated)
    """
    added = 0
    skipped = 0
    updated = 0
    
    for product_data in products:
        try:
            # Check if product exists by source_product_id
            source_product_id = product_data.get("source_product_id")
            source = product_data.get("source")
            
            existing = None
            if source_product_id and source:
                existing = db.query(Product).filter(
                    Product.source_product_id == source_product_id,
                    Product.source == source
                ).first()
            
            # Extract price and inventory
            price_cents = product_data.pop("price_cents", 0)
            available_qty = product_data.pop("available_qty", 0)
            
            # Convert metadata and tags if needed
            if "metadata" in product_data and isinstance(product_data["metadata"], dict):
                product_data["metadata"] = json.dumps(product_data["metadata"])
            
            if existing:
                # Update existing product
                for key, value in product_data.items():
                    setattr(existing, key, value)
                
                # Update price
                if existing.price_info:
                    existing.price_info.price_cents = price_cents
                else:
                    price = Price(product_id=existing.product_id, price_cents=price_cents)
                    db.add(price)
                
                # Update inventory
                if existing.inventory_info:
                    existing.inventory_info.available_qty = available_qty
                else:
                    inventory = Inventory(product_id=existing.product_id, available_qty=available_qty)
                    db.add(inventory)
                
                updated += 1
                print(f"   Updated: {product_data['name'][:50]}")
            else:
                # Create new product
                category_prefix = "elec" if product_data["category"] == "Electronics" else "prod"
                product_id = f"prod-{category_prefix}-{uuid.uuid4().hex[:16]}"
                
                product = Product(product_id=product_id, **product_data)
                db.add(product)
                
                price = Price(product_id=product_id, price_cents=price_cents)
                db.add(price)
                
                inventory = Inventory(product_id=product_id, available_qty=available_qty)
                db.add(inventory)
                
                added += 1
                print(f"   Added: {product_data['name'][:50]}")
            
        except Exception as e:
            print(f"  [FAIL] Error processing {product_data.get('name', 'Unknown')}: {e}")
            skipped += 1
            continue
    
    db.commit()
    return added, skipped, updated


def main():
    """Main scraper runner."""
    print("="*80)
    print("SAFE PRODUCT SCRAPER RUNNER")
    print("Scraping from public APIs (no Terms of Service violations)")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        all_products = []
        
        # Scrape from safe sources
        products_fake = scrape_fakestoreapi()
        all_products.extend(products_fake)
        
        products_dummy = scrape_dummyjson()
        all_products.extend(products_dummy)
        
        print("\n" + "="*80)
        print("ADDING PRODUCTS TO DATABASE")
        print("="*80)
        print(f"Total products scraped: {len(all_products)}\n")
        
        added, skipped, updated = add_products_to_database(all_products, db)
        
        # Final stats
        total = db.query(Product).count()
        electronics = db.query(Product).filter(Product.category == 'Electronics').count()
        
        print("\n" + "="*80)
        print("SCRAPING SUMMARY")
        print("="*80)
        print(f" Added: {added} new products")
        print(f" Updated: {updated} existing products")
        print(f"⏭ Skipped: {skipped} (errors)")
        print(f"\nTotal Database:")
        print(f"   Total products: {total}")
        print(f"   Electronics: {electronics}")
        print(f"   Books: {db.query(Product).filter(Product.category == 'Books').count()}")
        print(f"   Other: {total - electronics - db.query(Product).filter(Product.category == 'Books').count()}")
        print("="*80)
        
        print("\n Scraping completed successfully!")
        print("Note: These are test products from public APIs.")
        print("For production, use official product feeds or APIs.")
        
    except Exception as e:
        print(f"\n[FAIL] Scraping failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    main()
