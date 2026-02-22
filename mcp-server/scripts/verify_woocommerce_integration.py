#!/usr/bin/env python3
"""
Verify WooCommerce Integration

Check that WooCommerce products were successfully integrated.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product, Price, Inventory
import json


def main():
    """Verify WooCommerce integration."""
    print("="*80)
    print("WOOCOMMERCE INTEGRATION VERIFICATION")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        # Get WooCommerce products
        woo_products = db.query(Product).filter(Product.source == "WooCommerce").all()
        
        print(f"\n Total WooCommerce products: {len(woo_products)}")
        
        if not woo_products:
            print("[FAIL] No WooCommerce products found!")
            return
        
        # Check prices
        woo_with_prices = sum(1 for p in woo_products if p.price_info)
        print(f" Products with prices: {woo_with_prices}/{len(woo_products)}")
        
        # Check inventory
        woo_with_inventory = sum(1 for p in woo_products if p.inventory_info)
        print(f" Products with inventory: {woo_with_inventory}/{len(woo_products)}")
        
        # Check reviews
        woo_with_reviews = sum(1 for p in woo_products if p.reviews)
        print(f" Products with reviews: {woo_with_reviews}/{len(woo_products)}")
        
        # Check brands
        brands = set(p.brand for p in woo_products if p.brand)
        print(f"  Unique brands: {len(brands)}")
        print(f"   Brands: {', '.join(sorted(brands))}")
        
        # Check categories
        categories = {}
        for p in woo_products:
            cat = p.category or 'Unknown'
            categories[cat] = categories.get(cat, 0) + 1
        
        print(f"\n Categories:")
        for cat, count in sorted(categories.items()):
            print(f"   {cat}: {count}")
        
        # Show sample products
        print(f"\n Sample Products:")
        for i, p in enumerate(woo_products[:5], 1):
            price = f"${p.price_info.price_cents/100:.2f}" if p.price_info else "N/A"
            stock = p.inventory_info.available_qty if p.inventory_info else 0
            reviews_count = 0
            if p.reviews:
                try:
                    reviews = json.loads(p.reviews)
                    reviews_count = len(reviews) if isinstance(reviews, list) else 0
                except:
                    pass
            
            print(f"   {i}. {p.name[:50]:<50} {price:>8} | Stock: {stock:>3} | Reviews: {reviews_count}")
        
        # Validation
        print(f"\n{'='*80}")
        print("VALIDATION:")
        all_good = True
        
        if len(woo_products) >= 10:
            print(f"   Product count OK ({len(woo_products)} >= 10)")
        else:
            print(f"  [WARN]  Low product count ({len(woo_products)} < 10)")
            all_good = False
        
        if woo_with_prices == len(woo_products):
            print(f"   All products have prices")
        else:
            print(f"  [FAIL] Missing prices: {len(woo_products) - woo_with_prices}")
            all_good = False
        
        if woo_with_inventory == len(woo_products):
            print(f"   All products have inventory")
        else:
            print(f"  [FAIL] Missing inventory: {len(woo_products) - woo_with_inventory}")
            all_good = False
        
        if woo_with_reviews == len(woo_products):
            print(f"   All products have reviews")
        else:
            print(f"  [WARN]  Missing reviews: {len(woo_products) - woo_with_reviews}")
        
        if all_good:
            print(f"\n WooCommerce integration is PERFECT!")
        else:
            print(f"\n[WARN]  Some issues found, but integration is functional")
        
    finally:
        db.close()
    
    print("="*80)


if __name__ == "__main__":
    main()
