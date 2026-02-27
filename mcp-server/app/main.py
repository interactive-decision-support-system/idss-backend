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
from typing import Dict, Any, List, Optional
import uvicorn
import logging
import traceback
import os
import sys
import time as _time
import json as _json
from dotenv import load_dotenv
from pathlib import Path
from contextlib import asynccontextmanager
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

# Add repo root to Python path so `agent` package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logger = logging.getLogger("mcp.main")

# Load environment variables from .env file
# Look for .env in project root (parent of mcp-server)
# Path: app/main.py -> app -> mcp-server -> root/.env
# This makes OPENAI_API_KEY and other env vars available to all modules
env_path = Path(__file__).parent.parent.parent / '.env'
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
    search_products_idss, get_product_universal,
    search_products_universal
)
from app.ucp_schemas import (
    UCPSearchRequest, UCPSearchResponse,
    UCPGetProductRequest, UCPGetProductResponse,
    UCPAddToCartRequest, UCPAddToCartResponse,
    UCPCheckoutRequest, UCPCheckoutResponse,
    UCPGetCartRequest, UCPGetCartResponse,
    UCPRemoveFromCartRequest, UCPRemoveFromCartResponse,
    UCPUpdateCartRequest, UCPUpdateCartResponse,
)
from app.ucp_endpoints import (
    ucp_search, ucp_get_product, ucp_add_to_cart, ucp_checkout,
    ucp_get_cart, ucp_remove_from_cart, ucp_update_cart,
)
from app.ucp_event_logger import log_ucp_event
from app.cart_action_agent import (
    build_ucp_get_cart,
    build_ucp_add_to_cart,
    build_ucp_remove_from_cart,
    build_ucp_checkout,
    build_ucp_update_cart,
    build_acp_create_session,
    build_acp_update_session,
    build_acp_complete_session,
)
from app.acp_schemas import (
    ACPCreateSessionRequest, ACPUpdateSessionRequest, ACPCompleteSessionRequest,
    ACPCheckoutSession,
)
from app.acp_endpoints import (
    acp_create_checkout_session as _acp_create,
    acp_get_checkout_session as _acp_get,
    acp_update_checkout_session as _acp_update,
    acp_complete_checkout_session as _acp_complete,
    acp_cancel_checkout_session as _acp_cancel,
    generate_product_feed,
)
from app.protocol_config import is_acp
from app.supabase_cart import get_supabase_cart_client
from app.supplier_api import router as supplier_router
from app.shipping_tax import calculate_shipping, ALL_STATES
from app.coupons import validate_coupon
from agent import ChatRequest, ChatResponse, process_chat
from agent.interview.session_manager import SessionResponse, ResetRequest, ResetResponse
from agent.interview.session_manager import get_session_state, reset_session, delete_session, list_sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan handler to preload IDSS components.

    Replaces deprecated startup events.
    """
    # Create database tables if they don't exist
    # In production, use Alembic migrations instead
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as _e:
        logger.warning(
            "Could not run Base.metadata.create_all: %s. "
            "Tables should already exist (Supabase / remote DB).",
            _e,
        )

    skip_preload = os.getenv("MCP_SKIP_PRELOAD", "0") == "1"
    if skip_preload:
        logger.info("Skipping IDSS preload (MCP_SKIP_PRELOAD=1)")
        yield
        return

    logger.info("Starting IDSS component preload...")

    try:
        from app.tools.vehicle_search import preload_idss_components
        preload_idss_components()
        logger.info("IDSS components preloaded successfully")
    except Exception as e:
        logger.warning(f"Failed to preload IDSS components: {e}")
        logger.warning("Vehicle search will lazy-load on first request")

    yield

# Initialize FastAPI application
app = FastAPI(
    title="MCP E-commerce Server",
    description=
"Model Context Protocol e-commerce server with typed tool-call endpoints",
    version="1.0.0",
    lifespan=lifespan,
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

# ---------------------------------------------------------------------------
# Latency logging middleware
# Logs every non-OPTIONS request with method, path, status, and duration_ms.
# Lines are also written to latency_log.jsonl in the project root for offline
# analysis and the poster latency table.
# ---------------------------------------------------------------------------
_LATENCY_LOG_PATH = Path(__file__).parent.parent.parent / "backend_latency_logs.jsonl"

class LatencyLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next) -> StarletteResponse:
        if request.method == "OPTIONS":
            return await call_next(request)
        t0 = _time.perf_counter()
        response = await call_next(request)
        duration_ms = round((_time.perf_counter() - t0) * 1000, 1)
        path = request.url.path
        entry = {
            "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "method": request.method,
            "path": path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        }
        # Categorize for poster table
        if "/search-products" in path or "/ucp/search" in path:
            entry["category"] = "feature_search"
        elif "/get-product" in path or "/ucp/product" in path:
            entry["category"] = "get_product"
        elif "/complex-query" in path or "best-value" in path or "similar" in path:
            entry["category"] = "complex_query"
        elif "inventory" in path or "fetch-cart" in path:
            entry["category"] = "inventory"
        elif "/api/action/chat" in path:
            entry["category"] = "agent_chat"
        else:
            entry["category"] = "other"
        logger.info(
            "[LATENCY] %s %s -> %d  %.1fms  [%s]",
            entry["method"], path, entry["status"], duration_ms, entry["category"]
        )
        try:
            with open(_LATENCY_LOG_PATH, "a") as f:
                f.write(_json.dumps(entry) + "\n")
        except Exception:
            pass
        return response

app.add_middleware(LatencyLoggingMiddleware)


class ProtocolHeaderMiddleware(BaseHTTPMiddleware):
    """Add X-Commerce-Protocol header so agents can detect which protocol handled the request."""
    async def dispatch(self, request: StarletteRequest, call_next) -> StarletteResponse:
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/acp/"):
            response.headers["X-Commerce-Protocol"] = "acp"
        elif path.startswith("/ucp/"):
            response.headers["X-Commerce-Protocol"] = "ucp"
        return response


app.add_middleware(ProtocolHeaderMiddleware)

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
# Chat Endpoint (IDSS-compatible)
#

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Main conversation endpoint - compatible with IDSS /chat API.

    Provides multi-domain support:
    - Vehicles: MCP vehicle_search tool (Supabase)
    - Laptops/Books: MCP interview system

    Request format matches IDSS /chat for frontend compatibility.

    Config overrides (optional):
    - k: Number of interview questions (0 = skip interview)
    - method: 'embedding_similarity' or 'coverage_risk' (vehicles only)
    - n_rows: Number of result rows
    - n_per_row: Items per row
    """
    return await process_chat(request)


