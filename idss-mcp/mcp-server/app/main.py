"""
MCP E-commerce Server - Main FastAPI Application

Exposes typed tool-call endpoints for agent interactions.
All endpoints follow the standard response envelope pattern.
"""

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict, Any
import uvicorn
import logging
import traceback
import os
from dotenv import load_dotenv
from pathlib import Path

logger = logging.getLogger("mcp.main")

# Load environment variables from .env file
# Look for .env in project root (parent of idss-mcp/mcp-server)
# Path: app/main.py -> app -> mcp-server -> idss-mcp -> idss_new/.env
# This makes OPENAI_API_KEY and other env vars available to all modules
env_path = Path(__file__).parent.parent.parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)  # Explicitly specify path to .env file
else:
    # Fallback: try default locations
    load_dotenv()

from app.database import get_db, engine, Base
from app.schemas import (
    SearchProductsRequest, SearchProductsResponse,
    GetProductRequest, GetProductResponse,
    AddToCartRequest, AddToCartResponse,
    CheckoutRequest, CheckoutResponse
)
from app.endpoints import search_products, get_product, add_to_cart, checkout
from app.cache import cache_client
from app.metrics import metrics_collector
from app.tool_schemas import get_all_tools_for_provider, ALL_TOOLS
from app.merchant_feed import export_feed
from app.idss_adapter import (
    search_products_idss, get_product_idss,
    search_products_universal, get_product_universal
)
from app.ucp_schemas import (
    UCPSearchRequest, UCPSearchResponse,
    UCPGetProductRequest, UCPGetProductResponse,
    UCPAddToCartRequest, UCPAddToCartResponse,
    UCPCheckoutRequest, UCPCheckoutResponse
)
from app.ucp_endpoints import (
    ucp_search, ucp_get_product, ucp_add_to_cart, ucp_checkout
)
from app.ucp_event_logger import log_ucp_event
from app.supplier_api import router as supplier_router


# Create database tables if they don't exist
# In production, use Alembic migrations instead
Base.metadata.create_all(bind=engine)

# Initialize FastAPI application
app = FastAPI(
    title="MCP E-commerce Server",
    description=
"Model Context Protocol e-commerce server with typed tool-call endpoints",
    version="1.0.0"
)

