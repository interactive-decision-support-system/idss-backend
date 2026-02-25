"""
UCP (Universal Commerce Protocol) Endpoints.

Thin adapter layer that wraps MCP tools with UCP-compatible request/response format.
When cart_id is a user_id (UUID), uses Supabase cart; otherwise uses in-memory MCP cart.
"""

import re
from typing import Any
from sqlalchemy.orm import Session

from app.ucp_schemas import (
    UCPSearchRequest, UCPSearchResponse, UCPProductSummary,
    UCPGetProductRequest, UCPGetProductResponse, UCPProductDetail,
    UCPAddToCartRequest, UCPAddToCartResponse,
    UCPCheckoutRequest, UCPCheckoutResponse,
    UCPGetCartRequest, UCPGetCartResponse, UCPCartItemOut,
    UCPRemoveFromCartRequest, UCPRemoveFromCartResponse,
    UCPUpdateCartRequest, UCPUpdateCartResponse,
    mcp_status_to_ucp
)
from app.schemas import (
    SearchProductsRequest, GetProductRequest,
    AddToCartRequest, CheckoutRequest
)
from app.endpoints import (
    search_products, get_product, add_to_cart, checkout,
    get_cart_items, remove_from_cart_item, update_cart_quantity,
)
from app.supabase_cart import get_supabase_cart_client


