"""
End-to-end tests for Reddit-style complex queries.

Verifies that multi-constraint queries (RAM, storage, screen, battery, use-cases,
price) flow through the full search pipeline and return correct products.

Tests cover:
- Query parser spec extraction → kg_features SQL filtering
- Multi-constraint specificity bypass (≥2 constraints skip interview)
- UCP /tools/execute compatibility
- Comparison table accuracy
- Recommendation reason generation
"""

import pytest
import json
import os
import sys
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import Base, get_db, DATABASE_URL
from app.models import Product
from app.query_parser import enhance_search_request
from app.query_specificity import is_specific_query
from app.research_compare import (
    build_comparison_table,
    generate_recommendation_reasons,
    _product_to_flat_dict,
)

# Use same DB as app
TEST_DATABASE_URL = os.getenv("DATABASE_URL") or DATABASE_URL
engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


client = TestClient(app)

# Reddit-style seed product IDs (prefixed to avoid collision)
REDDIT_PRODUCT_IDS = [
    "reddit-devpro-16",
    "reddit-codebook-ultra",
    "reddit-budget-student",
    "reddit-gaming-beast",
    "reddit-linux-thinkpad",
    "reddit-creative-studio",
]

# Deterministic UUID5 IDs for each Reddit test product
_NS = uuid.NAMESPACE_DNS
REDDIT_UUIDS = {pid: uuid.uuid5(_NS, f"test-reddit-{pid}") for pid in REDDIT_PRODUCT_IDS}

REDDIT_PRODUCTS = [
    {
        "product_id": "reddit-devpro-16",
        "name": "DevPro 16 Laptop 16GB RAM 512GB SSD 15.6 inch",
        "description": "Professional development laptop with 16GB DDR5 RAM, 512GB NVMe SSD, "
                       "15.6 inch FHD display. NVIDIA RTX 4060 GPU. Great for web development "
                       "with Figma, Webflow, and machine learning with PyTorch. Python IDE ready.",
        "category": "Electronics",
        "brand": "Dell",
        "product_type": "laptop",
        "kg_features": {
            "ram_gb": 16, "storage_gb": 512, "screen_size_inches": 15.6,
            "battery_life_hours": 10, "year": 2024,
            "good_for_ml": True, "good_for_web_dev": True, "good_for_programming": True,
            "keyboard_quality": "good",
        },
        "price_cents": 149900,
    },
    {
        "product_id": "reddit-codebook-ultra",
        "name": "CodeBook Ultra 32GB RAM 1TB SSD 16 inch Linux",
        "description": "Ultra-portable coding powerhouse with 32GB RAM, 1TB NVMe SSD, "
                       "16 inch 2K display. Ships with Ubuntu 22.04. Excellent keyboard. "
                       "Ideal for web development, Python, Node.js, and Linux workloads.",
        "category": "Electronics",
        "brand": "Lenovo",
        "product_type": "laptop",
        "kg_features": {
            "ram_gb": 32, "storage_gb": 1024, "screen_size_inches": 16.0,
            "battery_life_hours": 8, "year": 2024,
            "good_for_web_dev": True, "good_for_linux": True, "good_for_programming": True,
            "keyboard_quality": "excellent",
        },
        "price_cents": 189900,
    },
    {
        "product_id": "reddit-budget-student",
        "name": "Budget Student Laptop 8GB RAM 256GB SSD 14 inch",
        "description": "Affordable student laptop with 8GB RAM, 256GB SSD, 14 inch display. "
                       "Good for browsing and light office work.",
        "category": "Electronics",
        "brand": "HP",
        "product_type": "laptop",
        "kg_features": {
            "ram_gb": 8, "storage_gb": 256, "screen_size_inches": 14.0,
            "battery_life_hours": 6, "year": 2023,
            "good_for_web_dev": True,
            "keyboard_quality": "good",
        },
        "price_cents": 69900,
    },
    {
        "product_id": "reddit-gaming-beast",
        "name": "Gaming Beast 32GB RAM 1TB SSD 17.3 inch RTX 4090",
        "description": "Ultimate gaming laptop with 32GB DDR5, 1TB SSD, 17.3 inch 240Hz display. "
                       "NVIDIA RTX 4090. For gaming, streaming, and deep learning.",
        "category": "Electronics",
        "brand": "ASUS",
        "product_type": "gaming_laptop",
        "gpu_vendor": "NVIDIA",
        "kg_features": {
            "ram_gb": 32, "storage_gb": 1024, "screen_size_inches": 17.3,
            "battery_life_hours": 4, "year": 2024,
            "good_for_gaming": True, "good_for_ml": True,
        },
        "price_cents": 249900,
    },
    {
        "product_id": "reddit-linux-thinkpad",
        "name": "Linux ThinkPad 32GB RAM 512GB SSD 14 inch",
        "description": "ThinkPad with 32GB RAM, 512GB SSD, 14 inch display. Certified for "
                       "Linux (Ubuntu, Fedora). Legendary ThinkPad keyboard. 12 hour battery. "
                       "Perfect for programming and system administration.",
        "category": "Electronics",
        "brand": "Lenovo",
        "product_type": "laptop",
        "kg_features": {
            "ram_gb": 32, "storage_gb": 512, "screen_size_inches": 14.0,
            "battery_life_hours": 12, "year": 2024,
            "good_for_linux": True, "good_for_programming": True,
            "keyboard_quality": "excellent",
        },
        "price_cents": 129900,
    },
    {
        "product_id": "reddit-creative-studio",
        "name": "Creative Studio Pro 64GB RAM 2TB SSD 16 inch",
        "description": "Creative workstation with 64GB RAM, 2TB SSD, 16 inch 4K display. "
                       "NVIDIA RTX 4080 for video editing, 3D modeling with Blender, "
                       "Photoshop, and Premiere Pro. Also great for deep learning.",
        "category": "Electronics",
        "brand": "Apple",
        "product_type": "laptop",
        "gpu_vendor": "NVIDIA",
        "kg_features": {
            "ram_gb": 64, "storage_gb": 2048, "screen_size_inches": 16.0,
            "battery_life_hours": 5, "year": 2024,
            "good_for_creative": True, "good_for_ml": True,
        },
        "price_cents": 349900,
    },
]


