"""
ACP (Agentic Commerce Protocol) Endpoints — spec version 2026-01-30.

Thin adapter layer that wraps internal MCP operations with ACP-compatible
request/response format (agenticcommerce.dev spec).

Session lifecycle: incomplete → ready_for_payment → completed | canceled
"""

import uuid
import math
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.acp_schemas import (
    ACPCheckoutSession, ACPCreateSessionRequest, ACPUpdateSessionRequest,
    ACPCompleteSessionRequest, ACPLineItem, ACPTotals, ACPProductFeedItem,
    ACPBuyer, ACPMessage,
    ACP_SHIPPING_RATES_CENTS, ACP_TAX_RATE, ACP_FULFILLMENT_OPTIONS,
)
from app.schemas import GetProductRequest
from app.endpoints import get_product
from app.models import Product

# In-memory session store (same pattern as ucp_checkout.py _checkout_sessions)
_acp_sessions: Dict[str, ACPCheckoutSession] = {}


# ============================================================================
# Internal Helpers
# ============================================================================

def _recalc_totals(session: ACPCheckoutSession) -> ACPTotals:
    """
    Recalculate order totals from line items + shipping method + tax rate.
    Called on create and on every update.
    """
    subtotal = sum(li.amount_total for li in session.line_items)
    shipping = ACP_SHIPPING_RATES_CENTS.get(
        session.shipping_method, ACP_SHIPPING_RATES_CENTS["standard"]
    )
    tax = math.ceil(subtotal * ACP_TAX_RATE)
    return ACPTotals(
        subtotal_cents=subtotal,
        shipping_cents=shipping,
        tax_cents=tax,
        total_cents=subtotal + shipping + tax,
    )


# ============================================================================
# Checkout Session CRUD
# ============================================================================

async def acp_create_checkout_session(
    request: ACPCreateSessionRequest,
    db: Session,
) -> ACPCheckoutSession:
    """
    POST /acp/checkout-sessions

    Validates each product exists (via get_product), builds line items,
    calculates totals, stores session with status='incomplete'.

    line_items[].unit_amount is already in cents (spec 2026-01-30).
    Does NOT require inventory check to succeed — will proceed even if
    get_product returns NOT_FOUND (records message in session.messages).
    """
    session_id = f"acp-session-{uuid.uuid4().hex}"
    line_items: List[ACPLineItem] = []
    messages: List[ACPMessage] = []

    for item_in in request.line_items:
        amount_total = item_in.unit_amount * item_in.quantity

        # Validate product exists in DB (optional enrichment — don't fail hard)
        mcp_req = GetProductRequest(product_id=item_in.product_id)
        mcp_resp = get_product(mcp_req, db)
        if mcp_resp.status != "OK":
            messages.append(ACPMessage(
                code="PRODUCT_NOT_FOUND",
                message=f"Product {item_in.product_id} not found in catalog",
                severity="warning",
            ))

        line_items.append(ACPLineItem(
            id=f"li-{uuid.uuid4().hex[:8]}",
            product_id=item_in.product_id,
            name=item_in.name,
            quantity=item_in.quantity,
            unit_amount=item_in.unit_amount,
            amount_total=amount_total,
        ))

    # Resolve buyer from either buyer object or buyer_email shorthand
    buyer: Optional[ACPBuyer] = request.buyer
    if buyer is None and request.buyer_email:
        buyer = ACPBuyer(email=request.buyer_email)

    # Resolve shipping address from fulfillment_details if provided
    shipping_address = None
    if request.fulfillment_details and request.fulfillment_details.address:
        shipping_address = request.fulfillment_details.address
        # Merge contact into buyer if not already set
        if buyer is None and request.fulfillment_details.email:
            buyer = ACPBuyer(
                email=request.fulfillment_details.email,
                name=request.fulfillment_details.name,
                phone=request.fulfillment_details.phone,
            )

    session = ACPCheckoutSession(
        id=session_id,
        status="incomplete",
        line_items=line_items,
        totals=ACPTotals(subtotal_cents=0, total_cents=0),  # recalculated below
        currency=request.currency,
        buyer=buyer,
        shipping_address=shipping_address,
        shipping_method="standard",
        fulfillment_options=ACP_FULFILLMENT_OPTIONS,
        selected_fulfillment_options=["standard"],
        messages=messages,
        error="; ".join(m.message for m in messages) if messages else None,
    )
    session.totals = _recalc_totals(session)

    _acp_sessions[session_id] = session
    return session


async def acp_get_checkout_session(session_id: str) -> Optional[ACPCheckoutSession]:
    """GET /acp/checkout-sessions/{session_id} — return None if not found."""
    return _acp_sessions.get(session_id)


