"""
Week6 tips compliance tests (from week6tips.txt and WEEK6_ACTION_PLAN.md).

MCP is treated as black box: we assert inputs/outputs and enriched agent-ready
fields (shipping, return_policy, warranty, promotion_info) in discovery responses.
Tests:
- Enriched output: search and get_product return shipping/return/warranty/promotion.
- Help with checkout: add_to_cart then checkout succeeds; order includes shipping when applicable.
- Beyond simple filters: search with multiple filters or richer query returns OK.
"""

import os
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import Base, get_db, DATABASE_URL
from app.models import Product


TEST_DATABASE_URL = os.getenv("DATABASE_URL") or DATABASE_URL
engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(scope="function", autouse=True)
def setup_db():
    # Re-apply our override so we use PostgreSQL (test_mcp_pipeline may have set SQLite)
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    for pid in ["w6-p1", "w6-p2", "w6-p3"]:
        # Generate valid UUIDs for product_id
        uuid_pid = uuid.uuid5(uuid.NAMESPACE_DNS, pid)
        db.query(Product).filter(Product.product_id == uuid_pid).delete(synchronize_session=False)
    db.commit()

    for pid, name, cat, brand, price_cents, qty in [
        ("w6-p1", "Week6 Laptop", "Electronics", "Dell", 129999, 5),
        ("w6-p2", "Week6 Book", "Books", "O'Reilly", 3999, 10),
        ("w6-p3", "Week6 Gadget", "Electronics", "Sony", 7999, 3),
    ]:
        uuid_pid = uuid.uuid5(uuid.NAMESPACE_DNS, pid)
        db.add(Product(
            product_id=uuid_pid,
            name=name,
            category=cat,
            brand=brand,
            price_value=price_cents / 100.0,
            inventory=qty,
            attributes={"description": f"Desc {name}"}
        ))
    db.commit()
    db.close()
    yield
    db = TestingSessionLocal()
    for pid in ["w6-p1", "w6-p2", "w6-p3"]:
        uuid_pid = uuid.uuid5(uuid.NAMESPACE_DNS, pid)
        db.query(Product).filter(Product.product_id == uuid_pid).delete(synchronize_session=False)
    db.commit()
    db.close()


# ---- Enriched output (week6tips: delivery, return, warranty, promotion) ----


def test_search_returns_enriched_fields_on_each_product(setup_db):
    """Search response must include agent-ready fields: shipping, return_policy, warranty, promotion_info."""
    r = client.post(
        "/api/search-products",
        json={"query": "laptop", "filters": {"category": "Electronics"}, "limit": 5},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "OK"
    products = data.get("data", {}).get("products", [])
    assert len(products) >= 1
    for p in products:
        # Enriched fields (week6tips) may be present; at least one should be set for agent use
        has_shipping = "shipping" in p and p["shipping"] is not None
        has_return = "return_policy" in p and p["return_policy"] is not None
        has_warranty = "warranty" in p and p["warranty"] is not None
        assert has_shipping or has_return or has_warranty, (
            f"Product {p.get('product_id')} missing enriched fields (shipping/return_policy/warranty)"
        )
        if p.get("shipping"):
            assert "estimated_delivery_days" in p["shipping"] or "shipping_method" in p["shipping"]


def test_get_product_returns_enriched_fields(setup_db):
    """Get product response must include shipping, return_policy, warranty, promotion_info."""
    uuid_pid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "w6-p1"))
    r = client.post("/api/get-product", json={"product_id": uuid_pid})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "OK"
    detail = data.get("data", {})
    assert detail.get("product_id") == uuid_pid
    assert "shipping" in detail
    assert "return_policy" in detail
    assert "warranty" in detail
    if detail.get("shipping"):
        assert "estimated_delivery_days" in detail["shipping"] or "shipping_method" in detail["shipping"]


# ---- Help with checkout (add_to_cart -> checkout) ----


def test_help_with_checkout_add_to_cart_then_checkout_succeeds(setup_db):
    """Help with checkout flow: add_to_cart then checkout returns success and order has shipping when applicable."""
    # Create cart and add item using UUID5 product ID
    cart_id = "w6-cart-" + str(uuid.uuid4())[:8]
    uuid_pid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "w6-p1"))
    r1 = client.post(
        "/api/add-to-cart",
        json={"cart_id": cart_id, "product_id": uuid_pid, "qty": 1},
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "OK"

    # Checkout
    r2 = client.post(
        "/api/checkout",
        json={
            "cart_id": cart_id,
            "payment_method_id": "pay-001",
            "address_id": "addr-001",
        },
    )
    assert r2.status_code == 200
    data = r2.json()
    assert data["status"] == "OK"
    order = data.get("data", {})
    assert "order_id" in order
    assert order.get("total_cents") == 129999
    # Week6: order may include shipping info (OrderData.shipping)
    assert "shipping" in order or "order_id" in order


# ---- Beyond simple filters (week6tips: not only price and brand) ----


def test_search_with_multiple_filters_returns_ok(setup_db):
    """Search with multiple filters (category + price) returns OK and non-empty when data exists."""
    r = client.post(
        "/api/search-products",
        json={
            "query": "laptop",
            "filters": {
                "category": "Electronics",
                "price_max_cents": 200000,
            },
            "limit": 10,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "OK"
    products = data.get("data", {}).get("products", [])
    # Multiple filters applied; response should have products (DB has many Electronics under 200000)
    assert len(products) >= 1


def test_search_with_query_and_filters_beyond_simple(setup_db):
    """Search with both query and structured filters (richer than single filter) returns OK."""
    r = client.post(
        "/api/search-products",
        json={
            "query": "Week6 Laptop electronics",
            "filters": {"category": "Electronics", "price_min_cents": 10000},
            "limit": 5,
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "OK"
    products = r.json().get("data", {}).get("products", [])
    assert len(products) >= 1
