"""
Integration tests for MCP E-commerce endpoints.

Tests verify:
- Schema strictness (Pydantic extra="forbid")
- Deterministic response envelope
- IDs-only execution rule
- OUT_OF_STOCK constraint handling
- Request tracing (request_id + timings)
- End-to-end flow: search -> get -> add_to_cart -> checkout

Uses PostgreSQL (same as app) via DATABASE_URL.
"""

import pytest
import os
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import Base, get_db, DATABASE_URL
from app.models import Product

# Deterministic UUID5 test product IDs (slug → UUID5)
_NS = uuid.NAMESPACE_DNS
TEST_PROD_001 = uuid.uuid5(_NS, "mcp-test-endpoint-001")
TEST_PROD_002 = uuid.uuid5(_NS, "mcp-test-endpoint-002")
TEST_PROD_003 = uuid.uuid5(_NS, "mcp-test-endpoint-003")


# Use PostgreSQL (same as app) - tests run against real DB
TEST_DATABASE_URL = os.getenv("DATABASE_URL", DATABASE_URL)
engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Override database dependency for testing."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# Override app database dependency
app.dependency_overrides[get_db] = override_get_db

# Create test client
client = TestClient(app)


# 
# Test Fixtures
# 

@pytest.fixture(scope="function", autouse=True)
def setup_database():
    """
    Create tables if needed and add test products for each test.
    Uses Supabase schema: price_value (dollars), inventory (BigInteger), attributes (JSONB).
    Cleans up test products after test (does not drop tables - preserves real data).
    """
    # Ensure tables exist (PostgreSQL - they usually do from app)
    Base.metadata.create_all(bind=engine)

    # Add test products
    db = TestingSessionLocal()
    test_uuids = [TEST_PROD_001, TEST_PROD_002, TEST_PROD_003]

    # Remove any leftover test products from previous run
    for uid in test_uuids:
        db.query(Product).filter(Product.product_id == uid).delete(synchronize_session=False)
    db.commit()

    # Product 1: In stock (price_value in dollars, inventory as BigInteger)
    db.add(Product(
        product_id=TEST_PROD_001,
        name="Test Laptop",
        category="Electronics",
        brand="TestBrand",
        price_value=999.99,
        inventory=10,
        attributes={"description": "A test laptop"},
    ))

    # Product 2: Low stock (for OUT_OF_STOCK testing)
    db.add(Product(
        product_id=TEST_PROD_002,
        name="Test Phone",
        category="Electronics",
        brand="TestBrand",
        price_value=799.99,
        inventory=1,
        attributes={"description": "A test phone"},
    ))

    # Product 3: Another product for search testing
    db.add(Product(
        product_id=TEST_PROD_003,
        name="Test Headphones",
        category="Electronics",
        brand="AudioBrand",
        price_value=299.99,
        inventory=50,
        attributes={"description": "Wireless headphones"},
    ))

    db.commit()
    db.close()

    yield

    # Clean up - remove only our test products (do not drop tables)
    db = TestingSessionLocal()
    for uid in test_uuids:
        db.query(Product).filter(Product.product_id == uid).delete(synchronize_session=False)
    db.commit()
    db.close()


# 
# Test: Schema Strictness
# 

def test_schema_strictness_rejects_unknown_fields():
    """
    Test that request schemas reject unknown fields.
    This ensures agents can't send arbitrary data.
    """
    # Try to send an unknown field in SearchProducts
    response = client.post(
        "/api/search-products",
        json={
            "query": "laptop",
            "limit": 10,
            "unknown_field": "should_be_rejected"  # This should fail
        }
    )
    
    # Should return 422 (Unprocessable Entity) for validation error
    assert response.status_code == 422


def test_schema_strictness_accepts_valid_fields():
    """
    Test that valid requests are accepted.
    """
    response = client.post(
        "/api/search-products",
        json={
            "query": "laptop",
            "limit": 10
        }
    )
    
    assert response.status_code == 200


# 
# Test: Response Envelope Structure
# 

