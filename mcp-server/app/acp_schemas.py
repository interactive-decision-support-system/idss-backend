"""
Agentic Commerce Protocol (ACP) Schemas — spec version 2026-01-30.

Implements the ACP request/response format for compatibility with OpenAI/Stripe's
agentic commerce protocol (agenticcommerce.dev).

ACP defines three interaction flows:
  1. Product Feed — merchant exposes catalog as JSON or CSV
  2. Agentic Checkout — 5-endpoint REST session state machine:
       POST   /acp/checkout-sessions                    → create (incomplete)
       GET    /acp/checkout-sessions/{id}               → read
       POST   /acp/checkout-sessions/{id}               → update buyer/shipping
       POST   /acp/checkout-sessions/{id}/complete      → complete (place order)
       POST   /acp/checkout-sessions/{id}/cancel        → cancel
  3. Webhook — merchant receives order events from OpenAI/Stripe

Status lifecycle: incomplete → ready_for_payment → completed | canceled
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ============================================================================
# Shared Sub-objects
# ============================================================================

class ACPBuyer(BaseModel):
    """Buyer contact information."""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class ACPShippingAddress(BaseModel):
    """Shipping destination address."""
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = Field("US", description="ISO 3166-1 alpha-2 country code")


class ACPFulfillmentDetails(BaseModel):
    """
    Fulfillment/shipping contact and address block (spec field in create/update).
    Maps to session.shipping_address + session.buyer contact fields.
    """
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[ACPShippingAddress] = None


class ACPFulfillmentOption(BaseModel):
    """An available fulfillment/shipping option returned in the session response."""
    id: str = Field(..., description="Identifier: standard | express | overnight")
    label: str
    description: str
    cost_cents: int = Field(..., description="Shipping cost in cents")


class ACPMessage(BaseModel):
    """
    Business-logic message surfaced in the session response body (not HTTP 4xx).
    Used for issues like out-of-stock, invalid address, coupon not found.
    """
    code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable description")
    severity: str = Field("error", description="error | warning | info")


class ACPLink(BaseModel):
    """Informational links returned with a session (TOS, return policy, support)."""
    rel: str = Field(..., description="terms_of_service | return_policy | support")
    href: str


# ============================================================================
# Payment Models (complete endpoint)
# ============================================================================

class ACPPaymentInstrument(BaseModel):
    """Payment instrument carrying an opaque Stripe SPT credential."""
    type: str = Field("card", description="Payment type: card | bank_account | wallet")
    credential_token: Optional[str] = Field(
        None, description="Opaque Stripe Secure Payment Token (SPT) — never raw card data"
    )


class ACPPaymentData(BaseModel):
    """
    Structured payment block for POST /complete (spec field).
    The agent passes a delegated payment token from Stripe; the merchant
    never sees raw card details (PCI compliance).
    """
    handler_id: str = Field("stripe", description="Payment handler identifier")
    instrument: ACPPaymentInstrument = Field(default_factory=ACPPaymentInstrument)
    billing_address: Optional[ACPShippingAddress] = None


# ============================================================================
# ACP Request Models
# ============================================================================

class ACPLineItemInput(BaseModel):
    """
    A single product line item in a create-session request.
    unit_amount is in CENTS (spec field), matching the ACP 2026-01-30 schema.
    """
    product_id: str = Field(..., description="Merchant product ID")
    name: str = Field(..., description="Product display name")
    unit_amount: int = Field(..., description="Unit price in cents (e.g. 79999 for $799.99)", ge=0)
    quantity: int = Field(1, description="Quantity", ge=1)
    image_url: Optional[str] = Field(None, description="Product image URL")


class ACPCreateSessionRequest(BaseModel):
    """Request body for POST /acp/checkout-sessions."""
    line_items: List[ACPLineItemInput] = Field(..., description="Products to purchase", min_length=1)
    currency: str = Field("USD", description="ISO 4217 currency code")
    # buyer_email kept for backward compatibility with internal tooling
    buyer_email: Optional[str] = Field(None, description="Buyer email (shorthand pre-fill)")
    buyer: Optional[ACPBuyer] = Field(None, description="Full buyer contact object")
    capabilities: Optional[Dict[str, Any]] = Field(
        None,
        description="Agent-declared capabilities (payment handlers, extensions, interventions)"
    )
    fulfillment_details: Optional[ACPFulfillmentDetails] = Field(
        None, description="Shipping address and contact info"
    )
    discounts: Optional[List[str]] = Field(None, description="Discount/coupon codes to apply")


class ACPUpdateSessionRequest(BaseModel):
    """Request body for POST /acp/checkout-sessions/{id} (update)."""
    buyer: Optional[ACPBuyer] = None
    shipping_address: Optional[ACPShippingAddress] = None
    shipping_method: Optional[str] = Field(None, description="standard | express | overnight")
    fulfillment_details: Optional[ACPFulfillmentDetails] = None
    selected_fulfillment_options: Optional[List[str]] = Field(
        None, description="IDs of the fulfillment options the buyer chose"
    )
    discounts: Optional[List[str]] = Field(None, description="Discount/coupon codes to apply")


class ACPCompleteSessionRequest(BaseModel):
    """
    Request body for POST /acp/checkout-sessions/{id}/complete.

    payment_data is the spec-compliant field (2026-01-30).
    payment_token / payment_method are kept as Optional for backward
    compatibility with internal tooling (build_acp_complete_session).
    """
    payment_data: Optional[ACPPaymentData] = Field(
        None, description="Structured payment block with Stripe delegated token"
    )
    # Legacy fields — used by build_acp_complete_session in cart_action_agent.py
    payment_token: Optional[str] = Field(None, description="Stripe delegated payment token (legacy)")
    payment_method: Optional[str] = Field("card", description="Payment method type (legacy)")


# ============================================================================
# ACP Session State Models
# ============================================================================

class ACPLineItem(BaseModel):
    """A resolved line item stored in and returned from a checkout session."""
    id: str = Field(..., description="Line item ID (li-{uuid})")
    product_id: str
    name: str = Field(..., description="Product display name")
    quantity: int
    unit_amount: int = Field(..., description="Unit price in cents")
    amount_total: int = Field(..., description="quantity × unit_amount in cents")


class ACPTotals(BaseModel):
    """Order totals calculated by the merchant."""
    subtotal_cents: int
    shipping_cents: int = Field(0, description="Shipping cost in cents")
    tax_cents: int = Field(0, description="Tax in cents")
    total_cents: int = Field(..., description="subtotal + shipping + tax")


class ACPCheckoutSession(BaseModel):
    """
    The canonical ACP session object exchanged between agent and merchant.

    Status lifecycle (spec 2026-01-30):
      incomplete        — session created, awaiting fulfillment details
      ready_for_payment — fulfillment confirmed, ready for payment
      completed         — order placed successfully
      canceled          — session abandoned
    """
    protocol: str = Field("acp", description="Protocol discriminator, always 'acp'")
    id: str = Field(..., description="Session ID: acp-session-{uuid}")
    status: str = Field(..., description="incomplete | ready_for_payment | completed | canceled")
    line_items: List[ACPLineItem]
    totals: ACPTotals
    currency: str = Field("USD")
    buyer: Optional[ACPBuyer] = None
    shipping_address: Optional[ACPShippingAddress] = None
    shipping_method: str = Field("standard")
    # Fulfillment options returned so the agent can present choices to the buyer
    fulfillment_options: List[ACPFulfillmentOption] = Field(default_factory=list)
    selected_fulfillment_options: List[str] = Field(
        default_factory=list,
        description="IDs of currently selected fulfillment options"
    )
    # Business-logic messages (errors, warnings) — surfaced in 200 response, not 4xx
    messages: List[ACPMessage] = Field(default_factory=list)
    links: List[ACPLink] = Field(default_factory=list)
    order_id: Optional[str] = Field(None, description="Set after complete; merchant order reference")
    # error kept for backward compatibility with existing tests and internal tooling
    error: Optional[str] = Field(None, description="Top-level error string (deprecated; prefer messages[])")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Merchant-defined extra data")


# ============================================================================
# ACP Product Feed Models
# ============================================================================

class ACPProductFeedItem(BaseModel):
    """
    A single row in the ACP product feed (GET /acp/feed.json or /acp/feed.csv).
    Mirrors the Google Shopping / OpenAI feed spec fields.
    """
    id: str = Field(..., description="Merchant product ID")
    title: str
    description: Optional[str] = None
    price_dollars: float = Field(..., description="Price in dollars")
    currency: str = Field("USD")
    availability: str = Field(..., description="in_stock | out_of_stock")
    inventory: int = Field(0, ge=0)
    image_url: Optional[str] = None
    product_url: str = Field(..., description="Canonical product page URL")
    category: Optional[str] = None
    brand: Optional[str] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None


# ============================================================================
# ACP Constants
# ============================================================================

ACP_SHIPPING_RATES_CENTS: Dict[str, int] = {
    "standard": 999,    # $9.99
    "express": 1999,    # $19.99
    "overnight": 2999,  # $29.99
}

# Default tax rate: 8.875% (New York)
ACP_TAX_RATE: float = 0.08875

# Standard fulfillment options returned with every session response
ACP_FULFILLMENT_OPTIONS: List[ACPFulfillmentOption] = [
    ACPFulfillmentOption(
        id="standard", label="Standard Shipping",
        description="5–7 business days", cost_cents=999,
    ),
    ACPFulfillmentOption(
        id="express", label="Express Shipping",
        description="2–3 business days", cost_cents=1999,
    ),
    ACPFulfillmentOption(
        id="overnight", label="Overnight Shipping",
        description="Next business day", cost_cents=2999,
    ),
]
