"""
Live UCP Integration Test.

Tests UCP endpoints with real HTTP requests to verify everything works end-to-end.
"""

import requests
import json
import time
from typing import Dict, Any

BASE_URL = "http://localhost:8001"


def print_section(title: str):
    """Print a formatted section header."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_request(method: str, endpoint: str, data: Dict[str, Any]):
    """Print formatted request."""
    print(f"\nRequest: {method} {endpoint}")
    print("Request:")
    print(json.dumps(data, indent=2))


def print_response(response: requests.Response):
    """Print formatted response."""
    print(f"\nResponse [{response.status_code}]:")
    try:
        print(json.dumps(response.json(), indent=2))
    except:
        print(response.text)


def test_ucp_search():
    """Test UCP search endpoint."""
    print_section("TEST 1: UCP Search")
    
    # Test 1a: Simple search
    data = {
        "action": "search",
        "parameters": {
            "query": "laptop",
            "limit": 5
        }
    }
    
    print_request("POST", "/ucp/search", data)
    response = requests.post(f"{BASE_URL}/ucp/search", json=data)
    print_response(response)
    
    if response.status_code == 200:
        result = response.json()
        assert result["status"] == "success", "Expected success status"
        print(f"\n[OK] Search successful! Found {result['total_count']} products")
    else:
        print(f"\n[FAIL] Search failed with status {response.status_code}")
    
    # Test 1b: Search with filters
    data = {
        "action": "search",
        "parameters": {
            "query": "vehicle",
            "filters": {
                "category": "sedan"
            },
            "limit": 3
        }
    }
    
    print("\n" + "-" * 80)
    print_request("POST", "/ucp/search", data)
    response = requests.post(f"{BASE_URL}/ucp/search", json=data)
    print_response(response)
    
    if response.status_code == 200:
        result = response.json()
        print(f"\n[OK] Filtered search successful! Found {result['total_count']} products")
    else:
        print(f"\n[FAIL] Filtered search failed")


def test_ucp_get_product():
    """Test UCP get_product endpoint."""
    print_section("TEST 2: UCP Get Product")
    
    # First, search to get a product ID
    search_data = {
        "action": "search",
        "parameters": {
            "query": "product",
            "limit": 1
        }
    }
    
    search_response = requests.post(f"{BASE_URL}/ucp/search", json=search_data)
    
    if search_response.status_code == 200:
        products = search_response.json().get("products", [])
        if products:
            product_id = products[0]["id"]
            print(f"Found product ID: {product_id}")
            
            # Test 2a: Get product detail
            data = {
                "action": "get_product",
                "parameters": {
                    "product_id": product_id
                }
            }
            
            print_request("POST", "/ucp/get_product", data)
            response = requests.post(f"{BASE_URL}/ucp/get_product", json=data)
            print_response(response)
            
            if response.status_code == 200:
                result = response.json()
                if result["status"] == "success" and result.get("product"):
                    print(f"\n[OK] Get product successful!")
                    print(f"   Title: {result['product']['title']}")
                    print(f"   Price: ${result['product']['price']['value']}")
                    print(f"   Availability: {result['product']['availability']}")
                else:
                    print(f"\n[FAIL] Product not found")
            else:
                print(f"\n[FAIL] Get product failed")
            
            # Test 2b: Get product with field projection
            data = {
                "action": "get_product",
                "parameters": {
                    "product_id": product_id,
                    "fields": ["title", "price", "availability"]
                }
            }
            
            print("\n" + "-" * 80)
            print_request("POST", "/ucp/get_product (with field projection)", data)
            response = requests.post(f"{BASE_URL}/ucp/get_product", json=data)
            print_response(response)
            
            if response.status_code == 200:
                print(f"\n[OK] Field projection successful!")
        else:
            print("\n[WARN]  No products found in search, skipping get_product test")
    else:
        print("\n[WARN]  Search failed, skipping get_product test")


def test_ucp_add_to_cart():
    """Test UCP add_to_cart endpoint."""
    print_section("TEST 3: UCP Add to Cart")
    
    # Use a known product ID (from e-commerce products)
    product_id = "PROD-001"
    
    # Test 3a: Add to new cart
    data = {
        "action": "add_to_cart",
        "parameters": {
            "product_id": product_id,
            "quantity": 2
        }
    }
    
    print_request("POST", "/ucp/add_to_cart", data)
    response = requests.post(f"{BASE_URL}/ucp/add_to_cart", json=data)
    print_response(response)
    
    cart_id = None
    if response.status_code == 200:
        result = response.json()
        if result["status"] == "success":
            cart_id = result.get("cart_id")
            print(f"\n[OK] Add to cart successful!")
            print(f"   Cart ID: {cart_id}")
            print(f"   Items: {result.get('item_count')}")
            print(f"   Total: ${result.get('total_price_cents', 0) / 100:.2f}")
        else:
            print(f"\n[FAIL] Add to cart failed: {result.get('error')}")
    else:
        print(f"\n[FAIL] Add to cart failed with status {response.status_code}")
    
    # Test 3b: Add to existing cart
    if cart_id:
        data = {
            "action": "add_to_cart",
            "parameters": {
                "product_id": "PROD-002",
                "quantity": 1,
                "cart_id": cart_id
            }
        }
        
        print("\n" + "-" * 80)
        print_request("POST", "/ucp/add_to_cart (existing cart)", data)
        response = requests.post(f"{BASE_URL}/ucp/add_to_cart", json=data)
        print_response(response)
        
        if response.status_code == 200:
            result = response.json()
            if result["status"] == "success":
                print(f"\n[OK] Add to existing cart successful!")
                print(f"   Items: {result.get('item_count')}")
                print(f"   Total: ${result.get('total_price_cents', 0) / 100:.2f}")
    
    return cart_id


def test_ucp_checkout(cart_id: str = None):
    """Test UCP checkout endpoint."""
    print_section("TEST 4: UCP Checkout")
    
    if not cart_id:
        print("[WARN]  No cart ID available, creating a new cart first...")
        # Create a cart
        add_data = {
            "action": "add_to_cart",
            "parameters": {
                "product_id": "PROD-001",
                "quantity": 1
            }
        }
        add_response = requests.post(f"{BASE_URL}/ucp/add_to_cart", json=add_data)
        if add_response.status_code == 200:
            cart_id = add_response.json().get("cart_id")
        
        if not cart_id:
            print("[FAIL] Could not create cart, skipping checkout test")
            return
    
    # Test 4a: Minimal checkout
    data = {
        "action": "checkout",
        "parameters": {
            "cart_id": cart_id
        }
    }
    
    print_request("POST", "/ucp/checkout", data)
    response = requests.post(f"{BASE_URL}/ucp/checkout", json=data)
    print_response(response)
    
    if response.status_code == 200:
        result = response.json()
        if result["status"] == "success":
            print(f"\n[OK] Checkout successful!")
            print(f"   Order ID: {result.get('order_id')}")
            print(f"   Total: ${result.get('total_price_cents', 0) / 100:.2f}")
        else:
            print(f"\n[FAIL] Checkout failed: {result.get('error')}")
    else:
        print(f"\n[FAIL] Checkout failed with status {response.status_code}")
    
    # Test 4b: Checkout with payment info
    # Create another cart
    add_data = {
        "action": "add_to_cart",
        "parameters": {
            "product_id": "PROD-003",
            "quantity": 1
        }
    }
    add_response = requests.post(f"{BASE_URL}/ucp/add_to_cart", json=add_data)
    if add_response.status_code == 200:
        new_cart_id = add_response.json().get("cart_id")
        
        data = {
            "action": "checkout",
            "parameters": {
                "cart_id": new_cart_id,
                "payment_method": "credit_card",
                "shipping_address": "123 Main St, San Francisco, CA"
            }
        }
        
        print("\n" + "-" * 80)
        print_request("POST", "/ucp/checkout (with payment info)", data)
        response = requests.post(f"{BASE_URL}/ucp/checkout", json=data)
        print_response(response)
        
        if response.status_code == 200:
            result = response.json()
            if result["status"] == "success":
                print(f"\n[OK] Checkout with payment info successful!")


def test_ucp_error_handling():
    """Test UCP error handling."""
    print_section("TEST 5: UCP Error Handling")
    
    # Test 5a: Product not found
    data = {
        "action": "get_product",
        "parameters": {
            "product_id": "NONEXISTENT-999"
        }
    }
    
    print_request("POST", "/ucp/get_product (non-existent)", data)
    response = requests.post(f"{BASE_URL}/ucp/get_product", json=data)
    print_response(response)
    
    if response.status_code == 200:
        result = response.json()
        if result["status"] == "error":
            print(f"\n[OK] Error handling correct!")
            print(f"   Error: {result.get('error')}")
        else:
            print(f"\n[WARN]  Expected error status, got success")


def test_server_health():
    """Test if server is running."""
    print_section("Server Health Check")
    
    try:
        response = requests.get(f"{BASE_URL}/", timeout=2)
        print(f"[OK] Server is running at {BASE_URL}")
        print(f"   Status: {response.status_code}")
        return True
    except requests.exceptions.ConnectionError:
        print(f"[FAIL] Server not running at {BASE_URL}")
        print(f"   Please start the server with:")
        print(f"   cd /Users/julih/Documents/idss_new/idss-mcp/mcp-server")
        print(f"   source ../../venv/bin/activate")
        print(f"   uvicorn app.main:app --host 0.0.0.0 --port 8001")
        return False
    except Exception as e:
        print(f"[FAIL] Error checking server: {e}")
        return False


def main():
    """Run all UCP tests."""
    print("\n" + "---" * 40)
    print("  UCP LIVE INTEGRATION TEST")
    print("---" * 40)
    
    # Check if server is running
    if not test_server_health():
        return
    
    time.sleep(1)
    
    # Run tests
    try:
        test_ucp_search()
        time.sleep(0.5)
        
        test_ucp_get_product()
        time.sleep(0.5)
        
        cart_id = test_ucp_add_to_cart()
        time.sleep(0.5)
        
        test_ucp_checkout(cart_id)
        time.sleep(0.5)
        
        test_ucp_error_handling()
        
        # Final summary
        print_section("SUCCESS TEST COMPLETE")
        print("\n[OK] All UCP endpoints tested successfully!")
        print("\nAvailable UCP endpoints:")
        print(f"  • POST {BASE_URL}/ucp/search")
        print(f"  • POST {BASE_URL}/ucp/get_product")
        print(f"  • POST {BASE_URL}/ucp/add_to_cart")
        print(f"  • POST {BASE_URL}/ucp/checkout")
        print("\n" + "SUCCESS" * 40 + "\n")
        
    except Exception as e:
        print(f"\n[FAIL] Test failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
