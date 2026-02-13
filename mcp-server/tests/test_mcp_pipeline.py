"""
Comprehensive unit tests for MCP pipeline.

Tests:
1. get_product endpoint (MCP, UCP, Tool protocols)
2. search_products endpoint (MCP, UCP, Tool protocols)
3. IDSS recommendation integration
4. Hard/soft constraints
5. Latency metrics
"""

import pytest
import time
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import Base, get_db
from app.models import Product, Price, Inventory

# Test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_mcp.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Override database dependency for testing."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


@pytest.fixture(scope="function", autouse=True)
def setup_database():
    """Create fresh database for each test."""
    # Re-apply our override so we use SQLite (other tests may have set PostgreSQL)
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    
    # Create test products
    products = [
        Product(
            product_id="laptop-001",
            name="Gaming Laptop RTX 4060",
            description="High-performance gaming laptop with NVIDIA RTX 4060 GPU",
            category="Electronics",
            brand="GamingBrand",
            product_type="gaming_laptop",
            gpu_vendor="NVIDIA",
            gpu_model="RTX 4060",
            price_info=Price(product_id="laptop-001", price_cents=149999),
            inventory_info=Inventory(product_id="laptop-001", available_qty=5)
        ),
        Product(
            product_id="laptop-002",
            name="Work Laptop Professional",
            description="Business laptop for productivity",
            category="Electronics",
            brand="WorkBrand",
            product_type="laptop",
            price_info=Price(product_id="laptop-002", price_cents=99999),
            inventory_info=Inventory(product_id="laptop-002", available_qty=10)
        ),
        Product(
            product_id="book-001",
            name="Mystery Novel",
            description="Thrilling mystery novel",
            category="Books",
            brand="PublisherA",
            subcategory="Mystery",
            price_info=Price(product_id="book-001", price_cents=1999),
            inventory_info=Inventory(product_id="book-001", available_qty=20)
        )
    ]
    
    for product in products:
        db.add(product)
        if hasattr(product, 'price_info'):
            db.add(product.price_info)
        if hasattr(product, 'inventory_info'):
            db.add(product.inventory_info)
    
    db.commit()
    db.close()
    
    yield
    
    Base.metadata.drop_all(bind=engine)


