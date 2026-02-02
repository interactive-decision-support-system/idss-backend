"""
UCP Native Checkout Implementation.

Per Google UCP Guide: https://developers.google.com/ucp/implementation/native-checkout

Implements the 5 required endpoints:
1. POST /checkout-sessions - Create checkout session
2. GET /checkout-sessions/{id} - Get checkout session
3. PUT /checkout-sessions/{id} - Update checkout session
4. POST /checkout-sessions/{id}/complete - Complete checkout
5. POST /checkout-sessions/{id}/cancel - Cancel checkout session
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
import uuid
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# UCP Checkout Session Models (per Google UCP spec)
# ============================================================================

class UCPSessionCapability(BaseModel):
    """UCP capability declaration."""
    name: str
    version: str = "2026-01-11"


class UCPVersion(BaseModel):
    """UCP version and capabilities."""
    version: str = "2026-01-11"
    capabilities: List[UCPSessionCapability] = Field(default_factory=lambda: [
        UCPSessionCapability(name="dev.ucp.shopping.checkout", version="2026-01-11"),
        UCPSessionCapability(name="dev.ucp.shopping.fulfillment", version="2026-01-11")
    ])


class UCPLineItem(BaseModel):
    """Line item in checkout session."""
    id: str
    item: Dict[str, Any]  # {id, title, price}
    quantity: int
    base_amount: int  # in cents
    subtotal: int  # in cents
    total: int  # in cents


class UCPTotal(BaseModel):
    """Total amount breakdown."""
    type: str  # "subtotal", "tax", "shipping", "total"
    amount: int  # in cents
    display_text: Optional[str] = None


class UCPPaymentHandler(BaseModel):
    """Payment handler configuration."""
    id: str
    name: str
    config: Dict[str, Any]


class UCPPayment(BaseModel):
    """Payment configuration."""
    handlers: List[UCPPaymentHandler]
    selected_instrument_id: Optional[str] = None
    instruments: Optional[List[Dict[str, Any]]] = None


class UCPLink(BaseModel):
    """Link to terms/privacy."""
    type: str  # "privacy_policy", "terms_of_service"
    url: str


class UCPBuyer(BaseModel):
    """Buyer information."""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None


class UCPFulfillmentDestination(BaseModel):
    """Shipping destination."""
    id: str
    postal_code: Optional[str] = None
    country: Optional[str] = None
    address_locality: Optional[str] = None
    address_region: Optional[str] = None


class UCPFulfillmentOption(BaseModel):
    """Shipping option."""
    id: str
    title: str
    totals: List[UCPTotal]


class UCPFulfillmentGroup(BaseModel):
    """Fulfillment group."""
    id: str
    line_item_ids: List[str]
    selected_option_id: Optional[str] = None
    options: List[UCPFulfillmentOption]


class UCPFulfillmentMethod(BaseModel):
    """Fulfillment method."""
    id: Optional[str] = None
    type: str  # "shipping"
    line_item_ids: Optional[List[str]] = None
    selected_destination_id: Optional[str] = None
    destinations: List[UCPFulfillmentDestination]
    groups: Optional[List[UCPFulfillmentGroup]] = None


class UCPFulfillment(BaseModel):
    """Fulfillment configuration."""
    methods: List[UCPFulfillmentMethod]


class UCPOrder(BaseModel):
    """Order information."""
    id: str
    permalink_url: str


class UCPCheckoutSession(BaseModel):
    """UCP checkout session (per Google spec)."""
    ucp: UCPVersion
    id: str  # gid://merchant.example.com/Checkout/session_abc123
    status: str  # "incomplete", "completed", "canceled"
    line_items: List[UCPLineItem]
    currency: str = "USD"
    totals: List[UCPTotal]
    payment: UCPPayment
    links: List[UCPLink]
    buyer: Optional[UCPBuyer] = None
    fulfillment: Optional[UCPFulfillment] = None
    order: Optional[UCPOrder] = None


# ============================================================================
# Request/Response Models
# ============================================================================

class CreateCheckoutSessionRequest(BaseModel):
    """Request to create checkout session."""
    line_items: List[Dict[str, Any]]  # [{item: {id, title}, quantity}]
    currency: str = "USD"


class UpdateCheckoutSessionRequest(BaseModel):
    """Request to update checkout session."""
    id: str
    buyer: Optional[UCPBuyer] = None
    fulfillment: Optional[UCPFulfillment] = None
    payment: Optional[UCPPayment] = None
    line_items: Optional[List[Dict[str, Any]]] = None
    currency: Optional[str] = None


class CompleteCheckoutRequest(BaseModel):
    """Request to complete checkout."""
    payment_data: Dict[str, Any]  # Payment token and billing info


# ============================================================================
# Checkout Session Storage (in-memory for now, should use DB)
# ============================================================================

_checkout_sessions: Dict[str, UCPCheckoutSession] = {}


def create_checkout_session(
    request: CreateCheckoutSessionRequest,
    db: Session
) -> UCPCheckoutSession:
    """
    Create a new checkout session.
    
    Per Google UCP Guide: POST /checkout-sessions
    """
    try:
        session_id = f"gid://mcp.example.com/Checkout/session_{uuid.uuid4().hex[:12]}"
        
        # Build line items
        line_items = []
        subtotal = 0
        
        for idx, item_data in enumerate(request.line_items):
            item = item_data["item"]
            quantity = item_data.get("quantity", 1)
            
            # Get product price from database
            product_id = item["id"]
            from app.models import Product, Price
            product = db.query(Product).filter(Product.product_id == product_id).first()
            
            if not product:
                # If product not found, use price from request or default
                logger.warning(f"Product {product_id} not found in database, using provided price")
                price_cents = item.get("price", 0)
                if isinstance(price_cents, (int, float)) and price_cents < 100:
                    # Assume it's in dollars, convert to cents
                    price_cents = int(price_cents * 100)
                elif isinstance(price_cents, (int, float)):
                    price_cents = int(price_cents)
                else:
                    price_cents = 0
                product_name = item.get("title", "Unknown Product")
            else:
                # Get price from Price table (relationship)
                price_obj = db.query(Price).filter(Price.product_id == product_id).first()
                if price_obj:
                    price_cents = price_obj.price_cents
                else:
                    # Fallback: use price from item if provided, or 0
                    price_cents = item.get("price", 0)
                    if isinstance(price_cents, (int, float)) and price_cents < 100:
                        price_cents = int(price_cents * 100)
                    elif isinstance(price_cents, (int, float)):
                        price_cents = int(price_cents)
                    else:
                        price_cents = 0
                product_name = product.name
            
            # Create line item
            line_item = UCPLineItem(
                id=f"line_{idx + 1}",
                item={
                    "id": product_id,
                    "title": item.get("title", product_name),
                    "price": price_cents
                },
                quantity=quantity,
                base_amount=price_cents,
                subtotal=price_cents * quantity,
                total=price_cents * quantity
            )
            line_items.append(line_item)
            subtotal += price_cents * quantity
        
        # Build totals (initially estimated, no tax/shipping)
        totals = [
            UCPTotal(type="subtotal", amount=subtotal),
            UCPTotal(type="tax", amount=0),
            UCPTotal(type="total", amount=subtotal)
        ]
        
        # Build payment handlers (Google Pay)
        payment_handlers = [
            UCPPaymentHandler(
                id="gpay",
                name="com.google.pay",
                config={
                    "api_version": 2,
                    "api_version_minor": 0,
                    "merchant_info": {
                        "merchant_id": "12345678901234567890",  # Should come from config
                        "merchant_name": "MCP E-commerce"
                    },
                    "allowed_payment_methods": [
                        {
                            "type": "CARD",
                            "parameters": {
                                "allowed_auth_methods": ["PAN_ONLY", "CRYPTOGRAM_3DS"],
                                "allowed_card_networks": ["VISA", "MASTERCARD"]
                            },
                            "tokenization_specification": {
                                "type": "PAYMENT_GATEWAY",
                                "parameters": {
                                    "gateway": "stripe",  # Should be configurable
                                    "gateway_merchant_id": "exampleGatewayMerchantId"
                                }
                            }
                        }
                    ]
                }
            )
        ]
        
        payment = UCPPayment(handlers=payment_handlers)
        
        # Build links
        links = [
            UCPLink(type="privacy_policy", url="https://mcp.example.com/privacy"),
            UCPLink(type="terms_of_service", url="https://mcp.example.com/terms")
        ]
        
        # Create session
        session = UCPCheckoutSession(
            ucp=UCPVersion(),
            id=session_id,
            status="incomplete",
            line_items=line_items,
            currency=request.currency,
            totals=totals,
            payment=payment,
            links=links
        )
        
        # Store session
        _checkout_sessions[session_id] = session
        
        logger.info(f"Created checkout session: {session_id}")
        return session
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create checkout session: {str(e)}")


def get_checkout_session(session_id: str) -> Optional[UCPCheckoutSession]:
    """
    Get checkout session by ID.
    
    Per Google UCP Guide: GET /checkout-sessions/{id}
    """
    # Extract session ID from full GID if needed
    if "/" in session_id:
        session_id = session_id.split("/")[-1]
    
    # Find session (check both full ID and short ID)
    for full_id, session in _checkout_sessions.items():
        if session_id in full_id or full_id.endswith(session_id):
            return session
    
    return None


def update_checkout_session(
    session_id: str,
    request: UpdateCheckoutSessionRequest,
    db: Session
) -> Optional[UCPCheckoutSession]:
    """
    Update checkout session.
    
    Per Google UCP Guide: PUT /checkout-sessions/{id}
    Recalculates taxes and shipping when address changes.
    """
    session = get_checkout_session(session_id)
    if not session:
        return None
    
    # Update buyer info
    if request.buyer:
        session.buyer = request.buyer
    
    # Update fulfillment (recalculate shipping/tax)
    if request.fulfillment:
        session.fulfillment = request.fulfillment
        
        # Recalculate shipping and tax based on destination
        if request.fulfillment.methods:
            shipping_amount = 0
            tax_amount = 0
            
            for method in request.fulfillment.methods:
                if method.selected_destination_id and method.groups:
                    for group in method.groups:
                        if group.selected_option_id:
                            for option in group.options:
                                if option.id == group.selected_option_id:
                                    # Get shipping cost from option totals
                                    for total in option.totals:
                                        if total.type == "total":
                                            shipping_amount += total.amount
            
            # Calculate tax (simplified: 8.5% of subtotal)
            subtotal = sum(t.amount for t in session.totals if t.type == "subtotal")
            tax_amount = int(subtotal * 0.085)  # 8.5% tax
            
            # Update totals
            session.totals = [
                UCPTotal(type="subtotal", amount=subtotal),
                UCPTotal(type="shipping", amount=shipping_amount, display_text="Ground Shipping"),
                UCPTotal(type="tax", amount=tax_amount),
                UCPTotal(type="total", amount=subtotal + shipping_amount + tax_amount)
            ]
    
    # Update payment
    if request.payment:
        session.payment = request.payment
    
    # Update line items if provided
    if request.line_items:
        # Rebuild line items (same logic as create)
        line_items = []
        for idx, item_data in enumerate(request.line_items):
            item = item_data["item"]
            quantity = item_data.get("quantity", 1)
            
            product_id = item["id"]
            from app.models import Product, Price
            product = db.query(Product).filter(Product.product_id == product_id).first()
            
            if product:
                # Get price from relationship or direct query
                try:
                    # Try relationship first (if loaded)
                    if hasattr(product, 'price_info') and product.price_info:
                        price_cents = product.price_info.price_cents
                    else:
                        # Fallback: query Price table directly
                        price_obj = db.query(Price).filter(Price.product_id == product_id).first()
                        if price_obj:
                            price_cents = price_obj.price_cents
                        else:
                            price_cents = 0
                except Exception as e:
                    logger.warning(f"Error getting price for {product_id}: {e}")
                    price_cents = 0
                line_item = UCPLineItem(
                    id=f"line_{idx + 1}",
                    item={"id": product_id, "title": item.get("title", product.name), "price": price_cents},
                    quantity=quantity,
                    base_amount=price_cents,
                    subtotal=price_cents * quantity,
                    total=price_cents * quantity
                )
                line_items.append(line_item)
        
        session.line_items = line_items
    
    # Update stored session
    for full_id in list(_checkout_sessions.keys()):
        if session_id in full_id or full_id.endswith(session_id):
            _checkout_sessions[full_id] = session
            break
    
    logger.info(f"Updated checkout session: {session_id}")
    return session


def complete_checkout_session(
    session_id: str,
    request: CompleteCheckoutRequest,
    db: Session
) -> Optional[UCPCheckoutSession]:
    """
    Complete checkout session and place order.
    
    Per Google UCP Guide: POST /checkout-sessions/{id}/complete
    """
    session = get_checkout_session(session_id)
    if not session:
        return None
    
    # Process payment (simplified - in production, call payment gateway)
    payment_data = request.payment_data
    logger.info(f"Processing payment for session {session_id}: {payment_data.get('id')}")
    
    # Create order in database
    from app.models import Order, Cart, CartItem
    order_id = f"order-{uuid.uuid4().hex[:12]}"
    
    # Calculate total
    total_cents = sum(t.amount for t in session.totals if t.type == "total")
    
    # Create order (with synthetic shipping per week4notes.txt)
    order = Order(
        order_id=order_id,
        cart_id=f"cart-{session_id.split('_')[-1]}",  # Extract from session ID
        payment_method_id=payment_data.get("id", "gpay"),
        address_id="default",  # Should come from fulfillment destination
        total_cents=total_cents,
        status="pending",
        shipping_method="standard",
        estimated_delivery_days=5,
        shipping_cost_cents=599,
        shipping_region="US",
    )
    db.add(order)
    db.commit()
    
    # Update session status
    session.status = "completed"
    session.order = UCPOrder(
        id=f"gid://mcp.example.com/Order/{order_id}",
        permalink_url=f"https://mcp.example.com/orders/{order_id}"
    )
    
    # Update stored session
    for full_id in list(_checkout_sessions.keys()):
        if session_id in full_id or full_id.endswith(session_id):
            _checkout_sessions[full_id] = session
            break
    
    logger.info(f"Completed checkout session {session_id}, created order {order_id}")
    return session


def cancel_checkout_session(session_id: str) -> Optional[UCPCheckoutSession]:
    """
    Cancel checkout session.
    
    Per Google UCP Guide: POST /checkout-sessions/{id}/cancel
    """
    session = get_checkout_session(session_id)
    if not session:
        return None
    
    session.status = "canceled"
    
    # Update stored session
    for full_id in list(_checkout_sessions.keys()):
        if session_id in full_id or full_id.endswith(session_id):
            _checkout_sessions[full_id] = session
            break
    
    logger.info(f"Canceled checkout session: {session_id}")
    return session
