"""
Agentic Commerce Protocol (ACP) Schemas.

Implements the ACP request/response format for compatibility with OpenAI/Stripe's
agentic commerce protocol (agenticcommerce.dev).

ACP defines three interaction flows:
  1. Product Feed — merchant exposes catalog as JSON or CSV
  2. Agentic Checkout — 5-endpoint REST session state machine:
       POST   /acp/checkout-sessions            → create (pending)
       GET    /acp/checkout-sessions/{id}       → read
       PUT    /acp/checkout-sessions/{id}       → update buyer/shipping
       POST   /acp/checkout-sessions/{id}/complete → complete (place order)
       POST   /acp/checkout-sessions/{id}/cancel   → cancel
  3. Webhook — merchant receives order events from OpenAI/Stripe
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ============================================================================
# ACP Request Models
# ============================================================================

class ACPLineItemInput(BaseModel):
    """A single product line item in a create-session request."""
    product_id: str = Field(..., description="Merchant product ID")
    title: str = Field(..., description="Product title / display name")
    price_dollars: float = Field(..., description="Unit price in dollars (e.g. 99.99)", ge=0)
    quantity: int = Field(1, description="Quantity", ge=1)
    image_url: Optional[str] = Field(None, description="Product image URL")


class ACPCreateSessionRequest(BaseModel):
    """Request body for POST /acp/checkout-sessions."""
    line_items: List[ACPLineItemInput] = Field(..., description="Products to purchase", min_length=1)
    currency: str = Field("USD", description="ISO 4217 currency code")
    buyer_email: Optional[str] = Field(None, description="Buyer email (pre-fill)")


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


class ACPUpdateSessionRequest(BaseModel):
    """Request body for PUT /acp/checkout-sessions/{id}."""
    buyer: Optional[ACPBuyer] = None
    shipping_address: Optional[ACPShippingAddress] = None
    shipping_method: Optional[str] = Field("standard", description="standard | express | overnight")


class ACPCompleteSessionRequest(BaseModel):
    """Request body for POST /acp/checkout-sessions/{id}/complete."""
    payment_token: Optional[str] = Field(None, description="Stripe delegated payment token")
    payment_method: str = Field("card", description="Payment method type")


# ============================================================================
# ACP Session State Models
# ============================================================================

class ACPLineItem(BaseModel):
    """A resolved line item stored in a checkout session."""
    id: str = Field(..., description="Line item ID (li-{uuid})")
    product_id: str
    title: str
    quantity: int
    unit_price_cents: int = Field(..., description="Unit price in cents")
    subtotal_cents: int = Field(..., description="quantity × unit_price_cents")


class ACPTotals(BaseModel):
    """Order totals calculated by the merchant."""
    subtotal_cents: int
    shipping_cents: int = Field(0, description="Shipping cost in cents")
    tax_cents: int = Field(0, description="Tax in cents")
    total_cents: int = Field(..., description="subtotal + shipping + tax")


class ACPCheckoutSession(BaseModel):
    """
    The canonical ACP session object exchanged between agent and merchant.
    Status transitions: pending → confirmed → completed | canceled
    """
    protocol: str = Field("acp", description="Protocol discriminator, always 'acp'")
    id: str = Field(..., description="Session ID: acp-session-{uuid}")
    status: str = Field(..., description="pending | confirmed | completed | canceled")
    line_items: List[ACPLineItem]
    totals: ACPTotals
    currency: str = Field("USD")
    buyer: Optional[ACPBuyer] = None
    shipping_address: Optional[ACPShippingAddress] = None
    shipping_method: str = Field("standard")
    order_id: Optional[str] = Field(None, description="Set after complete; merchant order reference")
    error: Optional[str] = Field(None, description="Error message if status could not be updated")
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
# ACP Shipping Rate Constants
# ============================================================================

ACP_SHIPPING_RATES_CENTS: Dict[str, int] = {
    "standard": 999,    # $9.99
    "express": 1999,    # $19.99
    "overnight": 2999,  # $29.99
}

# Default tax rate: 8.875% (New York)
ACP_TAX_RATE: float = 0.08875
