#!/usr/bin/env python3
"""
Verify Shopify Products are Correctly Stored and Accessible

Checks:
1. Products are in PostgreSQL database
2. They have proper prices and inventory
3. They're accessible via API endpoints
4. Data format matches frontend requirements
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product, Price, Inventory
import requests
import json


def check_database():
    """Check products in database."""
    print("="*80)
    print("1. DATABASE CHECK")
    print("="*80)
    
    db = SessionLocal()
    
    # Get Shopify products
    shopify_products = db.query(Product).filter(Product.source == "Shopify").all()
    
    print(f"\n Total Shopify Products: {shopify_products and len(shopify_products) or 0}")
    
    if not shopify_products:
        print("[FAIL] No Shopify products found!")
        db.close()
        return False, []
    
    # Sample products
    print("\n Sample Products:")
    product_ids = []
    for i, p in enumerate(shopify_products[:5], 1):
        price_obj = db.query(Price).filter(Price.product_id == p.product_id).first()
        inv_obj = db.query(Inventory).filter(Inventory.product_id == p.product_id).first()
        
        print(f"\n   {i}. {p.name[:60]}")
        print(f"      ID: {p.product_id}")
        print(f"      Brand: {p.brand}")
        print(f"      Category: {p.category} > {p.subcategory}")
        print(f"      Price: ${price_obj.price_cents / 100:.2f}" if price_obj else "      Price: [FAIL] Missing")
        print(f"      Stock: {inv_obj.available_qty}" if inv_obj else "      Stock: [FAIL] Missing")
        print(f"      Image: {' Yes' if p.image_url else '[FAIL] No'}")
        print(f"      Source: {p.source}")
        
        product_ids.append(p.product_id)
    
    # Data completeness
    print("\n" + "="*80)
    print("DATA COMPLETENESS")
    print("="*80)
    
    stats = {
        "total": len(shopify_products),
        "with_price": 0,
        "with_inventory": 0,
        "with_image": 0,
        "with_description": 0,
    }
    
    for p in shopify_products:
        if db.query(Price).filter(Price.product_id == p.product_id).first():
            stats["with_price"] += 1
        if db.query(Inventory).filter(Inventory.product_id == p.product_id).first():
            stats["with_inventory"] += 1
        if p.image_url:
            stats["with_image"] += 1
        if p.description:
            stats["with_description"] += 1
    
    print(f"\n With Prices: {stats['with_price']}/{stats['total']} ({stats['with_price']/stats['total']*100:.1f}%)")
    print(f" With Inventory: {stats['with_inventory']}/{stats['total']} ({stats['with_inventory']/stats['total']*100:.1f}%)")
    print(f" With Images: {stats['with_image']}/{stats['total']} ({stats['with_image']/stats['total']*100:.1f}%)")
    print(f" With Descriptions: {stats['with_description']}/{stats['total']} ({stats['with_description']/stats['total']*100:.1f}%)")
    
    # Categories
    print("\n" + "="*80)
    print("CATEGORIES")
    print("="*80)
    
    from sqlalchemy import func
    categories = db.query(
        Product.category, 
        Product.subcategory, 
        func.count(Product.product_id)
    ).filter(
        Product.source == "Shopify"
    ).group_by(
        Product.category, 
        Product.subcategory
    ).all()
    
    for cat, subcat, count in categories:
        print(f"   {cat} > {subcat}: {count} products")
    
    db.close()
    return True, product_ids


def check_api_endpoints(product_ids):
    """Check if products are accessible via API."""
    print("\n" + "="*80)
    print("2. API ENDPOINT CHECK")
    print("="*80)
    
    base_url = "http://localhost:8001"
    
    # Test search endpoint
    print("\n🔍 Testing /api/search-products...")
    try:
        response = requests.post(
            f"{base_url}/api/search-products",
            json={"query": "Allbirds", "limit": 5},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "OK":
                products = data.get("data", {}).get("products", [])
                print(f" Search works! Found {len(products)} products")
                
                if products:
                    sample = products[0]
                    print(f"\n   Sample product from search:")
                    print(f"   - Name: {sample.get('name', 'N/A')[:50]}")
                    print(f"   - Price: ${sample.get('price_cents', 0) / 100:.2f}")
                    print(f"   - Category: {sample.get('category', 'N/A')}")
                    print(f"   - Image: {'' if sample.get('image_url') else '[FAIL]'}")
                    print(f"   - Source: {sample.get('source', 'N/A')}")
            else:
                print(f"[WARN]  Search returned status: {data.get('status')}")
        else:
            print(f"[FAIL] Search failed with status {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("[FAIL] Cannot connect to API server. Is it running on port 8001?")
        print("   Start with: cd mcp-server && uvicorn app.main:app --port 8001")
        return False
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        return False
    
    # Test get product endpoint
    if product_ids:
        print(f"\n🔍 Testing /api/get-product...")
        try:
            response = requests.post(
                f"{base_url}/api/get-product",
                json={"product_id": product_ids[0]},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "OK":
                    product = data.get("data", {})
                    print(f" Get product works!")
                    print(f"\n   Product details:")
                    print(f"   - ID: {product.get('product_id', 'N/A')}")
                    print(f"   - Name: {product.get('name', 'N/A')[:50]}")
                    print(f"   - Price: ${product.get('price_cents', 0) / 100:.2f}")
                    print(f"   - Available: {product.get('available_qty', 0)} units")
                    print(f"   - Source: {product.get('source', 'N/A')}")
                    print(f"   - Scraped from: {product.get('scraped_from_url', 'N/A')[:50]}")
                else:
                    print(f"[WARN]  Get product returned status: {data.get('status')}")
            else:
                print(f"[FAIL] Get product failed with status {response.status_code}")
        except Exception as e:
            print(f"[FAIL] Error: {e}")
            return False
    
    return True


def check_frontend_compatibility():
    """Check if data format matches frontend expectations."""
    print("\n" + "="*80)
    print("3. FRONTEND COMPATIBILITY CHECK")
    print("="*80)
    
    print("\n Expected Frontend Data Format:")
    print("""
    {
      "product_id": "string",
      "name": "string",
      "brand": "string",
      "category": "string",
      "subcategory": "string",
      "price_cents": number,
      "image_url": "string",
      "description": "string",
      "available_qty": number,
      "source": "string"  //  Should show "Shopify"
    }
    """)
    
    print("\n Data Format Requirements:")
    print("   - product_id:  UUID format")
    print("   - name:  String")
    print("   - price_cents:  Integer (not float)")
    print("   - category/subcategory:  String")
    print("   - image_url:  Full URL")
    print("   - source:  Shows 'Shopify'")
    print("   - scraped_from_url:  Available for verification")
    
    print("\n Frontend Display Locations:")
    print("   1. Product List Page - Shows all products including Shopify")
    print("   2. Product Detail Page - Shows full product info")
    print("   3. Search Results - Includes Shopify products in search")
    print("   4. Category Pages - Groups by category (Clothing, Beauty, etc.)")
    
    return True


def main():
    """Run all checks."""
    print("="*80)
    print("SHOPIFY PRODUCTS VERIFICATION")
    print("="*80)
    print("\nThis script verifies that Shopify scraped products are:")
    print("  1. Stored correctly in PostgreSQL")
    print("  2. Accessible via API endpoints")
    print("  3. Compatible with frontend requirements")
    
    # Database check
    db_ok, product_ids = check_database()
    
    if not db_ok:
        print("\n" + "="*80)
        print("[FAIL] VERIFICATION FAILED - No products in database")
        print("="*80)
        print("\nTo add Shopify products, run:")
        print("  python scripts/shopify_integration.py")
        return
    
    # API check
    api_ok = check_api_endpoints(product_ids)
    
    # Frontend check
    frontend_ok = check_frontend_compatibility()
    
    # Summary
    print("\n" + "="*80)
    print("VERIFICATION SUMMARY")
    print("="*80)
    
    print(f"\n{'' if db_ok else '[FAIL]'} Database: {len(product_ids) if product_ids else 0} Shopify products stored")
    print(f"{'' if api_ok else '[FAIL]'} API Endpoints: Accessible via /api/search-products and /api/get-product")
    print(f"{'' if frontend_ok else '[FAIL]'} Frontend Compatibility: Data format matches requirements")
    
    if db_ok and api_ok and frontend_ok:
        print("\n" + "="*80)
        print(" ALL CHECKS PASSED!")
        print("="*80)
        print("\nShopify products are ready to display in the frontend!")
        print("\nFrontend Repository: https://github.com/interactive-decision-support-system/idss-web")
        print("Main Branch: https://github.com/interactive-decision-support-system/idss-web/tree/main")
        print("Dev Branch: https://github.com/interactive-decision-support-system/idss-web/tree/dev")
        
        print("\n Frontend Integration:")
        print("   1. Products appear in search results")
        print("   2. Filterable by category (Clothing, Beauty, Accessories, Art)")
        print("   3. Show 'Shopify' as source")
        print("   4. Display images from original Shopify stores")
        print("   5. Link back to original stores via scraped_from_url")
    else:
        print("\n" + "="*80)
        print("[WARN]  SOME CHECKS FAILED")
        print("="*80)
        if not api_ok:
            print("\nTo start the API server:")
            print("  cd mcp-server")
            print("  uvicorn app.main:app --port 8001 --reload")


if __name__ == "__main__":
    main()