@app.get("/session/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    """Get current session state."""
    return get_session_state(session_id)


@app.post("/session/reset", response_model=ResetResponse)
async def reset_session_endpoint(request: ResetRequest):
    """Reset session or create new one."""
    return reset_session(request.session_id)


@app.delete("/session/{session_id}")
async def delete_session_endpoint(session_id: str):
    """Delete a session."""
    return delete_session(session_id)


@app.get("/sessions")
async def list_sessions_endpoint():
    """List all active sessions."""
    return list_sessions()


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


@app.post("/api/get-product", response_model=GetProductResponse, response_model_exclude_none=True)
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
    Search for vehicles using MCP vehicle_search (Supabase).
    
    Returns: List of vehicles formatted as products
    """
    return await search_products_idss(request)


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


@app.post("/ucp/get-product", response_model=UCPGetProductResponse)
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
    logger.info("mcp_ucp_request: path=/ucp/add_to_cart payload=%s", request.model_dump())
    response = await ucp_add_to_cart(request, db)
    logger.info("mcp_ucp_response: path=/ucp/add_to_cart status=%s", response.status)
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
    logger.info("mcp_ucp_request: path=/ucp/checkout payload=%s", request.model_dump())
    response = await ucp_checkout(request, db)
    logger.info("mcp_ucp_response: path=/ucp/checkout status=%s order_id=%s", response.status, getattr(response, "order_id", None))
    # Log event for research replay
    log_ucp_event(db, "ucp_checkout", "/ucp/checkout", request, response, session_id=request.parameters.cart_id)
    return response


@app.post("/ucp/get_cart", response_model=UCPGetCartResponse)
async def ucp_get_cart_endpoint(request: UCPGetCartRequest, db: Session = Depends(get_db)):
    """UCP get_cart. Agent sends this to fetch cart (e.g. cart_id = user_id)."""
    logger.info("mcp_ucp_request: path=/ucp/get_cart payload=%s", request.model_dump())
    response = await ucp_get_cart(request, db)
    logger.info("mcp_ucp_response: path=/ucp/get_cart status=%s item_count=%s", response.status, response.item_count)
    log_ucp_event(db, "ucp_get_cart", "/ucp/get_cart", request, response, session_id=request.parameters.cart_id)
    return response


@app.post("/ucp/remove_from_cart", response_model=UCPRemoveFromCartResponse)
async def ucp_remove_from_cart_endpoint(request: UCPRemoveFromCartRequest, db: Session = Depends(get_db)):
    """UCP remove_from_cart."""
    logger.info("mcp_ucp_request: path=/ucp/remove_from_cart payload=%s", request.model_dump())
    response = await ucp_remove_from_cart(request, db)
    logger.info("mcp_ucp_response: path=/ucp/remove_from_cart status=%s", response.status)
    log_ucp_event(db, "ucp_remove_from_cart", "/ucp/remove_from_cart", request, response, session_id=request.parameters.cart_id)
    return response


@app.post("/ucp/update_cart", response_model=UCPUpdateCartResponse)
async def ucp_update_cart_endpoint(request: UCPUpdateCartRequest, db: Session = Depends(get_db)):
    """UCP update_cart (set quantity; 0 = remove)."""
    logger.info("mcp_ucp_request: path=/ucp/update_cart payload=%s", request.model_dump())
    response = await ucp_update_cart(request, db)
    logger.info("mcp_ucp_response: path=/ucp/update_cart status=%s", response.status)
    log_ucp_event(db, "ucp_update_cart", "/ucp/update_cart", request, response, session_id=request.parameters.get_cart_id())
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
    # Log event for research replay (non-critical, don't let it crash checkout)
    try:
        log_ucp_event(db, "ucp_create_checkout_session", "/ucp/checkout-sessions", request, response, session_id=response.id)
    except Exception:
        pass
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


# ─────────────────────────────────────────────────────────────────────────────
# ACP (OpenAI/Stripe Agentic Commerce Protocol) Endpoints
# Mirrors the UCP checkout session block above.
# External ACP agent flow: ChatGPT → POST /acp/checkout-sessions → merchant
# Reference: agenticcommerce.dev
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/acp/feed.json", tags=["ACP"])
async def acp_feed_json(db: Session = Depends(get_db)):
    """
    ACP Product Feed — JSON format.

    Returns the full product catalog in ACP feed format so OpenAI agents
    can discover and purchase products without a search step.
    """
    items = generate_product_feed(db)
    return {"protocol": "acp", "items": [i.model_dump() for i in items], "count": len(items)}


@app.get("/acp/feed.csv", tags=["ACP"])
async def acp_feed_csv(db: Session = Depends(get_db)):
    """
    ACP Product Feed — CSV format.

    Same data as /acp/feed.json but as text/csv for tools that prefer tabular feeds.
    """
    from fastapi.responses import PlainTextResponse
    items = generate_product_feed(db)
    if not items:
        return PlainTextResponse("id,title,price_dollars,availability,brand,category\n", media_type="text/csv")
    header = "id,title,price_dollars,currency,availability,inventory,brand,category,rating,product_url"
    rows = [header]
    for it in items:
        def _q(v: object) -> str:
            s = str(v) if v is not None else ""
            return f'"{s.replace(chr(34), chr(34)+chr(34))}"'
        rows.append(",".join([
            _q(it.id), _q(it.title), str(it.price_dollars), it.currency,
            it.availability, str(it.inventory),
            _q(it.brand or ""), _q(it.category or ""),
            str(it.rating or ""), _q(it.product_url),
        ]))
    return PlainTextResponse("\n".join(rows), media_type="text/csv")


@app.post("/acp/checkout-sessions", response_model=ACPCheckoutSession, tags=["ACP"])
async def acp_create_session_endpoint(
    request: ACPCreateSessionRequest,
    db: Session = Depends(get_db),
):
    """
    ACP — Create checkout session (status: pending).

    Per ACP spec: POST /checkout-sessions
    Trigger: agent has selected products and is ready to purchase.
    """
    session = await _acp_create(request, db)
    try:
        log_ucp_event(db, "acp_create_session", "/acp/checkout-sessions", request, session)
    except Exception:
        pass
    return session


@app.get("/acp/checkout-sessions/{session_id}", response_model=ACPCheckoutSession, tags=["ACP"])
async def acp_get_session_endpoint(session_id: str):
    """ACP — Get checkout session by ID."""
    session = await _acp_get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="ACP session not found")
    return session


