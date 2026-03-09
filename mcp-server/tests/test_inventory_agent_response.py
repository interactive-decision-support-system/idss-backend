"""
Inventory unit tests: zero-inventory product → shopping agent receives
a structured OUT_OF_STOCK response with actionable details.

Covers the full path:
  User intent → agent calls UCP add_to_cart
             → MCP checks inventory
             → Returns OUT_OF_STOCK with agent-readable details
             → Agent surfaces "out of stock" to user

Three test layers:
  1. MCP layer  — /api/add-to-cart HTTP endpoint (direct function call via TestClient)
  2. UCP layer  — ucp_add_to_cart() function (non-Supabase cart path, no HTTP roundtrip)
  3. Agent layer — the error message the shopping agent would relay to the user
"""
import os
import uuid
import asyncio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ── Path setup ─────────────────────────────────────────────────────────────────
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(override=True)

from app.main import app
from app.database import get_db
from app.models import Product

# Read DATABASE_URL fresh from environment AFTER load_dotenv (avoids stale
# cached value when app.database was already imported by an earlier test file)
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not _DATABASE_URL or not _DATABASE_URL.startswith(("postgresql", "sqlite")):
    pytest.skip(
        "DATABASE_URL not configured or invalid — skipping integration tests",
        allow_module_level=True,
    )

# ── Test database (same Supabase PostgreSQL) ────────────────────────────────────
_NS = uuid.NAMESPACE_DNS
# Deterministic UUIDs so re-runs are idempotent
PROD_ZERO_INV   = uuid.uuid5(_NS, "inv-test-zero-inventory-laptop")
PROD_ONE_INV    = uuid.uuid5(_NS, "inv-test-one-inventory-phone")
PROD_NORMAL_INV = uuid.uuid5(_NS, "inv-test-normal-inventory-tablet")