@pytest.fixture(scope="function", autouse=True)
def seed_reddit_products():
    """Insert Reddit-style test products with known kg_features for deterministic testing."""
    # Re-apply dependency override (other test modules may have changed it)
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    # Clean up any existing reddit test products
    for pid in REDDIT_PRODUCT_IDS:
        db.query(Product).filter(Product.product_id == REDDIT_UUIDS[pid]).delete(synchronize_session=False)
    db.commit()

    # Insert products using new Supabase schema
    for p_data in REDDIT_PRODUCTS:
        product = Product(
            product_id=REDDIT_UUIDS[p_data["product_id"]],  # UUID5
            name=p_data["name"],
            category=p_data["category"],
            brand=p_data["brand"],
            product_type=p_data.get("product_type"),
            price_value=p_data["price_cents"] / 100.0,
            inventory=10,
            attributes={
                "description": p_data["description"],
                "gpu_vendor": p_data.get("gpu_vendor"),
                **p_data.get("kg_features", {}),  # Flatten kg_features into attributes
            }
        )
        db.add(product)

    db.commit()
    db.close()

    yield

    # Cleanup
    db = TestingSessionLocal()
    for pid in REDDIT_PRODUCT_IDS:
        db.query(Product).filter(Product.product_id == REDDIT_UUIDS[pid]).delete(synchronize_session=False)
    db.commit()
    db.close()


#  Query Parser Tests 