@app.put("/acp/checkout-sessions/{session_id}", response_model=ACPCheckoutSession, tags=["ACP"])
async def acp_update_session_endpoint(
    session_id: str,
    request: ACPUpdateSessionRequest,
    db: Session = Depends(get_db),
):
    """
    ACP — Update checkout session (buyer info, shipping address, shipping method).

    Recalculates shipping + tax on every update.
    """
    session = await _acp_update(session_id, request, db)
    if not session:
        raise HTTPException(status_code=404, detail="ACP session not found")
    return session


@app.post("/acp/checkout-sessions/{session_id}/complete", response_model=ACPCheckoutSession, tags=["ACP"])
async def acp_complete_session_endpoint(
    session_id: str,
    request: ACPCompleteSessionRequest,
    db: Session = Depends(get_db),
):
    """
    ACP — Complete checkout session (place order).

    In production: Stripe delegated payment token is charged here.
    Returns completed session with order_id on success.
    """
    session = await _acp_complete(session_id, request, db)
    if not session:
        raise HTTPException(status_code=404, detail="ACP session not found")
    try:
        log_ucp_event(db, "acp_complete_session", f"/acp/checkout-sessions/{session_id}/complete", request, session)
    except Exception:
        pass
    return session


@app.post("/acp/checkout-sessions/{session_id}/cancel", response_model=ACPCheckoutSession, tags=["ACP"])
async def acp_cancel_session_endpoint(session_id: str):
    """ACP — Cancel checkout session."""
    session = await _acp_cancel(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="ACP session not found")
    return session


