"""
Test OR operations for filtering.

Tests:
1. Brand OR: "Dell OR HP laptop"
2. GPU Vendor OR: "NVIDIA or AMD graphics"
3. Use Case OR: "gaming or work laptop"
4. Combined: "Dell OR HP laptop under $2000"

Run from mcp-server directory:
    python test_or_operations.py
"""

import requests
import json
from typing import Dict, List, Any


MCP_BASE_URL = "http://localhost:8001"


def test_brand_or_operation(brands: List[str], query: str) -> Dict[str, Any]:
    """Test OR operation for brands."""
    print(f"\n{'='*80}")
    print(f"TEST: Brand OR Operation - '{query}'")
    print(f"Expected Brands: {' OR '.join(brands)}")
    print('='*80)
    
    url = f"{MCP_BASE_URL}/api/search-products"
    payload = {
        "query": query,
        "filters": {
            "category": "Electronics"
        },
        "limit": 20
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        products = data.get("data", {}).get("products", [])
        print(f"\n Status: {response.status_code}")
        print(f"Found {len(products)} products")
        
        # Count products by brand
        brand_counts = {}
        for product in products:
            brand = product.get("brand", "Unknown")
            brand_counts[brand] = brand_counts.get(brand, 0) + 1
        
        print(f"\nBrand Distribution:")
        for brand, count in sorted(brand_counts.items(), key=lambda x: -x[1]):
            status = "" if brand in brands else "[WARN]"
            print(f"  {status} {brand}: {count} products")
        
        # Check if we got products from all expected brands
        found_brands = set(brand_counts.keys())
        expected_brands = set(brands)
        missing = expected_brands - found_brands
        unexpected = found_brands - expected_brands
        
        if missing:
            print(f"\n[WARN] Missing brands: {', '.join(missing)}")
        if unexpected:
            print(f"\n[WARN] Unexpected brands (should only show {' OR '.join(brands)}): {', '.join(unexpected)}")
        
        if not missing and not unexpected:
            print(f"\n SUCCESS: All products are from {' OR '.join(brands)} only")
        
        # Show sample products
        if products:
            print(f"\nSample Products (first 5):")
            for i, product in enumerate(products[:5], 1):
                name = product.get("name", "Unknown")
                brand = product.get("brand", "Unknown")
                price = product.get("price_cents", 0) / 100
                print(f"  {i}. {name}")
                print(f"     Brand: {brand}, Price: ${price:.2f}")
        
        return {
            "status": "success",
            "count": len(products),
            "brands_found": list(found_brands),
            "correct": not missing and not unexpected
        }
        
    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        return {"status": "error", "error": str(e)}


def test_gpu_vendor_or_operation(vendors: List[str], query: str) -> Dict[str, Any]:
    """Test OR operation for GPU vendors."""
    print(f"\n{'='*80}")
    print(f"TEST: GPU Vendor OR Operation - '{query}'")
    print(f"Expected Vendors: {' OR '.join(vendors)}")
    print('='*80)
    
    url = f"{MCP_BASE_URL}/api/search-products"
    payload = {
        "query": query,
        "filters": {
            "category": "Electronics",
            "product_type": "gaming_laptop"
        },
        "limit": 20
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        products = data.get("data", {}).get("products", [])
        print(f"\n Status: {response.status_code}")
        print(f"Found {len(products)} products")
        
        # Count products by GPU vendor
        vendor_counts = {}
        for product in products:
            gpu_vendor = product.get("gpu_vendor") or product.get("metadata", {}).get("gpu_vendor", "Unknown")
            if gpu_vendor:
                vendor_counts[gpu_vendor] = vendor_counts.get(gpu_vendor, 0) + 1
        
        print(f"\nGPU Vendor Distribution:")
        for vendor, count in sorted(vendor_counts.items(), key=lambda x: -x[1]):
            status = "" if vendor in vendors else "[WARN]"
            print(f"  {status} {vendor}: {count} products")
        
        return {
            "status": "success",
            "count": len(products),
            "vendors_found": list(vendor_counts.keys())
        }
        
    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        return {"status": "error", "error": str(e)}


def test_combined_or_with_price(brands: List[str], max_price: int, query: str) -> Dict[str, Any]:
    """Test OR operation combined with price filter."""
    print(f"\n{'='*80}")
    print(f"TEST: Combined OR + Price Filter - '{query}'")
    print(f"Expected: {' OR '.join(brands)} laptops under ${max_price}")
    print('='*80)
    
    url = f"{MCP_BASE_URL}/api/search-products"
    payload = {
        "query": query,
        "filters": {
            "category": "Electronics",
            "price_max_cents": max_price * 100
        },
        "limit": 20
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        products = data.get("data", {}).get("products", [])
        print(f"\n Status: {response.status_code}")
        print(f"Found {len(products)} products")
        
        # Analyze results
        brand_counts = {}
        price_violations = []
        
        for product in products:
            brand = product.get("brand", "Unknown")
            price = product.get("price_cents", 0) / 100
            
            brand_counts[brand] = brand_counts.get(brand, 0) + 1
            
            if price > max_price:
                price_violations.append(f"{product.get('name')} (${price:.2f})")
        
        print(f"\nBrand Distribution:")
        for brand, count in sorted(brand_counts.items(), key=lambda x: -x[1]):
            status = "" if brand in brands else "[WARN]"
            print(f"  {status} {brand}: {count} products")
        
        if price_violations:
            print(f"\n[WARN] Price violations (over ${max_price}):")
            for violation in price_violations[:5]:
                print(f"  - {violation}")
        else:
            print(f"\n All products under ${max_price}")
        
        # Sample products
        if products:
            print(f"\nSample Products (first 5):")
            for i, product in enumerate(products[:5], 1):
                name = product.get("name", "Unknown")
                brand = product.get("brand", "Unknown")
                price = product.get("price_cents", 0) / 100
                print(f"  {i}. {name}")
                print(f"     Brand: {brand}, Price: ${price:.2f}")
        
        return {
            "status": "success",
            "count": len(products),
            "brands_found": list(brand_counts.keys()),
            "price_violations": len(price_violations)
        }
        
    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        return {"status": "error", "error": str(e)}


def run_all_or_tests():
    """Run all OR operation tests."""
    print("\n" + "="*80)
    print("OR OPERATION TESTING SUITE")
    print("Testing multiple value filters with OR logic")
    print("="*80)
    
    results = {}
    
    # Test 1: Dell OR HP
    result = test_brand_or_operation(
        ["Dell", "HP"],
        "Dell OR HP laptop"
    )
    results["dell_or_hp"] = result
    
    # Test 2: ASUS OR Lenovo
    result = test_brand_or_operation(
        ["ASUS", "Lenovo"],
        "ASUS or Lenovo laptop"
    )
    results["asus_or_lenovo"] = result
    
    # Test 3: Apple OR Microsoft
    result = test_brand_or_operation(
        ["Apple", "Microsoft"],
        "Apple OR Microsoft devices"
    )
    results["apple_or_microsoft"] = result
    
    # Test 4: NVIDIA OR AMD (GPU vendors)
    result = test_gpu_vendor_or_operation(
        ["NVIDIA", "AMD"],
        "NVIDIA or AMD gaming laptop"
    )
    results["nvidia_or_amd"] = result
    
    # Test 5: Combined - Dell OR HP under $2000
    result = test_combined_or_with_price(
        ["Dell", "HP"],
        2000,
        "Dell OR HP laptop under $2000"
    )
    results["combined_price"] = result
    
    # Test 6: Three brands
    result = test_brand_or_operation(
        ["Dell", "HP", "Lenovo"],
        "Dell OR HP OR Lenovo laptop"
    )
    results["three_brands"] = result
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    total_tests = len(results)
    successful = sum(1 for r in results.values() if r.get("status") == "success")
    correct = sum(1 for r in results.values() if r.get("correct") == True)
    failed = sum(1 for r in results.values() if r.get("status") == "error")
    
    print(f"Total Tests: {total_tests}")
    print(f" Successful: {successful}")
    print(f" Correct Filtering: {correct}")
    print(f"[FAIL] Failed: {failed}")
    print(f"Success Rate: {(successful/total_tests)*100:.1f}%")
    
    if failed > 0:
        print("\nFailed Tests:")
        for name, result in results.items():
            if result.get("status") == "error":
                print(f"  - {name}: {result.get('error', 'Unknown error')}")
    
    print("="*80)
    
    return results


if __name__ == "__main__":
    run_all_or_tests()