def test_response_envelope_structure():
    """
    Test that all responses follow the standard envelope pattern.
    Every response should have: status, data, constraints, trace, version
    """
    response = client.post(
        "/api/search-products",
        json={"query": "laptop"}
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify envelope structure
    assert "status" in data
    assert "data" in data
    assert "constraints" in data
    assert "trace" in data
    assert "version" in data
    
    # Verify trace structure
    trace = data["trace"]
    assert "request_id" in trace
    assert "cache_hit" in trace
    assert "timings_ms" in trace
    assert "sources" in trace
    
    # Verify timings breakdown
    timings = trace["timings_ms"]
    assert "db" in timings
    assert "total" in timings
    
    # Verify version structure
    version = data["version"]
    assert "catalog_version" in version
    assert "updated_at" in version


def test_deterministic_request_id():
    """
    Test that each request gets a unique request_id in trace.
    """
    response1 = client.post("/api/search-products", json={"query": "test"})
    response2 = client.post("/api/search-products", json={"query": "test"})
    
    request_id1 = response1.json()["trace"]["request_id"]
    request_id2 = response2.json()["trace"]["request_id"]
    
    # Each request should have a different ID
    assert request_id1 != request_id2


# 
# Test: IDs-Only Execution Rule
# 

def test_ids_only_add_to_cart():
    """
    Test that AddToCart only accepts product_id, never product name.
    The schema should enforce this at the type level.
    """
    # Valid request with product_id (UUID5 for test product 1)
    response = client.post(
        "/api/add-to-cart",
        json={
            "cart_id": "test-cart-001",
            "product_id": str(TEST_PROD_001),
            "qty": 1
        }
    )

    assert response.status_code == 200
    assert response.json()["status"] == "OK"


def test_ids_only_checkout():
    """
    Test that Checkout only accepts IDs (cart_id, payment_method_id, address_id).
    """
    # First add item to cart
    client.post(
        "/api/add-to-cart",
        json={
            "cart_id": "test-cart-002",
            "product_id": str(TEST_PROD_001),
            "qty": 1
        }
    )
    
    # Valid checkout with IDs only
    response = client.post(
        "/api/checkout",
        json={
            "cart_id": "test-cart-002",
            "payment_method_id": "payment-123",
            "address_id": "address-456"
        }
    )
    
    assert response.status_code == 200
    assert response.json()["status"] == "OK"


# 
# Test: OUT_OF_STOCK Constraint Handling
# 

def test_out_of_stock_constraint():
    """
    Test that OUT_OF_STOCK returns proper constraint details.
    
    Constraint should include:
    - code: OUT_OF_STOCK
    - message explaining the issue
    - details with requested_qty and available_qty
    - suggested_actions for self-correction
    """
    # Try to add more than available quantity
    response = client.post(
        "/api/add-to-cart",
        json={
            "cart_id": "test-cart-003",
            "product_id": str(TEST_PROD_002),  # Only 1 in stock
            "qty": 5  # Requesting 5
        }
    )

    assert response.status_code == 200
    data = response.json()

    # Verify OUT_OF_STOCK status
    assert data["status"] == "OUT_OF_STOCK"

    # Verify constraint structure
    assert len(data["constraints"]) > 0
    constraint = data["constraints"][0]

    assert constraint["code"] == "OUT_OF_STOCK"
    assert "message" in constraint
    assert "details" in constraint

    # Verify details contain the problem specifics
    details = constraint["details"]
    assert details["product_id"] == str(TEST_PROD_002)
    assert details["requested_qty"] == 5
    assert details["available_qty"] == 1
    
    # Verify suggested actions for agent self-correction
    assert "suggested_actions" in constraint
    assert len(constraint["suggested_actions"]) > 0


def test_out_of_stock_at_checkout():
    """
    Test OUT_OF_STOCK detection at checkout time (race condition simulation).
    """
    cart_id = "test-cart-004"
    # Add item to cart (1 in stock → should succeed)
    client.post(
        "/api/add-to-cart",
        json={
            "cart_id": cart_id,
            "product_id": str(TEST_PROD_002),
            "qty": 1
        }
    )

    # Manually reduce product inventory to 0 to simulate race condition
    db = TestingSessionLocal()
    product = db.query(Product).filter(Product.product_id == TEST_PROD_002).first()
    if product:
        product.inventory = 0
        db.commit()
    db.close()

    # Try to checkout - should fail with OUT_OF_STOCK
    response = client.post(
        "/api/checkout",
        json={
            "cart_id": cart_id,
            "payment_method_id": "payment-123",
            "address_id": "address-456"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "OUT_OF_STOCK"
    assert len(data["constraints"]) > 0


# 
# Test: NOT_FOUND Constraint Handling
# 

def test_product_not_found():
    """
    Test that requesting a non-existent product returns NOT_FOUND.
    """
    response = client.post(
        "/api/get-product",
        json={"product_id": "nonexistent-product"}
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["status"] == "NOT_FOUND"
    assert len(data["constraints"]) > 0
    
    constraint = data["constraints"][0]
    assert constraint["code"] == "PRODUCT_NOT_FOUND"
    assert "suggested_actions" in constraint


# 
# Test: End-to-End Flow
# 

def test_end_to_end_flow():
    """
    Test complete flow: search → get → add_to_cart → checkout
    
    This proves the system works end-to-end as specified in Stage 1.
    """
    # Step 1: Search for products (use specific query to avoid ambiguous/greeting detection)
    search_response = client.post(
        "/api/search-products",
        json={"query": "laptop", "limit": 10}
    )
    
    assert search_response.status_code == 200
    search_data = search_response.json()
    assert search_data["status"] == "OK"
    assert len(search_data["data"]["products"]) > 0
    
    # Get a product_id from search results
    product_id = search_data["data"]["products"][0]["product_id"]
    
    # Step 2: Get product details
    get_response = client.post(
        "/api/get-product",
        json={"product_id": product_id}
    )
    
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["status"] == "OK"
    assert get_data["data"]["product_id"] == product_id
    
    # Step 3: Add to cart
    cart_id = "test-cart-e2e"
    add_response = client.post(
        "/api/add-to-cart",
        json={
            "cart_id": cart_id,
            "product_id": product_id,
            "qty": 2
        }
    )
    
    assert add_response.status_code == 200
    add_data = add_response.json()
    assert add_data["status"] == "OK"
    assert len(add_data["data"]["items"]) == 1
    assert add_data["data"]["total_cents"] > 0
    
    # Step 4: Checkout
    checkout_response = client.post(
        "/api/checkout",
        json={
            "cart_id": cart_id,
            "payment_method_id": "payment-test",
            "address_id": "address-test"
        }
    )
    
    assert checkout_response.status_code == 200
    checkout_data = checkout_response.json()
    assert checkout_data["status"] == "OK"
    assert "order_id" in checkout_data["data"]
    assert checkout_data["data"]["total_cents"] > 0


# 
# Test: Search Functionality
# 

def test_search_with_filters():
    """
    Test search with structured filters.
    """
    response = client.post(
        "/api/search-products",
        json={
            "filters": {"category": "Electronics"},
            "limit": 10
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "OK"
    
    # All results should be Electronics
    for product in data["data"]["products"]:
        assert product["category"] == "Electronics"


def test_search_pagination():
    """
    Test search pagination with cursor.
    """
    # First page
    response1 = client.post(
        "/api/search-products",
        json={"limit": 2}
    )
    
    assert response1.status_code == 200
    data1 = response1.json()
    assert len(data1["data"]["products"]) <= 2
    
    # If there's a next cursor, test it
    if data1["data"]["next_cursor"]:
        response2 = client.post(
            "/api/search-products",
            json={"limit": 2, "cursor": data1["data"]["next_cursor"]}
        )
        
        assert response2.status_code == 200
        data2 = response2.json()
        
        # Second page should have different products
        page1_ids = {p["product_id"] for p in data1["data"]["products"]}
        page2_ids = {p["product_id"] for p in data2["data"]["products"]}
        assert page1_ids != page2_ids


# 
# Test: Timing Breakdown
# 

def test_timing_breakdown():
    """
    Test that trace includes timing breakdown.
    """
    response = client.post(
        "/api/get-product",
        json={"product_id": str(TEST_PROD_001)}
    )
    
    assert response.status_code == 200
    data = response.json()
    
    timings = data["trace"]["timings_ms"]
    
    # Verify all required timing fields exist
    assert "db" in timings
    assert "total" in timings
    
    # Verify timings are reasonable (positive numbers)
    assert timings["db"] >= 0
    assert timings["total"] >= 0
    assert timings["total"] >= timings["db"]  # Total should be at least as long as DB time


# 
# Test: Cart Operations
# 

def test_add_to_existing_cart():
    """
    Test adding multiple items to same cart.
    """
    cart_id = "test-cart-multi"

    # Add first item
    response1 = client.post(
        "/api/add-to-cart",
        json={"cart_id": cart_id, "product_id": str(TEST_PROD_001), "qty": 1}
    )
    assert response1.status_code == 200

    # Add second item
    response2 = client.post(
        "/api/add-to-cart",
        json={"cart_id": cart_id, "product_id": str(TEST_PROD_003), "qty": 2}
    )
    assert response2.status_code == 200

    data = response2.json()
    assert len(data["data"]["items"]) == 2


def test_increment_quantity_in_cart():
    """
    Test that adding same product twice increments quantity.
    """
    cart_id = "test-cart-increment"

    # Add item first time
    response1 = client.post(
        "/api/add-to-cart",
        json={"cart_id": cart_id, "product_id": str(TEST_PROD_001), "qty": 1}
    )
    data1 = response1.json()
    assert data1["data"]["items"][0]["quantity"] == 1

    # Add same item again
    response2 = client.post(
        "/api/add-to-cart",
        json={"cart_id": cart_id, "product_id": str(TEST_PROD_001), "qty": 2}
    )
    data2 = response2.json()

    # Should still be 1 item in cart, but quantity should be 3
    assert len(data2["data"]["items"]) == 1
    assert data2["data"]["items"][0]["quantity"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
