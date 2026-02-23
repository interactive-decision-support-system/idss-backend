"""
Universal Commerce Protocol (UCP) Schemas.

Implements the UCP request/response format for compatibility with Google's
agentic commerce protocol.

Reference: https://github.com/Universal-Commerce-Protocol/ucp
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, ConfigDict


# ============================================================================
# UCP Request Schemas
# ============================================================================

class UCPSearchParameters(BaseModel):
    """Parameters for UCP search action."""
    model_config = ConfigDict(extra="forbid")
    
    query: str = Field(..., description="Free-text search query")
    filters: Optional[Dict[str, Any]] = Field(None, description="Search filters (category, price_range, etc.)")
    limit: Optional[int] = Field(10, description="Maximum number of results")
    offset: Optional[int] = Field(0, description="Pagination offset")


class UCPSearchRequest(BaseModel):
    """UCP search action request."""
    model_config = ConfigDict(extra="forbid")
    
    action: str = Field("search", description="Action type (always 'search')")
    parameters: UCPSearchParameters = Field(..., description="Search parameters")


class UCPGetProductParameters(BaseModel):
    """Parameters for UCP get_product action."""
    model_config = ConfigDict(extra="forbid")
    
    product_id: str = Field(..., description="Unique product identifier")
    fields: Optional[List[str]] = Field(None, description="Optional field projection")


class UCPGetProductRequest(BaseModel):
    """UCP get_product action request."""
    model_config = ConfigDict(extra="forbid")
    
    action: str = Field("get_product", description="Action type (always 'get_product')")
    parameters: UCPGetProductParameters = Field(..., description="Product parameters")


class UCPAddToCartParameters(BaseModel):
    """Parameters for UCP add_to_cart action."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    
    product_id: str = Field(..., description="Unique product identifier")
    quantity: int = Field(1, alias="qty", description="Quantity to add", ge=1)
    cart_id: Optional[str] = Field(None, description="Existing cart ID (optional)")
    product_snapshot: Optional[Dict[str, Any]] = Field(None, description="Product snapshot for Supabase cart (user-based)")


class UCPAddToCartRequest(BaseModel):
    """UCP add_to_cart action request."""
    model_config = ConfigDict(extra="forbid")
    
    action: str = Field("add_to_cart", description="Action type (always 'add_to_cart')")
    parameters: UCPAddToCartParameters = Field(..., description="Cart parameters")


class UCPCheckoutParameters(BaseModel):
    """Parameters for UCP checkout action."""
    model_config = ConfigDict(extra="forbid")
    
    cart_id: str = Field(..., description="Cart identifier")
    payment_method: Optional[str] = Field(None, description="Payment method (optional for minimal UCP)")
    shipping_address: Optional[str] = Field(None, description="Shipping address (optional)")


class UCPCheckoutRequest(BaseModel):
    """UCP checkout action request."""
    model_config = ConfigDict(extra="forbid")
    
    action: str = Field("checkout", description="Action type (always 'checkout')")
    parameters: UCPCheckoutParameters = Field(..., description="Checkout parameters")


class UCPGetCartParameters(BaseModel):
    """Parameters for UCP get_cart action."""
    model_config = ConfigDict(extra="forbid")
    
    cart_id: str = Field(..., description="Cart identifier (user_id for signed-in)")


class UCPGetCartRequest(BaseModel):
    """UCP get_cart action request."""
    model_config = ConfigDict(extra="forbid")
    
    action: str = Field("get_cart", description="Action type (always 'get_cart')")
    parameters: UCPGetCartParameters = Field(..., description="Get cart parameters")


class UCPRemoveFromCartParameters(BaseModel):
    """Parameters for UCP remove_from_cart action."""
    model_config = ConfigDict(extra="forbid")
    
    cart_id: str = Field(..., description="Cart identifier (user_id for signed-in)")
    product_id: str = Field(..., description="Product to remove")


class UCPRemoveFromCartRequest(BaseModel):
    """UCP remove_from_cart action request."""
    model_config = ConfigDict(extra="forbid")
    
    action: str = Field("remove_from_cart", description="Action type (always 'remove_from_cart')")
    parameters: UCPRemoveFromCartParameters = Field(..., description="Remove parameters")


class UCPUpdateCartParameters(BaseModel):
    """Parameters for UCP update_cart action (quantity update; 0 = remove)."""
    model_config = ConfigDict(extra="forbid")
    
    cart_id: Optional[str] = Field(None, description="Cart identifier (user_id for signed-in)")
    user_id: Optional[str] = Field(None, description="Alias for cart_id (backward compat)")
    product_id: str = Field(..., description="Product to update")
    quantity: int = Field(..., ge=0, description="New quantity (0 to remove)")

    def get_cart_id(self) -> str:
        return (self.cart_id or self.user_id or "").strip()


class UCPUpdateCartRequest(BaseModel):
    """UCP update_cart action request."""
    model_config = ConfigDict(extra="forbid")
    
    action: str = Field("update_cart", description="Action type (always 'update_cart')")
    parameters: UCPUpdateCartParameters = Field(..., description="Update parameters")


# ============================================================================
# UCP Response Schemas
# ============================================================================