# Enable CORS for development
# In production, configure this more strictly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include supplier API router
app.include_router(supplier_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Log unhandled exceptions and return 500 so 'Internal server error' is debuggable."""
    err_msg = str(exc)
    tb = traceback.format_exc()
    logger.error("Unhandled exception: %s\n%s", err_msg, tb)
    # In development, include error detail in response to help debug
    is_dev = os.getenv("ENV", "development").lower() in ("development", "dev", "")
    detail = err_msg if is_dev else "Internal server error"
    return JSONResponse(
        status_code=500,
        content={"detail": detail, "type": type(exc).__name__},
    )


# 
# Health Check Endpoints
# 

@app.get("/")
def root():
    """
    Root endpoint - basic health check.
    """
    return {
        "service": "MCP E-commerce Server",
        "version": "1.0.0",
        "status": "operational"
    }


@app.get("/health")
def health_check():
    """
    Detailed health check including database and cache connectivity.
    """
    health_status = {
        "service": "healthy",
        "database": "unknown",
        "cache": "unknown"
    }
    
    # Check database connectivity
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        db.close()
        health_status["database"] = "healthy"
    except Exception as e:
        health_status["database"] = f"unhealthy: {str(e)}"
        health_status["service"] = "degraded"
    
    # Check Redis connectivity
    try:
        if cache_client.ping():
            health_status["cache"] = "healthy"
        else:
            health_status["cache"] = "unhealthy: no response"
            health_status["service"] = "degraded"
    except Exception as e:
        health_status["cache"] = f"unhealthy: {str(e)}"
        health_status["service"] = "degraded"
    
    return health_status


@app.get("/metrics")
def get_metrics():
    """
    Observability metrics endpoint.
    
    Returns:
    - Latency percentiles (p50, p95, p99) per endpoint
    - Cache hit rate
    - Request counts and error rates
    - Uptime
    
    For research and performance analysis.
    """
    return metrics_collector.get_summary()


# 
# Merchant Center Feed Export
# 

@app.get("/export/merchant-feed")
async def export_merchant_feed(
    format: str = "json",
    limit: int = None,
    category: str = None,
    db: Session = Depends(get_db)
):
    """
    Export products in Google Merchant Center compatible format.
    
    Supports:
    - JSON (default): Programmatic access with full metadata
    - XML: Google Shopping Content API format
    - CSV: Simple spreadsheet format
    
    Query Parameters:
    - format: Export format (json|xml|csv), default=json
    - limit: Maximum number of products to export
    - category: Filter by product category
    
    Returns:
    Product feed in specified format
    
    Reference: https://github.com/Universal-Commerce-Protocol/ucp
    """
    result = export_feed(db, format=format, limit=limit, category=category)
    
    if format == "xml":
        from fastapi.responses import Response
        return Response(content=result, media_type="application/xml")
    elif format == "csv":
        from fastapi.responses import Response
        return Response(content=result, media_type="text/csv")
    else:
        return result


# 
# Multi-LLM Tool Discovery Endpoints
# 

@app.get("/tools")
def list_tools():
    """
    List all available MCP tools in canonical format.
    
    This is the provider-neutral tool discovery endpoint.
    Use provider-specific endpoints (/tools/openai, /tools/gemini, /tools/claude)
    for LLM-specific formats.
    """
    return {
        "tools": ALL_TOOLS,
        "total_count": len(ALL_TOOLS),
        "providers_supported": ["openai", "gemini", "claude"]
    }


@app.get("/tools/openai")
def list_tools_openai():
    """
    List all tools in OpenAI function calling format.
    
    Returns tools formatted for OpenAI's function calling API.
    Use this to register MCP tools with GPT-4, GPT-3.5, etc.
    """
    return {
        "functions": get_all_tools_for_provider("openai"),
        "provider": "openai",
        "documentation": "https://platform.openai.com/docs/guides/function-calling"
    }


@app.get("/tools/gemini")
def list_tools_gemini():
    """
    List all tools in Google Gemini function declarations format.
    
    Returns tools formatted for Gemini's function calling API.
    Use this to register MCP tools with Gemini Pro, Gemini Ultra, etc.
    """
    return {
        "functions": get_all_tools_for_provider("gemini"),
        "provider": "gemini",
        "documentation": "https://ai.google.dev/gemini-api/docs/function-calling"
    }


@app.get("/tools/claude")
def list_tools_claude():
    """
    List all tools in Claude tool use format.
    
    Returns tools formatted for Claude's tool use API.
    Use this to register MCP tools with Claude 3 Opus, Sonnet, Haiku, etc.
    """
    return {
        "tools": get_all_tools_for_provider("claude"),
        "provider": "claude",
        "documentation": "https://docs.anthropic.com/claude/docs/tool-use"
    }


class ToolExecutionRequest(BaseModel):
    """Universal tool execution request."""
    tool_name: str
    parameters: Dict[str, Any]


@app.post("/tools/execute")
async def execute_tool(
    request: ToolExecutionRequest,
    db: Session = Depends(get_db)
):
    """
    Universal tool executor for multi-LLM support.
    
    Accepts tool calls from any LLM provider and routes to the appropriate MCP endpoint.
    This enables OpenAI, Gemini, and Claude to all call the same MCP tools.
    
    Args:
        tool_name: Name of the tool to execute (e.g., "search_products")
        parameters: Tool parameters as a dictionary
    
    Returns:
        Tool execution result in standard MCP format
    """
    tool_name = request.tool_name
    params = request.parameters
    
    try:
        # Route to appropriate endpoint based on tool name
        if tool_name == "search_products":
            search_req = SearchProductsRequest(**params)
            return await search_products(search_req, db)
        
        elif tool_name == "get_product":
            get_req = GetProductRequest(**params)
            return get_product(get_req, db)
        
        elif tool_name == "add_to_cart":
            cart_req = AddToCartRequest(**params)
            return add_to_cart(cart_req, db)
        
        elif tool_name == "checkout":
            checkout_req = CheckoutRequest(**params)
            return checkout(checkout_req, db)
        
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Tool '{tool_name}' not found. Available tools: search_products, get_product, add_to_cart, checkout"
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Tool execution error: {str(e)}"
        )


# 
# Tool-Call Endpoints (Main MCP API)
# 

@app.post("/api/search-products", response_model=SearchProductsResponse)
async def api_search_products(
    request: SearchProductsRequest,
    db: Session = Depends(get_db)
):
    """
    Search for products in the catalog.
    
    Tool-call endpoint for product discovery.
    Supports free-text search, structured filters, and pagination.
    
    Returns: List of product summaries with standard response envelope
    """
    return await search_products(request, db)


@app.post("/api/get-product", response_model=GetProductResponse)
def api_get_product(
    request: GetProductRequest,
    db: Session = Depends(get_db)
):
    """
    Get detailed information about a single product.
    
    Tool-call endpoint for product details.
    Uses cache-aside pattern for performance.
    
    IDs-only: Accepts product_id, never product name.
    
    Returns: Full product details or NOT_FOUND
    """
    return get_product(request, db)


@app.post("/api/add-to-cart", response_model=AddToCartResponse)
def api_add_to_cart(
    request: AddToCartRequest,
    db: Session = Depends(get_db)
):
    """
    Add a product to a shopping cart.
    
    Execution endpoint with strict validation.
    IDs-only: Accepts product_id, never product name.
    
    Validates:
    - Product exists
    - Sufficient inventory
    - Creates cart if it doesn't exist
    
    Returns: Updated cart or OUT_OF_STOCK/NOT_FOUND constraint
    """
    return add_to_cart(request, db)


@app.post("/api/checkout", response_model=CheckoutResponse)
def api_checkout(
    request: CheckoutRequest,
    db: Session = Depends(get_db)
):
    """
    Complete checkout for a cart.
    
    Execution endpoint that finalizes a purchase.
    IDs-only: Accepts cart_id, payment_method_id, address_id.
    
    Validates:
    - Cart exists and has items
    - All items still in stock
    - Decrements inventory on success
    
    Returns: Order confirmation or constraints
    """
    return checkout(request, db)


# 
# IDSS-Specific Endpoints (Vehicle Recommendations)
# 

@app.post("/api/idss/search-products", response_model=SearchProductsResponse)
async def api_search_products_idss(request: SearchProductsRequest):
    """
    Search for vehicles using IDSS backend.
    
    This endpoint bridges the MCP product format to IDSS vehicle recommendations.
    Uses the IDSS recommendation engine instead of PostgreSQL product catalog.
    
    Returns: List of vehicles formatted as products
    """
    return await search_products_idss(request)


@app.post("/api/idss/get-product", response_model=GetProductResponse)
async def api_get_product_idss(request: GetProductRequest):
    """
    Get detailed information about a single vehicle from IDSS.
    
    IDs-only: Accepts vehicle_id as product_id.
    
    Returns: Full vehicle details formatted as product
    """
    return await get_product_idss(request)


# 
# Real Estate Endpoints
# 

@app.post("/api/real-estate/search-products", response_model=SearchProductsResponse)
async def api_search_real_estate(request: SearchProductsRequest):
    """
    Search for properties using Real Estate backend.
    
    This endpoint bridges the MCP product format to property listings.
    
    Returns: List of properties formatted as products
    """
    return await search_products_universal(request, product_type="real_estate")


@app.post("/api/real-estate/get-product", response_model=GetProductResponse)
async def api_get_property(request: GetProductRequest):
    """
    Get detailed information about a single property.
    
    IDs-only: Accepts property_id as product_id (format: PROP-###).
    
    Returns: Full property details formatted as product
    """
    return await get_product_universal(request)


# 
# Travel Endpoints
# 

@app.post("/api/travel/search-products", response_model=SearchProductsResponse)
async def api_search_travel(request: SearchProductsRequest):
    """
    Search for travel bookings (flights, hotels, packages) using Travel backend.
    
    This endpoint bridges the MCP product format to travel listings.
    
    Returns: List of travel options formatted as products
    """
    return await search_products_universal(request, product_type="travel")


@app.post("/api/travel/get-product", response_model=GetProductResponse)
async def api_get_travel(request: GetProductRequest):
    """
    Get detailed information about a single travel booking.
    
    IDs-only: Accepts booking_id as product_id (format: BOOK-###).
    
    Returns: Full booking details formatted as product
    """
    return await get_product_universal(request)


# 
# UCP (Universal Commerce Protocol) Endpoints
# 

@app.post("/ucp/search", response_model=UCPSearchResponse)
async def ucp_search_endpoint(
    request: UCPSearchRequest,
    db: Session = Depends(get_db)
):
    """
    UCP-compatible product search endpoint.
    
    Implements Google's Universal Commerce Protocol for agentic commerce.
    Maps UCP format to MCP search_products tool.
    
    Reference: https://github.com/Universal-Commerce-Protocol/ucp
    """
    response = await ucp_search(request, db, base_url="http://localhost:8001")
    # Log event for research replay
    log_ucp_event(db, "ucp_search", "/ucp/search", request, response)
    return response


@app.post("/ucp/get_product", response_model=UCPGetProductResponse)
async def ucp_get_product_endpoint(
    request: UCPGetProductRequest,
    db: Session = Depends(get_db)
):
    """
    UCP-compatible product detail endpoint.
    
    Implements Google's Universal Commerce Protocol for product retrieval.
    Maps UCP format to MCP get_product tool.
    """
    response = await ucp_get_product(request, db, base_url="http://localhost:8001")
    # Log event for research replay
    log_ucp_event(db, "ucp_get_product", "/ucp/get_product", request, response)
    return response


@app.post("/ucp/add_to_cart", response_model=UCPAddToCartResponse)
async def ucp_add_to_cart_endpoint(
    request: UCPAddToCartRequest,
    db: Session = Depends(get_db)
):
    """
    UCP-compatible add to cart endpoint.
    
    Implements Google's Universal Commerce Protocol for cart management.
    Maps UCP format to MCP add_to_cart tool.
    """
    response = await ucp_add_to_cart(request, db)
    # Log event for research replay
    log_ucp_event(db, "ucp_add_to_cart", "/ucp/add_to_cart", request, response, session_id=response.cart_id)
    return response


@app.post("/ucp/checkout", response_model=UCPCheckoutResponse)
async def ucp_checkout_endpoint(
    request: UCPCheckoutRequest,
    db: Session = Depends(get_db)
):
    """
    UCP-compatible checkout endpoint.
    
    Implements Google's Universal Commerce Protocol for order placement.
    Maps UCP format to MCP checkout tool.
    
    Note: Minimal happy-path implementation for research purposes.
    Production would require payment processing, fraud detection, etc.
    """
    response = await ucp_checkout(request, db)
    # Log event for research replay
    log_ucp_event(db, "ucp_checkout", "/ucp/checkout", request, response, session_id=request.parameters.cart_id)
    return response


# 
# UCP Native Checkout Endpoints (per Google UCP Guide)
# 

from app.ucp_checkout import (
    CreateCheckoutSessionRequest, UpdateCheckoutSessionRequest, CompleteCheckoutRequest,
    create_checkout_session, get_checkout_session, update_checkout_session,
    complete_checkout_session, cancel_checkout_session, UCPCheckoutSession
)


@app.post("/ucp/checkout-sessions", response_model=UCPCheckoutSession)
async def ucp_create_checkout_session(
    request: CreateCheckoutSessionRequest,
    db: Session = Depends(get_db)
):
    """
    Create a new UCP checkout session.
    
    Per Google UCP Guide: POST /checkout-sessions
    Trigger: User clicks "Buy" on a product.
    """
    response = create_checkout_session(request, db)
    # Log event for research replay
    log_ucp_event(db, "ucp_create_checkout_session", "/ucp/checkout-sessions", request, response, session_id=response.id)
    return response


@app.get("/ucp/checkout-sessions/{session_id}", response_model=UCPCheckoutSession)
async def ucp_get_checkout_session(session_id: str, db: Session = Depends(get_db)):
    """
    Get checkout session by ID.
    
    Per Google UCP Guide: GET /checkout-sessions/{id}
    """
    session = get_checkout_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    # Log event for research replay
    log_ucp_event(db, "ucp_get_checkout_session", f"/ucp/checkout-sessions/{session_id}", {"session_id": session_id}, session, session_id=session_id)
    return session


@app.put("/ucp/checkout-sessions/{session_id}", response_model=UCPCheckoutSession)
async def ucp_update_checkout_session(
    session_id: str,
    request: UpdateCheckoutSessionRequest,
    db: Session = Depends(get_db)
):
    """
    Update checkout session.
    
    Per Google UCP Guide: PUT /checkout-sessions/{id}
    Recalculates taxes and shipping when address changes.
    """
    session = update_checkout_session(session_id, request, db)
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    # Log event for research replay
    log_ucp_event(db, "ucp_update_checkout_session", f"/ucp/checkout-sessions/{session_id}", request, session, session_id=session_id)
    return session


@app.post("/ucp/checkout-sessions/{session_id}/complete", response_model=UCPCheckoutSession)
async def ucp_complete_checkout_session(
    session_id: str,
    request: CompleteCheckoutRequest,
    db: Session = Depends(get_db)
):
    """
    Complete checkout session and place order.
    
    Per Google UCP Guide: POST /checkout-sessions/{id}/complete
    Trigger: User clicks "Place Order".
    """
    session = complete_checkout_session(session_id, request, db)
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    # Log event for research replay
    log_ucp_event(db, "ucp_complete_checkout_session", f"/ucp/checkout-sessions/{session_id}/complete", request, session, session_id=session_id)
    return session


@app.post("/ucp/checkout-sessions/{session_id}/cancel", response_model=UCPCheckoutSession)
async def ucp_cancel_checkout_session(session_id: str, db: Session = Depends(get_db)):
    """
    Cancel checkout session.
    
    Per Google UCP Guide: POST /checkout-sessions/{id}/cancel
    """
    session = cancel_checkout_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    # Log event for research replay
    log_ucp_event(db, "ucp_cancel_checkout_session", f"/ucp/checkout-sessions/{session_id}/cancel", {"session_id": session_id}, session, session_id=session_id)
    return session


# 
# Development Server
# 

if __name__ == "__main__":
    # Run server with auto-reload for development
    # Note: MCP server runs on port 8001 to avoid conflict with IDSS backend (port 8000)
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8001,
        reload=True
    )