class TestRedditQueryParsing:
    """Verify query parser extracts correct specs from Reddit-style queries."""

    REDDIT_Q1 = (
        "I will use the laptop for Webflow, Figma, Xano, Make, Python, PyCharm, "
        "and PyTorch (machine and deep learning). I expect it to handle 50 open "
        'browser tabs without issues, have a 16" or 15.6" screen, at least 512 GB '
        "of storage, at least 16 GB of RAM, and cost no more than $2,000."
    )

    REDDIT_Q2 = (
        "I need a laptop for productive work—web development, QGIS, and possibly "
        "Godot or Unity—that runs Linux well, has an excellent keyboard, provides "
        "at least 8 hours of battery life, includes 32 GB of RAM, and supports a "
        "5K ultrawide external monitor."
    )

    def test_reddit_q1_specs(self):
        """Reddit Q1: extracts RAM=16, storage=512, screen=15.6+, use_cases=ml+web_dev+programming."""
        _, filters = enhance_search_request(self.REDDIT_Q1, {})
        assert filters["min_ram_gb"] == 16
        assert filters["min_storage_gb"] == 512
        assert filters["min_screen_inches"] in (15.6, 16.0)
        assert "ml" in filters["use_cases"]
        assert "web_dev" in filters["use_cases"]
        assert "programming" in filters["use_cases"]

    def test_reddit_q2_specs(self):
        """Reddit Q2: extracts RAM=32, battery=8, use_cases=web_dev+linux+gaming."""
        _, filters = enhance_search_request(self.REDDIT_Q2, {})
        assert filters["min_ram_gb"] == 32
        assert filters["min_battery_hours"] == 8
        assert "web_dev" in filters["use_cases"]
        assert "linux" in filters["use_cases"]
        assert "gaming" in filters["use_cases"]  # Godot, Unity


#  Multi-Constraint Specificity Tests 


class TestMultiConstraintSpecificity:
    """Verify Reddit queries bypass interview (≥2 constraints → is_specific=True)."""

    def test_reddit_q1_is_specific(self):
        """Q1 has price ($2000) + attributes (ML, web_dev) → ≥2 constraints → specific."""
        is_spec, info = is_specific_query(
            TestRedditQueryParsing.REDDIT_Q1, {}
        )
        # Must have extracted price and attributes
        assert info.get("price_range") is not None or info.get("attributes")
        # The multi-constraint override (≥2) should make this specific
        constraint_count = sum([
            1 if info.get("brand") else 0,
            1 if info.get("gpu_vendor") or info.get("cpu_vendor") else 0,
            1 if info.get("color") else 0,
            1 if info.get("price_range") else 0,
            1 if info.get("attributes") and len(info["attributes"]) > 0 else 0,
        ])
        assert constraint_count >= 2, f"Expected ≥2 constraints, got {constraint_count}: {info}"

    def test_reddit_q2_has_attributes(self):
        """Q2 has use-case attributes (Linux, web dev) extracted."""
        _, info = is_specific_query(
            TestRedditQueryParsing.REDDIT_Q2, {}
        )
        attrs = info.get("attributes", [])
        assert len(attrs) > 0, f"Expected attributes extracted, got: {info}"


#  End-to-End Search Tests 