@app.post("/acp/webhooks/orders", tags=["ACP"])
async def acp_order_webhook(payload: Dict[str, Any], request: Request):
    """
    ACP — Order event webhook from OpenAI/Stripe.

    Returns 200 immediately to avoid retries. Events are logged for async processing.
    Signature validation (X-ACP-Signature header) should be added before production use.
    """
    logger.info("acp_webhook_received: %s", payload)
    return {"received": True}


#
# Agent Action Endpoints (Frontend → API → Agent → UCP/ACP → MCP → Supabase)
# Frontend calls these with user_id (signed-in). When Supabase is configured,
# they use the Supabase cart table and products.inventory; otherwise in-memory fallback.
#

# In-memory user cart store: user_id → {product_id → {"qty": int, "product_snapshot": dict}}
# All cart ops go through Agent → UCP → MCP/Supabase (see action_* endpoints below).


class FetchCartRequest(BaseModel):
    user_id: str


class CartItemOut(BaseModel):
    id: str
    product_id: str
    product_snapshot: Dict[str, Any]
    quantity: int


class FetchCartResponse(BaseModel):
    status: str
    cart_id: Optional[str] = None
    items: Optional[List[CartItemOut]] = None
    item_count: Optional[int] = None
    error: Optional[str] = None


class AddToCartActionRequest(BaseModel):
    user_id: str
    product_id: str
    quantity: int = 1
    product_snapshot: Dict[str, Any] = {}


class RemoveFromCartRequest(BaseModel):
    user_id: str
    product_id: str


class CheckoutActionRequest(BaseModel):
    user_id: str
    items: Optional[List[Dict[str, Any]]] = None
    product_type: Optional[str] = None
    shipping_method: Optional[str] = "standard"


