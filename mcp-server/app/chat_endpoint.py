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

from app.conversation_controller import (
    detect_domain,
    is_domain_switch,
    is_greeting_or_ambiguous,
    Domain,
)
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

async def process_chat(request: ChatRequest) -> ChatResponse:
    """
    Process a chat message with multi-domain support.

    Routes to:
    - IDSS backend (port 8000) for vehicles
    - MCP interview system for laptops/books
    """
    start_time = time.time()

    # Get or create session ID
    session_id = request.session_id or str(uuid.uuid4())
    session_manager = get_session_manager()

    # Get current session state
    session = session_manager.get_session(session_id)
    active_domain_before = session.active_domain

    # Detect domain from message
    detected_domain, route_reason = detect_domain(
        request.message,
        active_domain_before,
        None  # No category filter from request
    )

    logger.info("chat_routing", f"Domain detection: {detected_domain.value}", {
        "message": request.message[:100],
        "detected_domain": detected_domain.value,
        "active_domain_before": active_domain_before,
        "route_reason": route_reason,
    })

    # Handle domain switch - reset session
    if is_domain_switch(active_domain_before, detected_domain):
        session_manager.reset_session(session_id)
        session = session_manager.get_session(session_id)
        logger.info("chat_domain_switch", f"Domain switch: {active_domain_before} -> {detected_domain.value}", {
            "session_id": session_id,
        })

    # Update active domain
    if detected_domain != Domain.NONE:
        session_manager.set_active_domain(session_id, detected_domain.value)

    # Handle greeting/ambiguous - ask what category
    if is_greeting_or_ambiguous(request.message) and detected_domain == Domain.NONE:
        return ChatResponse(
            response_type="question",
            message="Hi! What are you looking for today?",
            session_id=session_id,
            quick_replies=["Cars", "Laptops", "Books"],
            filters={},
            preferences={},
            question_count=0,
            domain="none",
        )

    # Route based on domain
    if detected_domain == Domain.VEHICLES:
        return await _handle_vehicles(request, session_id)
    elif detected_domain in (Domain.LAPTOPS, Domain.BOOKS):
        return await _handle_ecommerce(request, session_id, detected_domain)
    else:
        # Default: try to continue with active domain or ask
        if active_domain_before == "vehicles":
            return await _handle_vehicles(request, session_id)
        elif active_domain_before in ("laptops", "books"):
            domain = Domain.LAPTOPS if active_domain_before == "laptops" else Domain.BOOKS
            return await _handle_ecommerce(request, session_id, domain)
        else:
            # No domain context - ask
            return ChatResponse(
                response_type="question",
                message="What are you looking for?",
                session_id=session_id,
                quick_replies=["Cars", "Laptops", "Books"],
                filters={},
                preferences={},
                question_count=0,
                domain="none",
            )