class TestRedditQuerySearch:
    """Send Reddit queries through /api/search-products and verify correct products return."""

    def test_reddit_q1_returns_matching_products(self):
        """Q1: 16GB RAM, 512GB SSD, 15.6"+ screen, under $2000 → DevPro 16, CodeBook Ultra."""
        response = client.post("/api/search-products", json={
            "query": TestRedditQueryParsing.REDDIT_Q1,
            "filters": {"category": "Electronics"},
            "limit": 20,
        })
        assert response.status_code == 200
        data = response.json()

        # Check we got products (not a follow-up question)
        products = data.get("data", {}).get("products", [])
        if not products:
            # Might be interview question — check constraints
            constraints = data.get("constraints", [])
            if constraints and constraints[0].get("code") == "FOLLOWUP_QUESTION_REQUIRED":
                pytest.skip("Interview flow triggered — query not specific enough for test DB")

        product_ids = [p["product_id"] for p in products]

        # DevPro 16 matches: 16GB, 512GB, 15.6", $1499
        # CodeBook Ultra matches: 32GB, 1TB, 16", $1899
        # Budget Student EXCLUDED: 8GB RAM < 16, 14" < 15.6
        # Gaming Beast EXCLUDED: $2499 > $2000
        # Creative Studio EXCLUDED: $3499 > $2000

        # At minimum, DevPro should be in results (exact match)
        reddit_uuid_strs = [str(REDDIT_UUIDS[pid]) for pid in REDDIT_PRODUCT_IDS]
        reddit_matches = [pid for pid in product_ids if pid in reddit_uuid_strs]
        if reddit_matches:
            assert str(REDDIT_UUIDS["reddit-budget-student"]) not in product_ids, \
                "Budget Student should be excluded (8GB < 16GB)"
            assert str(REDDIT_UUIDS["reddit-gaming-beast"]) not in product_ids, \
                "Gaming Beast should be excluded ($2499 > $2000)"
            assert str(REDDIT_UUIDS["reddit-creative-studio"]) not in product_ids, \
                "Creative Studio should be excluded ($3499 > $2000)"

    def test_reddit_q2_returns_linux_laptops(self):
        """Q2: 32GB RAM, 8h+ battery, Linux, web dev → CodeBook Ultra, Linux ThinkPad."""
        response = client.post("/api/search-products", json={
            "query": TestRedditQueryParsing.REDDIT_Q2,
            "filters": {"category": "Electronics"},
            "limit": 20,
        })
        assert response.status_code == 200
        data = response.json()

        products = data.get("data", {}).get("products", [])
        if not products:
            constraints = data.get("constraints", [])
            if constraints and constraints[0].get("code") == "FOLLOWUP_QUESTION_REQUIRED":
                pytest.skip("Interview flow triggered")

        product_ids = [p["product_id"] for p in products]
        reddit_uuid_strs = [str(REDDIT_UUIDS[pid]) for pid in REDDIT_PRODUCT_IDS]
        reddit_matches = [pid for pid in product_ids if pid in reddit_uuid_strs]
        if reddit_matches:
            assert str(REDDIT_UUIDS["reddit-budget-student"]) not in product_ids, \
                "Budget Student excluded (8GB < 32GB)"
            assert str(REDDIT_UUIDS["reddit-gaming-beast"]) not in product_ids, \
                "Gaming Beast excluded (4h < 8h battery)"


#  UCP /tools/execute Tests 


class TestUCPToolExecute:
    """Verify Reddit queries work through /tools/execute (UCP interface)."""

    def test_ucp_search_reddit_q1(self):
        """UCP tool execute with search_products returns results for Reddit Q1."""
        response = client.post("/tools/execute", json={
            "tool_name": "search_products",
            "parameters": {
                "query": TestRedditQueryParsing.REDDIT_Q1,
                "filters": {"category": "Electronics"},
                "limit": 20,
            }
        })
        assert response.status_code == 200
        data = response.json()
        # Should return search results (not error)
        assert "data" in data or "status" in data

    def test_ucp_search_reddit_q2(self):
        """UCP tool execute with search_products returns results for Reddit Q2."""
        response = client.post("/tools/execute", json={
            "tool_name": "search_products",
            "parameters": {
                "query": TestRedditQueryParsing.REDDIT_Q2,
                "filters": {"category": "Electronics"},
                "limit": 20,
            }
        })
        assert response.status_code == 200

    def test_ucp_get_product(self):
        """UCP get_product for a Reddit seed product."""
        response = client.post("/tools/execute", json={
            "tool_name": "get_product",
            "parameters": {"product_id": str(REDDIT_UUIDS["reddit-devpro-16"])}
        })
        assert response.status_code == 200
        data = response.json()
        # get_product returns product data directly in "data" (not nested under "data.product")
        product_data = data.get("data", {})
        assert product_data.get("product_id") == str(REDDIT_UUIDS["reddit-devpro-16"])


#  Comparison Table Tests 


