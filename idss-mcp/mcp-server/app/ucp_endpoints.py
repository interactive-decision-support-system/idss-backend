"""
UCP (Universal Commerce Protocol) Endpoints.

Thin adapter layer that wraps MCP tools with UCP-compatible request/response format.
"""

from typing import Dict, Any
from sqlalchemy.orm import Session

from app.ucp_schemas import (
    UCPSearchRequest, UCPSearchResponse, UCPProductSummary,
    UCPGetProductRequest, UCPGetProductResponse, UCPProductDetail,
    UCPAddToCartRequest, UCPAddToCartResponse,
    UCPCheckoutRequest, UCPCheckoutResponse,
    mcp_status_to_ucp
)
from app.schemas import (
    SearchProductsRequest, GetProductRequest,
    AddToCartRequest, CheckoutRequest
)
from app.endpoints import search_products, get_product, add_to_cart, checkout
from app.database import get_db


# ============================================================================
# Helper Functions
# ============================================================================

def mcp_product_to_ucp_summary(mcp_product: Any, base_url: str = "https://example.com") -> UCPProductSummary:
    """
    Convert MCP ProductSummary to UCP format.
    
    Args:
        mcp_product: MCP ProductSummary object
        base_url: Base URL for product links
        
    Returns:
        UCPProductSummary
    """
    # Determine availability
    availability = "in stock" if mcp_product.available_qty > 0 else "out of stock"
    
    # Extract image link
    image_link = None
    if mcp_product.metadata:
        image_link = mcp_product.metadata.get("primary_image") or mcp_product.metadata.get("images", [None])[0]
    
    return UCPProductSummary(
        id=mcp_product.product_id,
        title=mcp_product.name,
        price={
            "value": round(mcp_product.price_cents / 100, 2),
            "currency": mcp_product.currency
        },
        availability=availability,
        image_link=image_link,
        link=f"{base_url}/products/{mcp_product.product_id}"
    )


def mcp_product_to_ucp_detail(mcp_product: Any, base_url: str = "https://example.com") -> UCPProductDetail:
    """
    Convert MCP ProductDetail to UCP format.
    
    Args:
        mcp_product: MCP ProductDetail object
        base_url: Base URL for product links
        
    Returns:
        UCPProductDetail
    """
    # Determine availability
    availability = "in stock" if mcp_product.available_qty > 0 else "out of stock"
    
    # Extract image link
    image_link = None
    if mcp_product.metadata:
        image_link = mcp_product.metadata.get("primary_image") or mcp_product.metadata.get("images", [None])[0]
    
    return UCPProductDetail(
        id=mcp_product.product_id,
        title=mcp_product.name,
        description=mcp_product.description,
        price={
            "value": round(mcp_product.price_cents / 100, 2),
            "currency": mcp_product.currency
        },
        availability=availability,
        image_link=image_link,
        link=f"{base_url}/products/{mcp_product.product_id}",
        category=mcp_product.category,
        brand=mcp_product.brand,
        metadata=mcp_product.metadata
    )


# ============================================================================
# UCP Endpoints
# ============================================================================

async def ucp_search(
    request: UCPSearchRequest,
    db: Session,
    base_url: str = "https://example.com"
) -> UCPSearchResponse:
    """
    UCP-compatible product search endpoint.
    
    Maps UCP search request to MCP search_products tool.
    When the backend asks a follow-up question (interview), response status is "error"
    with the question in error/details (details.response_type == "question").
    To get products in one call for "laptop"/"books", pass filters, e.g.:
      filters: {"category": "Electronics", "use_case": "Work"} or {"category": "Books"}.
    """
    filters = request.parameters.filters or {}
    # If UCP caller sends no filters and query is just category-like, add defaults
    # so a single "laptop" or "books" call returns products (skip interview).
    query = (request.parameters.query or "").strip().lower()
    if not filters and query in ("laptop", "laptops", "electronics", "books", "book"):
        if query in ("books", "book"):
            filters = {"category": "Books"}
        else:
            # Backend needs use_case + budget (+ brand) to skip interview; add use_case + budget
            filters = {
                "category": "Electronics",
                "use_case": "Work",
                "price_max_cents": 300000,  # $3000 so we have budget; brand still asked unless passed
            }
    # Convert UCP request to MCP format
    mcp_request = SearchProductsRequest(
        query=request.parameters.query,
        filters=filters,
        limit=request.parameters.limit or 10
    )
    
    # Call MCP search_products (async — must await)
    mcp_response = await search_products(mcp_request, db)
    
    # Convert MCP response to UCP format
    ucp_status = mcp_status_to_ucp(mcp_response.status)
    
    if ucp_status == "success" and mcp_response.data:
        ucp_products = [
            mcp_product_to_ucp_summary(p, base_url)
            for p in mcp_response.data.products
        ]
        
        return UCPSearchResponse(
            status="success",
            products=ucp_products,
            total_count=mcp_response.data.total_count
        )
    else:
        # Error response
        error_msg = mcp_response.constraints[0].message if mcp_response.constraints else "Search failed"
        error_details = mcp_response.constraints[0].details if mcp_response.constraints else {}
        
        return UCPSearchResponse(
            status="error",
            products=[],
            total_count=0,
            error=error_msg,
            details=error_details
        )