class UCPProductSummary(BaseModel):
    """UCP-compatible product summary. Includes enriched agent-ready fields (week6tips)."""
    id: str = Field(..., description="Product ID")
    title: str = Field(..., description="Product title/name")
    price: Dict[str, Any] = Field(..., description="Price object {value: float, currency: str}")
    availability: str = Field(..., description="Availability status (in stock | out of stock)")
    image_link: Optional[str] = Field(None, description="Primary product image URL")
    link: str = Field(..., description="Product detail page URL")
    # Enriched fields (week6tips: reduce back-and-forth for any AI agent)
    shipping: Optional[Dict[str, Any]] = Field(None, description="Delivery ETA, method, cost")
    return_policy: Optional[str] = Field(None, description="e.g. Free 30-day returns")
    warranty: Optional[str] = Field(None, description="e.g. 1-year manufacturer warranty")
    promotion_info: Optional[str] = Field(None, description="Promotion or discount info")
    rating: Optional[float] = Field(None, description="Average product rating")
    rating_count: Optional[int] = Field(None, description="Number of ratings")
    reviews: Optional[str] = Field(None, description="User reviews as free text")


class UCPProductDetail(BaseModel):
    """UCP-compatible product detail. Includes enriched agent-ready fields (week6tips)."""
    id: str = Field(..., description="Product ID")
    title: str = Field(..., description="Product title/name")
    description: str = Field(..., description="Product description")
    price: Dict[str, Any] = Field(..., description="Price object {value: float, currency: str}")
    availability: str = Field(..., description="Availability status")
    image_link: Optional[str] = Field(None, description="Primary product image URL")
    link: str = Field(..., description="Product detail page URL")
    category: Optional[str] = Field(None, description="Product category")
    brand: Optional[str] = Field(None, description="Product brand")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional product metadata")
    # Enriched fields (week6tips)
    shipping: Optional[Dict[str, Any]] = Field(None, description="Delivery ETA, method, cost")
    return_policy: Optional[str] = Field(None, description="Return policy")
    warranty: Optional[str] = Field(None, description="Warranty")
    promotion_info: Optional[str] = Field(None, description="Promotion info")
    rating: Optional[float] = Field(None, description="Average product rating")
    rating_count: Optional[int] = Field(None, description="Number of ratings")
    reviews: Optional[str] = Field(None, description="User reviews as free text")


class UCPSearchResponse(BaseModel):
    """UCP search action response."""
    status: str = Field(..., description="Response status (success | error)")
    products: List[UCPProductSummary] = Field(..., description="List of matching products")
    total_count: int = Field(..., description="Total number of matching products")
    error: Optional[str] = Field(None, description="Error message if status is error")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")


class UCPGetProductResponse(BaseModel):
    """UCP get_product action response."""
    status: str = Field(..., description="Response status (success | error)")
    product: Optional[UCPProductDetail] = Field(None, description="Product details")
    error: Optional[str] = Field(None, description="Error message if status is error")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")


class UCPAddToCartResponse(BaseModel):
    """UCP add_to_cart action response."""
    status: str = Field(..., description="Response status (success | error)")
    cart_id: Optional[str] = Field(None, description="Cart identifier")
    item_count: Optional[int] = Field(None, description="Total items in cart")
    total_price_cents: Optional[int] = Field(None, description="Total cart price in cents")
    error: Optional[str] = Field(None, description="Error message if status is error")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")


class UCPCheckoutResponse(BaseModel):
    """UCP checkout action response."""
    status: str = Field(..., description="Response status (success | error)")
    order_id: Optional[str] = Field(None, description="Order identifier")
    total_price_cents: Optional[int] = Field(None, description="Total order price in cents")
    error: Optional[str] = Field(None, description="Error message if status is error")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")


class UCPCartItemOut(BaseModel):
    """Single cart item in UCP get_cart response."""
    id: str = Field(..., description="Row or item id")
    product_id: str = Field(..., description="Product identifier")
    product_snapshot: Dict[str, Any] = Field(default_factory=dict, description="Product snapshot")
    quantity: int = Field(..., description="Quantity")


class UCPGetCartResponse(BaseModel):
    """UCP get_cart action response."""
    status: str = Field(..., description="Response status (success | error)")
    cart_id: Optional[str] = Field(None, description="Cart identifier")
    items: List[UCPCartItemOut] = Field(default_factory=list, description="Cart items")
    item_count: Optional[int] = Field(None, description="Number of items")
    error: Optional[str] = Field(None, description="Error message if status is error")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")


class UCPRemoveFromCartResponse(BaseModel):
    """UCP remove_from_cart action response."""
    status: str = Field(..., description="Response status (success | error)")
    error: Optional[str] = Field(None, description="Error message if status is error")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")


class UCPUpdateCartResponse(BaseModel):
    """UCP update_cart action response."""
    status: str = Field(..., description="Response status (success | error)")
    error: Optional[str] = Field(None, description="Error message if status is error")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")


# ============================================================================
# UCP Error Mapping
# ============================================================================

UCP_ERROR_MAP = {
    "NOT_FOUND": "product_not_found",
    "OUT_OF_STOCK": "insufficient_inventory",
    "INVALID": "validation_error",
    "NEEDS_CLARIFICATION": "ambiguous_request",
    "ERROR": "internal_error"
}


def mcp_status_to_ucp(mcp_status: str) -> str:
    """
    Convert MCP status to UCP error code.
    
    Args:
        mcp_status: MCP response status
        
    Returns:
        UCP error code
    """
    if mcp_status == "OK":
        return "success"
    return UCP_ERROR_MAP.get(mcp_status, "internal_error")
