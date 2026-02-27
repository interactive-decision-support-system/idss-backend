"""
ACP (Agentic Commerce Protocol) Tests — spec version 2026-01-30.

Tests all layers of the ACP integration:
  Layer 1 — Protocol config (COMMERCE_PROTOCOL env var)
  Layer 2 — ACP schema models
  Layer 3 — ACP endpoint functions (unit, no HTTP)
  Layer 4 — ACP HTTP routes via TestClient
  Layer 5 — Protocol header middleware
  Layer 6 — OpenAI function-calling tools (legacy)
"""

import os
import sys
import asyncio
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ============================================================================
# Layer 1 — Protocol Config
# ============================================================================

class TestProtocolConfig:
    def test_get_protocol_returns_string(self):
        from app.protocol_config import get_protocol
        result = get_protocol()
        assert isinstance(result, str)
        assert result in ("ucp", "acp")

    def test_is_acp_and_is_ucp_are_mutually_exclusive(self):
        from app.protocol_config import is_acp, is_ucp
        assert is_acp() != is_ucp()

    def test_is_ucp_when_not_acp(self):
        from app.protocol_config import COMMERCE_PROTOCOL, is_ucp, is_acp
        if COMMERCE_PROTOCOL != "acp":
            assert is_ucp() is True
            assert is_acp() is False


# ============================================================================
# Layer 2 — ACP Schema Models
# ============================================================================

class TestACPSchemas:
    def test_line_item_input_basic(self):
        from app.acp_schemas import ACPLineItemInput
        item = ACPLineItemInput(
            product_id="prod-1",
            name="Test Laptop",
            unit_amount=79999,  # $799.99 in cents
            quantity=2,
        )
        assert item.product_id == "prod-1"
        assert item.quantity == 2
        assert item.unit_amount == 79999
        assert item.image_url is None

    def test_create_session_request_defaults(self):
        from app.acp_schemas import ACPCreateSessionRequest, ACPLineItemInput
        req = ACPCreateSessionRequest(
            line_items=[ACPLineItemInput(product_id="p1", name="P", unit_amount=999, quantity=1)]
        )
        assert req.currency == "USD"
        assert req.buyer_email is None
        assert req.capabilities is None

    def test_create_session_request_accepts_capabilities(self):
        from app.acp_schemas import ACPCreateSessionRequest, ACPLineItemInput
        req = ACPCreateSessionRequest(
            line_items=[ACPLineItemInput(product_id="p1", name="P", unit_amount=999, quantity=1)],
            capabilities={"payment_handlers": ["stripe"], "interventions": []},
        )
        assert req.capabilities is not None
        assert "payment_handlers" in req.capabilities

    def test_totals_model(self):
        from app.acp_schemas import ACPTotals
        t = ACPTotals(subtotal_cents=10000, shipping_cents=999, tax_cents=887, total_cents=11886)
        assert t.total_cents == 11886

    def test_checkout_session_protocol_field(self):
        from app.acp_schemas import ACPCheckoutSession, ACPTotals
        s = ACPCheckoutSession(
            id="acp-session-abc",
            status="incomplete",
            line_items=[],
            totals=ACPTotals(subtotal_cents=0, total_cents=0),
        )
        assert s.protocol == "acp"

    def test_checkout_session_has_messages_field(self):
        from app.acp_schemas import ACPCheckoutSession, ACPTotals
        s = ACPCheckoutSession(
            id="acp-session-abc",
            status="incomplete",
            line_items=[],
            totals=ACPTotals(subtotal_cents=0, total_cents=0),
        )
        assert isinstance(s.messages, list)

    def test_checkout_session_has_fulfillment_options(self):
        from app.acp_schemas import ACPCheckoutSession, ACPTotals
        s = ACPCheckoutSession(
            id="acp-session-abc",
            status="incomplete",
            line_items=[],
            totals=ACPTotals(subtotal_cents=0, total_cents=0),
        )
        assert isinstance(s.fulfillment_options, list)

    def test_product_feed_item(self):
        from app.acp_schemas import ACPProductFeedItem
        item = ACPProductFeedItem(
            id="p1", title="Laptop", price_dollars=999.0,
            availability="in_stock", inventory=5,
            image_url=None, product_url="https://example.com/p1",
        )
        assert item.currency == "USD"
        assert item.availability == "in_stock"

    def test_shipping_rate_constants(self):
        from app.acp_schemas import ACP_SHIPPING_RATES_CENTS
        assert ACP_SHIPPING_RATES_CENTS["standard"] == 999
        assert ACP_SHIPPING_RATES_CENTS["express"] == 1999
        assert ACP_SHIPPING_RATES_CENTS["overnight"] == 2999

    def test_tax_rate_is_positive(self):
        from app.acp_schemas import ACP_TAX_RATE
        assert 0 < ACP_TAX_RATE < 1

    def test_fulfillment_options_constant(self):
        from app.acp_schemas import ACP_FULFILLMENT_OPTIONS
        assert len(ACP_FULFILLMENT_OPTIONS) == 3
        ids = {o.id for o in ACP_FULFILLMENT_OPTIONS}
        assert ids == {"standard", "express", "overnight"}

    def test_payment_data_model(self):
        from app.acp_schemas import ACPPaymentData, ACPPaymentInstrument
        pd = ACPPaymentData(
            handler_id="stripe",
            instrument=ACPPaymentInstrument(type="card", credential_token="spt_test_123"),
        )
        assert pd.handler_id == "stripe"
        assert pd.instrument.credential_token == "spt_test_123"


