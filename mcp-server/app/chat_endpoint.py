"""
Chat endpoint for MCP server - compatible with IDSS /chat API.

Provides a unified /chat endpoint that:
1. Accepts the same request format as IDSS /chat
2. Routes to IDSS backend for vehicles
3. Uses MCP interview system for laptops/books
4. Returns the same response format as IDSS /chat
"""

import uuid
import time
import httpx
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from app.interview.session_manager import (
    get_session_manager,
    STAGE_INTERVIEW,
    STAGE_RECOMMENDATIONS,
)
from app.structured_logger import StructuredLogger

logger = StructuredLogger("chat_endpoint")

# IDSS backend URL
IDSS_BACKEND_URL = "http://localhost:8000"


# ============================================================================
# Request/Response Models (compatible with IDSS)
# ============================================================================

class ChatRequest(BaseModel):
    """Request model for chat endpoint - matches IDSS format."""
    message: str = Field(description="User's message")
    session_id: Optional[str] = Field(default=None, description="Session ID (auto-generated if not provided)")

    # Per-request config overrides
    k: Optional[int] = Field(default=None, description="Number of interview questions (0 = skip interview)")
    method: Optional[str] = Field(default=None, description="Recommendation method: 'embedding_similarity' or 'coverage_risk'")
    n_rows: Optional[int] = Field(default=None, description="Number of result rows")
    n_per_row: Optional[int] = Field(default=None, description="Items per row")


class ChatResponse(BaseModel):
    """Response model for chat endpoint - matches IDSS format."""
    response_type: str = Field(description="'question' or 'recommendations'")
    message: str = Field(description="AI response message")
    session_id: str = Field(description="Session ID")

    # Question-specific fields
    quick_replies: Optional[List[str]] = Field(default=None, description="Quick reply options for questions")

    # Recommendation-specific fields
    recommendations: Optional[List[List[Dict[str, Any]]]] = Field(default=None, description="2D grid of products [rows][items]")
    bucket_labels: Optional[List[str]] = Field(default=None, description="Labels for each row/bucket")
    diversification_dimension: Optional[str] = Field(default=None, description="Dimension used for diversification")

    # State info
    filters: Dict[str, Any] = Field(default_factory=dict, description="Extracted explicit filters")
    preferences: Dict[str, Any] = Field(default_factory=dict, description="Extracted implicit preferences")
    question_count: int = Field(default=0, description="Number of questions asked so far")

    # Domain info (MCP extension)
    domain: Optional[str] = Field(default=None, description="Active domain (vehicles, laptops, books)")


# ============================================================================
# Chat Endpoint Logic
# ============================================================================

# Global agent cache (in-memory for demo, Redis for prod)
# Map: session_id -> UniversalAgent
active_agents: Dict[str, Any] = {}

from app.universal_agent import UniversalAgent

async def process_chat(request: ChatRequest) -> ChatResponse:
    """
    Process a chat message using the Universal Agent.
    
    Unified pipeline for all domains.
    """
    # Get or create session ID
    session_id = request.session_id or str(uuid.uuid4())
    
    # Retrieve or create agent
    if session_id not in active_agents:
        # Initialize with empty history (or load from DB)
        active_agents[session_id] = UniversalAgent(session_id=session_id)
        logger.info("agent_created", f"Created new UniversalAgent for {session_id}")
    
    agent = active_agents[session_id]
    
    # Process message
    agent_response = agent.process_message(request.message)
    
    # Handle "recommendations_ready" signal (Handoff)
    if agent_response.get("response_type") == "recommendations_ready":
        # This is where we call the actual Search Tools
        # For now, we route to the existing handlers based on domain
        # In the future, the Agent should call the search tool itself
        
        domain = agent_response["domain"]
        filters = agent_response["filters"]
        
        if domain == "vehicles":
            # Use IDSS vehicle search tool with embedding-based ranking
            from app.tools.vehicle_search import search_vehicles, VehicleSearchRequest

            # Build search request from agent filters
            search_request = VehicleSearchRequest(
                filters=filters,
                preferences=filters.get("preferences", {}),
                method="embedding_similarity",  # Use sentence embeddings
                n_rows=3,
                n_per_row=3,
                limit=500
            )

            # Execute search using IDSS recommendation system
            search_result = search_vehicles(search_request)

            # Build response message
            total = sum(len(row) for row in search_result.recommendations)
            if total > 0:
                message = f"Based on your preferences, here are {total} vehicle recommendations:"
            else:
                message = "I couldn't find vehicles matching your criteria. Try adjusting your preferences."

            return ChatResponse(
                response_type="recommendations",
                message=message,
                session_id=session_id,
                domain="vehicles",
                recommendations=search_result.recommendations,
                bucket_labels=search_result.bucket_labels,
                diversification_dimension=search_result.diversification_dimension,
                filters=filters
            )
            
        elif domain in ["laptops", "books"]:
             # Similar logic for e-commerce
             # Reuse _search_ecommerce_products logic
             from app.main import search_products # Import issues potential?
             # Let's use the local helper _search_ecommerce_products defined in this file (chat_endpoint.py)
             
             # Need category mapping
             category = "Electronics" if domain == "laptops" else "Books"
             
             # Call helper
             recs, labels = await _search_ecommerce_products(
                 filters, 
                 category,
                 n_rows=3,
                 n_per_row=3
             )
             
             return ChatResponse(
                 response_type="recommendations",
                 message=f"Here are top {domain} recommendations:",
                 session_id=session_id,
                 domain=domain,
                 recommendations=recs,
                 bucket_labels=labels,
                 filters=filters
             )

    # Standard Question Response
    return ChatResponse(
        response_type=agent_response.get("response_type", "question"),
        message=agent_response.get("message", "Internal Error"),
        session_id=session_id,
        quick_replies=agent_response.get("quick_replies", []),
        filters=agent_response.get("filters", {}),
        domain=agent_response.get("domain", "none")
    )