async def ucp_get_product(
    request: UCPGetProductRequest,
    db: Session,
    base_url: str = "https://example.com"
) -> UCPGetProductResponse:
    """
    UCP-compatible product detail endpoint.
    
    Maps UCP get_product request to MCP get_product tool.
    """
    # Convert UCP request to MCP format
    mcp_request = GetProductRequest(
        product_id=request.parameters.product_id,
        fields=request.parameters.fields
    )
    
    # Call MCP get_product (use the universal adapter)
    from app.idss_adapter import get_product_universal
    mcp_response = await get_product_universal(mcp_request)
    
    # Convert MCP response to UCP format
    ucp_status = mcp_status_to_ucp(mcp_response.status)
    
    if ucp_status == "success" and mcp_response.data:
        ucp_product = mcp_product_to_ucp_detail(mcp_response.data, base_url)
        
        return UCPGetProductResponse(
            status="success",
            product=ucp_product
        )
    else:
        # Error response
        error_msg = mcp_response.constraints[0].message if mcp_response.constraints else "Product not found"
        error_details = mcp_response.constraints[0].details if mcp_response.constraints else {}
        
        return UCPGetProductResponse(
            status="error",
            product=None,
            error=error_msg,
            details=error_details
        )


async def ucp_add_to_cart(
    request: UCPAddToCartRequest,
    db: Session
) -> UCPAddToCartResponse:
    """
    UCP-compatible add to cart endpoint.
    
    Maps UCP add_to_cart request to MCP add_to_cart tool.
    """
    # Generate cart_id if not provided (UCP allows None, MCP requires a value)
    import uuid
    cart_id = request.parameters.cart_id or f"CART-{uuid.uuid4().hex[:8]}"
    
    # Convert UCP request to MCP format
    mcp_request = AddToCartRequest(
        product_id=request.parameters.product_id,
        qty=request.parameters.quantity,
        cart_id=cart_id
    )
    
    # Call MCP add_to_cart
    mcp_response = add_to_cart(mcp_request, db)
    
    # Convert MCP response to UCP format
    ucp_status = mcp_status_to_ucp(mcp_response.status)
    
    if ucp_status == "success" and mcp_response.data:
        return UCPAddToCartResponse(
            status="success",
            cart_id=mcp_response.data.cart_id,
            item_count=len(mcp_response.data.items),
            total_price_cents=sum(item.price_cents * item.quantity for item in mcp_response.data.items)
        )
    else:
        # Error response
        error_msg = mcp_response.constraints[0].message if mcp_response.constraints else "Add to cart failed"
        error_details = mcp_response.constraints[0].details if mcp_response.constraints else {}
        
        return UCPAddToCartResponse(
            status="error",
            cart_id=None,
            item_count=None,
            total_price_cents=None,
            error=error_msg,
            details=error_details
        )


async def ucp_checkout(
    request: UCPCheckoutRequest,
    db: Session
) -> UCPCheckoutResponse:
    """
    UCP-compatible checkout endpoint.
    
    Maps UCP checkout request to MCP checkout tool.
    
    Note: This is a minimal happy-path implementation.
    Production would require payment processing, fraud detection, etc.
    """
    # Convert UCP request to MCP format
    mcp_request = CheckoutRequest(
        cart_id=request.parameters.cart_id,
        payment_method_id=request.parameters.payment_method or "default",
        address_id=request.parameters.shipping_address or "default"
    )
    
    # Call MCP checkout
    mcp_response = checkout(mcp_request, db)
    
    # Convert MCP response to UCP format
    ucp_status = mcp_status_to_ucp(mcp_response.status)
    
    if ucp_status == "success" and mcp_response.data:
        return UCPCheckoutResponse(
            status="success",
            order_id=mcp_response.data.order_id,
            total_price_cents=mcp_response.data.total_cents
        )
    else:
        # Error response
        error_msg = mcp_response.constraints[0].message if mcp_response.constraints else "Checkout failed"
        error_details = mcp_response.constraints[0].details if mcp_response.constraints else {}
        
        return UCPCheckoutResponse(
            status="error",
            order_id=None,
            total_price_cents=None,
            error=error_msg,
            details=error_details
        )