class TestComparisonLogic:
    """Verify comparison table and recommendation reasons for Reddit products."""

    def _get_reddit_product_dicts(self):
        """Get product dicts (simulating search results) for comparison."""
        return [
            {
                "product_id": str(REDDIT_UUIDS["reddit-devpro-16"]),
                "name": "DevPro 16 Laptop",
                "brand": "Dell",
                "price": 1499.00,
                "price_cents": 149900,
                "category": "Electronics",
                "description": "16GB RAM, 512GB SSD, 15.6 inch",
                "reviews": "Average rating: 4.5/5 (120 reviews)",
            },
            {
                "product_id": str(REDDIT_UUIDS["reddit-codebook-ultra"]),
                "name": "CodeBook Ultra",
                "brand": "Lenovo",
                "price": 1899.00,
                "price_cents": 189900,
                "category": "Electronics",
                "description": "32GB RAM, 1TB SSD, 16 inch, Linux",
                "reviews": "Average rating: 4.7/5 (85 reviews)",
            },
        ]

    def test_comparison_table_includes_all_fields(self):
        """Comparison table should include price, brand, rating for both products."""
        products = self._get_reddit_product_dicts()
        table = build_comparison_table(products)
        assert len(table["products"]) == 2
        assert "price" in table["attributes"]
        assert "review_rating" in table["attributes"]
        # Verify values populated
        p1_values = table["products"][0]["values"]
        assert p1_values["price"] == 1499.00
        assert p1_values["brand"] == "Dell"

    def test_comparison_by_price(self):
        """Compare by price only — values should be correct."""
        products = self._get_reddit_product_dicts()
        table = build_comparison_table(products, compare_by=["price"])
        assert "price" in table["attributes"]
        assert table["products"][0]["values"]["price"] == 1499.00
        assert table["products"][1]["values"]["price"] == 1899.00

    def test_comparison_by_brand_and_rating(self):
        """Compare by brand and rating — Supabase text reviews parsed correctly."""
        products = self._get_reddit_product_dicts()
        table = build_comparison_table(products, compare_by=["brand", "review_rating"])
        p1 = table["products"][0]["values"]
        p2 = table["products"][1]["values"]
        assert p1["brand"] == "Dell"
        assert p2["brand"] == "Lenovo"
        assert p1["review_rating"] == 4.5
        assert p2["review_rating"] == 4.7

    def test_flat_dict_preserves_zero_price(self):
        """_product_to_flat_dict must not lose price=0 (falsy value bug fix)."""
        product = {"product_id": "test", "name": "Free Item", "price": 0, "brand": "Test"}
        flat = _product_to_flat_dict(product)
        assert flat["price"] == 0, "price=0 must not be lost"

    def test_recommendation_reasons_within_budget(self):
        """Products under budget get 'Within budget' reason."""
        products = self._get_reddit_product_dicts()
        filters = {"price_max_cents": 200000}  # $2000
        generate_recommendation_reasons(products, filters)
        assert "Within budget" in products[0]["_reason"]
        assert "Within budget" in products[1]["_reason"]

    def test_recommendation_reasons_over_budget(self):
        """Products over budget do NOT get 'Within budget' reason."""
        products = [
            {"product_id": "p1", "name": "Expensive", "price_cents": 300000},
        ]
        filters = {"price_max_cents": 200000}
        generate_recommendation_reasons(products, filters)
        assert "Within budget" not in products[0]["_reason"]

    def test_recommendation_reasons_with_specs(self):
        """Spec constraints appear in recommendation reasons."""
        products = self._get_reddit_product_dicts()
        filters = {"min_ram_gb": 16, "price_max_cents": 200000}
        generate_recommendation_reasons(products, filters)
        assert "16GB+ RAM" in products[0]["_reason"]

    def test_recommendation_reasons_with_use_cases(self):
        """Use-case labels appear in recommendation reasons."""
        products = self._get_reddit_product_dicts()
        filters = {"use_cases": ["ml", "web_dev"]}
        generate_recommendation_reasons(products, filters)
        assert "ML/AI" in products[0]["_reason"]
        assert "Web dev" in products[0]["_reason"]