# ============================================================================
# Session Management
# ============================================================================

class SessionResponse(BaseModel):
    """Response model for session state endpoint."""
    session_id: str
    filters: Dict[str, Any]
    preferences: Dict[str, Any]
    question_count: int
    conversation_history: List[Dict[str, str]]
    domain: Optional[str] = None


class ResetRequest(BaseModel):
    """Request model for session reset."""
    session_id: Optional[str] = None


class ResetResponse(BaseModel):
    """Response model for session reset."""
    session_id: str
    status: str


def get_session_state(session_id: str) -> SessionResponse:
    """Get current session state."""
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)

    return SessionResponse(
        session_id=session_id,
        filters=session.explicit_filters,
        preferences={},
        question_count=session.question_count,
        conversation_history=session.conversation_history,
        domain=session.active_domain,
    )


def reset_session(session_id: Optional[str] = None) -> ResetResponse:
    """Reset session or create new one."""
    import uuid

    session_manager = get_session_manager()

    if session_id:
        session_manager.reset_session(session_id)
        new_session_id = session_id
    else:
        new_session_id = str(uuid.uuid4())

    # Ensure the session exists
    session_manager.get_session(new_session_id)

    logger.info("session_reset", f"Session reset/created: {new_session_id}", {})

    return ResetResponse(
        session_id=new_session_id,
        status="reset"
    )


def delete_session(session_id: str) -> Dict[str, str]:
    """Delete a session."""
    session_manager = get_session_manager()
    session_manager.reset_session(session_id)

    return {"status": "deleted", "session_id": session_id}


def list_sessions() -> Dict[str, Any]:
    """List all active sessions."""
    session_manager = get_session_manager()

    return {
        "active_sessions": len(session_manager.sessions),
        "session_ids": list(session_manager.sessions.keys())
    }


# ============================================================================
# Product Search Helper
# ============================================================================

def _format_product_as_vehicle(product_dict: Dict[str, Any], category: str) -> Dict[str, Any]:
    """
    Format an e-commerce product to match IDSS vehicle format for frontend compatibility.

    The frontend was built for vehicles, so we map product fields to the vehicle structure:
    - @id: unique identifier
    - vehicle: contains product specs (make=brand, model=name, etc.)
    - retailListing: contains pricing and image info
    """
    product_id = product_dict.get("product_id", "")
    name = product_dict.get("name", "")
    description = product_dict.get("description", "")
    brand = product_dict.get("brand", "")
    price = product_dict.get("price", 0)
    price_cents = product_dict.get("price_cents", 0)
    image_url = product_dict.get("image_url")

    # Determine product type for display
    if category == "Electronics":
        product_type = "Laptop"
        body_style = "Electronics"
    elif category == "Books":
        product_type = "Book"
        body_style = "Books"
    else:
        product_type = category
        body_style = category

    return {
        # Top-level identifiers (matching IDSS vehicle format)
        "@id": product_id,
        "vin": product_id,  # Use product_id as "vin" for compatibility
        "online": True,

        # Vehicle object with product specs (mapped to vehicle fields)
        "vehicle": {
            "vin": product_id,
            "year": 2024,  # Current year for products
            "make": brand,  # Brand maps to make
            "model": name,  # Product name maps to model
            "trim": "",  # No trim for products
            "price": int(price),  # Price in dollars (frontend expects dollars)
            "mileage": 0,
            "bodyStyle": body_style,
            "drivetrain": product_type,  # Use as product type indicator
            "engine": "",
            "fuel": "",
            "transmission": "",
            "doors": 0,
            "seats": 0,
            "exteriorColor": "",
            "interiorColor": "",
            "build_city_mpg": 0,
            "build_highway_mpg": 0,
            "norm_body_type": body_style,
            "norm_fuel_type": "",
            "norm_is_used": 0,
            # Additional product-specific fields
            "description": description,
            "category": category,
        },

        # Retail listing with pricing and image
        "retailListing": {
            "price": int(price),  # Price in dollars (frontend expects dollars)
            "miles": 0,
            "dealer": brand,  # Use brand as dealer
            "city": "",
            "state": "",
            "zip": "",
            "vdp": "",  # Vehicle detail page URL
            "carfaxUrl": "",
            "primaryImage": image_url or "",
            "photoCount": 1 if image_url else 0,
            "used": False,
            "cpo": False,
        },

        # Keep original product data for reference
        "_product": product_dict,
    }


