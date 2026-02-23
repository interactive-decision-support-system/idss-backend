#!/usr/bin/env python3
"""
Shopify Store Integration

Integrates real products from Shopify stores into the database.
Uses public JSON endpoints from verified accessible stores.

Features:
- Fetches products from multiple Shopify stores
- Normalizes product data to our schema
- Adds products to database with proper relationships
- Respects rate limits and handles errors gracefully

Run: python scripts/shopify_integration.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product, Price, Inventory
import requests
import json
import time
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime


# Verified accessible Shopify stores (from test results)
ACCESSIBLE_STORES = [
    {
        "name": "Allbirds",
        "domain": "allbirds.com",
        "category": "Clothing",
        "subcategory": "Footwear"
    },
    {
        "name": "Gymshark",
        "domain": "gymshark.com",
        "category": "Clothing",
        "subcategory": "Fitness Apparel"
    },
    {
        "name": "ColourPop",
        "domain": "colourpop.com",
        "category": "Beauty",
        "subcategory": "Cosmetics"
    },
    {
        "name": "Kylie Cosmetics",
        "domain": "kyliecosmetics.com",
        "category": "Beauty",
        "subcategory": "Cosmetics"
    },
    {
        "name": "Fashion Nova",
        "domain": "fashionnova.com",
        "category": "Clothing",
        "subcategory": "Fashion"
    },
    {
        "name": "Tattly",
        "domain": "tattly.com",
        "category": "Art",
        "subcategory": "Temporary Tattoos"
    },
    {
        "name": "Pura Vida",
        "domain": "puravidabracelets.com",
        "category": "Accessories",
        "subcategory": "Jewelry"
    }
]


class ShopifyIntegration:
    """Handles Shopify store integration."""
    
    def __init__(self, db_session):
        self.db = db_session
        self.request_delay = 2  # seconds between requests
        self.stats = {
            "fetched": 0,
            "added": 0,
            "skipped": 0,
            "errors": 0
        }
    
    def fetch_products(self, domain: str, limit: Optional[int] = None) -> List[Dict]:
        """Fetch products from a Shopify store."""
        url = f"https://{domain}/products.json"
        
        if limit:
            url += f"?limit={limit}"
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                products = data.get('products', [])
                self.stats['fetched'] += len(products)
                return products
            else:
                print(f"    [FAIL] HTTP {response.status_code}")
                return []
        
        except Exception as e:
            print(f"    [FAIL] Error: {e}")
            self.stats['errors'] += 1
            return []
    
    def normalize_product(self, shopify_product: Dict, store_info: Dict) -> Optional[Dict]:
        """Convert Shopify product to our schema."""
        try:
            # Get the first variant for pricing
            variants = shopify_product.get('variants', [])
            if not variants:
                return None
            
            first_variant = variants[0]
            
            # Get price (convert from string to float)
            price_str = first_variant.get('price', '0')
            try:
                price = float(price_str)
            except:
                price = 0.0
            
            # Get image URL
            images = shopify_product.get('images', [])
            image_url = images[0].get('src', '') if images else ''
            
            # Check availability
            available = first_variant.get('available', False)
            
            # Get inventory quantity
            inventory_qty = first_variant.get('inventory_quantity', 0)
            if inventory_qty is None:
                inventory_qty = 100 if available else 0
            
            # Handle tags (can be string or list)
            tags_field = shopify_product.get('tags', [])
            if isinstance(tags_field, str):
                tags = [t.strip() for t in tags_field.split(',') if t.strip()]
            elif isinstance(tags_field, list):
                tags = tags_field
            else:
                tags = []
            
            # Build normalized product
            normalized = {
                "shopify_id": str(shopify_product.get('id')),
                "name": shopify_product.get('title', 'Unknown'),
                "brand": shopify_product.get('vendor', store_info['name']),
                "description": shopify_product.get('body_html', ''),
                "price": price,
                "category": store_info['category'],
                "subcategory": store_info['subcategory'],
                "product_type": shopify_product.get('product_type', ''),
                "image_url": image_url,
                "available": available,
                "inventory_qty": max(0, inventory_qty),
                "tags": tags,
                "variants_count": len(variants),
                "created_at": shopify_product.get('created_at'),
                "shopify_handle": shopify_product.get('handle', ''),
                "store_domain": store_info['domain']
            }
            
            return normalized
        
        except Exception as e:
            print(f"    [WARN]  Normalization error: {e}")
            self.stats['errors'] += 1
            return None
    
    def add_to_database(self, normalized: Dict) -> bool:
        """Add normalized product to database."""
        try:
            # Check if already exists using source_product_id
            # Format: shopify:{domain}:{shopify_id}
            source_id = f"shopify:{normalized['store_domain']}:{normalized['shopify_id']}"
            
            existing = self.db.query(Product).filter(
                Product.source_product_id == source_id
            ).first()
            
            if existing:
                self.stats['skipped'] += 1
                return False
            
            # Create product
            product_id = str(uuid.uuid4())
            
            product = Product(
                product_id=product_id,
                name=normalized["name"],
                brand=normalized["brand"],
                description=normalized["description"][:500] if normalized["description"] else "",
                category=normalized["category"],
                subcategory=normalized["subcategory"],
                product_type=normalized["product_type"] or "shopify_product",
                image_url=normalized["image_url"],
                source="Shopify",
                source_product_id=source_id,
                scraped_from_url=f"https://{normalized['store_domain']}/products/{normalized['shopify_handle']}",
                tags=normalized["tags"] if normalized["tags"] else None
            )
            
            # Create price (price_id is auto-increment)
            price = Price(
                product_id=product_id,
                price_cents=int(normalized["price"] * 100),
                currency="USD"
            )
            
            # Create inventory
            inventory = Inventory(
                product_id=product_id,
                available_qty=normalized["inventory_qty"],
                reserved_qty=0
            )
            
            # Add to database
            self.db.add(product)
            self.db.add(price)
            self.db.add(inventory)
            
            self.stats['added'] += 1
            return True
        
        except Exception as e:
            print(f"    [WARN]  Database error: {e}")
            self.stats['errors'] += 1
            return False
    
    def integrate_store(self, store: Dict, products_per_store: int = 20):
        """Integrate products from a single store."""
        print(f"\n{'='*60}")
        print(f" {store['name']} ({store['domain']})")
        print(f"{'='*60}")
        
        # Fetch products
        print(f" Fetching products...")
        products = self.fetch_products(store['domain'], limit=products_per_store)
        
        if not products:
            print(f"[WARN]  No products fetched")
            return
        
        print(f" Fetched {len(products)} products")
        
        # Process each product
        print(f" Processing products...")
        added_count = 0
        
        for i, shopify_product in enumerate(products, 1):
            # Normalize
            normalized = self.normalize_product(shopify_product, store)
            if not normalized:
                continue
            
            # Add to database
            if self.add_to_database(normalized):
                added_count += 1
                if added_count <= 3:  # Show first 3
                    print(f"    {normalized['name']} (${normalized['price']:.2f})")
        
        # Commit changes
        self.db.commit()
        
        print(f"\n Store Summary:")
        print(f"   Added: {added_count}")
        print(f"   Skipped: {len(products) - added_count}")


def main():
    """Main integration function."""
    print("="*80)
    print("SHOPIFY STORE INTEGRATION")
    print("="*80)
    print("\nIntegrating real products from Shopify stores...")
    print("This will add products to the database with proper metadata.\n")
    
    db = SessionLocal()
    integration = ShopifyIntegration(db)
    
    try:
        # Get initial counts
        initial_count = db.query(Product).count()
        print(f" Initial product count: {initial_count}\n")
        
        # Integrate each store
        for i, store in enumerate(ACCESSIBLE_STORES):
            integration.integrate_store(store, products_per_store=15)
            
            # Rate limiting - be respectful
            if i < len(ACCESSIBLE_STORES) - 1:
                print(f"\n⏳ Waiting {integration.request_delay}s before next store...")
                time.sleep(integration.request_delay)
        
        # Final statistics
        final_count = db.query(Product).count()
        
        print("\n" + "="*80)
        print("INTEGRATION COMPLETE")
        print("="*80)
        
        print(f"\n Statistics:")
        print(f"   Products fetched: {integration.stats['fetched']}")
        print(f"   Products added: {integration.stats['added']}")
        print(f"   Products skipped: {integration.stats['skipped']}")
        print(f"   Errors: {integration.stats['errors']}")
        
        print(f"\n Database Growth:")
        print(f"   Before: {initial_count}")
        print(f"   After: {final_count}")
        print(f"   Added: +{final_count - initial_count}")
        
        # Show sample products
        print("\n" + "="*80)
        print("SAMPLE SHOPIFY PRODUCTS")
        print("="*80)
        
        shopify_products = db.query(Product).filter(
            Product.source == "Shopify"
        ).limit(5).all()
        
        for prod in shopify_products:
            price_obj = db.query(Price).filter(Price.product_id == prod.product_id).first()
            price = price_obj.price_cents / 100 if price_obj else 0
            
            # Extract store domain from source_product_id (format: shopify:domain:id)
            store_domain = "Unknown"
            if prod.source_product_id:
                parts = prod.source_product_id.split(':')
                if len(parts) >= 2:
                    store_domain = parts[1]
            
            print(f"\n {prod.name}")
            print(f"   Brand: {prod.brand}")
            print(f"   Category: {prod.category} > {prod.subcategory}")
            print(f"   Price: ${price:.2f}")
            print(f"   Store: {store_domain}")
        
        print("\n" + "="*80)
        print(" SHOPIFY INTEGRATION SUCCESSFUL")
        print("="*80)
        
    except Exception as e:
        print(f"\n[FAIL] Integration failed: {e}")
        db.rollback()
    
    finally:
        db.close()


if __name__ == "__main__":
    main()
