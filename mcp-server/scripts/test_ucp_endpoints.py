"""
Test script for UCP Native Checkout endpoints.

Run this after starting the MCP server to test UCP endpoints.
"""

import requests
import json

BASE_URL = "http://localhost:8001"

def test_create_checkout_session():
    """Test POST /ucp/checkout-sessions"""
    print("Testing: POST /ucp/checkout-sessions")
    
    payload = {
        "line_items": [
            {
                "item": {
                    "id": "PROD-001",  # Replace with actual product ID
                    "title": "Test Laptop"
                },
                "quantity": 1
            }
        ],
        "currency": "USD"
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/ucp/checkout-sessions",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        data = response.json()
        print(f"OK Created checkout session: {data.get('id')}")
        return data.get('id')
    except Exception as e:
        print(f"FAIL Failed: {e}")
        return None


def test_get_checkout_session(session_id):
    """Test GET /ucp/checkout-sessions/{id}"""
    if not session_id:
        print("Skipping GET test (no session ID)")
        return
    
    print(f"\nTesting: GET /ucp/checkout-sessions/{session_id}")
    
    # Extract short ID from full GID
    short_id = session_id.split('/')[-1] if '/' in session_id else session_id
    
    try:
        response = requests.get(f"{BASE_URL}/ucp/checkout-sessions/{short_id}")
        response.raise_for_status()
        data = response.json()
        print(f"OK Retrieved checkout session: {data.get('status')}")
    except Exception as e:
        print(f"FAIL Failed: {e}")


def test_update_checkout_session(session_id):
    """Test PUT /ucp/checkout-sessions/{id}"""
    if not session_id:
        print("Skipping UPDATE test (no session ID)")
        return
    
    print(f"\nTesting: PUT /ucp/checkout-sessions/{session_id}")
    
    short_id = session_id.split('/')[-1] if '/' in session_id else session_id
    
    payload = {
        "id": session_id,
        "buyer": {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com"
        },
        "fulfillment": {
            "methods": [{
                "type": "shipping",
                "destinations": [{
                    "id": "dest_1",
                    "postal_code": "94043",
                    "country": "US",
                    "address_locality": "Mountain View",
                    "address_region": "CA"
                }],
                "selected_destination_id": "dest_1",
                "groups": [{
                    "id": "group_1",
                    "line_item_ids": ["line_1"],
                    "selected_option_id": "ship_ground",
                    "options": [{
                        "id": "ship_ground",
                        "title": "Ground (3-5 days)",
                        "totals": [{"type": "total", "amount": 500}]
                    }]
                }]
            }]
        }
    }
    
    try:
        response = requests.put(
            f"{BASE_URL}/ucp/checkout-sessions/{short_id}",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        data = response.json()
        print(f"OK Updated checkout session. New total: ${data.get('totals', [{}])[-1].get('amount', 0) / 100}")
    except Exception as e:
        print(f"FAIL Failed: {e}")


def main():
    print("=" * 50)
    print("UCP Native Checkout Endpoint Tests")
    print("=" * 50)
    print(f"\nTesting against: {BASE_URL}")
    print("Make sure MCP server is running: python -m uvicorn app.main:app --reload\n")
    
    # Test 1: Create session
    session_id = test_create_checkout_session()
    
    # Test 2: Get session
    test_get_checkout_session(session_id)
    
    # Test 3: Update session
    test_update_checkout_session(session_id)
    
    print("\n" + "=" * 50)
    print("Tests Complete")
    print("=" * 50)
    print("\nNote: Complete and Cancel endpoints require payment processing setup.")


if __name__ == "__main__":
    main()
