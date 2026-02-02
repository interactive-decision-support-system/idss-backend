"""
Integration Test Script for Universal Product Adapter

This script tests:
1. IDSS backend connection
2. Vehicle data transformation
3. 2D grid flattening
4. Product metadata enrichment
5. End-to-end search flow

Usage:
    python test_integration.py
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.idss_adapter import (
    search_products_idss,
    get_product_idss,
    vehicle_to_product_summary,
    vehicle_to_product_detail,
)
from app.schemas import SearchProductsRequest, GetProductRequest


# ============================================================================
# Test Data
# ============================================================================

SAMPLE_VEHICLE = {
    "vehicle": {
        "vin": "1HGBH41JXMN109186",
        "year": 2020,
        "make": "Honda",
        "model": "Accord",
        "trim": "Sport",
        "bodyStyle": "Sedan",
        "drivetrain": "FWD",
        "fuel": "Gasoline",
        "transmission": "CVT",
        "exteriorColor": "Blue",
        "mileage": 35000,
    },
    "retailListing": {
        "price": 22500,
        "miles": 35000,
        "dealer": "Downtown Honda",
        "city": "San Francisco",
        "state": "CA",
        "primaryImage": "https://example.com/image.jpg",
        "vdp": "https://example.com/listing",
    }
}


# ============================================================================
# Unit Tests
# ============================================================================

def test_vehicle_to_product_summary():
    """Test vehicle transformation to product summary."""
    print("\nTest 1: Vehicle → Product Summary")
    print("=" * 60)
    
    product = vehicle_to_product_summary(SAMPLE_VEHICLE)
    
    # Verify basic fields
    assert product.product_id.startswith("VIN-"), "Product ID should have VIN- prefix"
    assert "Honda" in product.name and "Accord" in product.name, f"Name should contain Honda Accord: {product.name}"
    assert product.price_cents == 2250000, f"Price incorrect: {product.price_cents}"
    assert product.category == "Sedan", f"Category incorrect: {product.category}"
    assert product.brand == "Honda", f"Brand incorrect: {product.brand}"
    assert product.available_qty == 1, "Vehicles should have qty=1"
    
    # Verify metadata
    assert product.product_type == "vehicle", "Product type should be 'vehicle'"
    assert product.metadata is not None, "Metadata should exist"
    assert "vin" in product.metadata, "Metadata should include VIN"
    assert product.metadata["mileage"] == 35000, "Metadata should include mileage"
    
    print("[OK] Product Summary transformation correct")
    print(f"   ID: {product.product_id}")
    print(f"   Name: {product.name}")
    print(f"   Price: ${product.price_cents / 100:,.2f}")
    print(f"   Category: {product.category}")
    print(f"   Metadata: {product.metadata}")


def test_vehicle_to_product_detail():
    """Test vehicle transformation to product detail."""
    print("\nTest 2: Vehicle → Product Detail")
    print("=" * 60)
    
    product = vehicle_to_product_detail(SAMPLE_VEHICLE)
    
    # Verify basic fields
    assert product.product_id.startswith("VIN-"), "Product ID should have VIN- prefix"
    assert "Honda" in product.name, "Name should include make"
    assert product.description is not None, "Description should exist"
    assert len(product.description) > 0, "Description should not be empty"
    
    # Verify metadata enrichment
    assert product.product_type == "vehicle", "Product type should be 'vehicle'"
    assert product.metadata is not None, "Metadata should exist"
    
    # Check rich metadata
    metadata = product.metadata
    assert "vin" in metadata, "Should have VIN"
    assert "make" in metadata, "Should have make"
    assert "model" in metadata, "Should have model"
    assert "fuel_type" in metadata, "Should have fuel type"
    assert "drivetrain" in metadata, "Should have drivetrain"
    assert "dealer" in metadata, "Should have dealer"
    assert "location" in metadata, "Should have location"
    assert "primary_image" in metadata, "Should have image URL"
    assert "listing_url" in metadata, "Should have listing URL"
    
    print("[OK] Product Detail transformation correct")
    print(f"   ID: {product.product_id}")
    print(f"   Name: {product.name}")
    print(f"   Description: {product.description}")
    print(f"   Metadata fields: {list(metadata.keys())}")


def test_flat_format_vehicle():
    """Test transformation with flat (non-nested) vehicle data."""
    print("\nTest 3: Flat Format Vehicle")
    print("=" * 60)
    
    flat_vehicle = {
        "vin": "1HGBH41JXMN109186",
        "year": 2020,
        "make": "Toyota",
        "model": "Camry",
        "body_style": "Sedan",
        "price": 23000,
        "mileage": 40000,
    }
    
    product = vehicle_to_product_summary(flat_vehicle)
    
    assert product.name == "2020 Toyota Camry", f"Name incorrect: {product.name}"
    assert product.price_cents == 2300000, f"Price incorrect: {product.price_cents}"
    assert product.brand == "Toyota", f"Brand incorrect: {product.brand}"
    
    print("[OK] Flat format transformation works")
    print(f"   Name: {product.name}")
    print(f"   Price: ${product.price_cents / 100:,.2f}")


# ============================================================================
# Integration Tests
# ============================================================================

async def test_search_vehicles():
    """Test live search against IDSS backend."""
    print("\nTest 4: Live Search - IDSS Backend")
    print("=" * 60)
    
    try:
        request = SearchProductsRequest(
            query="affordable sedan",
            limit=5
        )
        
        print(f"Searching: '{request.query}'")
        print("   (This calls IDSS backend on port 8000)")
        
        response = await search_products_idss(request)
        
        # Verify response structure
        assert response.status == "OK", f"Status should be OK, got: {response.status}"
        assert response.data is not None, "Data should exist"
        assert isinstance(response.data.products, list), "Products should be a list"
        assert response.trace is not None, "Trace should exist"
        assert response.version is not None, "Version should exist"
        
        # Check products
        products = response.data.products
        print(f"\n[OK] Search successful: {len(products)} vehicles found")
        
        if products:
            print("\n   Sample results:")
            for i, p in enumerate(products[:3], 1):
                print(f"   {i}. {p.name}")
                print(f"      Price: ${p.price_cents / 100:,.2f}")
                print(f"      Category: {p.category}")
                print(f"      Type: {p.product_type}")
                if p.metadata:
                    print(f"      Metadata: VIN={p.metadata.get('vin', 'N/A')[:10]}...")
        
        # Verify metadata
        if products:
            first = products[0]
            assert first.product_type == "vehicle", "Product type should be vehicle"
            assert first.product_id.startswith("VIN-"), "ID should have VIN prefix"
            assert first.metadata is not None, "Metadata should exist"
            assert "vin" in first.metadata, "Metadata should have VIN"
        
        # Check trace
        print(f"\n   Trace:")
        print(f"   - Request ID: {response.trace.request_id}")
        print(f"   - Sources: {response.trace.sources}")
        print(f"   - Timing: {response.trace.timings_ms.get('total', 0):.1f}ms")
        
        return True
        
    except Exception as e:
        print(f"\n[FAIL] Search failed: {e}")
        print("   Make sure IDSS backend is running on port 8000")
        return False


async def test_get_vehicle_detail():
    """Test retrieving vehicle details."""
    print("\nTest 5: Get Vehicle Detail")
    print("=" * 60)
    
    try:
        # First do a search to get a real VIN
        search_request = SearchProductsRequest(query="sedan", limit=1)
        search_response = await search_products_idss(search_request)
        
        if not search_response.data.products:
            print("[WARN] No vehicles found to test detail retrieval")
            return False
        
        product_id = search_response.data.products[0].product_id
        print(f"Getting details for: {product_id}")
        
        # Get detail
        request = GetProductRequest(product_id=product_id)
        response = await get_product_idss(request)
        
        # Verify response
        assert response.status == "OK", f"Status should be OK, got: {response.status}"
        assert response.data is not None, "Data should exist"
        
        product = response.data
        print(f"\n✅ Detail retrieval successful")
        print(f"   Name: {product.name}")
        print(f"   Price: ${product.price_cents / 100:,.2f}")
        print(f"   Description: {product.description}")
        print(f"   Metadata fields: {len(product.metadata)} fields")
        
        # Verify rich metadata
        assert "vin" in product.metadata, "Should have VIN"
        assert "make" in product.metadata, "Should have make"
        assert "model" in product.metadata, "Should have model"
        
        return True
        
    except Exception as e:
        print(f"\n[FAIL] Detail retrieval failed: {e}")
        return False


# ============================================================================
# Main Test Runner
# ============================================================================

async def run_all_tests():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("UNIVERSAL PRODUCT ADAPTER - INTEGRATION TESTS")
    print("=" * 60)
    
    results = []
    
    # Unit tests
    try:
        test_vehicle_to_product_summary()
        results.append(("Vehicle → Summary", True))
    except AssertionError as e:
        print(f"[FAIL] Test failed: {e}")
        results.append(("Vehicle → Summary", False))
    
    try:
        test_vehicle_to_product_detail()
        results.append(("Vehicle → Detail", True))
    except AssertionError as e:
        print(f"[FAIL] Test failed: {e}")
        results.append(("Vehicle → Detail", False))
    
    try:
        test_flat_format_vehicle()
        results.append(("Flat Format", True))
    except AssertionError as e:
        print(f"[FAIL] Test failed: {e}")
        results.append(("Flat Format", False))
    
    # Integration tests (require running backend)
    search_ok = await test_search_vehicles()
    results.append(("Live Search", search_ok))
    
    if search_ok:
        detail_ok = await test_get_vehicle_detail()
        results.append(("Get Detail", detail_ok))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    
    for name, ok in results:
        status = "[OK] PASS" if ok else "[FAIL] FAIL"
        print(f"{status}  {name}")
    
    print(f"\nResult: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! The adapter is working correctly.")
    else:
        print("\n[WARN] Some tests failed. Check the output above.")
    
    return passed == total


if __name__ == "__main__":
    print("\n--- Starting Integration Tests...")
    print("   Note: Make sure IDSS backend is running on port 8000")
    print("   Run: uvicorn idss.api.server:app --port 8000\n")
    
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
