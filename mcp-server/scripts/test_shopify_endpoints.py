#!/usr/bin/env python3
"""
Test Real Shopify Store Public Endpoints

Tests public JSON endpoints from various Shopify stores to verify access
and understand the data structure.

Public Shopify endpoints typically available:
- /products.json - All products
- /collections.json - Product collections
- /collections/{handle}/products.json - Products in a collection

Run: python scripts/test_shopify_endpoints.py
"""

import requests
import json
from typing import Dict, Any, List, Optional
import time


# List of real Shopify stores to test (public endpoints only)
SHOPIFY_STORES = [
    {
        "name": "Allbirds",
        "domain": "allbirds.com",
        "description": "Sustainable footwear"
    },
    {
        "name": "Gymshark",
        "domain": "gymshark.com",
        "description": "Fitness apparel"
    },
    {
        "name": "ColourPop",
        "domain": "colourpop.com",
        "description": "Cosmetics"
    },
    {
        "name": "MVMT Watches",
        "domain": "mvmt.com",
        "description": "Watches and accessories"
    },
    {
        "name": "Kylie Cosmetics",
        "domain": "kyliecosmetics.com",
        "description": "Celebrity beauty brand"
    },
    {
        "name": "Fashion Nova",
        "domain": "fashionnova.com",
        "description": "Fast fashion"
    },
    {
        "name": "Tattly",
        "domain": "tattly.com",
        "description": "Temporary tattoos"
    },
    {
        "name": "Pura Vida",
        "domain": "puravidabracelets.com",
        "description": "Bracelets and accessories"
    }
]


def test_endpoint(domain: str, endpoint: str) -> Optional[Dict]:
    """Test a Shopify endpoint and return the response."""
    url = f"https://{domain}{endpoint}"
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/json'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return {
                "success": True,
                "status_code": response.status_code,
                "data": response.json(),
                "url": url
            }
        else:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": f"HTTP {response.status_code}",
                "url": url
            }
    
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Timeout", "url": url}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "Connection Error", "url": url}
    except Exception as e:
        return {"success": False, "error": str(e), "url": url}


def analyze_product_data(products: List[Dict]) -> Dict[str, Any]:
    """Analyze the structure of product data."""
    if not products:
        return {"error": "No products"}
    
    sample = products[0]
    
    return {
        "total_products": len(products),
        "sample_product": {
            "id": sample.get("id"),
            "title": sample.get("title"),
            "vendor": sample.get("vendor"),
            "product_type": sample.get("product_type"),
            "has_images": len(sample.get("images", [])) > 0,
            "has_variants": len(sample.get("variants", [])) > 0,
            "price_range": {
                "min": min([v.get("price", "0") for v in sample.get("variants", [])]),
                "max": max([v.get("price", "0") for v in sample.get("variants", [])])
            } if sample.get("variants") else None
        },
        "available_fields": list(sample.keys())
    }


def main():
    """Test Shopify stores."""
    print("="*80)
    print("TESTING REAL SHOPIFY STORE PUBLIC ENDPOINTS")
    print("="*80)
    print("\nTesting public JSON endpoints without authentication...")
    print("Note: Some stores may block or rate-limit requests.\n")
    
    results = []
    
    for i, store in enumerate(SHOPIFY_STORES):
        print(f"\n[{i+1}/{len(SHOPIFY_STORES)}] Testing {store['name']} ({store['description']})")
        print(f"    Domain: {store['domain']}")
        
        # Test /products.json
        result = test_endpoint(store['domain'], '/products.json')
        
        if result.get('success'):
            print(f"     SUCCESS - Products endpoint accessible!")
            
            # Analyze the data
            products = result['data'].get('products', [])
            analysis = analyze_product_data(products)
            
            print(f"     Found {analysis['total_products']} products")
            
            if 'sample_product' in analysis:
                sample = analysis['sample_product']
                print(f"    📝 Sample: \"{sample['title']}\" by {sample['vendor']}")
                print(f"    🏷️  Type: {sample['product_type']}")
            
            results.append({
                "store": store['name'],
                "domain": store['domain'],
                "success": True,
                "products_found": analysis['total_products'],
                "data_structure": analysis
            })
        else:
            print(f"    [FAIL] FAILED - {result.get('error', 'Unknown error')}")
            print(f"    URL: {result.get('url')}")
            
            results.append({
                "store": store['name'],
                "domain": store['domain'],
                "success": False,
                "error": result.get('error')
            })
        
        # Be respectful, don't hammer servers
        if i < len(SHOPIFY_STORES) - 1:
            time.sleep(2)
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    successful = [r for r in results if r['success']]
    failed = [r for r in results if not r['success']]
    
    print(f"\n Successful: {len(successful)}/{len(results)}")
    print(f"[FAIL] Failed: {len(failed)}/{len(results)}")
    
    if successful:
        print("\n Accessible Stores:")
        for r in successful:
            print(f"   - {r['store']} ({r['domain']}): {r['products_found']} products")
    
    if failed:
        print("\n[FAIL] Blocked/Unavailable Stores:")
        for r in failed:
            print(f"   - {r['store']} ({r['domain']}): {r['error']}")
    
    # Save results
    output_file = "shopify_test_results.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n💾 Detailed results saved to: {output_file}")
    
    # Recommendations
    print("\n" + "="*80)
    print("RECOMMENDATIONS")
    print("="*80)
    
    if successful:
        print("\n Next Steps:")
        print("   1. Use the accessible stores for integration")
        print("   2. Respect rate limits (add delays between requests)")
        print("   3. Consider Shopify Storefront API for official access")
        print("   4. Cache product data to minimize requests")
    else:
        print("\n[WARN]  All stores blocked public access.")
        print("   Options:")
        print("   1. Use Shopify Storefront API (requires API credentials)")
        print("   2. Join affiliate programs for official product feeds")
        print("   3. Contact stores for partnership opportunities")
    
    print("\n" + "="*80)


if __name__ == "__main__":
    main()
