"""
Chat endpoint for MCP server - compatible with IDSS /chat API.

Provides a unified /chat endpoint that:
1. Accepts the same request format as IDSS /chat
2. Routes to IDSS backend for vehicles
3. Uses MCP interview system for laptops/books
4. Returns the same response format as IDSS /chat
"""

import uuid
import httpx
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from app.interview.session_manager import (
    get_session_manager,
    STAGE_INTERVIEW,
    STAGE_RECOMMENDATIONS,
)
from app.conversation_controller import (
    detect_domain,
    is_domain_switch,
    is_greeting_or_ambiguous,
    Domain,
)
from app.query_specificity import (
    is_specific_query,
    should_ask_followup,
    generate_followup_question,
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

async def process_chat(request: ChatRequest) -> ChatResponse:
    """
    Process a chat message with strict interview flow for laptops/books.

    - Vehicles: routed to IDSS backend (/chat)
    - Laptops/Books: MCP interview flow (query specificity + follow-up questions)
    """
    # Get or create session ID
    session_id = request.session_id or str(uuid.uuid4())
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)

    detected_domain, route_reason = detect_domain(
        request.message,
        active_domain=session.active_domain,
        filters_category=session.explicit_filters.get("category") if session else None,
    )

    if is_domain_switch(session.active_domain, detected_domain):
        session_manager.reset_session(session_id)
        session = session_manager.get_session(session_id)

    if detected_domain != Domain.NONE:
        session_manager.set_active_domain(session_id, detected_domain.value)

    if detected_domain == Domain.NONE and is_greeting_or_ambiguous(request.message):
        return ChatResponse(
            response_type="question",
            message="What are you looking for today?",
            session_id=session_id,
            quick_replies=["Vehicles", "Laptops", "Books"],
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=None,
        )

    active_domain = detected_domain.value if detected_domain != Domain.NONE else session.active_domain or "vehicles"

    if active_domain in ["laptops", "books"]:
        category = "Electronics" if active_domain == "laptops" else "Books"
        filters = dict(session.explicit_filters)
        filters["category"] = category

        is_specific, extracted_info = is_specific_query(request.message, filters)
        product_type = "book" if active_domain == "books" else str(extracted_info.get("product_type") or "laptop")
        filters["product_type"] = product_type
        session_manager.set_product_type(session_id, product_type)

        # Respect explicit k=0 to skip interview questions
        if request.k == 0:
            is_specific = True

        # Apply extracted filters
        if extracted_info.get("gpu_vendor"):
            filters["gpu_vendor"] = extracted_info["gpu_vendor"]
        if extracted_info.get("cpu_vendor"):
            filters["cpu_vendor"] = extracted_info["cpu_vendor"]
        if extracted_info.get("brand"):
            filters["brand"] = extracted_info["brand"]
        if extracted_info.get("color"):
            filters["color"] = extracted_info["color"]
        attributes = extracted_info.get("attributes")
        if isinstance(attributes, list) and attributes:
            attribute_map = {
                "gaming": "Gaming",
                "work": "Work",
                "school": "School",
                "creative": "Creative",
                "entertainment": "Entertainment",
                "education": "Education",
            }
            first_attr = str(attributes[0]).lower()
            mapped_subcategory = attribute_map.get(first_attr)
            if mapped_subcategory:
                filters["subcategory"] = mapped_subcategory
                filters["use_case"] = mapped_subcategory
        price_range = extracted_info.get("price_range")
        if isinstance(price_range, dict):
            min_price = price_range.get("min")
            max_price = price_range.get("max")
            if isinstance(min_price, (int, float)):
                filters["price_min_cents"] = int(min_price) * (100 if active_domain == "laptops" else 1)
            if isinstance(max_price, (int, float)):
                filters["price_max_cents"] = int(max_price) * (100 if active_domain == "laptops" else 1)
        if extracted_info.get("soft_preferences"):
            filters["_soft_preferences"] = extracted_info["soft_preferences"]

        session_manager.update_filters(session_id, filters)
        session_manager.set_stage(session_id, STAGE_INTERVIEW)

        should_ask, missing_info = should_ask_followup(request.message, filters)
        if not is_specific:
            should_ask = True

        if should_ask:
            question, quick_replies = generate_followup_question(product_type, missing_info, filters)

            session_manager.add_message(session_id, "user", request.message)
            session_manager.add_question_asked(session_id, missing_info[0] if missing_info else "general")
            session_manager.add_message(session_id, "assistant", question)

            session = session_manager.get_session(session_id)
            return ChatResponse(
                response_type="question",
                message=question,
                session_id=session_id,
                quick_replies=quick_replies,
                filters=filters,
                preferences=filters.get("_soft_preferences", {}),
                question_count=session.question_count,
                domain=active_domain,
            )

        session_manager.add_message(session_id, "user", request.message)
        recs, labels = await _search_ecommerce_products(
            filters,
            category,
            n_rows=request.n_rows or 3,
            n_per_row=request.n_per_row or 3,
        )

        session_manager.set_stage(session_id, STAGE_RECOMMENDATIONS)
        return ChatResponse(
            response_type="recommendations",
            message=f"Here are top {active_domain} recommendations:",
            session_id=session_id,
            domain=active_domain,
            recommendations=recs,
            bucket_labels=labels,
            filters=filters,
            preferences=filters.get("_soft_preferences", {}),
            question_count=session.question_count,
        )

    # Vehicles route to IDSS backend
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            idss_request: Dict[str, Any] = {
                "message": request.message,
                "session_id": session_id,
            }

            # Forward optional config overrides
            if request.k is not None:
                idss_request["k"] = request.k
            if request.method:
                idss_request["method"] = request.method
            if request.n_rows is not None:
                idss_request["n_rows"] = request.n_rows
            if request.n_per_row is not None:
                idss_request["n_per_row"] = request.n_per_row

            logger.info("routing_to_idss", "Routing chat request to IDSS backend", {
                "session_id": session_id,
                "message": request.message[:100],
                "domain": active_domain,
                "route_reason": route_reason,
            })

            idss_response = await client.post(
                f"{IDSS_BACKEND_URL}/chat",
                json=idss_request,
            )
            idss_response.raise_for_status()
            idss_data = idss_response.json()

            response_type = idss_data.get("response_type", "question")
            domain = idss_data.get("domain") or "vehicles"

            return ChatResponse(
                response_type=response_type,
                message=idss_data.get("message", ""),
                session_id=idss_data.get("session_id", session_id),
                quick_replies=idss_data.get("quick_replies"),
                recommendations=idss_data.get("recommendations"),
                bucket_labels=idss_data.get("bucket_labels"),
                diversification_dimension=idss_data.get("diversification_dimension"),
                filters=idss_data.get("filters", {}),
                preferences=idss_data.get("preferences", {}),
                question_count=idss_data.get("question_count", 0),
                domain=domain,
            )
    except Exception as e:
        logger.error("idss_routing_failed", f"Failed to route to IDSS backend: {e}", {
            "error": str(e)
        })
        return ChatResponse(
            response_type="question",
            message=f"I'm having trouble connecting to the recommendation system. Please try again. Error: {str(e)[:100]}",
            session_id=session_id,
            quick_replies=["Try again"],
            domain=active_domain,
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
    n_per_row: int = 3,
    idss_preferences: Optional[Dict[str, Any]] = None
) -> tuple[List[List[Dict[str, Any]]], List[str]]:
    """
    Search e-commerce products from PostgreSQL database.

    Returns products formatted to match IDSS vehicle structure for frontend compatibility.
    """
    from app.database import SessionLocal
    from app.models import Product, Price

    logger.info("search_ecommerce_start", "Searching products", {
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
        if filters.get("subcategory"):
            query = query.filter(Product.subcategory == filters["subcategory"])
        if filters.get("product_type"):
            query = query.filter(Product.product_type == filters["product_type"])
        if filters.get("color"):
            query = query.filter(Product.color == filters["color"])
        if filters.get("gpu_vendor"):
            query = query.filter(Product.gpu_vendor == filters["gpu_vendor"])

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
                "image_url": getattr(product, "image_url", None),
            }
            product_dicts.append(product_dict)

        # Bucket into rows by price
        if len(product_dicts) == 0:
            return [], []

        # Sort by price
        product_dicts.sort(key=lambda x: float(x.get("price_cents", 0) or 0))

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
                min_price = min(float(p.get("price", 0) or 0) for p in bucket_products)
                max_price = max(float(p.get("price", 0) or 0) for p in bucket_products)
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