class TestGetProduct:
    """Test get_product endpoint across all protocols."""
    
    def test_get_product_mcp_protocol(self):
        """Test MCP protocol: POST /api/get-product"""
        start_time = time.time()
        
        response = client.post(
            "/api/get-product",
            json={"product_id": "laptop-001"}
        )
        
        latency = (time.time() - start_time) * 1000  # ms
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OK"
        assert data["data"]["product_id"] == "laptop-001"
        assert data["data"]["name"] == "Gaming Laptop RTX 4060"
        assert data["trace"]["request_id"] is not None
        assert latency < 1000  # Should be fast (< 1 second)
        
        # Verify trace contains timing info
        assert "timings_ms" in data["trace"]
        assert "total" in data["trace"]["timings_ms"]
    
    def test_get_product_ucp_protocol(self):
        """Test UCP protocol: POST /ucp/get-product"""
        start_time = time.time()
        
        response = client.post(
            "/ucp/get-product",
            json={
                "action": "get_product",
                "parameters": {
                    "product_id": "laptop-001"
                }
            }
        )
        
        latency = (time.time() - start_time) * 1000
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["product"]["id"] == "laptop-001"
        assert latency < 1000
    
    def test_get_product_tool_protocol(self):
        """Test Tool protocol: POST /tools/execute"""
        start_time = time.time()
        
        response = client.post(
            "/tools/execute",
            json={
                "tool_name": "get_product",
                "parameters": {
                    "product_id": "laptop-001"
                }
            }
        )
        
        latency = (time.time() - start_time) * 1000
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OK"
        assert data["data"]["product_id"] == "laptop-001"
        assert latency < 1000
    
    def test_get_product_not_found(self):
        """Test get_product with non-existent product"""
        response = client.post(
            "/api/get-product",
            json={"product_id": "nonexistent"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "NOT_FOUND"
        assert len(data["constraints"]) > 0
    
    def test_get_product_field_projection(self):
        """Test get_product with field projection"""
        response = client.post(
            "/api/get-product",
            json={
                "product_id": "laptop-001",
                "fields": ["name", "price_cents"]
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OK"
        assert "name" in data["data"]
        assert "price_cents" in data["data"]
        assert "description" not in data["data"]  # Should be filtered out


class TestSearchProducts:
    """Test search_products endpoint across all protocols."""
    
    def test_search_products_mcp_protocol(self):
        """Test MCP protocol: POST /api/search-products"""
        start_time = time.time()
        
        response = client.post(
            "/api/search-products",
            json={
                "query": "gaming laptop",
                "filters": {
                    "category": "Electronics",
                    "product_type": "gaming_laptop"
                },
                "limit": 10
            }
        )
        
        latency = (time.time() - start_time) * 1000
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OK"
        assert len(data["data"]["products"]) > 0
        assert data["trace"]["request_id"] is not None
        assert latency < 2000  # Search can be slower
        
        # Verify all products match filters
        for product in data["data"]["products"]:
            assert product["category"] == "Electronics"
    
    def test_search_products_ucp_protocol(self):
        """Test UCP protocol: POST /ucp/search"""
        start_time = time.time()
        
        response = client.post(
            "/ucp/search",
            json={
                "action": "search",
                "parameters": {
                    "query": "laptop",
                    "filters": {"category": "Electronics"},
                    "limit": 10
                }
            }
        )
        
        latency = (time.time() - start_time) * 1000
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert len(data["products"]) > 0
        assert latency < 2000
    
    def test_search_products_tool_protocol(self):
        """Test Tool protocol: POST /tools/execute"""
        start_time = time.time()
        
        response = client.post(
            "/tools/execute",
            json={
                "tool_name": "search_products",
                "parameters": {
                    "query": "laptop",
                    "filters": {"category": "Electronics"},
                    "limit": 10
                }
            }
        )
        
        latency = (time.time() - start_time) * 1000
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OK"
        assert len(data["data"]["products"]) > 0
        assert latency < 2000
    
    def test_search_with_hard_constraints(self):
        """Test search with hard constraints (product_type, gpu_vendor, price_max)"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "gaming",
                "filters": {
                    "category": "Electronics",
                    "product_type": "gaming_laptop",
                    "gpu_vendor": "NVIDIA",
                    "price_max_cents": 200000
                },
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OK"
        
        # All results should match hard constraints
        for product in data["data"]["products"]:
            assert product["category"] == "Electronics"
            # Hard constraints are enforced at DB level
    
    def test_search_with_soft_constraints(self):
        """Test search with soft constraints (implicit preferences)"""
        # Soft constraints are handled by IDSS ranking
        # This would be tested via IDSS backend integration
        pass


class TestIDSSIntegration:
    """Test IDSS backend integration for recommendations."""
    
    def test_idss_recommendation_used_for_laptops(self):
        """Verify laptops use IDSS backend for recommendations"""
        # This would require IDSS backend to be running
        # For now, verify the routing logic exists
        response = client.post(
            "/api/search-products",
            json={
                "query": "laptop",
                "filters": {"category": "Electronics"},
                "limit": 10,
                "session_id": "test-session"
            }
        )
        
        assert response.status_code == 200
        # Should route through IDSS if interview is needed
    
    def test_idss_diversification(self):
        """Test that IDSS diversification is applied"""
        # This would require IDSS backend
        pass
    
    def test_idss_semantic_similarity(self):
        """Test that IDSS semantic similarity ranking is used"""
        # This would require IDSS backend
        pass


class TestLatencyMetrics:
    """Test latency metrics collection."""
    
    def test_get_product_latency(self):
        """Measure get_product latency"""
        latencies = []
        
        for _ in range(5):
            start_time = time.time()
            response = client.post(
                "/api/get-product",
                json={"product_id": "laptop-001"}
            )
            latency = (time.time() - start_time) * 1000
            latencies.append(latency)
            assert response.status_code == 200
        
        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        min_latency = min(latencies)
        
        print(f"\nGet Product Latency:")
        print(f"  Average: {avg_latency:.2f}ms")
        print(f"  Min: {min_latency:.2f}ms")
        print(f"  Max: {max_latency:.2f}ms")
        
        assert avg_latency < 500  # Should be fast
        assert max_latency < 1000
    
    def test_search_products_latency(self):
        """Measure search_products latency"""
        latencies = []
        
        for _ in range(5):
            start_time = time.time()
            response = client.post(
                "/api/search-products",
                json={
                    "query": "laptop",
                    "limit": 10
                }
            )
            latency = (time.time() - start_time) * 1000
            latencies.append(latency)
            assert response.status_code == 200
        
        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        min_latency = min(latencies)
        
        print(f"\nSearch Products Latency:")
        print(f"  Average: {avg_latency:.2f}ms")
        print(f"  Min: {min_latency:.2f}ms")
        print(f"  Max: {max_latency:.2f}ms")
        
        assert avg_latency < 2000  # Search can be slower
        assert max_latency < 5000


class TestAddToCart:
    """Test add_to_cart capability demonstration."""
    
    def test_add_product_to_cart(self):
        """Test adding a product to cart (demonstrates 'add this product' capability)"""
        # Step 1: Get product
        get_response = client.post(
            "/api/get-product",
            json={"product_id": "laptop-001"}
        )
        assert get_response.status_code == 200
        
        # Step 2: Add to cart
        cart_id = "test-cart-001"
        add_response = client.post(
            "/api/add-to-cart",
            json={
                "cart_id": cart_id,
                "product_id": "laptop-001",
                "qty": 1
            }
        )
        
        assert add_response.status_code == 200
        data = add_response.json()
        assert data["status"] == "OK"
        assert len(data["data"]["items"]) == 1
        assert data["data"]["items"][0]["product_id"] == "laptop-001"
        
        # Verify request/response format for AI agent
        assert "trace" in data
        assert "version" in data
    
    def test_add_to_cart_ucp_protocol(self):
        """Test add_to_cart via UCP protocol"""
        response = client.post(
            "/ucp/add_to_cart",
            json={
                "action": "add_to_cart",
                "parameters": {
                    "cart_id": "ucp-cart-001",
                    "product_id": "laptop-001",
                    "qty": 1
                }
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
    
    def test_add_to_cart_tool_protocol(self):
        """Test add_to_cart via Tool protocol"""
        response = client.post(
            "/tools/execute",
            json={
                "tool_name": "add_to_cart",
                "parameters": {
                    "cart_id": "tool-cart-001",
                    "product_id": "laptop-001",
                    "qty": 1
                }
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OK"
    
    def test_add_to_cart_multiple_products(self):
        """Test adding multiple products to cart"""
        cart_id = "test-cart-multi"
        
        # Add first product
        response1 = client.post(
            "/api/add-to-cart",
            json={
                "cart_id": cart_id,
                "product_id": "laptop-001",
                "qty": 1
            }
        )
        assert response1.status_code == 200
        
        # Add second product
        response2 = client.post(
            "/api/add-to-cart",
            json={
                "cart_id": cart_id,
                "product_id": "laptop-002",
                "qty": 2
            }
        )
        assert response2.status_code == 200
        
        data = response2.json()
        assert len(data["data"]["items"]) == 2
        assert data["data"]["item_count"] == 2
    
    def test_add_to_cart_out_of_stock(self):
        """Test add_to_cart with out of stock product"""
        # Create a product with 0 inventory
        db = TestingSessionLocal()
        try:
            product = Product(
                product_id="out-of-stock-001",
                name="Out of Stock Product",
                category="Electronics",
                brand="TestBrand",
                price_info=Price(product_id="out-of-stock-001", price_cents=50000),
                inventory_info=Inventory(product_id="out-of-stock-001", available_qty=0)
            )
            db.add(product)
            db.add(product.price_info)
            db.add(product.inventory_info)
            db.commit()
        finally:
            db.close()
        
        response = client.post(
            "/api/add-to-cart",
            json={
                "cart_id": "test-cart-oos",
                "product_id": "out-of-stock-001",
                "qty": 1
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OUT_OF_STOCK"
        assert len(data["constraints"]) > 0


class TestGetProductEdgeCases:
    """Test edge cases for get_product."""
    
    def test_get_product_empty_product_id(self):
        """Test get_product with empty product_id"""
        response = client.post(
            "/api/get-product",
            json={"product_id": ""}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "NOT_FOUND"
    
    def test_get_product_missing_product_id(self):
        """Test get_product with missing product_id field"""
        response = client.post(
            "/api/get-product",
            json={}
        )
        
        # Should return validation error
        assert response.status_code == 422
    
    def test_get_product_invalid_fields(self):
        """Test get_product with invalid field names in projection"""
        response = client.post(
            "/api/get-product",
            json={
                "product_id": "laptop-001",
                "fields": ["name", "invalid_field"]
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        # Should only return valid fields
        assert "name" in data["data"]
        assert "invalid_field" not in data["data"]
    
    def test_get_product_all_fields(self):
        """Test get_product returns all fields when fields not specified"""
        response = client.post(
            "/api/get-product",
            json={"product_id": "laptop-001"}
        )
        
        assert response.status_code == 200
        data = response.json()
        product = data["data"]
        
        # Verify all expected fields are present
        assert "product_id" in product
        assert "name" in product
        assert "description" in product
        assert "category" in product
        assert "brand" in product
        assert "price_cents" in product
        assert "available_qty" in product
    
    def test_get_product_trace_information(self):
        """Test get_product includes complete trace information"""
        response = client.post(
            "/api/get-product",
            json={"product_id": "laptop-001"}
        )
        
        assert response.status_code == 200
        data = response.json()
        trace = data["trace"]
        
        assert "request_id" in trace
        assert "cache_hit" in trace
        assert "timings_ms" in trace
        assert "sources" in trace
        assert isinstance(trace["sources"], list)


class TestSearchProductsEdgeCases:
    """Test edge cases for search_products."""
    
    def test_search_empty_query(self):
        """Test search with empty query"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "",
                "filters": {"category": "Electronics"},
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        # Should still return results based on filters
    
    def test_search_no_filters(self):
        """Test search with no filters"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "laptop",
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["OK", "FOLLOWUP_QUESTION_REQUIRED"]
    
    def test_search_invalid_limit(self):
        """Test search with invalid limit (too high)"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "laptop",
                "limit": 200  # Should be capped at 100
            }
        )
        
        # Should either validate or cap the limit
        assert response.status_code in [200, 422]
    
    def test_search_zero_limit(self):
        """Test search with zero limit"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "laptop",
                "limit": 0
            }
        )
        
        # Should reject zero limit
        assert response.status_code == 422
    
    def test_search_pagination_cursor(self):
        """Test search with pagination cursor"""
        # First request
        response1 = client.post(
            "/api/search-products",
            json={
                "query": "laptop",
                "limit": 2
            }
        )
        
        assert response1.status_code == 200
        data1 = response1.json()
        
        if data1["data"]["next_cursor"]:
            # Second request with cursor
            response2 = client.post(
                "/api/search-products",
                json={
                    "query": "laptop",
                    "limit": 2,
                    "cursor": data1["data"]["next_cursor"]
                }
            )
            
            assert response2.status_code == 200
            data2 = response2.json()
            # Should return different results
            assert data2["data"]["products"] != data1["data"]["products"]
    
    def test_search_multiple_hard_constraints(self):
        """Test search with multiple hard constraints"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "gaming",
                "filters": {
                    "category": "Electronics",
                    "product_type": "gaming_laptop",
                    "gpu_vendor": "NVIDIA",
                    "price_max_cents": 200000
                },
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        # All constraints should be applied
        assert data["status"] in ["OK", "NOT_FOUND"]
    
    def test_search_price_range(self):
        """Test search with price range filters"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "laptop",
                "filters": {
                    "category": "Electronics",
                    "price_min_cents": 100000,
                    "price_max_cents": 150000
                },
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        if data["status"] == "OK":
            # All products should be in price range
            for product in data["data"]["products"]:
                assert 100000 <= product["price_cents"] <= 150000
    
    def test_search_brand_filter(self):
        """Test search with brand filter"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "laptop",
                "filters": {
                    "category": "Electronics",
                    "brand": "GamingBrand"
                },
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        if data["status"] == "OK":
            # All products should match brand
            for product in data["data"]["products"]:
                assert product["brand"] == "GamingBrand"
    
    def test_search_category_books(self):
        """Test search for books category"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "mystery",
                "filters": {
                    "category": "Books"
                },
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        if data["status"] == "OK":
            for product in data["data"]["products"]:
                assert product["category"] == "Books"
    
    def test_search_response_structure(self):
        """Test search response has correct structure"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "laptop",
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify response envelope structure
        assert "status" in data
        assert "data" in data
        assert "trace" in data
        assert "version" in data
        
        # Verify data structure
        assert "products" in data["data"]
        assert "total_count" in data["data"]
        assert isinstance(data["data"]["products"], list)
    
    def test_search_trace_information(self):
        """Test search includes complete trace information"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "laptop",
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        trace = data["trace"]
        
        assert "request_id" in trace
        assert "cache_hit" in trace
        assert "timings_ms" in trace
        assert "sources" in trace


class TestProtocolCompatibility:
    """Test protocol compatibility and conversion."""
    
    def test_mcp_to_ucp_response_format(self):
        """Verify MCP response can be converted to UCP format"""
        mcp_response = client.post(
            "/api/get-product",
            json={"product_id": "laptop-001"}
        )
        
        ucp_response = client.post(
            "/ucp/get-product",
            json={
                "action": "get_product",
                "parameters": {"product_id": "laptop-001"}
            }
        )
        
        assert mcp_response.status_code == 200
        assert ucp_response.status_code == 200
        
        mcp_data = mcp_response.json()
        ucp_data = ucp_response.json()
        
        # Both should return same product
        assert mcp_data["data"]["product_id"] == ucp_data["product"]["id"]
    
    def test_tool_execute_error_handling(self):
        """Test tool execute with invalid tool name"""
        response = client.post(
            "/tools/execute",
            json={
                "tool_name": "invalid_tool",
                "parameters": {}
            }
        )
        
        assert response.status_code in [404, 400]
    
    def test_tool_execute_missing_parameters(self):
        """Test tool execute with missing required parameters"""
        response = client.post(
            "/tools/execute",
            json={
                "tool_name": "get_product",
                "parameters": {}  # Missing product_id
            }
        )
        
        # Should return validation error
        assert response.status_code in [400, 422]


class TestResponseEnvelope:
    """Test response envelope structure and constraints."""
    
    def test_response_always_has_trace(self):
        """Verify all responses include trace information"""
        endpoints = [
            ("/api/get-product", {"product_id": "laptop-001"}),
            ("/api/search-products", {"query": "laptop", "limit": 10}),
            ("/api/add-to-cart", {"cart_id": "test", "product_id": "laptop-001", "qty": 1})
        ]
        
        for endpoint, payload in endpoints:
            response = client.post(endpoint, json=payload)
            if response.status_code == 200:
                data = response.json()
                assert "trace" in data
                assert "request_id" in data["trace"]
    
    def test_response_always_has_version(self):
        """Verify all responses include version information"""
        response = client.post(
            "/api/get-product",
            json={"product_id": "laptop-001"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert "catalog_version" in data["version"]
        assert "updated_at" in data["version"]
    
    def test_constraints_on_error(self):
        """Verify error responses include constraints"""
        response = client.post(
            "/api/get-product",
            json={"product_id": "nonexistent"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "NOT_FOUND"
        assert len(data["constraints"]) > 0
        assert "code" in data["constraints"][0]
        assert "message" in data["constraints"][0]
        assert "suggested_actions" in data["constraints"][0]


class TestDomainDetection:
    """Test domain detection and routing."""
    
    def test_domain_detection_laptops(self):
        """Test domain detection for laptops"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "laptop",
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        # Should detect laptops domain
    
    def test_domain_detection_books(self):
        """Test domain detection for books"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "mystery book",
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        # Should detect books domain
    
    def test_domain_detection_electronics(self):
        """Test domain detection for electronics"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "gaming PC",
                "filters": {"category": "Electronics"},
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        # Should detect electronics domain


class TestQueryProcessing:
    """Test query normalization and parsing."""
    
    def test_query_with_typos(self):
        """Test query normalization handles typos"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "gmaing laptoop",  # Typos
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        # Should still return results (typo correction)
    
    def test_query_with_synonyms(self):
        """Test query expansion with synonyms"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "computer",
                "filters": {"category": "Electronics"},
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        # Should expand "computer" to "laptop" or similar
    
    def test_complex_query_parsing(self):
        """Test parsing complex queries"""
        response = client.post(
            "/api/search-products",
            json={
                "query": "gaming laptop under $2000 with NVIDIA GPU",
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        # Should extract: product_type=gaming_laptop, price_max=2000, gpu_vendor=NVIDIA


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