async def _handle_vehicles(request: ChatRequest, session_id: str) -> ChatResponse:
    """Forward vehicle queries to IDSS backend."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            idss_request = {
                "message": request.message,
                "session_id": session_id,
            }

            # Pass through optional parameters
            if request.k is not None:
                idss_request["k"] = request.k
            if request.method is not None:
                idss_request["method"] = request.method
            if request.n_rows is not None:
                idss_request["n_rows"] = request.n_rows
            if request.n_per_row is not None:
                idss_request["n_per_row"] = request.n_per_row

            logger.info("chat_idss_request", f"Forwarding to IDSS backend", {
                "session_id": session_id,
                "message": request.message[:100],
            })

            response = await client.post(
                f"{IDSS_BACKEND_URL}/chat",
                json=idss_request
            )
            response.raise_for_status()
            idss_data = response.json()

            # Return IDSS response with domain info added
            return ChatResponse(
                response_type=idss_data.get("response_type", "question"),
                message=idss_data.get("message", ""),
                session_id=idss_data.get("session_id", session_id),
                quick_replies=idss_data.get("quick_replies"),
                recommendations=idss_data.get("recommendations"),
                bucket_labels=idss_data.get("bucket_labels"),
                diversification_dimension=idss_data.get("diversification_dimension"),
                filters=idss_data.get("filters", {}),
                preferences=idss_data.get("preferences", {}),
                question_count=idss_data.get("question_count", 0),
                domain="vehicles",
            )

    except httpx.HTTPStatusError as e:
        logger.error("chat_idss_error", f"IDSS backend error: {e}", {
            "status_code": e.response.status_code,
        })
        return ChatResponse(
            response_type="question",
            message=f"Sorry, I couldn't connect to the vehicle system. Please try again.",
            session_id=session_id,
            quick_replies=["Try again", "Look for laptops instead", "Look for books instead"],
            filters={},
            preferences={},
            question_count=0,
            domain="vehicles",
        )
    except httpx.RequestError as e:
        logger.error("chat_idss_network_error", f"Network error: {e}", {})
        return ChatResponse(
            response_type="question",
            message="Sorry, I couldn't connect to the vehicle system. Is the IDSS backend running on port 8000?",
            session_id=session_id,
            quick_replies=["Try again", "Look for laptops instead"],
            filters={},
            preferences={},
            question_count=0,
            domain="vehicles",
        )


async def _handle_ecommerce(
    request: ChatRequest,
    session_id: str,
    domain: Domain
) -> ChatResponse:
    """Handle laptops/books using MCP interview system."""
    from app.query_specificity import is_specific_query, should_ask_followup, generate_followup_question
    from app.query_normalizer import normalize_query, enhance_query_for_search
    from app.query_parser import enhance_search_request
    from sqlalchemy.orm import Session
    from app.database import SessionLocal

    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)

    # Determine product type
    product_type = "laptop" if domain == Domain.LAPTOPS else "book"
    category = "Electronics" if domain == Domain.LAPTOPS else "Books"

    # Set product type in session
    if not session.product_type:
        session_manager.set_product_type(session_id, product_type)

    # Add user message to history
    session_manager.add_message(session_id, "user", request.message)

    # Parse query and extract filters
    normalized_query, _ = enhance_query_for_search(request.message)
    cleaned_query, enhanced_filters = enhance_search_request(normalized_query, {})

    filters = {"category": category}
    if enhanced_filters:
        filters.update(enhanced_filters)

    # Update session filters
    session_manager.update_filters(session_id, filters)

    # Check if query is specific enough
    is_specific, extracted_info = is_specific_query(cleaned_query, filters)

    logger.info("chat_ecommerce_extracted", f"Query analysis", {
        "cleaned_query": cleaned_query,
        "is_specific": is_specific,
        "extracted_info": extracted_info,
        "filters_so_far": filters,
    })

    # Apply extracted info to filters
    if extracted_info.get("brand"):
        # Map brand to proper case (database stores specific casing)
        brand_lower = extracted_info["brand"].lower()
        brand_map = {
            "apple": "Apple",
            "mac": "Apple",
            "macbook": "Apple",
            "dell": "Dell",
            "hp": "HP",
            "lenovo": "Lenovo",
            "asus": "ASUS",
            "acer": "Acer",
            "microsoft": "Microsoft",
            "samsung": "Samsung",
            "msi": "MSI",
            "razer": "Razer",
            "google": "Google",
        }
        brand = brand_map.get(brand_lower, extracted_info["brand"].title())
        filters["brand"] = brand
        session_manager.update_filters(session_id, {"brand": brand})
    if extracted_info.get("price_range"):
        price_range = extracted_info["price_range"]
        if "min" in price_range:
            filters["price_min_cents"] = price_range["min"] * 100
        if "max" in price_range:
            filters["price_max_cents"] = price_range["max"] * 100
        session_manager.update_filters(session_id, filters)

    logger.info("chat_ecommerce_final_filters", f"Final filters before search", {
        "filters": filters,
        "session_filters": session.explicit_filters,
    })

    # Determine max questions based on k parameter
    max_questions = request.k if request.k is not None else 3

    # If k=0, skip interview entirely
    if max_questions == 0:
        is_specific = True

    # Check if we should ask more questions
    if not is_specific and session_manager.should_ask_question(session_id, max_questions):
        should_ask, missing_info = should_ask_followup(cleaned_query, session.explicit_filters)

        if should_ask and missing_info:
            # Try LLM-based question generation
            try:
                from app.interview.question_generator import generate_question
                question_response = generate_question(
                    product_type=product_type,
                    conversation_history=session.conversation_history,
                    explicit_filters=session.explicit_filters,
                    questions_asked=session.questions_asked
                )
                question = question_response.question
                quick_replies = question_response.quick_replies
                topic = question_response.topic
            except (ImportError, ModuleNotFoundError, Exception) as e:
                logger.info("chat_fallback_questions", f"Using rule-based questions: {e}", {})
                question, quick_replies = generate_followup_question(product_type, missing_info, filters)
                topic = missing_info[0] if missing_info else "general"

            # Record question
            session_manager.add_question_asked(session_id, topic)
            session_manager.add_message(session_id, "assistant", question)

            return ChatResponse(
                response_type="question",
                message=question,
                session_id=session_id,
                quick_replies=quick_replies,
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=domain.value,
            )

    # Ready for recommendations - search products
    session_manager.set_stage(session_id, STAGE_RECOMMENDATIONS)

    # Search products from database
    recommendations, bucket_labels = await _search_ecommerce_products(
        session.explicit_filters,
        category,
        request.n_rows or 3,
        request.n_per_row or 3
    )

    if recommendations and any(len(row) > 0 for row in recommendations):
        # Generate intro message
        total_count = sum(len(row) for row in recommendations)
        if domain == Domain.LAPTOPS:
            message = f"Based on your preferences, here are {total_count} laptop recommendations:"
        else:
            message = f"Based on your preferences, here are {total_count} book recommendations:"

        session_manager.add_message(session_id, "assistant", message)

        return ChatResponse(
            response_type="recommendations",
            message=message,
            session_id=session_id,
            recommendations=recommendations,
            bucket_labels=bucket_labels,
            diversification_dimension="price",
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=domain.value,
        )
    else:
        # No results found
        message = "I couldn't find any products matching your criteria. Would you like to try different preferences?"
        session_manager.add_message(session_id, "assistant", message)

        return ChatResponse(
            response_type="question",
            message=message,
            session_id=session_id,
            quick_replies=["Show me all options", "Change my budget", "Different brand"],
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=domain.value,
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
