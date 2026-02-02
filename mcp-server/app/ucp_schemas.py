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
    model_config = ConfigDict(extra="forbid")
    
    product_id: str = Field(..., description="Unique product identifier")
    quantity: int = Field(1, description="Quantity to add", ge=1)
    cart_id: Optional[str] = Field(None, description="Existing cart ID (optional)")


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


# ============================================================================
# UCP Response Schemas
# ============================================================================

class UCPProductSummary(BaseModel):
    """UCP-compatible product summary."""
    id: str = Field(..., description="Product ID")
    title: str = Field(..., description="Product title/name")
    price: Dict[str, Any] = Field(..., description="Price object {value: float, currency: str}")
    availability: str = Field(..., description="Availability status (in stock | out of stock)")
    image_link: Optional[str] = Field(None, description="Primary product image URL")
    link: str = Field(..., description="Product detail page URL")


class UCPProductDetail(BaseModel):
    """UCP-compatible product detail."""
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