# ============================================================================
# Layer 3 — ACP Endpoint Functions (unit, no HTTP)
# ============================================================================

def _run(coro):
    return asyncio.run(coro)


def _mock_db():
    return MagicMock()


class TestACPEndpointFunctions:
    def setup_method(self):
        from app.acp_endpoints import _acp_sessions
        _acp_sessions.clear()

    def _create_session(self):
        from app.acp_schemas import ACPCreateSessionRequest, ACPLineItemInput
        from app.acp_endpoints import acp_create_checkout_session
        req = ACPCreateSessionRequest(
            line_items=[
                ACPLineItemInput(
                    product_id="test-prod-1",
                    name="Test Laptop",
                    unit_amount=79999,  # $799.99 in cents
                    quantity=1,
                )
            ]
        )
        return _run(acp_create_checkout_session(req, _mock_db()))

    def test_create_returns_incomplete_status(self):
        """Create session starts as 'incomplete' per ACP spec 2026-01-30."""
        session = self._create_session()
        assert session.status == "incomplete"

    def test_create_session_id_format(self):
        session = self._create_session()
        assert session.id.startswith("acp-session-")

    def test_create_protocol_field(self):
        session = self._create_session()
        assert session.protocol == "acp"

    def test_create_calculates_subtotal(self):
        """Subtotal equals unit_amount (cents) × quantity."""
        session = self._create_session()
        assert session.totals.subtotal_cents == 79999  # 79999 × 1

    def test_create_adds_standard_shipping(self):
        session = self._create_session()
        assert session.totals.shipping_cents == 999

    def test_create_adds_tax(self):
        session = self._create_session()
        assert session.totals.tax_cents > 0

    def test_create_total_equals_sum(self):
        session = self._create_session()
        t = session.totals
        assert t.total_cents == t.subtotal_cents + t.shipping_cents + t.tax_cents

    def test_create_includes_fulfillment_options(self):
        """Session response must include available fulfillment options per spec."""
        session = self._create_session()
        assert len(session.fulfillment_options) == 3
        option_ids = {o.id for o in session.fulfillment_options}
        assert option_ids == {"standard", "express", "overnight"}

    def test_create_sets_selected_fulfillment_options(self):
        session = self._create_session()
        assert "standard" in session.selected_fulfillment_options

    def test_create_with_buyer_email(self):
        from app.acp_schemas import ACPCreateSessionRequest, ACPLineItemInput
        from app.acp_endpoints import acp_create_checkout_session
        req = ACPCreateSessionRequest(
            line_items=[ACPLineItemInput(product_id="p1", name="P", unit_amount=999, quantity=1)],
            buyer_email="test@example.com",
        )
        session = _run(acp_create_checkout_session(req, _mock_db()))
        assert session.buyer is not None
        assert session.buyer.email == "test@example.com"

    def test_create_with_capabilities(self):
        """capabilities field is accepted and stored (ignored by merchant, echoed back)."""
        from app.acp_schemas import ACPCreateSessionRequest, ACPLineItemInput
        from app.acp_endpoints import acp_create_checkout_session
        req = ACPCreateSessionRequest(
            line_items=[ACPLineItemInput(product_id="p1", name="P", unit_amount=999, quantity=1)],
            capabilities={"payment_handlers": ["stripe"]},
        )
        session = _run(acp_create_checkout_session(req, _mock_db()))
        assert session.status == "incomplete"

    def test_get_session_by_id(self):
        from app.acp_endpoints import acp_get_checkout_session
        created = self._create_session()
        retrieved = _run(acp_get_checkout_session(created.id))
        assert retrieved is not None
        assert retrieved.id == created.id

    def test_get_session_not_found(self):
        from app.acp_endpoints import acp_get_checkout_session
        result = _run(acp_get_checkout_session("nonexistent-session-id"))
        assert result is None

    def test_update_session_sets_ready_for_payment(self):
        """Update transitions status to 'ready_for_payment' per ACP spec."""
        from app.acp_schemas import ACPUpdateSessionRequest
        from app.acp_endpoints import acp_update_checkout_session
        created = self._create_session()
        update = ACPUpdateSessionRequest(shipping_method="express")
        updated = _run(acp_update_checkout_session(created.id, update, _mock_db()))
        assert updated.status == "ready_for_payment"

    def test_update_session_changes_shipping(self):
        from app.acp_schemas import ACPUpdateSessionRequest
        from app.acp_endpoints import acp_update_checkout_session
        created = self._create_session()
        update = ACPUpdateSessionRequest(shipping_method="overnight")
        updated = _run(acp_update_checkout_session(created.id, update, _mock_db()))
        assert updated.shipping_method == "overnight"
        assert updated.totals.shipping_cents == 2999

    def test_update_session_recalculates_tax(self):
        import math
        from app.acp_schemas import ACPUpdateSessionRequest, ACP_TAX_RATE
        from app.acp_endpoints import acp_update_checkout_session
        created = self._create_session()
        update = ACPUpdateSessionRequest(shipping_method="standard")
        updated = _run(acp_update_checkout_session(created.id, update, _mock_db()))
        expected_tax = math.ceil(updated.totals.subtotal_cents * ACP_TAX_RATE)
        assert updated.totals.tax_cents == expected_tax

    def test_update_session_reflects_selected_fulfillment_options(self):
        from app.acp_schemas import ACPUpdateSessionRequest
        from app.acp_endpoints import acp_update_checkout_session
        created = self._create_session()
        update = ACPUpdateSessionRequest(shipping_method="express")
        updated = _run(acp_update_checkout_session(created.id, update, _mock_db()))
        assert "express" in updated.selected_fulfillment_options

    def test_update_session_not_found(self):
        from app.acp_schemas import ACPUpdateSessionRequest
        from app.acp_endpoints import acp_update_checkout_session
        update = ACPUpdateSessionRequest(shipping_method="standard")
        result = _run(acp_update_checkout_session("bad-id", update, _mock_db()))
        assert result is None

    def test_complete_session_returns_order_id(self):
        from app.acp_schemas import ACPCompleteSessionRequest
        from app.acp_endpoints import acp_complete_checkout_session
        created = self._create_session()
        req = ACPCompleteSessionRequest(payment_method="card")
        completed = _run(acp_complete_checkout_session(created.id, req, _mock_db()))
        assert completed.status == "completed"
        assert completed.order_id is not None
        assert completed.order_id.startswith("acp-order-")

    def test_complete_session_with_payment_data(self):
        """payment_data (spec field) is accepted and payment_token_provided=True."""
        from app.acp_schemas import ACPCompleteSessionRequest, ACPPaymentData, ACPPaymentInstrument
        from app.acp_endpoints import acp_complete_checkout_session
        created = self._create_session()
        req = ACPCompleteSessionRequest(
            payment_data=ACPPaymentData(
                handler_id="stripe",
                instrument=ACPPaymentInstrument(type="card", credential_token="spt_test_456"),
            )
        )
        completed = _run(acp_complete_checkout_session(created.id, req, _mock_db()))
        assert completed.status == "completed"
        assert completed.metadata["payment_token_provided"] is True

    def test_complete_session_stores_payment_info(self):
        """Legacy payment_token field is still accepted for backward compatibility."""
        from app.acp_schemas import ACPCompleteSessionRequest
        from app.acp_endpoints import acp_complete_checkout_session
        created = self._create_session()
        req = ACPCompleteSessionRequest(payment_method="card", payment_token="tok_test_123")
        completed = _run(acp_complete_checkout_session(created.id, req, _mock_db()))
        assert completed.metadata["payment_token_provided"] is True

    def test_complete_already_completed_session_fails(self):
        from app.acp_schemas import ACPCompleteSessionRequest
        from app.acp_endpoints import acp_complete_checkout_session
        created = self._create_session()
        req = ACPCompleteSessionRequest(payment_method="card")
        _run(acp_complete_checkout_session(created.id, req, _mock_db()))
        again = _run(acp_complete_checkout_session(created.id, req, _mock_db()))
        assert again.error is not None

    def test_complete_session_not_found(self):
        from app.acp_schemas import ACPCompleteSessionRequest
        from app.acp_endpoints import acp_complete_checkout_session
        req = ACPCompleteSessionRequest(payment_method="card")
        result = _run(acp_complete_checkout_session("nonexistent", req, _mock_db()))
        assert result is None

    def test_cancel_session_changes_status(self):
        from app.acp_endpoints import acp_cancel_checkout_session
        created = self._create_session()
        canceled = _run(acp_cancel_checkout_session(created.id))
        assert canceled.status == "canceled"

    def test_cancel_session_not_found(self):
        from app.acp_endpoints import acp_cancel_checkout_session
        result = _run(acp_cancel_checkout_session("bad-id"))
        assert result is None