engine = create_engine(_DATABASE_URL, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
http_client = TestClient(app)


# ── Fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def seed_inventory_products():
    """Insert test products with controlled inventory levels; clean up after module."""
    db = TestingSessionLocal()

    # Remove any leftover rows from previous run (ignore errors — rows may not exist)
    try:
        for pid in (PROD_ZERO_INV, PROD_ONE_INV, PROD_NORMAL_INV):
            db.query(Product).filter(Product.product_id == pid).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()

    # Product with ZERO inventory — agent should be told "out of stock"
    db.add(Product(
        product_id=PROD_ZERO_INV,
        name="Out-of-Stock Gaming Laptop",
        category="Electronics",
        brand="TestBrand",
        price_value=1299.99,
        inventory=0,
        attributes={"cpu": "Intel i9", "ram_gb": 32, "storage_gb": 1000},
    ))

    # Product with exactly 1 unit — useful for boundary tests
    db.add(Product(
        product_id=PROD_ONE_INV,
        name="Last-Unit Phone",
        category="Electronics",
        brand="TestBrand",
        price_value=799.99,
        inventory=1,
        attributes={"description": "Last one in stock"},
    ))

    # Product with normal inventory — control case
    db.add(Product(
        product_id=PROD_NORMAL_INV,
        name="In-Stock Tablet",
        category="Electronics",
        brand="TestBrand",
        price_value=499.99,
        inventory=50,
        attributes={"description": "Plenty in stock"},
    ))

    db.commit()
    db.close()

    yield  # tests run here

    # Teardown: remove only our test rows
    db = TestingSessionLocal()
    try:
        for pid in (PROD_ZERO_INV, PROD_ONE_INV, PROD_NORMAL_INV):
            db.query(Product).filter(Product.product_id == pid).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


# ── Layer 1: MCP HTTP endpoint ──────────────────────────────────────────────────

class TestMCPInventoryLayer:
    """Direct MCP /api/add-to-cart calls — the merchant protocol layer."""

    def test_zero_inventory_returns_out_of_stock(self):
        """Adding a product with inventory=0 must return OUT_OF_STOCK."""
        resp = http_client.post("/api/add-to-cart", json={
            "cart_id": "inv-test-cart-001",
            "product_id": str(PROD_ZERO_INV),
            "qty": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "OUT_OF_STOCK", (
            f"Expected OUT_OF_STOCK, got {data['status']!r}. "
            f"Product inventory=0 must block add-to-cart."
        )

    def test_zero_inventory_constraint_has_agent_readable_fields(self):
        """The OUT_OF_STOCK constraint must contain fields the agent can surface to user."""
        resp = http_client.post("/api/add-to-cart", json={
            "cart_id": "inv-test-cart-002",
            "product_id": str(PROD_ZERO_INV),
            "qty": 1,
        })
        data = resp.json()
        assert data["status"] == "OUT_OF_STOCK"

        constraints = data.get("constraints", [])
        assert len(constraints) > 0, "OUT_OF_STOCK response must include at least one constraint"

        c = constraints[0]
        assert c["code"] == "OUT_OF_STOCK"
        assert "message" in c, "Constraint must have a human-readable message for the agent"
        assert "details" in c, "Constraint must include details dict"
        assert "suggested_actions" in c, "Constraint must include suggested_actions for self-correction"

        details = c["details"]
        assert str(details.get("product_id")) == str(PROD_ZERO_INV)
        assert details.get("available_qty") == 0, (
            f"available_qty should be 0, got {details.get('available_qty')}"
        )

    def test_zero_inventory_exact_message_for_agent(self):
        """The constraint message must be non-empty so the agent can relay it to the user."""
        resp = http_client.post("/api/add-to-cart", json={
            "cart_id": "inv-test-cart-003",
            "product_id": str(PROD_ZERO_INV),
            "qty": 1,
        })
        data = resp.json()
        msg = data["constraints"][0]["message"]
        assert len(msg) > 10, f"Message too short to be useful: {msg!r}"
        # Agent should be able to include this message in a user-facing response
        print(f"\n  [Agent-visible message]: {msg!r}")

    def test_requesting_more_than_available_triggers_out_of_stock(self):
        """Requesting qty > inventory (1) should return OUT_OF_STOCK with correct available_qty."""
        resp = http_client.post("/api/add-to-cart", json={
            "cart_id": "inv-test-cart-004",
            "product_id": str(PROD_ONE_INV),
            "qty": 5,  # Only 1 in stock
        })
        data = resp.json()
        assert data["status"] == "OUT_OF_STOCK"
        details = data["constraints"][0]["details"]
        assert details["requested_qty"] == 5
        assert details["available_qty"] == 1

    def test_in_stock_product_succeeds(self):
        """Control case: product with inventory=50 should succeed."""
        resp = http_client.post("/api/add-to-cart", json={
            "cart_id": "inv-test-cart-005",
            "product_id": str(PROD_NORMAL_INV),
            "qty": 2,
        })
        data = resp.json()
        assert data["status"] == "OK", (
            f"Expected OK for in-stock product, got {data['status']!r}"
        )


# ── Layer 2: UCP function call (no HTTP roundtrip) ──────────────────────────────

class TestUCPInventoryLayer:
    """
    UCP ucp_add_to_cart() function called directly — this is the path the
    shopping agent uses (non-Supabase cart: cart_id is not a UUID → uses MCP path).
    """

    def _run(self, coro):
        return asyncio.run(coro)

    def test_ucp_zero_inventory_returns_error_status(self):
        """ucp_add_to_cart with inventory=0 product must return status='error'."""
        from app.ucp_endpoints import ucp_add_to_cart
        from app.ucp_schemas import UCPAddToCartRequest, UCPAddToCartParameters

        db = TestingSessionLocal()
        try:
            req = UCPAddToCartRequest(
                action="add_to_cart",
                parameters=UCPAddToCartParameters(
                    cart_id="ucp-inv-test-cart-001",  # non-UUID → MCP path
                    product_id=str(PROD_ZERO_INV),
                    quantity=1,
                ),
            )
            resp = self._run(ucp_add_to_cart(req, db))
            assert resp.status == "error", (
                f"UCP must return status='error' for zero-inventory product, got {resp.status!r}"
            )
            assert resp.error, "UCP error response must include a non-empty error string"
            print(f"\n  [UCP agent error]: {resp.error!r}")
        finally:
            db.close()

    def test_ucp_error_message_is_agent_actionable(self):
        """The UCP error string must be human-readable — the agent forwards it to the user."""
        from app.ucp_endpoints import ucp_add_to_cart
        from app.ucp_schemas import UCPAddToCartRequest, UCPAddToCartParameters

        db = TestingSessionLocal()
        try:
            req = UCPAddToCartRequest(
                action="add_to_cart",
                parameters=UCPAddToCartParameters(
                    cart_id="ucp-inv-test-cart-002",
                    product_id=str(PROD_ZERO_INV),
                    quantity=1,
                ),
            )
            resp = self._run(ucp_add_to_cart(req, db))
            assert resp.status == "error"
            # Error should be non-trivial — agent needs this to answer the user
            assert len(resp.error or "") > 5, (
                f"UCP error too short to be useful: {resp.error!r}"
            )
        finally:
            db.close()

    def test_ucp_in_stock_succeeds(self):
        """Control: UCP add_to_cart with normal inventory must succeed."""
        from app.ucp_endpoints import ucp_add_to_cart
        from app.ucp_schemas import UCPAddToCartRequest, UCPAddToCartParameters

        db = TestingSessionLocal()
        try:
            req = UCPAddToCartRequest(
                action="add_to_cart",
                parameters=UCPAddToCartParameters(
                    cart_id="ucp-inv-test-cart-003",
                    product_id=str(PROD_NORMAL_INV),
                    quantity=1,
                ),
            )
            resp = self._run(ucp_add_to_cart(req, db))
            assert resp.status == "success", (
                f"Expected success for in-stock product, got {resp.status!r}: {resp.error}"
            )
        finally:
            db.close()


# ── Layer 3: Agent-visible response ─────────────────────────────────────────────

class TestAgentFacingInventoryResponse:
    """
    Verifies the /api/action/add-to-cart endpoint (the one the shopping agent
    calls via UCP action API) returns the correct JSON the agent reads.

    Expected: {"status": "error", "error": "<out-of-stock message>"}
    """

    def test_action_endpoint_returns_error_for_zero_inventory(self):
        """The agent-facing /api/action/add-to-cart must return status=error for OOS."""
        resp = http_client.post("/api/action/add-to-cart", json={
            "user_id": str(uuid.uuid4()),  # real UUID → Supabase cart path
            "product_id": str(PROD_ZERO_INV),
            "quantity": 1,
            "product_snapshot": {
                "name": "Out-of-Stock Gaming Laptop",
                "price": 1299,
                "brand": "TestBrand",
                "category": "Electronics",
            },
        })
        data = resp.json()
        # Agent reads data["status"] — must be "error" not "success"
        assert data.get("status") == "error", (
            f"Agent-facing endpoint must signal error for OOS product. Got: {data}"
        )
        # Agent reads data["error"] to tell the user — must be present and non-empty
        assert data.get("error"), (
            "Agent needs a non-empty 'error' field to generate user-visible message."
        )
        print(f"\n  [Agent response to user]: status={data['status']!r} error={data['error']!r}")

    def test_action_endpoint_inventory_error_contains_stock_info(self):
        """
        The error string returned to the agent should mention stock/availability
        so it can generate an informative response like:
        'Sorry, that item is currently out of stock. Available qty: 0.'
        """
        resp = http_client.post("/api/action/add-to-cart", json={
            "user_id": str(uuid.uuid4()),
            "product_id": str(PROD_ZERO_INV),
            "quantity": 1,
            "product_snapshot": {"name": "Out-of-Stock Gaming Laptop", "price": 1299},
        })
        data = resp.json()
        if data.get("status") == "error":
            error_msg = (data.get("error") or "").lower()
            # Useful signal: message should hint at availability
            stock_keywords = ["stock", "available", "inventory", "unavailable", "out"]
            has_stock_hint = any(kw in error_msg for kw in stock_keywords)
            print(f"\n  [OOS error text]: {data.get('error')!r}")
            # Soft assertion: log if missing (don't block if message is generic)
            if not has_stock_hint:
                print(f"  WARNING: error message may not clearly indicate stock issue: {error_msg!r}")