@app.post("/api/action/fetch-cart")
async def action_fetch_cart(request: FetchCartRequest, db: Session = Depends(get_db)):
    """Return the current cart for a user. Agent sends UCP get_cart to MCP over HTTP."""
    logger.info("action_api_request: action=%s user_id=%s", "fetch_cart", request.user_id)
    ucp_req = build_ucp_get_cart(request.user_id)
    logger.info("ucp_request: ucp_action=%s payload=%s", "get_cart", ucp_req.model_dump())
    response = await ucp_get_cart(ucp_req, db)
    logger.info("ucp_response: ucp_action=%s status=%s item_count=%s", "get_cart", response.status, response.item_count)
    log_ucp_event(db, "ucp_get_cart", "/api/action/fetch-cart", ucp_req, response, session_id=request.user_id)
    if response.status != "success":
        return FetchCartResponse(status="error", error=response.error or "Failed to get cart")
    items = [
        CartItemOut(id=it.id, product_id=it.product_id, product_snapshot=it.product_snapshot, quantity=it.quantity)
        for it in response.items
    ]
    return FetchCartResponse(status="success", cart_id=response.cart_id, items=items, item_count=response.item_count or len(items))


@app.post("/api/action/add-to-cart")
async def action_add_to_cart(request: AddToCartActionRequest, db: Session = Depends(get_db)):
    """Add (or increment) a product in the user's cart. Uses ACP or UCP based on COMMERCE_PROTOCOL."""
    logger.info("action_api_request: action=%s user_id=%s product_id=%s quantity=%s protocol=%s",
                "add_to_cart", request.user_id, request.product_id, request.quantity,
                "acp" if is_acp() else "ucp")

    if is_acp():
        # ACP path: create a checkout session with this product
        snapshot = request.product_snapshot or {}
        acp_req = build_acp_create_session(
            product_id=request.product_id,
            title=snapshot.get("name") or snapshot.get("title") or "Product",
            price_dollars=float(snapshot.get("price", 0)),
            quantity=request.quantity,
            image_url=snapshot.get("image_url") or snapshot.get("imageurl"),
        )
        session = await _acp_create(acp_req, db)
        if session.error:
            return {"status": "error", "error": session.error}
        return {"status": "success", "acp_session_id": session.id}

    # UCP path (default)
    ucp_req = build_ucp_add_to_cart(request.user_id, request.product_id, request.quantity, request.product_snapshot)
    logger.info("ucp_request: ucp_action=%s payload=%s", "add_to_cart", ucp_req.model_dump())
    response = await ucp_add_to_cart(ucp_req, db)
    logger.info("ucp_response: ucp_action=%s status=%s", "add_to_cart", response.status)
    log_ucp_event(db, "ucp_add_to_cart", "/api/action/add-to-cart", ucp_req, response, session_id=request.user_id)
    if response.status != "success":
        return {"status": "error", "error": response.error or "Add to cart failed"}
    return {"status": "success"}


@app.post("/api/action/remove-from-cart")
async def action_remove_from_cart(request: RemoveFromCartRequest, db: Session = Depends(get_db)):
    """Remove a product from the user's cart. Agent sends UCP remove_from_cart to MCP over HTTP."""
    logger.info("action_api_request: action=%s user_id=%s product_id=%s", "remove_from_cart", request.user_id, request.product_id)
    ucp_req = build_ucp_remove_from_cart(request.user_id, request.product_id)
    logger.info("ucp_request: ucp_action=%s payload=%s", "remove_from_cart", ucp_req.model_dump())
    response = await ucp_remove_from_cart(ucp_req, db)
    logger.info("ucp_response: ucp_action=%s status=%s", "remove_from_cart", response.status)
    log_ucp_event(db, "ucp_remove_from_cart", "/api/action/remove-from-cart", ucp_req, response, session_id=request.user_id)
    if response.status != "success":
        return {"status": "error", "error": response.error or "Remove failed"}
    return {"status": "success"}