async def acp_update_checkout_session(
    session_id: str,
    request: ACPUpdateSessionRequest,
    db: Session,
) -> Optional[ACPCheckoutSession]:
    """
    POST /acp/checkout-sessions/{session_id} (update)

    Update buyer info, shipping address, and/or shipping method.
    Recalculates totals (shipping + tax) on every update.
    Transitions status to ready_for_payment.
    Returns None if session not found.
    """
    session = _acp_sessions.get(session_id)
    if session is None:
        return None

    if request.buyer is not None:
        session.buyer = request.buyer

    # shipping_address can come from request.shipping_address or request.fulfillment_details
    if request.shipping_address is not None:
        session.shipping_address = request.shipping_address
    elif request.fulfillment_details and request.fulfillment_details.address:
        session.shipping_address = request.fulfillment_details.address

    if request.shipping_method is not None:
        session.shipping_method = request.shipping_method

    # Update selected fulfillment options
    if request.selected_fulfillment_options is not None:
        session.selected_fulfillment_options = request.selected_fulfillment_options
    else:
        # Default: reflect the chosen shipping_method
        session.selected_fulfillment_options = [session.shipping_method]

    session.totals = _recalc_totals(session)
    session.status = "ready_for_payment"
    _acp_sessions[session_id] = session
    return session


async def acp_complete_checkout_session(
    session_id: str,
    request: ACPCompleteSessionRequest,
    db: Session,
) -> Optional[ACPCheckoutSession]:
    """
    POST /acp/checkout-sessions/{session_id}/complete

    Validates session is incomplete/ready_for_payment, then places the order.
    In production this would call Stripe with the delegated payment token.
    Sets status='completed' and assigns an order_id.
    Returns None if session not found.
    """
    session = _acp_sessions.get(session_id)
    if session is None:
        return None

    if session.status not in ("incomplete", "ready_for_payment"):
        msg = f"Cannot complete session with status '{session.status}'"
        session.error = msg
        session.messages = [ACPMessage(code="INVALID_STATUS", message=msg)]
        return session

    # Resolve payment token from either payment_data (spec) or legacy fields
    payment_token_provided = False
    if request.payment_data and request.payment_data.instrument.credential_token:
        payment_token_provided = True
    elif request.payment_token:
        payment_token_provided = True

    payment_method = "card"
    if request.payment_data:
        payment_method = request.payment_data.instrument.type
    elif request.payment_method:
        payment_method = request.payment_method

    # Generate order ID (in production: call Stripe with the credential_token)
    order_id = f"acp-order-{uuid.uuid4().hex[:12]}"
    session.status = "completed"
    session.order_id = order_id
    session.error = None
    session.messages = []
    session.metadata = {
        "payment_method": payment_method,
        "payment_token_provided": payment_token_provided,
    }
    _acp_sessions[session_id] = session
    return session


async def acp_cancel_checkout_session(session_id: str) -> Optional[ACPCheckoutSession]:
    """
    POST /acp/checkout-sessions/{session_id}/cancel

    Marks session as canceled. Returns None if session not found.
    """
    session = _acp_sessions.get(session_id)
    if session is None:
        return None

    session.status = "canceled"
    session.error = None
    session.messages = []
    _acp_sessions[session_id] = session
    return session


# ============================================================================
# Product Feed
# ============================================================================

def generate_product_feed(db: Session, limit: int = 500) -> List[ACPProductFeedItem]:
    """
    Build ACP product feed from Supabase product catalog.

    Queries the products table directly via SQLAlchemy (same pattern as
    search_products in endpoints.py). Returns up to `limit` items.

    GET /acp/feed.json  or  GET /acp/feed.csv
    """
    base_url = "https://idss-web.vercel.app"

    try:
        rows = (
            db.query(Product)
            .filter(Product.name.isnot(None))
            .limit(limit)
            .all()
        )
    except Exception:
        return []

    items: List[ACPProductFeedItem] = []
    for p in rows:
        price_dollars = float(p.price_value) if p.price_value is not None else 0.0
        inventory = int(p.inventory) if p.inventory is not None else 0
        availability = "in_stock" if inventory > 0 else "out_of_stock"
        product_url = p.link or f"{base_url}/products/{p.product_id}"

        items.append(ACPProductFeedItem(
            id=str(p.product_id),
            title=p.name or "",
            description=p.description,
            price_dollars=round(price_dollars, 2),
            currency="USD",
            availability=availability,
            inventory=inventory,
            image_url=p.image_url,
            product_url=product_url,
            category=p.category,
            brand=p.brand,
            rating=float(p.rating) if p.rating is not None else None,
            rating_count=int(p.rating_count) if p.rating_count is not None else None,
        ))

    return items