# ============================================================================
# Layer 4 — ACP HTTP Routes (TestClient, no real DB)
# ============================================================================

@pytest.fixture(scope="module")
def client():
    from dotenv import load_dotenv
    load_dotenv(override=True)

    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_db

    mock_db = MagicMock()

    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def clear_acp_sessions():
    from app.acp_endpoints import _acp_sessions
    _acp_sessions.clear()
    yield
    _acp_sessions.clear()


# Sample line item using spec-compliant field names (name + unit_amount in cents)
_SAMPLE_LINE_ITEM = {
    "product_id": "http-test-prod-1",
    "name": "HTTP Test Laptop",
    "unit_amount": 49999,   # $499.99 in cents
    "quantity": 1,
}


class TestACPHTTPRoutes:
    def test_create_session_returns_200(self, client):
        resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        assert resp.status_code == 200

    def test_create_session_status_incomplete(self, client):
        """Status must be 'incomplete' per ACP spec 2026-01-30 (not 'pending')."""
        resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        assert resp.json()["status"] == "incomplete"

    def test_create_session_has_id(self, client):
        resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        assert resp.json()["id"].startswith("acp-session-")

    def test_create_session_has_totals(self, client):
        resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        data = resp.json()
        assert "totals" in data
        assert data["totals"]["subtotal_cents"] > 0

    def test_create_session_has_fulfillment_options(self, client):
        """Response must include fulfillment_options[] per ACP spec."""
        resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        data = resp.json()
        assert "fulfillment_options" in data
        assert len(data["fulfillment_options"]) == 3

    def test_create_session_accepts_capabilities(self, client):
        """capabilities field must be accepted without error."""
        payload = {
            "line_items": [_SAMPLE_LINE_ITEM],
            "capabilities": {"payment_handlers": ["stripe"], "interventions": []},
        }
        resp = client.post("/acp/checkout-sessions", json=payload)
        assert resp.status_code == 200

    def test_get_session_by_id(self, client):
        create_resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        session_id = create_resp.json()["id"]
        get_resp = client.get(f"/acp/checkout-sessions/{session_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == session_id

    def test_get_session_not_found_404(self, client):
        resp = client.get("/acp/checkout-sessions/acp-session-doesnotexist")
        assert resp.status_code == 404

    def test_update_session_uses_post_method(self, client):
        """Update endpoint must be POST (not PUT) per ACP spec 2026-01-30."""
        create_resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        session_id = create_resp.json()["id"]
        update_resp = client.post(
            f"/acp/checkout-sessions/{session_id}",
            json={"shipping_method": "express"},
        )
        assert update_resp.status_code == 200

    def test_update_session_returns_ready_for_payment(self, client):
        """Status must be 'ready_for_payment' after update (not 'confirmed')."""
        create_resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        session_id = create_resp.json()["id"]
        update_resp = client.post(
            f"/acp/checkout-sessions/{session_id}",
            json={"shipping_method": "express"},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["status"] == "ready_for_payment"

    def test_update_session_recalculates_shipping(self, client):
        create_resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        session_id = create_resp.json()["id"]
        update_resp = client.post(
            f"/acp/checkout-sessions/{session_id}",
            json={"shipping_method": "overnight"},
        )
        assert update_resp.json()["totals"]["shipping_cents"] == 2999

    def test_complete_session_returns_order_id(self, client):
        create_resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        session_id = create_resp.json()["id"]
        complete_resp = client.post(
            f"/acp/checkout-sessions/{session_id}/complete",
            json={"payment_method": "card"},
        )
        assert complete_resp.status_code == 200
        data = complete_resp.json()
        assert data["status"] == "completed"
        assert data["order_id"] is not None

    def test_complete_session_with_payment_data(self, client):
        """payment_data (spec field) is accepted by the complete endpoint."""
        create_resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        session_id = create_resp.json()["id"]
        complete_resp = client.post(
            f"/acp/checkout-sessions/{session_id}/complete",
            json={
                "payment_data": {
                    "handler_id": "stripe",
                    "instrument": {"type": "card", "credential_token": "spt_test_789"},
                }
            },
        )
        assert complete_resp.status_code == 200
        assert complete_resp.json()["status"] == "completed"

    def test_cancel_session_changes_status(self, client):
        create_resp = client.post("/acp/checkout-sessions", json={"line_items": [_SAMPLE_LINE_ITEM]})
        session_id = create_resp.json()["id"]
        cancel_resp = client.post(f"/acp/checkout-sessions/{session_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "canceled"

    def test_cancel_not_found_404(self, client):
        resp = client.post("/acp/checkout-sessions/acp-session-bad/cancel")
        assert resp.status_code == 404

    def test_feed_json_returns_list(self, client):
        resp = client.get("/acp/feed.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "count" in data
        assert data["protocol"] == "acp"
        assert isinstance(data["items"], list)

    def test_feed_csv_returns_text(self, client):
        resp = client.get("/acp/feed.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

    def test_webhook_returns_received(self, client):
        resp = client.post(
            "/acp/webhooks/orders",
            json={"event": "order.created", "order_id": "ord-1"},
        )
        assert resp.status_code == 200
        assert resp.json().get("received") is True


# ============================================================================
# Layer 5 — Protocol Header Middleware
# ============================================================================

class TestProtocolHeaderMiddleware:
    def test_acp_response_has_protocol_header(self, client):
        resp = client.get("/acp/feed.json")
        assert resp.headers.get("x-commerce-protocol") == "acp"

    def test_ucp_route_has_ucp_header(self, client):
        # Use the lightweight /ucp/cart endpoint (GET) so we don't trigger a
        # full DB search that would try to JSON-serialize internal query objects.
        resp = client.get("/ucp/cart/test-cart-id")
        assert resp.headers.get("x-commerce-protocol") == "ucp"

    def test_acp_api_version_header_echoed(self, client):
        """API-Version request header must be echoed back in ACP responses."""
        resp = client.get(
            "/acp/feed.json",
            headers={"API-Version": "2026-01-30"},
        )
        assert resp.headers.get("api-version") == "2026-01-30"

    def test_acp_idempotency_key_echoed(self, client):
        """Idempotency-Key must be echoed back for safe retries."""
        resp = client.post(
            "/acp/checkout-sessions",
            json={"line_items": [_SAMPLE_LINE_ITEM]},
            headers={"Idempotency-Key": "test-idem-key-abc"},
        )
        assert resp.headers.get("idempotency-key") == "test-idem-key-abc"


# ============================================================================
# Layer 6 — OpenAI Function-Calling Tools
# ============================================================================

class TestACPOpenAITools:
    def test_get_acp_tools_returns_list(self):
        from app.acp_protocol import get_acp_tools
        tools = get_acp_tools()
        assert isinstance(tools, list)
        assert len(tools) >= 1

    def test_get_acp_tools_have_required_keys(self):
        from app.acp_protocol import get_acp_tools
        for tool in get_acp_tools():
            assert tool["type"] == "function"
            assert "name" in tool
            assert "parameters" in tool

    def test_get_acp_session_tools_returns_list(self):
        from app.acp_protocol import get_acp_session_tools
        tools = get_acp_session_tools()
        assert isinstance(tools, list)
        assert len(tools) >= 3

    def test_acp_session_tools_cover_lifecycle(self):
        from app.acp_protocol import get_acp_session_tools
        names = {t["name"] for t in get_acp_session_tools()}
        assert "acp_create_checkout_session" in names
        assert "acp_complete_checkout_session" in names
        assert "acp_cancel_checkout_session" in names

    def test_acp_session_tool_uses_spec_field_names(self):
        """line_items must use 'name' and 'unit_amount' per spec 2026-01-30."""
        from app.acp_protocol import get_acp_session_tools
        tools = {t["name"]: t for t in get_acp_session_tools()}
        create_tool = tools["acp_create_checkout_session"]
        item_props = create_tool["parameters"]["properties"]["line_items"]["items"]["properties"]
        assert "name" in item_props
        assert "unit_amount" in item_props
        assert "title" not in item_props
        assert "price_dollars" not in item_props

    def test_acp_protocol_re_exports_real_functions(self):
        from app.acp_protocol import (
            acp_create_checkout_session,
            acp_get_checkout_session,
            acp_update_checkout_session,
            acp_complete_checkout_session,
            acp_cancel_checkout_session,
            generate_product_feed,
        )
        assert callable(acp_create_checkout_session)
        assert callable(acp_get_checkout_session)
        assert callable(generate_product_feed)

    # Backward-compat: original test still passes
    def test_acp_protocol_basic(self):
        from app.acp_protocol import get_acp_tools
        tools = get_acp_tools()
        assert isinstance(tools, list)