@app.post("/api/action/checkout")
async def action_checkout(request: CheckoutActionRequest, db: Session = Depends(get_db)):
    """Checkout the user's cart. Uses ACP or UCP based on COMMERCE_PROTOCOL."""
    logger.info("action_api_request: action=%s user_id=%s shipping_method=%s protocol=%s",
                "checkout", request.user_id, request.shipping_method, "acp" if is_acp() else "ucp")

    if is_acp():
        # ACP path: complete the most-recent ACP session for this user.
        # In a real deployment, the session_id would be stored per-user.
        # Here we look for a pending session and complete it.
        from app.acp_endpoints import _acp_sessions
        pending = [s for s in _acp_sessions.values() if s.status in ("pending", "confirmed")]
        if not pending:
            return {"status": "error", "error": "No active ACP checkout session"}
        # Complete the most-recently created session
        session_id = pending[-1].id
        acp_complete_req = build_acp_complete_session(payment_method="card")
        session = await _acp_complete(session_id, acp_complete_req, db)
        if not session or session.status != "completed":
            return {"status": "error", "error": (session.error if session else "Checkout failed")}
        return {"status": "success", "order_id": session.order_id, "acp_session_id": session.id}

    # UCP path (default)
    ucp_req = build_ucp_checkout(request.user_id, shipping_method=request.shipping_method or "standard")
    logger.info("ucp_request: ucp_action=%s payload=%s", "checkout", ucp_req.model_dump())
    response = await ucp_checkout(ucp_req, db)
    logger.info("ucp_response: ucp_action=%s status=%s order_id=%s", "checkout", response.status, getattr(response, "order_id", None))
    log_ucp_event(db, "ucp_checkout", "/api/action/checkout", ucp_req, response, session_id=request.user_id)
    if response.status != "success":
        return {"status": "error", "error": response.error or "Checkout failed", "details": getattr(response, "details", None)}
    return {"status": "success", "order_id": response.order_id}


# ─── Shipping / Tax calculation ──────────────────────────────────────────────

class ShippingCalcItem(BaseModel):
    product_id: str
    unit_price_cents: Optional[int] = None
    unit_price_dollars: Optional[float] = None
    quantity: int = 1
    weight_lbs: Optional[float] = None

class ShippingCalcRequest(BaseModel):
    state_code: str                          # 2-letter US state code
    shipping_method: str = "standard"        # standard | express | overnight
    items: List[ShippingCalcItem]

@app.post("/api/calculate-shipping")
async def api_calculate_shipping(request: ShippingCalcRequest):
    """
    Calculate shipping cost + state sales tax for a list of items.

    Returns subtotal, shipping, tax, and total — all in cents.
    Accepts all 50 US states + DC. Tax rates are combined state+avg-local (2025).
    """
    try:
        items_data = [item.model_dump(exclude_none=False) for item in request.items]
        result = calculate_shipping(
            state_code=request.state_code,
            shipping_method=request.shipping_method,
            items=items_data,
        )
        return {
            "status": "ok",
            "state_code": result.state_code,
            "state_name": result.state_name,
            "tax_rate_pct": result.tax_rate_pct,
            "shipping_method": result.shipping_method,
            "subtotal_cents": result.subtotal_cents,
            "shipping_cents": result.shipping_cents,
            "tax_cents": result.tax_cents,
            "total_cents": result.total_cents,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/states")
async def api_get_states():
    """Return all US states + DC with their tax rates, for populating UI dropdowns."""
    return {"status": "ok", "states": ALL_STATES}


# ─── Coupon / Discount code validation ───────────────────────────────────────

class CouponValidateRequest(BaseModel):
    code: str
    subtotal_cents: int
    shipping_cents: int = 0

@app.post("/api/validate-coupon")
async def api_validate_coupon(request: CouponValidateRequest):
    """
    Validate a coupon code and return the discount amount.

    Demo codes: STANFORD10, SAVE20, FREESHIP, WELCOME5, TECH50
    """
    result = validate_coupon(
        code=request.code,
        subtotal_cents=request.subtotal_cents,
        shipping_cents=request.shipping_cents,
    )
    return {
        "status": "ok",
        "valid": result.valid,
        "code": result.code,
        "description": result.description,
        "discount_type": result.discount_type,
        "discount_value": result.discount_value,
        "discount_cents": result.discount_cents,
        "error": result.error,
    }


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