# ============================================================================
# Helper Functions
# ============================================================================

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def _is_user_cart_id(cart_id: str | None) -> bool:
    """True if cart_id looks like a user_id UUID (signed-in cart in Supabase)."""
    return bool(cart_id and _UUID_RE.match(cart_id.strip()))


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
    
    # Enriched fields (week6tips) - pass through from MCP
    shipping = None
    if getattr(mcp_product, "shipping", None):
        s = mcp_product.shipping
        shipping = s.model_dump() if hasattr(s, "model_dump") else s
    return UCPProductSummary(
        id=mcp_product.product_id,
        title=mcp_product.name,
        price={
            "value": round(mcp_product.price_cents / 100, 2),
            "currency": mcp_product.currency
        },
        availability=availability,
        image_link=image_link,
        link=f"{base_url}/products/{mcp_product.product_id}",
        shipping=shipping,
        return_policy=getattr(mcp_product, "return_policy", None),
        warranty=getattr(mcp_product, "warranty", None),
        promotion_info=getattr(mcp_product, "promotion_info", None),
        rating=getattr(mcp_product, "rating", None),
        rating_count=getattr(mcp_product, "rating_count", None),
        reviews=getattr(mcp_product, "reviews", None),
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
    
    shipping = None
    if getattr(mcp_product, "shipping", None):
        s = mcp_product.shipping
        shipping = s.model_dump() if hasattr(s, "model_dump") else s
    return UCPProductDetail(
        id=mcp_product.product_id,
        title=mcp_product.name,
        description=mcp_product.description or "",
        price={
            "value": round(mcp_product.price_cents / 100, 2),
            "currency": mcp_product.currency
        },
        availability=availability,
        image_link=image_link,
        link=f"{base_url}/products/{mcp_product.product_id}",
        category=mcp_product.category,
        brand=mcp_product.brand,
        metadata=mcp_product.metadata,
        shipping=shipping,
        return_policy=getattr(mcp_product, "return_policy", None),
        warranty=getattr(mcp_product, "warranty", None),
        promotion_info=getattr(mcp_product, "promotion_info", None),
        rating=getattr(mcp_product, "rating", None),
        rating_count=getattr(mcp_product, "rating_count", None),
        reviews=getattr(mcp_product, "reviews", None),
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
    
    # Call MCP get_product (local DB for tests and default execution)
    mcp_response = get_product(mcp_request, db)
    
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
    When cart_id is a user_id (UUID), uses Supabase; else MCP in-memory cart.
    """
    cart_id = request.parameters.cart_id or ""
    if not cart_id:
        import uuid
        cart_id = f"CART-{uuid.uuid4().hex[:8]}"

    client = get_supabase_cart_client()
    if _is_user_cart_id(cart_id) and client:
        ok, err = client.add_to_cart(
            cart_id,
            request.parameters.product_id,
            request.parameters.product_snapshot or {},
            request.parameters.quantity,
        )
        if ok:
            rows = client.get_cart(cart_id)
            return UCPAddToCartResponse(
                status="success",
                cart_id=cart_id,
                item_count=len(rows),
                total_price_cents=None,
            )
        return UCPAddToCartResponse(
            status="error",
            cart_id=cart_id,
            item_count=None,
            total_price_cents=None,
            error=err or "Add to cart failed",
            details={},
        )

    mcp_request = AddToCartRequest(
        product_id=request.parameters.product_id,
        qty=request.parameters.quantity,
        cart_id=cart_id,
    )
    mcp_response = add_to_cart(mcp_request, db)
    ucp_status = mcp_status_to_ucp(mcp_response.status)

    if ucp_status == "success" and mcp_response.data:
        return UCPAddToCartResponse(
            status="success",
            cart_id=mcp_response.data.cart_id,
            item_count=len(mcp_response.data.items),
            total_price_cents=sum(item.price_cents * item.quantity for item in mcp_response.data.items),
        )
    error_msg = mcp_response.constraints[0].message if mcp_response.constraints else "Add to cart failed"
    error_details = mcp_response.constraints[0].details if mcp_response.constraints else {}
    return UCPAddToCartResponse(
        status="error",
        cart_id=None,
        item_count=None,
        total_price_cents=None,
        error=error_msg,
        details=error_details,
    )


async def ucp_checkout(
    request: UCPCheckoutRequest,
    db: Session
) -> UCPCheckoutResponse:
    """
    UCP-compatible checkout endpoint.
    When cart_id is a user_id (UUID), uses Supabase; else MCP in-memory checkout.
    """
    cart_id = request.parameters.cart_id
    client = get_supabase_cart_client()
    if _is_user_cart_id(cart_id) and client:
        ok, order_id, err, sold_out_ids = client.checkout(cart_id)
        if ok:
            return UCPCheckoutResponse(
                status="success",
                order_id=order_id,
                total_price_cents=None,
            )
        return UCPCheckoutResponse(
            status="error",
            order_id=None,
            total_price_cents=None,
            error=err or "Checkout failed",
            details={"sold_out_ids": sold_out_ids} if sold_out_ids else None,
        )

    mcp_request = CheckoutRequest(
        cart_id=cart_id,
        payment_method_id=request.parameters.payment_method or "default",
        address_id=request.parameters.shipping_address or "default",
        shipping_method=request.parameters.shipping_method or "standard",
    )
    mcp_response = checkout(mcp_request, db)
    ucp_status = mcp_status_to_ucp(mcp_response.status)

    if ucp_status == "success" and mcp_response.data:
        return UCPCheckoutResponse(
            status="success",
            order_id=mcp_response.data.order_id,
            total_price_cents=mcp_response.data.total_cents,
        )
    error_msg = mcp_response.constraints[0].message if mcp_response.constraints else "Checkout failed"
    error_details = mcp_response.constraints[0].details if mcp_response.constraints else {}
    return UCPCheckoutResponse(
        status="error",
        order_id=None,
        total_price_cents=None,
        error=error_msg,
        details=error_details,
    )


async def ucp_get_cart(request: UCPGetCartRequest, db: Session) -> UCPGetCartResponse:
    """UCP get_cart. When cart_id is user_id (UUID), uses Supabase; else in-memory."""
    cart_id = request.parameters.cart_id
    client = get_supabase_cart_client()
    if _is_user_cart_id(cart_id) and client:
        rows = client.get_cart(cart_id)
        items = [
            UCPCartItemOut(
                id=str(row.get("id", row.get("product_id", ""))),
                product_id=str(row.get("product_id", "")),
                product_snapshot=row.get("product_snapshot") or {},
                quantity=int(row.get("quantity") or 1),
            )
            for row in rows
        ]
        return UCPGetCartResponse(
            status="success",
            cart_id=cart_id,
            items=items,
            item_count=len(items),
        )
    raw_items = get_cart_items(cart_id)
    items = [
        UCPCartItemOut(id=x["id"], product_id=x["product_id"], product_snapshot=x["product_snapshot"], quantity=x["quantity"])
        for x in raw_items
    ]
    return UCPGetCartResponse(
        status="success",
        cart_id=cart_id,
        items=items,
        item_count=len(items),
    )


async def ucp_remove_from_cart(
    request: UCPRemoveFromCartRequest,
    db: Session,
) -> UCPRemoveFromCartResponse:
    """UCP remove_from_cart. When cart_id is user_id (UUID), uses Supabase; else in-memory."""
    cart_id = request.parameters.cart_id
    product_id = request.parameters.product_id
    client = get_supabase_cart_client()
    if _is_user_cart_id(cart_id) and client:
        ok, err = client.remove_from_cart(cart_id, product_id)
        if ok:
            return UCPRemoveFromCartResponse(status="success")
        return UCPRemoveFromCartResponse(status="error", error=err or "Remove failed", details={})
    remove_from_cart_item(cart_id, product_id)
    return UCPRemoveFromCartResponse(status="success")


async def ucp_update_cart(
    request: UCPUpdateCartRequest,
    db: Session,
) -> UCPUpdateCartResponse:
    """UCP update_cart (set quantity; 0 = remove). When cart_id is user_id (UUID), uses Supabase; else in-memory."""
    cart_id = request.parameters.get_cart_id()
    product_id = request.parameters.product_id
    quantity = request.parameters.quantity
    client = get_supabase_cart_client()
    if _is_user_cart_id(cart_id) and client:
        ok, err = client.update_quantity(cart_id, product_id, quantity)
        if ok:
            return UCPUpdateCartResponse(status="success")
        return UCPUpdateCartResponse(status="error", error=err or "Update failed", details={})
    update_cart_quantity(cart_id, product_id, quantity)
    return UCPUpdateCartResponse(status="success")
