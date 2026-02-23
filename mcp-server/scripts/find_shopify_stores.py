#!/usr/bin/env python3
"""
Shopify Store Finder and Product Fetcher

Finds Shopify stores and fetches products using their public JSON endpoints.
Many Shopify stores expose /products.json - this is a public endpoint.

Run: python scripts/find_shopify_stores.py
"""

import requests
import json
from typing import List, Dict, Any, Optional


def check_shopify_store(domain: str) -> bool:
    """
    Check if a domain is a Shopify store.
    
    Args:
        domain: Domain to check (e.g., "example.com")
        
    Returns:
        True if it's a Shopify store
    """
    try:
        # Try to access /products.json endpoint
        url = f"https://{domain}/products.json?limit=1"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            return "products" in data
        
        return False
    except Exception:
        return False


def fetch_shopify_products(domain: str, max_products: int = 50) -> List[Dict[str, Any]]:
    """
    Fetch products from a Shopify store using public JSON endpoint.
    
    Args:
        domain: Shopify store domain
        max_products: Maximum products to fetch
        
    Returns:
        List of products
    """
    products = []
    page = 1
    
    print(f"\nFetching from {domain}...")
    
    while len(products) < max_products:
        try:
            url = f"https://{domain}/products.json?limit=50&page={page}"
            response = requests.get(url, timeout=10)
            
            if response.status_code != 200:
                print(f"  [WARN] Status {response.status_code} on page {page}")
                break
            
            data = response.json()
            page_products = data.get("products", [])
            
            if not page_products:
                break  # No more products
            
            products.extend(page_products)
            print(f"   Page {page}: {len(page_products)} products (total: {len(products)})")
            
            page += 1
            
            if len(page_products) < 50:
                break  # Last page
            
        except Exception as e:
            print(f"  [FAIL] Error on page {page}: {e}")
            break
    
    return products[:max_products]


def convert_shopify_to_mcp_format(shopify_product: Dict[str, Any], source_domain: str) -> Dict[str, Any]:
    """
    Convert Shopify product to MCP format.
    
    Args:
        shopify_product: Shopify product data
        source_domain: Source domain
        
    Returns:
        MCP-compatible product dict
    """
    # Get first variant for pricing
    variants = shopify_product.get("variants", [])
    first_variant = variants[0] if variants else {}
    
    # Get price in cents
    price = first_variant.get("price", "0")
    try:
        price_cents = int(float(price) * 100)
    except (ValueError, TypeError):
        price_cents = 0
    
    # Get image
    images = shopify_product.get("images", [])
    image_url = images[0].get("src") if images else ""
    
    # Get inventory
    inventory = first_variant.get("inventory_quantity", 0)
    
    # Determine category from tags or product_type
    product_type = shopify_product.get("product_type", "")
    tags = shopify_product.get("tags", [])
    
    category = "General"
    if "electronics" in product_type.lower() or any("tech" in t.lower() for t in tags):
        category = "Electronics"
    elif "book" in product_type.lower():
        category = "Books"
    elif any(word in product_type.lower() for word in ["clothing", "apparel", "fashion"]):
        category = "Clothing"
    
    return {
        "name": shopify_product.get("title", "Unknown Product"),
        "description": shopify_product.get("body_html", ""),
        "category": category,
        "brand": shopify_product.get("vendor", ""),
        "price_cents": price_cents,
        "available_qty": max(0, inventory),
        "image_url": image_url,
        "source": f"Shopify:{source_domain}",
        "source_product_id": str(shopify_product.get("id", "")),
        "scraped_from_url": f"https://{source_domain}/products/{shopify_product.get('handle', '')}",
        "product_type": product_type,
        "tags": tags if isinstance(tags, list) else [],
        "metadata": json.dumps({
            "shopify_id": shopify_product.get("id"),
            "handle": shopify_product.get("handle"),
            "created_at": shopify_product.get("created_at"),
            "updated_at": shopify_product.get("updated_at"),
            "variants_count": len(variants)
        })
    }


# Curated list of Shopify stores with public product endpoints
SHOPIFY_STORES = [
    # Demo/Test stores (100% safe)
    "shopify-demo.myshopify.com",
    
    # Add more stores here as you discover them
    # Note: Only add stores you have permission to access
]


def main():
    """Main function to fetch from Shopify stores."""
    print("="*80)
    print("SHOPIFY STORE PRODUCT FETCHER")
    print("Uses public /products.json endpoints")
    print("="*80)
    
    print("\n[WARN] IMPORTANT:")
    print("  - Only fetch from stores you have permission to access")
    print("  - Many stores have Terms of Service against automated access")
    print("  - Consider using official APIs or affiliate programs instead")
    print("  - This tool is for educational/testing purposes")
    
    print("\n" + "="*80)
    print("TESTING STORES")
    print("="*80)
    
    all_products = []
    
    for domain in SHOPIFY_STORES:
        print(f"\nChecking {domain}...")
        
        is_shopify = check_shopify_store(domain)
        
        if is_shopify:
            print(f"   Valid Shopify store")
            
            products = fetch_shopify_products(domain, max_products=10)
            
            if products:
                print(f"   Fetched {len(products)} products")
                
                # Convert to MCP format
                for sp in products:
                    mcp_product = convert_shopify_to_mcp_format(sp, domain)
                    all_products.append(mcp_product)
            else:
                print(f"  [WARN] No products found")
        else:
            print(f"  [FAIL] Not accessible or not a Shopify store")
    
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"Total products fetched: {len(all_products)}")
    
    if all_products:
        print("\nSample products:")
        for i, p in enumerate(all_products[:5], 1):
            print(f"  {i}. {p['name']} - ${p['price_cents']/100:.2f}")
            print(f"     Source: {p['source']}")
    
    print("\n To add more stores:")
    print("  1. Find Shopify stores (look for .myshopify.com or Shopify powered sites)")
    print("  2. Check if /products.json is accessible")
    print("  3. Add to SHOPIFY_STORES list")
    print("  4. Re-run this script")
    
    print("\n" + "="*80)


if __name__ == "__main__":
    main()
