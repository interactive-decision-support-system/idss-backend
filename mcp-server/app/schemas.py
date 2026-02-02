"""
Pydantic v2 schemas for strict request/response validation.

All request schemas use extra="forbid" to reject unknown fields.
All response schemas follow the standard envelope pattern.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from enum import Enum


# 
# Enums for Response Status
# 

class ResponseStatus(str, Enum):
    """
    Standard response status codes for all MCP endpoints.
    These enable deterministic agent behavior and self-correction.
    """
    OK = "OK"
    INVALID = "INVALID"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


# 
# Response Envelope Components
# 

class ConstraintDetail(BaseModel):
    """
    Structured constraint information for "why rejected" self-correction.
    
    Example: When an item is out of stock, this tells the agent:
    - What the problem is (code + message)
    - Specific details (requested vs available)
    - What fields are allowed
    - What actions the agent can take to fix it
    """
    code: str = Field(..., description="Machine-readable constraint code")
    message: str = Field(..., description="Human-readable explanation")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional structured data about the constraint")
    allowed_fields: Optional[List[str]] = Field(None, description="Fields that are valid for this request")
    suggested_actions: Optional[List[str]] = Field(None, description="Actions the agent can take to resolve this")


class RequestTrace(BaseModel):
    """
    Request tracing information for observability and debugging.
    Shows timing breakdown, cache hits, and data sources queried.

    metadata may include (per week4notes.txt):
    - kg_reasoning, kg_verification, kg_explanation: KG verification/reasoning
    - latency_target_ms, within_latency_target: Latency target (e.g. 400ms)
    - relaxed: True when results came from progressive relaxation (category-only fallback)
    - dropped_filters: List of filter keys that were dropped (e.g. product_type, gpu_vendor, color)
    - relaxation_reason: Human-readable message for UI banner (e.g. "No matches with your filters; showing Electronics (dropped: ...)")
    """
    request_id: str = Field(..., description="Unique identifier for this request")
    cache_hit: bool = Field(..., description="Whether this request hit the cache")
    timings_ms: Dict[str, float] = Field(..., description="Timing breakdown (cache, db, total, etc)")
    sources: List[str] = Field(..., description="Data sources queried (redis, postgres, etc)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata (e.g., session_id for IDSS)")


class ProvenanceInfo(BaseModel):
    """
    Per-field provenance tracking for research-grade grounding.
    Tracks where each piece of data came from and when.
    """
    source: str = Field(..., description="Data source: postgres | idss_sqlite | real_estate_api | travel_api | neo4j | vlm_inferred | snapshot")
    timestamp: datetime = Field(..., description="When this data was retrieved/generated")
    confidence: Optional[float] = Field(None, description="Confidence score for inferred data (0.0-1.0)")
    model_name: Optional[str] = Field(None, description="Model name if data is VLM-inferred")


class VersionInfo(BaseModel):
    """
    Version and staleness control information.
    Allows agents to detect when data might be stale.
    """
    catalog_version: str = Field(..., description="Current catalog version")
    updated_at: datetime = Field(..., description="When this data was last updated")
    db_version: Optional[str] = Field(None, description="Database schema version")
    snapshot_version: Optional[str] = Field(None, description="Snapshot version if applicable")
    kg_version: Optional[str] = Field(None, description="Knowledge graph version (reserved for Stage 3)")


# 
# Request Schemas - All use extra="forbid" for strictness
# 

class ProductFilters(BaseModel):
    """
    Structured filters for search. Hard constraints (product_type, gpu_vendor, price_max)
    are applied first; items with NULL for a required field do not pass.
    """
    model_config = ConfigDict(extra="allow")  # Allow additional keys for backward compat
    category: Optional[str] = None
    product_type: Optional[List[str]] = Field(None, description="e.g. desktop_pc, gaming_laptop, laptop, book")
    brand: Optional[List[str]] = None
    gpu_vendor: Optional[List[str]] = Field(None, description="NVIDIA, AMD; NULL in DB = unknown, must not pass when required")
    color: Optional[List[str]] = None
    price_min_cents: Optional[int] = None
    price_max_cents: Optional[int] = None
    price_max: Optional[int] = Field(None, description="Max price in dollars (converted to cents)")
    in_stock: Optional[bool] = None
    genre: Optional[str] = None
    format: Optional[str] = None


class SearchProductsRequest(BaseModel):
    """
    Search for products in the catalog.
    Use filters.product_type + filters.gpu_vendor + filters.price_max_cents for hard-filtering
    so "gaming PC NVIDIA < $2000" does not return MacBooks.
    """
    model_config = ConfigDict(extra="forbid")
    query: Optional[str] = Field(None, description="Free-text search query")
    filters: Optional[Dict[str, Any]] = Field(
        None,
        description="Structured filters: category, product_type[], gpu_vendor[], price_max_cents, brand, color, etc."
    )
    limit: int = Field(10, ge=1, le=100, description="Maximum number of results to return")
    cursor: Optional[str] = Field(None, description="Pagination cursor")
    session_id: Optional[str] = Field(
        None,
        description="Opaque session identifier passed through to backends (e.g., IDSS). MCP itself does not manage conversation state."
    )


class GetProductRequest(BaseModel):
    """
    Get a single product by its ID.
    
    Note: Uses product_id only, never product name (IDs-only execution).
    Supports field projection to reduce context size.
    """
    model_config = ConfigDict(extra="forbid")
    
    product_id: str = Field(..., description="Unique product identifier")
    fields: Optional[List[str]] = Field(None, description="Specific fields to return (for field projection). If None, returns full details.")


class AddToCartRequest(BaseModel):
    """
    Add a product to a cart.
    
    CRITICAL: Only accepts product_id, never product name.
    This is the "IDs-only execution" rule for deterministic behavior.
    """
    model_config = ConfigDict(extra="forbid")
    
    cart_id: str = Field(..., description="Cart identifier")
    product_id: str = Field(..., description="Product identifier (IDs only, never names)")
    qty: int = Field(..., ge=1, description="Quantity to add")


class CheckoutRequest(BaseModel):
    """
    Complete checkout for a cart.
    
    Note: Uses IDs only for cart, payment method, and address.
    """
    model_config = ConfigDict(extra="forbid")
    
    cart_id: str = Field(..., description="Cart identifier")
    payment_method_id: str = Field(..., description="Payment method identifier")
    address_id: str = Field(..., description="Shipping address identifier")


# 
# Data Schemas - The actual payload content
# 

class ProductSummary(BaseModel):
    """
    Summary view of a product (used in search results).
    Contains essential fields for discovery and selection.
    """
    """
    Minimal product summary for search results.
    Keeps context small - agent can request full details separately.
    
    Supports multiple product types:
    - Vehicles: category = body style, brand = make
    - E-commerce: category = product category, brand = manufacturer
    - Real estate: category = property type, brand = builder
    """
    product_id: str
    name: str
    price_cents: int
    currency: str = "USD"
    category: Optional[str] = None
    brand: Optional[str] = None
    available_qty: int
    source: Optional[str] = Field(None, description="Platform: WooCommerce, Shopify, Temu, Seed, etc.")
    color: Optional[str] = Field(None, description="Product color (e.g. Silver, Space Gray)")
    scraped_from_url: Optional[str] = Field(None, description="URL or domain scraped from (e.g. mc-demo.mybigcommerce.com). Null for Seed.")

    # Product type metadata
    product_type: Optional[str] = Field(None, description="Product type: vehicle, ecommerce, real_estate, etc")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Type-specific metadata")
    
    # Provenance tracking (research-grade)
    provenance: Optional[ProvenanceInfo] = Field(None, description="Data source provenance")


class ProductDetail(BaseModel):
    """
    Full product details including description and metadata.
    
    Extended to support multiple product types with type-specific metadata:
    - Vehicles: VIN, mileage, fuel type, drivetrain, etc.
    - E-commerce: SKU, dimensions, weight, specifications
    - Real estate: Square footage, bedrooms, lot size, etc.
    """
    product_id: str
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    price_cents: int
    currency: str = "USD"
    available_qty: int
    source: Optional[str] = Field(None, description="Platform: WooCommerce, Shopify, Temu, Seed, etc.")
    color: Optional[str] = Field(None, description="Product color (e.g. Silver, Space Gray)")
    scraped_from_url: Optional[str] = Field(None, description="URL or domain scraped from. Null for Seed.")
    reviews: Optional[str] = Field(None, description="User reviews as free text")
    created_at: datetime
    updated_at: datetime

    # Provenance tracking (research-grade)
    provenance: Optional[ProvenanceInfo] = Field(None, description="Data source provenance")
    
    # Product type metadata
    product_type: Optional[str] = Field(None, description="Product type: vehicle, ecommerce, real_estate, etc")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Type-specific metadata (VIN, SKU, etc)")


class CartItemData(BaseModel):
    """
    Cart item with product information.
    """
    cart_item_id: int
    product_id: str
    product_name: str
    quantity: int
    price_cents: int
    currency: str = "USD"


class CartData(BaseModel):
    """
    Cart with all items and totals.
    """
    cart_id: str
    status: str
    items: List[CartItemData]
    total_cents: int
    currency: str = "USD"


class ShippingInfo(BaseModel):
    """
    Logistics/shipping information (per week4notes.txt).
    May be synthetic at first; supports location-dependent delivery and cost.
    """
    shipping_method: Optional[str] = Field(None, description="e.g. standard, express")
    estimated_delivery_days: Optional[int] = Field(None, description="Estimated days to delivery")
    shipping_cost_cents: Optional[int] = Field(None, description="Shipping cost in cents")
    shipping_region: Optional[str] = Field(None, description="Region/country code for delivery (e.g. US, EU)")


class OrderData(BaseModel):
    """
    Completed order information.
    Includes optional logistics/shipping (week4notes.txt).
    """
    order_id: str
    cart_id: str
    total_cents: int
    currency: str = "USD"
    status: str
    created_at: datetime
    shipping: Optional[ShippingInfo] = Field(None, description="Shipping time, cost, region (synthetic OK)")


class SearchResultsData(BaseModel):
    """
    Search results with pagination support.
    """
    products: List[ProductSummary]
    total_count: int
    next_cursor: Optional[str] = None


# 
# Response Schemas - Standard Envelope
# 

class BaseResponse(BaseModel):
    """
    Base response envelope that all endpoints return.
    
    This ensures deterministic, structured responses that agents can reliably parse.
    """
    status: ResponseStatus = Field(..., description="Response status code")
    data: Optional[Any] = Field(None, description="Response payload (typed per endpoint)")
    constraints: List[ConstraintDetail] = Field(default_factory=list, description="Constraint violations and corrections")
    trace: RequestTrace = Field(..., description="Request tracing information")
    version: VersionInfo = Field(..., description="Catalog version and staleness info")


class SearchProductsResponse(BaseResponse):
    """Search products response with typed data field."""
    data: Optional[SearchResultsData] = None


class GetProductResponse(BaseResponse):
    """Get product response with typed data field."""
    data: Optional[ProductDetail] = None


class AddToCartResponse(BaseResponse):
    """Add to cart response with typed data field."""
    data: Optional[CartData] = None


class CheckoutResponse(BaseResponse):
    """Checkout response with typed data field."""
    data: Optional[OrderData] = None