async def _search_ecommerce_products(
    filters: Dict[str, Any],
    category: str,
    n_rows: int = 3,
    n_per_row: int = 3
) -> tuple[List[List[Dict[str, Any]]], List[str]]:
    """
    Search e-commerce products from PostgreSQL database.

    Returns products formatted to match IDSS vehicle structure for frontend compatibility.
    """
    from app.database import SessionLocal
    from app.models import Product, Price, Inventory
    from sqlalchemy import and_

    logger.info("search_ecommerce_start", f"Searching products", {
        "category": category,
        "filters": filters,
        "n_rows": n_rows,
        "n_per_row": n_per_row,
    })

    db = SessionLocal()
    try:
        # Build query
        query = db.query(Product).filter(Product.category == category)

        # Debug: count products in category
        category_count = db.query(Product).filter(Product.category == category).count()
        logger.info("search_category_count", f"Products in category {category}: {category_count}", {})

        # Apply filters
        if filters.get("brand"):
            query = query.filter(Product.brand == filters["brand"])

        # Join with prices for price filtering
        query = query.join(Price, Product.product_id == Price.product_id, isouter=True)

        if filters.get("price_min_cents"):
            query = query.filter(Price.price_cents >= filters["price_min_cents"])
        if filters.get("price_max_cents"):
            query = query.filter(Price.price_cents <= filters["price_max_cents"])

        # Order by price and limit
        query = query.order_by(Price.price_cents.asc())
        products = query.limit(n_rows * n_per_row * 2).all()  # Get extra for bucketing

        logger.info("search_query_result", f"Query returned {len(products)} products", {
            "brand_filter": filters.get("brand"),
            "price_min": filters.get("price_min_cents"),
            "price_max": filters.get("price_max_cents"),
        })

        if not products:
            logger.info("search_no_products", "No products found, returning empty", {})
            return [], []

        # Convert to product dicts (intermediate format)
        product_dicts = []
        for product in products:
            # Get price
            price = db.query(Price).filter(Price.product_id == product.product_id).first()
            price_cents = price.price_cents if price else 0

            product_dict = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "category": product.category,
                "brand": product.brand,
                "price": price_cents / 100,  # Convert to dollars for display
                "price_cents": price_cents,
                "image_url": getattr(product, 'image_url', None),
            }
            product_dicts.append(product_dict)

        # Bucket into rows by price
        if len(product_dicts) == 0:
            return [], []

        # Sort by price
        product_dicts.sort(key=lambda x: x.get("price_cents", 0))

        # Create buckets
        total = len(product_dicts)
        bucket_size = max(1, total // n_rows)

        buckets = []
        bucket_labels = []

        for i in range(n_rows):
            start = i * bucket_size
            end = start + n_per_row
            if i == n_rows - 1:
                end = min(start + n_per_row, total)

            bucket_products = product_dicts[start:end]
            if bucket_products:
                # Convert products to vehicle format for frontend compatibility
                formatted_bucket = [
                    _format_product_as_vehicle(p, category)
                    for p in bucket_products
                ]
                buckets.append(formatted_bucket)

                # Generate label based on price range
                min_price = min(p.get("price", 0) for p in bucket_products)
                max_price = max(p.get("price", 0) for p in bucket_products)
                if i == 0:
                    bucket_labels.append(f"Budget-Friendly (${min_price:.0f}-${max_price:.0f})")
                elif i == n_rows - 1:
                    bucket_labels.append(f"Premium (${min_price:.0f}-${max_price:.0f})")
                else:
                    bucket_labels.append(f"Mid-Range (${min_price:.0f}-${max_price:.0f})")

        return buckets, bucket_labels

    except Exception as e:
        logger.error("chat_search_error", f"Error searching products: {e}", {})
        return [], []
    finally:
        db.close()
