"""
MCP E-commerce Tool-Call Endpoints.

All endpoints follow the standard response envelope pattern.
All execution endpoints (AddToCart, Checkout) accept IDs only, never names.
"""

import json
import os
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_, and_

from app.models import Product, Price, Inventory, Cart, CartItem, Order
from app.schemas import (
    ResponseStatus, ConstraintDetail, RequestTrace, VersionInfo,
    SearchProductsRequest, SearchProductsResponse, SearchResultsData, ProductSummary,
    GetProductRequest, GetProductResponse, ProductDetail,
    AddToCartRequest, AddToCartResponse, CartData, CartItemData,
    CheckoutRequest, CheckoutResponse, OrderData, ShippingInfo
)
from app.cache import cache_client
from app.metrics import record_request_metrics
from app.structured_logger import log_request, log_response, StructuredLogger
from app.vector_search import get_vector_store
from app.event_logger import log_event
from app.kg_service import get_kg_service

logger = StructuredLogger("endpoints", log_level="INFO")

def log_mcp_event(
    db: Session,
    request_id: str,
    tool_name: str,
    endpoint_path: str,
    request_data: Any,
    response: Any
) -> None:
    """
    Helper function to log MCP events for research replay.
    Wraps event logging with error handling.
    """
    try:
        log_event(
            db=db,
            request_id=request_id,
            tool_name=tool_name,
            endpoint_path=endpoint_path,
            request_data=request_data.model_dump() if hasattr(request_data, 'model_dump') else request_data,
            response_status=response.status,
            response_data=response.model_dump() if hasattr(response, 'model_dump') else response,
            trace=response.trace.model_dump() if hasattr(response.trace, 'model_dump') else response.trace,
            version=response.version.model_dump() if response.version and hasattr(response.version, 'model_dump') else None
        )
    except Exception as e:
        # Don't fail the request if event logging fails
        logger.warning("event_log_failed", f"Failed to log event: {e}", {"error": str(e)})



# Current catalog version - increment when catalog changes
# In production, this would come from database or config
CATALOG_VERSION = "1.0.0"


def apply_field_projection(
    product_detail: ProductDetail,
    fields: Optional[List[str]]
) -> ProductDetail:
    """
    Apply field projection to ProductDetail.
    
    If fields is None, returns full details.
    If fields is specified, returns only requested fields.
    
    Args:
        product_detail: Full product detail object
        fields: List of field names to include, or None for all
    
    Returns:
        ProductDetail with only requested fields (others set to None/default)
    """
    if not fields:
        return product_detail
    
    # Start with a minimal dict with required fields
    projected = {
        "product_id": product_detail.product_id,
        "name": product_detail.name if "name" in fields else None,
        "description": product_detail.description if "description" in fields else None,
        "category": product_detail.category if "category" in fields else None,
        "brand": product_detail.brand if "brand" in fields else None,
        "price_cents": product_detail.price_cents if "price_cents" in fields or "price" in fields else 0,
        "currency": product_detail.currency if "currency" in fields else "USD",
        "available_qty": product_detail.available_qty if "available_qty" in fields or "qty" in fields else 0,
        "source": getattr(product_detail, "source", None) if "source" in fields else None,
        "color": getattr(product_detail, "color", None) if "color" in fields else None,
        "scraped_from_url": getattr(product_detail, "scraped_from_url", None) if "scraped_from_url" in fields else None,
        "reviews": product_detail.reviews if "reviews" in fields else None,
        "created_at": product_detail.created_at,
        "updated_at": product_detail.updated_at,
        "product_type": product_detail.product_type if "product_type" in fields else None,
        "metadata": product_detail.metadata if "metadata" in fields else None,
        "provenance": product_detail.provenance if "provenance" in fields else None,
    }
    
    return ProductDetail(**projected)


def create_trace(
    request_id: str,
    cache_hit: bool,
    timings: dict,
    sources: List[str],
    metadata: Optional[dict] = None
) -> RequestTrace:
    """
    Helper to create standardized request trace objects.
    """
    return RequestTrace(
        request_id=request_id,
        cache_hit=cache_hit,
        timings_ms=timings,
        sources=sources,
        metadata=metadata
    )


def create_version_info() -> VersionInfo:
    """
    Helper to create version information.
    """
    return VersionInfo(
        catalog_version=CATALOG_VERSION,
        updated_at=datetime.utcnow()
    )


# 
# SearchProducts - Discovery Tool
# 

async def search_products(
    request: SearchProductsRequest,
    db: Session
) -> SearchProductsResponse:
    """
    Search for products in the MCP e-commerce catalog.

    When category + session are present, this endpoint can also drive interview flow:
    it may return FOLLOWUP_QUESTION_REQUIRED with domain/tool/question_id so the client
    routes quick replies (e.g. "Under $500") to the correct domain. Core behavior is
    still data: search by query + filters; session and questions are optional and
    client-driven via session_id and constraint.details.
    """
    request_id = str(uuid.uuid4())
    start_time = time.time()

    # Log raw incoming request body (as received) for debugging state leak / schema drop / normalization
    _raw_body = {
        "request_id": request_id,
        "query": request.query,
        "filters": dict(request.filters) if request.filters else None,
        "limit": request.limit,
        "cursor": getattr(request, "cursor", None),
        "session_id": getattr(request, "session_id", None),
    }
    logger.info("search_products_request_body", "Raw request body", _raw_body)
    if os.getenv("LOG_RAW_REQUESTS") == "1":
        print(f"[MCP RAW REQUEST BODY] {json.dumps(_raw_body)}", flush=True)

    # Initialize timings early (needed for validation errors)
    timings = {}

    # Validate query - reject invalid/nonsensical queries
    search_query = request.query.strip() if request.query else ""
    parse_start = time.time()

    # Normalize query: correct typos and expand synonyms
    from app.query_normalizer import normalize_query, enhance_query_for_search
    normalized_query, expanded_terms = enhance_query_for_search(search_query)

    # Parse complex queries (e.g., "family suv fuel efficient")
    from app.query_parser import enhance_search_request
    cleaned_query, enhanced_filters = enhance_search_request(normalized_query, request.filters or {})
    timings["parse_ms"] = round((time.time() - parse_start) * 1000, 1)
    
    # Clone filters so we never mutate request (per review: avoid bleed in middleware/async)
    filters = dict(request.filters) if request.filters else {}
    if enhanced_filters:
        filters.update(enhanced_filters)

    # --- Deterministic router (conversation_controller) per bigerrorjan29.txt ---
    from app.conversation_controller import (
        detect_domain,
        is_domain_switch,
        is_short_domain_intent,
        is_greeting_or_ambiguous,
        Domain,
    )
    from app.interview.session_manager import get_session_manager, STAGE_INTERVIEW

    active_domain_before = None
    if request.session_id:
        try:
            sess = get_session_manager().get_session(request.session_id)
            active_domain_before = sess.active_domain
        except Exception:
            pass

    detected_domain, route_reason = detect_domain(
        cleaned_query or search_query,
        active_domain_before,
        filters.get("category"),
    )

    # Domain switch: reset session so old interview state doesn't bleed
    if request.session_id and is_domain_switch(active_domain_before, detected_domain):
        get_session_manager().reset_session(request.session_id)
        logger.info("domain_switch_reset", f"Domain switch {active_domain_before} -> {detected_domain.value}, session reset", {
            "session_id": request.session_id,
            "active_domain_before": active_domain_before,
            "detected_domain": detected_domain.value,
            "route_reason": route_reason,
        })
        active_domain_before = None

    # Greeting or ambiguous: ask "What category?" instead of "2-3 characters" error
    if is_greeting_or_ambiguous(cleaned_query or search_query) and detected_domain == Domain.NONE:
        timings["total"] = (time.time() - start_time) * 1000
        logger.info("routing_decision", "Greeting/ambiguous -> ask category", {
            "input": (cleaned_query or search_query)[:80],
            "detected_domain": detected_domain.value,
            "route_reason": route_reason,
        })
        return SearchProductsResponse(
            status=ResponseStatus.INVALID,
            data=SearchResultsData(products=[], total_count=0, next_cursor=None),
            constraints=[
                ConstraintDetail(
                    code="FOLLOWUP_QUESTION_REQUIRED",
                    message="What are you looking for?",
                    details={
                        "question": "What are you looking for?",
                        "quick_replies": ["Laptops", "Books", "Vehicles"],
                        "response_type": "question",
                        "question_id": "category",
                        "domain": "none",
                        "tool": "mcp_ecommerce",
                    },
                    allowed_fields=None,
                    suggested_actions=["Laptops", "Books", "Vehicles"],
                )
            ],
            trace=create_trace(request_id, False, timings, ["conversation_controller"]),
            version=create_version_info(),
        )

    # Ensure category + active_domain for short domain intents (e.g. "books" -> Books Q1)
    if is_short_domain_intent(cleaned_query or search_query):
        if detected_domain == Domain.BOOKS:
            filters["category"] = "Books"
        elif detected_domain == Domain.LAPTOPS:
            filters["category"] = "Electronics"
            filters["_product_type_hint"] = "laptop"

    # Log routing decision (per bigerrorjan29.txt)
    active_domain_after = detected_domain.value if detected_domain != Domain.NONE else active_domain_before
    logger.info("routing_decision", "Router", {
        "input": (cleaned_query or search_query)[:80],
        "detected_domain": detected_domain.value,
        "active_domain_before": active_domain_before,
        "active_domain_after": active_domain_after,
        "route_reason": route_reason,
    })
    if request.session_id:
        try:
            sm = get_session_manager()
            sess = sm.get_session(request.session_id)
            if detected_domain != Domain.NONE:
                sm.set_active_domain(request.session_id, detected_domain.value)
            logger.info("session_snapshot", "Session state", {
                "session_id": request.session_id,
                "question_index": sess.question_index,
                "question_count": sess.question_count,
                "filters": list(sess.explicit_filters.keys()),
                "stage": sess.stage,
            })
        except Exception:
            pass

    # Check if we have category filter (user intent was clear - e.g., "Show me laptops" → category=Electronics)
    has_category_filter = "category" in filters
    
    # ALWAYS check query specificity, even with category filter
    # Generic queries like "computer" or "novel" should still ask follow-up questions
    # Category filter helps narrow results, but doesn't make generic queries specific
    from app.query_specificity import is_specific_query, should_ask_followup, generate_followup_question
    
    # Check if this is a laptop/electronics query that should ALWAYS use interview system
    is_laptop_or_electronics_query = (
        (filters.get("category") == "Electronics") or
        cleaned_query.lower().strip() in ["laptop", "laptops", "computer", "computers", "electronics"]
    )
    
    is_specific, extracted_info = is_specific_query(cleaned_query, filters)

    # Fix 4: If query has ≥2 constraints (brand, gpu/cpu vendor, color, price, use-case), search directly — skip interview
    constraint_count = sum([
        1 if extracted_info.get("brand") else 0,
        1 if extracted_info.get("gpu_vendor") or extracted_info.get("cpu_vendor") else 0,
        1 if extracted_info.get("color") else 0,
        1 if extracted_info.get("price_range") else 0,
        1 if (extracted_info.get("attributes") and len(extracted_info["attributes"]) > 0) else 0,
    ])
    if constraint_count >= 2:
        is_specific = True
        logger.info("multi_constraint_specific", f"Query has {constraint_count} constraints, searching directly", {"extracted_info": extracted_info})

    # CRITICAL: For generic laptop/electronics queries, force interview ONLY if query is NOT already specific
    # A query like "laptops" alone is NOT specific enough - we need use_case, brand, budget
    # BUT a query like "gaming PC with NVIDIA GPU under $2000" IS specific enough - return results immediately
    # Use effective_session_id so we can create one server-side when absent (first turn "laptops")
    effective_session_id = getattr(request, "session_id", None)
    if is_laptop_or_electronics_query and not is_specific and not effective_session_id:
        effective_session_id = str(uuid.uuid4())
    if is_laptop_or_electronics_query and effective_session_id and not is_specific:
        # Check if we have enough information (use_case, brand, budget)
        has_use_case = bool(filters.get("use_case") or filters.get("subcategory"))
        has_brand = bool(filters.get("brand"))
        has_budget = bool(filters.get("price_min_cents") or filters.get("price_max_cents"))
        
        # Also check extracted_info from current query (for complex queries like "gaming PC with NVIDIA under $2000")
        if not has_brand and (extracted_info.get("brand") or extracted_info.get("gpu_vendor") or extracted_info.get("cpu_vendor")):
            has_brand = True
        if not has_budget and extracted_info.get("price_range"):
            has_budget = True
        if not has_use_case and extracted_info.get("attributes"):
            has_use_case = True  # Attributes like "gaming" count as use_case
        
        # If we don't have all three, force interview (but only if query is NOT already specific)
        if not (has_use_case and has_brand and has_budget):
            is_specific = False  # Force interview
            logger.info("forcing_interview", f"Forcing interview for laptop/electronics query: use_case={has_use_case}, brand={has_brand}, budget={has_budget}", {
                "use_case": has_use_case,
                "brand": has_brand,
                "budget": has_budget,
                "extracted_info": extracted_info
            })
        else:
            # Query has enough info (brand + price + use_case/attribute) - mark as specific
            is_specific = True
            logger.info("complex_query_specific", f"Complex query recognized as specific: {cleaned_query}", {
                "extracted_info": extracted_info,
                "has_use_case": has_use_case,
                "has_brand": has_brand,
                "has_budget": has_budget
            })
    
    # Apply extracted filters from specificity detection
    # Component vendors (NVIDIA/AMD/Intel) go to gpu_vendor/cpu_vendor, NOT brand — so backend filters name/description
    if extracted_info.get("gpu_vendor"):
        filters["gpu_vendor"] = extracted_info["gpu_vendor"]
    if extracted_info.get("cpu_vendor"):
        filters["cpu_vendor"] = extracted_info["cpu_vendor"]
    if extracted_info.get("brand"):
        # Map lowercase brand to proper case (e.g., "apple" → "Apple") — only device/OEM brands
        brand_lower = extracted_info["brand"].lower()
        brand_map = {
            "apple": "Apple",
            "mac": "Apple",  # "pink mac laptop" → Apple (per bigerrorjan29)
            "dell": "Dell",
            "hp": "HP",
            "lenovo": "Lenovo",
            "asus": "ASUS",
            "microsoft": "Microsoft",
            "samsung": "Samsung",
            "acer": "Acer",
        }
        filters["brand"] = brand_map.get(brand_lower, extracted_info["brand"].title())
    
    if extracted_info.get("color"):
        # Store color in filters for database filtering
        filters["color"] = extracted_info["color"]
        # Also store in metadata for reference
        if "_metadata" not in filters:
            filters["_metadata"] = {}
        filters["_metadata"]["color"] = extracted_info["color"]
    
    if extracted_info.get("price_range"):
        price_range = extracted_info["price_range"]
        if "min" in price_range:
            filters["price_min_cents"] = price_range["min"] * 100
        if "max" in price_range:
            filters["price_max_cents"] = price_range["max"] * 100

    # Apply extracted attributes (e.g., "gaming" → subcategory="Gaming")
    if extracted_info.get("attributes"):
        attributes = extracted_info["attributes"]
        # Map attributes to subcategory/use_case
        # "gaming" → subcategory="Gaming"
        # "work" → subcategory="Work"
        # "school" → subcategory="School"
        # "creative" → subcategory="Creative"
        attribute_map = {
            "gaming": "Gaming",
            "work": "Work",
            "school": "School",
            "creative": "Creative",
            "entertainment": "Entertainment",
            "education": "Education",
        }
        # Use first attribute (most relevant)
        if attributes:
            first_attr = attributes[0].lower()
            mapped_subcategory = attribute_map.get(first_attr)
            if mapped_subcategory:
                filters["subcategory"] = mapped_subcategory
                filters["use_case"] = mapped_subcategory  # Also set use_case for compatibility
    
    # Set product type hint for desktop/PC queries
    if extracted_info.get("product_type") == "desktop":
        filters["_product_type_hint"] = "desktop"
        if "category" not in filters:
            filters["category"] = "Electronics"

    # Normalize price filters: values 500-5000 that are round hundreds were likely sent as dollars (e.g. 1000 = $1000)
    # BUT for Books, frontend already sends cents (1500, 3000, 5000 = $15, $30, $50) — do NOT multiply
    is_books = filters.get("category") == "Books"
    for key in ("price_min_cents", "price_max_cents"):
        v = filters.get(key)
        if v is not None and isinstance(v, (int, float)):
            v = int(v)
            if is_books:
                filters[key] = v  # Books: already in cents ($15=1500, $30=3000, $50=5000)
            elif 500 <= v <= 5000 and v % 100 == 0:
                filters[key] = v * 100  # Laptops/etc: treat as dollars -> cents (1000 -> 100000)
            else:
                filters[key] = v

    # If query is NOT specific enough, return a follow-up question instead of searching
    if not is_specific:
        should_ask, missing_info = should_ask_followup(cleaned_query, filters)
        
        if should_ask:
            product_type = extracted_info.get("product_type")
            
            if filters.get("category") == "Books" and not product_type:
                product_type = "book"
                extracted_info["product_type"] = "book"
            
            is_laptop_or_electronics = (
                product_type in ["laptop", "electronics"] or
                filters.get("category") == "Electronics"
            )
            
            is_book_query = (
                product_type == "book" or
                filters.get("category") == "Books"
            )
            
            if is_laptop_or_electronics and effective_session_id:
                # Use LLM-based interview when available; else rule-based (no openai required)
                from app.interview.session_manager import get_session_manager

                session_manager = get_session_manager()
                session = session_manager.get_session(effective_session_id)
                if not session.active_domain:
                    session_manager.set_active_domain(effective_session_id, "laptops")

                # Set product type if not set
                if not session.product_type:
                    session_manager.set_product_type(effective_session_id, product_type or "electronics")
                
                # Update filters in session
                if filters:
                    session_manager.update_filters(effective_session_id, filters)
                
                # Add user message to conversation history
                session_manager.add_message(effective_session_id, "user", cleaned_query or "Show me products")
                
                # Check if we should ask another question
                if session_manager.should_ask_question(effective_session_id, max_questions=3):
                    # Deterministic: if next missing topic is "price", always ask the price question (use_case → price → brand)
                    next_topic = missing_info[0] if missing_info else None
                    if next_topic == "price":
                        question, quick_replies = generate_followup_question(product_type, missing_info, filters)
                        session_manager.add_question_asked(effective_session_id, "price")
                        session_manager.add_message(effective_session_id, "assistant", question)
                        timings["total"] = (time.time() - start_time) * 1000
                        _domain_fq = "laptops" if (product_type or "") in ["laptop", "electronics"] else "books" if (product_type or "") == "book" else "vehicles"
                        return SearchProductsResponse(
                            status=ResponseStatus.INVALID,
                            data=SearchResultsData(products=[], total_count=0, next_cursor=None),
                            constraints=[
                                ConstraintDetail(
                                    code="FOLLOWUP_QUESTION_REQUIRED",
                                    message=question,
                                    details={
                                        "question": question,
                                        "quick_replies": quick_replies,
                                        "missing_info": missing_info,
                                        "product_type": product_type,
                                        "topic": "price",
                                        "response_type": "question",
                                        "session_id": effective_session_id,
                                        "domain": _domain_fq,
                                        "tool": "mcp_ecommerce" if _domain_fq in ["laptops", "books"] else "idss_vehicle",
                                        "question_id": "price",
                                    },
                                    allowed_fields=None,
                                    suggested_actions=quick_replies
                                )
                            ],
                            trace=create_trace(request_id, False, timings, ["interview_system"]),
                            version=create_version_info()
                        )
                    # Otherwise use LLM for brand/other questions (fall back to rule-based if openai not installed)
                    try:
                        from app.interview.question_generator import generate_question
                        question_response = generate_question(
                            product_type=product_type or "electronics",
                            conversation_history=session.conversation_history,
                            explicit_filters=session.explicit_filters,
                            questions_asked=session.questions_asked
                        )
                        q_msg = question_response.question
                        q_replies = question_response.quick_replies
                        q_topic = question_response.topic
                    except (ImportError, ModuleNotFoundError):
                        logger.info("openai_not_available", "Using rule-based questions (install openai for LLM)", {})
                        question, q_replies = generate_followup_question(product_type, missing_info, filters)
                        q_msg = question
                        q_topic = missing_info[0] if missing_info else "brand"

                    session_manager.add_question_asked(effective_session_id, q_topic)
                    session_manager.add_message(effective_session_id, "assistant", q_msg)
                    timings["total"] = (time.time() - start_time) * 1000
                    _domain_llm = "laptops" if (product_type or "") in ["laptop", "electronics"] else "books" if (product_type or "") == "book" else "vehicles"
                    return SearchProductsResponse(
                        status=ResponseStatus.INVALID,
                        data=SearchResultsData(products=[], total_count=0, next_cursor=None),
                        constraints=[
                            ConstraintDetail(
                                code="FOLLOWUP_QUESTION_REQUIRED",
                                message=q_msg,
                                details={
                                    "question": q_msg,
                                    "quick_replies": q_replies,
                                    "missing_info": missing_info,
                                    "product_type": product_type,
                                    "topic": q_topic,
                                    "response_type": "question",
                                    "session_id": effective_session_id,
                                    "domain": _domain_llm,
                                    "tool": "mcp_ecommerce" if _domain_llm in ["laptops", "books"] else "idss_vehicle",
                                    "question_id": q_topic,
                                },
                                allowed_fields=None,
                                suggested_actions=q_replies
                            )
                        ],
                        trace=create_trace(request_id, False, timings, ["interview_system"]),
                        version=create_version_info()
                    )
                # If we've asked enough questions, proceed to search
                else:
                    logger.info("interview_complete", f"Session {effective_session_id}: Asked enough questions, proceeding to search", {
                        "session_id": effective_session_id
                    })
            else:
                # Use simple follow-up question system (for books or non-laptop/electronics or no session_id)
                # CRITICAL: Ensure product_type is set for books; persist book session and return session_id
                if is_book_query and not product_type:
                    product_type = "book"
                    extracted_info["product_type"] = "book"
                question, quick_replies = generate_followup_question(product_type, missing_info, filters)

                # Books: use session_manager so question_count and filters persist; always return session_id
                out_session_id = request.session_id
                if is_book_query and request.session_id:
                    sm = get_session_manager()
                    sm.set_active_domain(request.session_id, "books")
                    sm.set_product_type(request.session_id, "book")
                    if filters:
                        sm.update_filters(request.session_id, filters)
                    sm.add_message(request.session_id, "user", cleaned_query or "books")
                    sm.add_question_asked(request.session_id, missing_info[0] if missing_info else "genre")

                timings["total"] = (time.time() - start_time) * 1000
                _domain = "books" if is_book_query else "laptops" if (product_type or "") in ["laptop", "electronics"] else "vehicles"
                details = {
                    "question": question,
                    "quick_replies": quick_replies,
                    "missing_info": missing_info,
                    "product_type": product_type,
                    "response_type": "question",
                    "domain": _domain,
                    "tool": "mcp_ecommerce" if _domain in ["laptops", "books"] else "idss_vehicle",
                    "question_id": (missing_info[0] if missing_info else "general"),
                }
                out_session_id = effective_session_id if (is_laptop_or_electronics_query or is_book_query) else request.session_id
                if out_session_id:
                    details["session_id"] = out_session_id
                return SearchProductsResponse(
                    status=ResponseStatus.INVALID,
                    data=SearchResultsData(products=[], total_count=0, next_cursor=None),
                    constraints=[
                        ConstraintDetail(
                            code="FOLLOWUP_QUESTION_REQUIRED",
                            message=question,
                            details=details,
                            allowed_fields=None,
                            suggested_actions=quick_replies
                        )
                    ],
                    trace=create_trace(request_id, False, timings, ["query_specificity"]),
                    version=create_version_info()
                )
    
    # Update session state if we have session_id and filters (from quick replies)
    # This ensures the interview system tracks user responses
    if request.session_id and filters:
        is_laptop_or_electronics = (
            (filters.get("category") == "Electronics") or
            extracted_info.get("product_type") in ["laptop", "electronics"]
        )
        
        if is_laptop_or_electronics:
            from app.interview.session_manager import get_session_manager
            session_manager = get_session_manager()
            
            # Update filters in session (from quick reply answers)
            session_manager.update_filters(request.session_id, filters)
            
            # Add user message if query is provided
            if cleaned_query:
                session_manager.add_message(request.session_id, "user", cleaned_query)
    
    # If color is in filters but the *current* user message did not mention a color (e.g. "mac laptop"),
    # clear it so we don't filter by a carried-over color and don't show "I don't see any Gray laptops"
    _raw_query = (request.query or "").strip().lower()
    _color_terms = (
        "pink", "red", "blue", "black", "white", "silver", "gold", "gray", "grey",
        "midnight", "rose", "starlight", "green", "yellow", "purple", "orange", "blush",
        "space gray", "space grey", "rose gold",
    )
    _current_query_mentions_color = any(
        re.search(r"\b" + re.escape(t) + r"\b", _raw_query) for t in _color_terms
    )
    if not _current_query_mentions_color and filters.get("color"):
        filters.pop("color", None)
        if filters.get("_metadata") and isinstance(filters["_metadata"], dict):
            filters["_metadata"].pop("color", None)
        logger.info("color_cleared", "Cleared color filter — current query does not mention a color", {"query": _raw_query[:80]})

    # Structured logging: log request
    log_request("search_products", request_id, params={"query": search_query, "filters": filters, "limit": request.limit})
    
    # If we have category filters, that means the user's intent was clear
    has_category_filter = "category" in filters
    has_structured_filters = bool(
        any(k in filters for k in ("color", "brand", "product_type", "_product_type_hint"))
    )
    # When we already have structured filters, don't use raw query for keyword/vector (avoids slow ILIKE on "pink mac laptop")
    effective_search_query = (
        "" if (has_category_filter and has_structured_filters) else search_query
    )
    
    # Only validate query length if we don't have category filter and query is not empty
    if search_query and not has_category_filter:
        # Reject queries that are too short (less than 3 characters)
        if len(search_query) < 3:
            timings["total"] = (time.time() - start_time) * 1000
            return SearchProductsResponse(
                status=ResponseStatus.INVALID,
                data=SearchResultsData(products=[], total_count=0, next_cursor=None),
                constraints=[
                    ConstraintDetail(
                        code="INVALID_QUERY",
                        message="Query is too short. Please provide at least 3 characters.",
                        details={"query": search_query, "min_length": 3},
                        allowed_fields=None,
                        suggested_actions=["Provide a more specific search term like 'laptops', 'headphones', or 'books'"]
                    )
                ],
                trace=create_trace(request_id, False, timings, ["validation"]),
                version=create_version_info()
            )
        
        # Reject queries that are just random characters (no meaningful words)
        # Check if query contains at least one word with 3+ characters
        words = search_query.split()
        meaningful_words = [w for w in words if len(w) >= 3]
        # If query is 3-4 characters and has no meaningful words, reject it
        if not meaningful_words and len(search_query) <= 4:
            if "total" not in timings:
                timings["total"] = (time.time() - start_time) * 1000
            return SearchProductsResponse(
                status=ResponseStatus.INVALID,
                data=SearchResultsData(products=[], total_count=0, next_cursor=None),
                constraints=[
                    ConstraintDetail(
                        code="INVALID_QUERY",
                        message="Query is not meaningful. Please provide a valid product search term.",
                        details={"query": search_query},
                        allowed_fields=None,
                        suggested_actions=["Try searching for specific products like 'laptops' or 'books'"]
                    )
                ],
                trace=create_trace(request_id, False, timings, ["validation"]),
                version=create_version_info()
            )
    
    # Track timing breakdown (timings already initialized above)
    sources = ["postgres"]  # Always query postgres for search
    cache_hit = False  # Search doesn't use cache
    
    # Knowledge Graph search (Stage 3A - per week4notes.txt)
    # KG provides candidate IDs, then hydrate from Postgres
    kg_candidate_ids = None
    kg_explanation = {}
    kg_service = get_kg_service()
    
    if kg_service.is_available() and effective_search_query and len(effective_search_query) >= 3:
        try:
            kg_start = time.time()
            # Use normalized query for KG search (includes typo correction)
            kg_query = normalized_query if normalized_query else effective_search_query
            kg_candidate_ids, kg_explanation = kg_service.search_candidates(
                query=kg_query,
                filters=filters,
                limit=request.limit * 2  # Get more candidates for filtering
            )
            timings["kg"] = (time.time() - kg_start) * 1000
            sources.append("neo4j")
            
            if kg_candidate_ids:
                logger.info("kg_search_success", f"KG found {len(kg_candidate_ids)} candidates", {
                    "query": search_query[:100],
                    "normalized_query": normalized_query[:100] if normalized_query else None,
                    "candidates": len(kg_candidate_ids)
                })
        except Exception as e:
            logger.warning("kg_search_failed", f"KG search failed: {e}", {"error": str(e)})
            kg_candidate_ids = None
    
    # STRICT: Apply category filter FIRST to prevent leakage
    # Eager load price_info and inventory_info to avoid N+1 in the results loop
    base_query = (
        db.query(Product)
        .options(
            selectinload(Product.price_info),
            selectinload(Product.inventory_info),
        )
        .join(Price)
        .join(Inventory)
    )
    
    # CRITICAL: Exclude demo/test products from search results
    # Demo stores like "mc-demo.mybigcommerce.com" are not real products
    # Filter out products from demo/test stores, but allow seed products (NULL scraped_from_url)
    base_query = base_query.filter(
        or_(
            Product.scraped_from_url.is_(None),  # Seed products (no scraped_from_url) are OK
            and_(
                Product.scraped_from_url.isnot(None),  # Has scraped_from_url
                ~Product.scraped_from_url.ilike("%demo%"),  # But not demo
                ~Product.scraped_from_url.ilike("%test%"),  # And not test
                ~Product.scraped_from_url.ilike("%example%"),  # And not example
                ~Product.scraped_from_url.ilike("%mc-demo%"),  # And not mc-demo
            )
        )
    )
    
    if has_category_filter:
        base_query = base_query.filter(Product.category == filters["category"])
        logger.info("category_filter_applied", f"Strict category filter: {filters['category']}", {
            "category": filters["category"]
        })

    # HARD FILTERS FIRST: product_type, gpu_vendor (DB columns), price_max
    if filters:
        if "product_type" in filters:
            pt = filters["product_type"]
            types_list = [pt] if isinstance(pt, str) else (pt or [])
            if types_list:
                base_query = base_query.filter(Product.product_type.in_(types_list))
                logger.info("hard_filter_product_type", "product_type in " + str(types_list), {"product_type": types_list})
        if "gpu_vendor" in filters:
            gv = filters["gpu_vendor"]
            vendors_list = [gv] if isinstance(gv, str) else (gv or [])
            if vendors_list:
                # Require column to be set; NULL = unknown = must not pass
                base_query = base_query.filter(
                    Product.gpu_vendor.isnot(None),
                    Product.gpu_vendor.in_([v.strip() for v in vendors_list if v])
                )
                logger.info("hard_filter_gpu_vendor", "gpu_vendor in " + str(vendors_list), {"gpu_vendor": vendors_list})
        if "price_max_cents" in filters:
            pm = filters["price_max_cents"]
            if pm is not None:
                base_query = base_query.filter(Price.price_cents <= int(pm))
                logger.info("hard_filter_price_max", f"price_cents <= {pm}", {"price_max_cents": pm})
        elif "price_max" in filters:
            pm = filters["price_max"]
            if pm is not None:
                base_query = base_query.filter(Price.price_cents <= int(pm) * 100)
                logger.info("hard_filter_price_max", f"price_cents <= {int(pm)*100}", {"price_max": pm})

    # Vector search (if query provided and vector search available)
    # Skip when we have structured filters (rely on filters, not slow vector/keyword)
    vector_product_ids = None
    vector_scores = None
    use_vector_search = bool(effective_search_query and len(effective_search_query) >= 3)
    
    if use_vector_search:
        try:
            vector_start = time.time()
            vector_store = get_vector_store()
            
            # Check if index exists and is ready
            has_index = vector_store._index is not None and len(vector_store._product_ids) > 0
            
            if not has_index:
                # Try to load from disk first
                if vector_store.use_cache:
                    has_index = vector_store._load_index()
            
            if not has_index:
                # Index doesn't exist - skip vector search for this request
                # Use keyword search instead (faster, no delay)
                logger.info("vector_index_not_ready", "Vector index not ready, using keyword search", {
                    "reason": "Index not built yet"
                })
                use_vector_search = False
                vector_product_ids = None
                vector_scores = None
            else:
                # Index exists - use vector search
                # Use normalized query for vector search (includes typo correction)
                vector_query = normalized_query if normalized_query else effective_search_query
                vector_product_ids, vector_scores = vector_store.search(
                    vector_query,
                    k=request.limit * 2  # Get more candidates for filtering
                )
                timings["vector"] = (time.time() - vector_start) * 1000
                sources.append("vector_search")
                
                if vector_product_ids:
                    logger.info("vector_search_success", f"Vector search found {len(vector_product_ids)} candidates", {
                        "candidates": len(vector_product_ids),
                        "query": search_query[:100]
                    })
        except Exception as e:
            # Fall back to keyword search if vector search fails
            logger.warning("vector_search_failed", f"Vector search failed, using keyword: {e}", {"error": str(e)})
            use_vector_search = False
            vector_product_ids = None
            vector_scores = None
    
    # Build query (category already filtered in base_query above)
    # Use db_query to avoid conflict with SQLAlchemy query object
    db_query = base_query
    
    # Priority: KG candidates > Vector search > Keyword search
    # KG provides high-quality candidates, vector search provides semantic matches, keyword is fallback
    candidate_ids = None
    
    if kg_candidate_ids:
        # Use KG candidates (highest priority - per week4notes.txt)
        candidate_ids = kg_candidate_ids
        logger.info("using_kg_candidates", f"Using {len(candidate_ids)} KG candidates")
    elif use_vector_search and vector_product_ids:
        # Fallback to vector search if KG not available
        candidate_ids = vector_product_ids
        logger.info("using_vector_candidates", f"Using {len(candidate_ids)} vector search candidates")
    
    # Apply candidate filtering
    if candidate_ids:
        # Filter by candidate IDs (already filtered by category if specified)
        db_query = db_query.filter(Product.product_id.in_(candidate_ids))
    elif effective_search_query and len(effective_search_query) >= 3:
        # Fallback to keyword search (already filtered by category if specified)
        # Use normalized query and expanded terms for better matching
        search_terms = [normalized_query] if normalized_query else [effective_search_query]
        
        # Add expanded terms (limit to first 5 to avoid too many OR conditions)
        if expanded_terms:
            search_terms.extend(expanded_terms[:5])
        
        # Remove duplicates while preserving order
        seen = set()
        unique_terms = []
        for term in search_terms:
            term_lower = term.lower()
            if term_lower not in seen and len(term) >= 2:  # Skip very short terms
                seen.add(term_lower)
                unique_terms.append(term)
        
        # Build search conditions for all terms (original + synonyms)
        search_conditions = []
        for term in unique_terms:
            search_pattern = f"%{term}%"
            search_conditions.extend([
                Product.name.ilike(search_pattern),
                Product.description.ilike(search_pattern),
                Product.category.ilike(search_pattern),
                Product.brand.ilike(search_pattern)
            ])
        
        # If query is basically the category word, don't require keyword match — avoid 0 results
        CATEGORY_ONLY_TERMS = {"laptop", "laptops", "book", "books", "computer", "computers", "pc", "pcs", "desktops", "desktop", "electronics"}
        query_stripped = (normalized_query or effective_search_query or "").strip().lower()
        is_category_only_query = has_category_filter and query_stripped in CATEGORY_ONLY_TERMS

        if search_conditions and not is_category_only_query:
            # With category filter but query not just category word: try to match query
            # No category filter: require query match (with synonyms)
            db_query = db_query.filter(or_(*search_conditions))
        
        # Log synonym expansion for debugging (unique_terms is defined only in this branch)
        if expanded_terms and len(expanded_terms) > 0:
            logger.info("synonym_expansion_applied", f"Expanded '{search_query}' to {len(unique_terms)} terms", {
                "original": search_query,
                "normalized": normalized_query,
                "expanded_terms": expanded_terms[:5],
                "total_search_terms": len(unique_terms)
            })
    
    # CRITICAL: Filter by product type hint if provided (e.g., "Show me laptops" should only return laptops, not iPods)
    # This prevents "Show me laptops" from returning all Electronics (iPods, etc.)
    # BUT: Be lenient - if no products match the strict filter, include all Electronics (for seed products)
    if "_product_type_hint" in filters:
        product_type_hint = filters["_product_type_hint"]
        if product_type_hint == "laptop":
            # Filter for laptops: name contains "laptop", "notebook", "macbook", "chromebook", "thinkpad"
            # This is lenient enough to catch seed products like "ThinkPad X1 Carbon"
            db_query = db_query.filter(
                or_(
                    Product.name.ilike("%laptop%"),
                    Product.name.ilike("%notebook%"),
                    Product.name.ilike("%macbook%"),
                    Product.name.ilike("%chromebook%"),
                    Product.name.ilike("%thinkpad%"),
                    Product.description.ilike("%laptop%"),
                    Product.description.ilike("%notebook%"),
                    Product.description.ilike("%thinkpad%")  # ThinkPad is a laptop
                )
            )
        elif product_type_hint == "desktop":
            # Try strict desktop filter first; if 0 results, relax (don't filter by ptype) so we return category matches
            strict_desktop = db_query.filter(
                or_(
                    Product.name.ilike("%desktop%"),
                    Product.name.ilike("%pc%"),
                    Product.name.ilike("%workstation%"),
                    Product.name.ilike("%tower%"),
                    Product.name.ilike("%gaming pc%"),
                    Product.name.ilike("%gaming computer%"),
                    Product.description.ilike("%desktop%"),
                    Product.description.ilike("%gaming pc%"),
                    Product.description.ilike("%gaming computer%")
                )
            ).filter(
                ~Product.name.ilike("%laptop%"),
                ~Product.description.ilike("%laptop%")
            )
            strict_count = strict_desktop.count()
            if strict_count > 0:
                db_query = strict_desktop
            else:
                logger.info("ptype_hint_relax", "No results with strict _product_type_hint=desktop; relaxing to category-only", {"hint": "desktop"})
    
    # If no search_query but we have category filter, return all products in that category
    # This handles cases like "Show me laptops" → category=Electronics, query=""
    
    # Apply structured filters
    if filters:
        if "category" in filters:
            db_query = db_query.filter(Product.category == filters["category"])

        # gpu_vendor: applied as HARD filter above (Product.gpu_vendor column); skip soft filter here to avoid double-apply
        # cpu_vendor: soft filter on name/description (no cpu_vendor column yet)
        if "cpu_vendor" in filters:
            cpu = (filters["cpu_vendor"] or "").strip().lower()
            if cpu:
                db_query = db_query.filter(
                    or_(
                        Product.name.ilike(f"%{cpu}%"),
                        Product.description.ilike(f"%{cpu}%"),
                    )
                )

        if "brand" in filters:
            brand = filters["brand"]
            # For complex queries with brand (e.g., "gaming PC with NVIDIA"), also search in name/description
            # This allows "NVIDIA" to match products with "NVIDIA" in name even if brand isn't set
            brand_lower = brand.lower()
            # Check if brand is a component brand (NVIDIA, AMD, Intel) - these often appear in descriptions
            component_brands = ["nvidia", "amd", "intel", "geforce", "radeon", "rtx", "gtx"]
            is_component_brand = any(comp in brand_lower for comp in component_brands)
            
            # Check if this is a desktop/gaming PC query - for these, be even more lenient
            is_desktop_query = (
                filters.get("_product_type_hint") == "desktop" or
                (search_query and any(term in search_query.lower() for term in ["gaming pc", "desktop", "pc", "gaming computer"]))
            )
            
            if is_component_brand:
                # For component brands (NVIDIA, AMD, Intel), search in name/description
                # This handles "nvidia type laptops" - find laptops with NVIDIA GPU even if brand isn't set
                # Check if this is a laptop query (not just desktop)
                is_laptop_query = (
                    filters.get("_product_type_hint") == "laptop" or
                    (search_query and any(term in search_query.lower() for term in ["laptop", "notebook", "macbook"]))
                )
                
                if is_desktop_query:
                    # For desktop queries with component brands, prioritize products with brand match
                    # but also include gaming PCs that might have the component in description
                    db_query = db_query.filter(
                        or_(
                            Product.brand == brand,
                            Product.name.ilike(f"%{brand}%"),
                            Product.description.ilike(f"%{brand}%"),
                            # Also include gaming PCs/desktops even without brand match (very lenient for desktop queries)
                            and_(
                                or_(
                                    Product.name.ilike("%gaming%"),
                                    Product.name.ilike("%pc%"),
                                    Product.name.ilike("%desktop%"),
                                    Product.description.ilike("%gaming%"),
                                    Product.description.ilike("%pc%"),
                                    Product.description.ilike("%desktop%")
                                ),
                                ~Product.name.ilike("%laptop%"),
                                ~Product.description.ilike("%laptop%")
                            )
                        )
                    )
                elif is_laptop_query:
                    # For laptop queries with component brands, be lenient - search in name/description
                    # This handles "nvidia type laptops" - find laptops with NVIDIA in name/description
                    db_query = db_query.filter(
                        or_(
                            Product.brand == brand,
                            Product.name.ilike(f"%{brand}%"),
                            Product.description.ilike(f"%{brand}%"),
                            # Also allow laptops even without explicit brand match (very lenient for component brands)
                            and_(
                                or_(
                                    Product.name.ilike("%laptop%"),
                                    Product.name.ilike("%notebook%"),
                                    Product.name.ilike("%macbook%"),
                                    Product.description.ilike("%laptop%"),
                                    Product.description.ilike("%notebook%")
                                ),
                                ~Product.name.ilike("%desktop%"),
                                ~Product.description.ilike("%desktop%")
                            )
                        )
                    )
                else:
                    # For other queries, search in name/description too
                    db_query = db_query.filter(
                        or_(
                            Product.brand == brand,
                            Product.brand.is_(None),
                            Product.brand == "",
                            Product.name.ilike(f"%{brand}%"),
                            Product.description.ilike(f"%{brand}%")
                        )
                    )
            else:
                # When user explicitly asked for a brand (e.g. "dell laptop"), require that brand only.
                # Do NOT include NULL/empty brand — otherwise "dell laptop" would show MacBook (brand=NULL).
                # Lenient (include NULL) was only for "pink mac laptop" when MacBook has no brand set; for
                # explicit brand queries we must be strict so "Dell" returns only Dell.
                db_query = db_query.filter(Product.brand == brand)
        
        # Handle use_case/subcategory filter (from follow-up questions or extracted attributes)
        # Maps to product subcategory column (Gaming, Work, School, Creative for laptops)
        # CRITICAL: Make this filter VERY lenient - seed products don't have subcategory set
        # For seed products (NULL subcategory), always include them regardless of use_case filter
        # This ensures "Show me laptops" → "Gaming" still shows the ThinkPad even though it has no subcategory
        if "subcategory" in filters:
            subcategory = filters["subcategory"]
            # For gaming/desktop queries, also search in name/description for "gaming"
            if subcategory.lower() == "gaming":
                # VERY lenient: match subcategory OR name/description contains "gaming" OR NULL subcategory (seed products)
                db_query = db_query.filter(
                    or_(
                        Product.subcategory == subcategory,
                        Product.subcategory.is_(None),  # Seed products (always include)
                        Product.subcategory == "",
                        Product.name.ilike("%gaming%"),
                        Product.description.ilike("%gaming%")
                    )
                )
            elif subcategory.lower() in ["work", "school", "creative", "entertainment", "education"]:
                # For Work/School/Creative: VERY lenient - include all products with NULL subcategory (seed products)
                # This ensures seed products are always shown when user selects a use case
                # FIX "laptop for video editing": when user asked for Creative/video editing, EXCLUDE explicitly
                # Gaming-only laptops so vector search does not surface ASUS ROG etc. as top results
                db_query = db_query.filter(
                    or_(
                        Product.subcategory == subcategory,
                        Product.subcategory.is_(None),  # Seed products (always include)
                        Product.subcategory == "",
                        # Also search in description for use case keywords
                        Product.description.ilike(f"%{subcategory.lower()}%")
                    )
                )
                if subcategory.lower() == "creative":
                    # Exclude gaming-only laptops when user asked for video editing / creative
                    # So "laptop for video editing" does not surface ASUS ROG / gaming-first devices
                    db_query = db_query.filter(
                        or_(
                            Product.subcategory != "Gaming",
                            Product.subcategory.is_(None),
                            Product.subcategory == ""
                        )
                    )
                    # Exclude products whose name is primarily gaming (ROG, gaming, esports)
                    # so vector search does not rank them for "video editing"
                    db_query = db_query.filter(
                        ~Product.name.ilike("%ROG%"),
                        ~Product.name.ilike("%esports%")
                    )
                    db_query = db_query.filter(~Product.name.ilike("%gaming%"))
            else:
                # For other subcategories, use lenient matching (always include NULL)
                db_query = db_query.filter(
                    or_(
                        Product.subcategory == subcategory,
                        Product.subcategory.is_(None),  # Seed products (always include)
                        Product.subcategory == ""
                    )
                )
        
        if "use_case" in filters:
            # Also check use_case (same as subcategory for laptops)
            use_case = filters["use_case"]
            # VERY lenient filtering - always include products without subcategory (seed products)
            db_query = db_query.filter(
                or_(
                    Product.subcategory == use_case,
                    Product.subcategory.is_(None),  # Seed products (always include)
                    Product.subcategory == "",
                    # Also search in description
                    Product.description.ilike(f"%{use_case.lower()}%")
                )
            )
        
        # Book-specific filters: genre (maps to subcategory), format (name/description)
        if "genre" in filters:
            genre = filters["genre"]
            db_query = db_query.filter(
                or_(
                    Product.subcategory.ilike(f"%{genre}%"),
                    Product.category.ilike(f"%{genre}%"),
                    Product.name.ilike(f"%{genre}%"),
                    Product.description.ilike(f"%{genre}%"),
                    Product.subcategory.is_(None),
                    Product.subcategory == ""
                )
            )
        if "format" in filters:
            fmt = filters["format"]
            db_query = db_query.filter(
                or_(
                    Product.name.ilike(f"%{fmt}%"),
                    Product.description.ilike(f"%{fmt}%")
                )
            )
        
        # Handle color filter (from extracted query or filters)
        # When user asks for a color (e.g. pink mac laptop), require at least one match — do NOT allow null/empty
        if "color" in filters:
            color = (filters["color"] or "").strip()
            if color:
                color_lower = color.lower()
                # Domain-specific color families (hard constraint when user asks explicitly)
                COLOR_FAMILIES = {
                    "pink": ["pink", "rose", "rose gold", "blush"],  # starlight is Apple champagne, not pink
                    "red": ["red", "crimson", "scarlet", "burgundy"],
                    "blue": ["blue", "navy", "sapphire", "midnight"],
                    "black": ["black", "space black", "midnight"],
                    "silver": ["silver", "space gray", "space grey", "grey", "gray", "starlight"],
                    "gold": ["gold", "rose gold", "yellow gold"],
                }
                color_terms = [color_lower]
                for family, terms in COLOR_FAMILIES.items():
                    if family in color_lower or color_lower in [t.lower() for t in terms]:
                        color_terms = [t.lower() for t in terms[:8]]
                        break
                # Require at least one positive match (color, name, or description) — do not allow null/empty
                color_match_conditions = []
                for term in color_terms[:8]:
                    color_match_conditions.append(Product.color.ilike(f"%{term}%"))
                    color_match_conditions.append(Product.name.ilike(f"%{term}%"))
                    color_match_conditions.append(Product.description.ilike(f"%{term}%"))
                db_query = db_query.filter(or_(*color_match_conditions))
        
        # Handle price filters (check both price_min/max and price_min_cents/price_max_cents)
        # For desktop/gaming PC queries with very low prices, be more lenient
        is_desktop_query = (
            filters.get("_product_type_hint") == "desktop" or
            (search_query and any(term in search_query.lower() for term in ["gaming pc", "desktop", "pc", "gaming computer"]))
        )
        
        if "price_min_cents" in filters:
            db_query = db_query.filter(Price.price_cents >= filters["price_min_cents"])
        elif "price_min" in filters:
            price_min_cents = int(filters["price_min"] * 100)
            db_query = db_query.filter(Price.price_cents >= price_min_cents)
        
        if "price_max_cents" in filters:
            price_max = filters["price_max_cents"]
            # For desktop queries with very low price (< $500), be lenient - show products up to 2x the price
            # This handles "gaming PC under $200" - show PCs up to $400 since $200 is unrealistic
            if is_desktop_query and price_max < 50000:  # Less than $500
                logger.info("lenient_price_filter", f"Desktop query with low price ${price_max/100}, applying lenient filter (up to ${price_max*2/100})", {
                    "original_max": price_max,
                    "lenient_max": price_max * 2
                })
                db_query = db_query.filter(Price.price_cents <= price_max * 2)  # Allow up to 2x the requested price
            else:
                db_query = db_query.filter(Price.price_cents <= price_max)
        elif "price_max" in filters:
            price_max_cents = int(filters["price_max"] * 100)
            # Same lenient logic for price_max
            if is_desktop_query and price_max_cents < 50000:
                logger.info("lenient_price_filter", f"Desktop query with low price ${price_max_cents/100}, applying lenient filter (up to ${price_max_cents*2/100})", {
                    "original_max": price_max_cents,
                    "lenient_max": price_max_cents * 2
                })
                db_query = db_query.filter(Price.price_cents <= price_max_cents * 2)
            else:
                db_query = db_query.filter(Price.price_cents <= price_max_cents)
    
    # Get total count for pagination
    total_count = db_query.count()

    # Debug: log final query state (helps diagnose "no results" — pipeline-killers)
    logger.info("final_search_debug", "final query debug", {
        "search_query": (search_query or "")[:100],
        "normalized_query": (normalized_query or "")[:100] if normalized_query else None,
        "expanded_terms": expanded_terms[:5] if expanded_terms else [],
        "filters": dict(filters),
        "has_category_filter": has_category_filter,
        "used_candidate_ids": bool(candidate_ids),
        "candidate_count": len(candidate_ids) if candidate_ids else 0,
        "total_count_before_pagination": total_count,
    })
    
    # Progressive relaxation ladder: strict → drop color → drop brand → category-only
    # CRITICAL: Build each step from scratch; do NOT use base_query (it carries product_type/gpu_vendor)
    relaxed = False
    dropped_filters: List[str] = []
    relaxation_reason: Optional[str] = None

    def _demo_and_category_query(session):
        q = session.query(Product).join(Price).join(Inventory)
        q = q.filter(
            or_(
                Product.scraped_from_url.is_(None),
                and_(
                    Product.scraped_from_url.isnot(None),
                    ~Product.scraped_from_url.ilike("%demo%"),
                    ~Product.scraped_from_url.ilike("%test%"),
                    ~Product.scraped_from_url.ilike("%example%"),
                    ~Product.scraped_from_url.ilike("%mc-demo%"),
                ),
            )
        )
        return q

    def _apply_price(q, filters):
        if not filters:
            return q
        if filters.get("price_min_cents") is not None:
            q = q.filter(Price.price_cents >= filters["price_min_cents"])
        elif filters.get("price_min") is not None:
            q = q.filter(Price.price_cents >= int(filters["price_min"]) * 100)
        if filters.get("price_max_cents") is not None:
            q = q.filter(Price.price_cents <= filters["price_max_cents"])
        elif filters.get("price_max") is not None:
            q = q.filter(Price.price_cents <= int(filters["price_max"]) * 100)
        return q

    def _apply_product_type_hint(q, hint):
        if not hint or hint == "laptop":
            return q.filter(
                or_(
                    Product.name.ilike("%laptop%"),
                    Product.name.ilike("%notebook%"),
                    Product.name.ilike("%macbook%"),
                    Product.name.ilike("%chromebook%"),
                    Product.name.ilike("%thinkpad%"),
                    Product.description.ilike("%laptop%"),
                    Product.description.ilike("%notebook%"),
                    Product.description.ilike("%thinkpad%"),
                )
            )
        if hint == "desktop":
            return q.filter(
                or_(
                    Product.name.ilike("%desktop%"),
                    Product.name.ilike("%pc%"),
                    Product.name.ilike("%workstation%"),
                    Product.name.ilike("%tower%"),
                    Product.name.ilike("%gaming pc%"),
                    Product.name.ilike("%gaming computer%"),
                    Product.description.ilike("%desktop%"),
                    Product.description.ilike("%gaming pc%"),
                    Product.description.ilike("%gaming computer%"),
                )
            ).filter(
                ~Product.name.ilike("%laptop%"),
                ~Product.description.ilike("%laptop%"),
            )
        return q

    # Don't relax when user set a hard constraint (color, gpu_vendor, desktop) — return 0 with tailored message instead
    req_f = filters
    pt = req_f.get("product_type")
    pt_list = [pt] if isinstance(pt, str) else (pt or [])
    has_desktop_pt = any(t in ("desktop_pc", "gaming_laptop") for t in pt_list)
    has_hard_constraint = bool(
        req_f.get("color")
        or req_f.get("gpu_vendor")
        or req_f.get("_product_type_hint") == "desktop"
        or has_desktop_pt
    )
    # Up to 3 DB counts per request: strict (already done) + relax step 1 + relax step 2; then .all().
    relaxation_start = time.time()
    if has_category_filter and total_count == 0 and effective_search_query and len(effective_search_query) >= 3 and not candidate_ids and not has_hard_constraint:
        category_val = req_f["category"]
        logger.info("category_search_no_results", "Trying relaxation (max 1 step)", {
            "category": category_val,
            "had_filters": list(req_f.keys()),
        })

        # Step 1 only: drop color (keep category, price, brand, _product_type_hint)
        q1 = _demo_and_category_query(db).filter(Product.category == category_val)
        q1 = _apply_price(q1, req_f)
        if req_f.get("brand"):
            q1 = q1.filter(Product.brand == req_f["brand"])
        if req_f.get("_product_type_hint"):
            q1 = _apply_product_type_hint(q1, req_f["_product_type_hint"])
        count1 = q1.count()
        if count1 > 0:
            db_query = q1
            total_count = count1
            relaxed = True
            dropped_filters = ["color"] if req_f.get("color") else []
            relaxation_reason = f"No matches with your filters; showing {category_val} (dropped: color)."
            logger.info("relaxation_step", "Step 1 (drop color) found results", {"count": count1, "dropped": dropped_filters})
        else:
            # Step 2 (last): category + price only. No further steps (cap at 2 searches).
            q2 = _demo_and_category_query(db).filter(Product.category == category_val)
            q2 = _apply_price(q2, req_f)
            count2 = q2.count()
            db_query = q2
            total_count = count2
            relaxed = count2 > 0
            dropped_filters = [k for k in req_f.keys() if k not in ("category", "price_min_cents", "price_max_cents", "price_min", "price_max")]
            if relaxed:
                relaxation_reason = f"No matches with your filters; showing {category_val} (dropped: {', '.join(dropped_filters) or 'query'})."
                logger.info("relaxation_step", "Step 2 (category-only) found results", {"count": count2, "dropped": dropped_filters})
            # If count2 == 0 we leave total_count 0 and return NO_MATCHING_PRODUCTS (no more steps)
    timings["relaxation_ms"] = round((time.time() - relaxation_start) * 1000, 1)
    
    # Apply pagination
    offset = 0
    if request.cursor:
        try:
            offset = int(request.cursor)
        except ValueError:
            # Invalid cursor - start from beginning
            offset = 0
    
    db_query = db_query.offset(offset).limit(request.limit)
    
    # Execute query
    db_start = time.time()
    products = db_query.all()
    timings["db"] = (time.time() - db_start) * 1000
    
    # Build response data
    product_summaries = []
    products_with_scores = []
    
    for product in products:
        summary = ProductSummary(
            product_id=product.product_id,
            name=product.name,
            price_cents=product.price_info.price_cents,
            currency=product.price_info.currency,
            category=product.category,
            brand=product.brand,
            available_qty=product.inventory_info.available_qty,
            source=getattr(product, 'source', None),
            color=getattr(product, 'color', None),
            scraped_from_url=getattr(product, 'scraped_from_url', None),
        )
        products_with_scores.append((summary, product))
    
    # Apply ranking: KG candidates first, then vector scores, then price
    if kg_candidate_ids:
        # KG candidates are already ordered by relevance
        # Keep KG order (most relevant first)
        kg_order = {pid: idx for idx, pid in enumerate(kg_candidate_ids)}
        products_with_scores.sort(
            key=lambda x: kg_order.get(x[0].product_id, 9999)
        )
    elif use_vector_search and vector_product_ids and vector_scores and len(vector_scores) > 0:
        # Fallback: Sort by vector score (highest similarity first)
        score_map = dict(zip(vector_product_ids, vector_scores))
        products_with_scores.sort(
            key=lambda x: score_map.get(x[0].product_id, 0.0),
            reverse=True
        )
    
    # Extract summaries (limit to requested limit)
    product_summaries = [summary for summary, _ in products_with_scores[:request.limit]]

    # GUARDRAIL: Category cannot change in results — drop any item that doesn't match requested category
    # Prevents "book flow" from ever returning vehicles, and routing bugs from leaking categories
    requested_category = filters.get("category")
    raw_count = len(product_summaries)
    if requested_category:
        before_guardrail = len(product_summaries)
        product_summaries = [s for s in product_summaries if (s.category or "").strip() == (requested_category or "").strip()]
        dropped = before_guardrail - len(product_summaries)
        if dropped > 0:
            logger.error(
                "category_guardrail_dropped",
                f"Dropped {dropped} results with wrong category (requested={requested_category})",
                {"requested_category": requested_category, "dropped": dropped, "before": before_guardrail, "after": len(product_summaries)}
            )
            total_count = max(0, total_count - dropped)  # Approximate: we only have this page
    post_validation_count = len(product_summaries)
    
    # Calculate next cursor
    next_cursor = None
    if offset + request.limit < total_count:
        next_cursor = str(offset + request.limit)
    
    # Calculate total time
    timings["total"] = (time.time() - start_time) * 1000
    
    # Record metrics
    record_request_metrics("search_products", timings["total"], cache_hit, is_error=False)
    
    # Structured logging: log response
    log_response("search_products", request_id, "OK", timings["total"], cache_hit=cache_hit)
    
    # Build response — contract: trace always includes intent/category and counts
    constraints_out: List[ConstraintDetail] = []
    total_ms = timings.get("total", 0)
    latency_target_ms = int(os.getenv("LATENCY_TARGET_MS", "400"))
    trace_metadata: Dict[str, Any] = {
        "chosen_category": requested_category,
        "raw_count": raw_count,
        "post_validation_count": post_validation_count,
        "total_count": total_count,
        "applied_filters": dict(filters),
        "search_query": (search_query or "")[:100] if search_query else None,
        "latency_target_ms": latency_target_ms,
        "within_latency_target": total_ms <= latency_target_ms,
    }
    if request.session_id:
        trace_metadata["session_id"] = request.session_id  # So frontend preserves session on NO_MATCH etc.
    if relaxed:
        trace_metadata["relaxed"] = True
        trace_metadata["dropped_filters"] = dropped_filters
        trace_metadata["relaxation_reason"] = relaxation_reason
    # Decision audit for agent eval (used_kg, used_vector, used_keyword, relaxation_step)
    trace_metadata["used_kg"] = bool(kg_candidate_ids)
    trace_metadata["used_vector"] = bool(use_vector_search and vector_product_ids)
    trace_metadata["used_keyword"] = bool(
        effective_search_query and len(effective_search_query) >= 3 and not kg_candidate_ids and not (use_vector_search and vector_product_ids)
    )
    # KG verification and reasoning (week4notes.txt: richer agent responses)
    if kg_explanation:
        trace_metadata["kg_explanation"] = kg_explanation
        n_candidates = len(kg_candidate_ids) if kg_candidate_ids else 0
        trace_metadata["kg_reasoning"] = (
            f"Knowledge graph matched query and filters; {n_candidates} candidates ranked by relevance. "
            "Results verified against product use cases and attributes."
        )
        trace_metadata["kg_verification"] = {
            "source": "neo4j",
            "candidate_count": n_candidates,
            "query": (kg_explanation.get("query") or "")[:80],
        }
    if total_count == 0:
        # Tailored message and suggested actions when user set hard constraints (don't silently drop)
        suggested_actions: List[str] = []
        msg = "No products matched your criteria."
        explanations: List[str] = []
        if filters:
            if filters.get("gpu_vendor"):
                explanations.append(f"gpu_vendor={filters['gpu_vendor']}")
            if filters.get("product_type"):
                explanations.append(f"product_type={filters['product_type']}")
            if filters.get("price_max_cents"):
                explanations.append(f"price<=${filters['price_max_cents']//100}")
            if filters.get("price_min_cents"):
                explanations.append(f"price>=${filters['price_min_cents']//100}")

        # Only show color-specific message when the *current* query actually asked for a color
        # (not when color came from accumulated_filters only — e.g. user said "mac laptop" not "gray laptop")
        _query_lower = (request.query or cleaned_query or search_query or "").strip().lower()
        _color_query_terms = (
            "pink", "red", "blue", "black", "white", "silver", "gold", "gray", "grey",
            "midnight", "rose", "starlight", "green", "yellow", "purple", "orange", "blush",
            "space gray", "space grey", "rose gold",
        )
        _color_mentioned_in_query = any(
            re.search(r"\b" + re.escape(t) + r"\b", _query_lower) for t in _color_query_terms
        )
        if filters.get("color") and _color_mentioned_in_query:
            color_val = (filters.get("color") or "").strip()
            # Silver = Gray = Grey: show as one family in the message
            if color_val.lower() in ("gray", "grey", "silver", "space gray", "space grey"):
                color_val = "Gray/Silver"
            msg = f"I don't see any {color_val} laptops in the catalog."
            suggested_actions = ["Any color", "Rose Gold / Starlight", "Show me laptops (any color)", "Show me books"]
        else:
            pt_val = filters.get("product_type")
            product_types = pt_val if isinstance(pt_val, list) else ([pt_val] if pt_val else [])
            has_desktopish_type = any(t in ("desktop_pc", "gaming_laptop") for t in product_types)
            if (
                filters.get("gpu_vendor")
                or filters.get("_product_type_hint") == "desktop"
                or has_desktopish_type
            ):
                msg = "I don't see any gaming PCs with NVIDIA in that price range."
                suggested_actions = ["Show me laptops", "Increase budget", "Show me all Electronics", "Show me books"]
            else:
                if requested_category == "Books":
                    suggested_actions = [
                        "Broaden within Books (try different genre or price)",
                        "Try Mystery or Fiction",
                        "Switch to laptops",
                        "Switch to vehicles",
                    ]
                elif requested_category == "Electronics":
                    suggested_actions = [
                        "Broaden within Electronics (try different brand or price)",
                        "Show me laptops",
                        "Show me desktops",
                        "Switch to books",
                        "Switch to vehicles",
                    ]
                else:
                    suggested_actions = ["Show me laptops", "Show me books", "Show me vehicles", "Increase budget"]
                if explanations:
                    msg += " Applied filters: " + ", ".join(explanations) + ". Want to broaden or switch category?"
                else:
                    msg += " Want to broaden within this category or switch category?"
        no_match_details: Dict[str, Any] = {"total_count": 0, "category": requested_category, "explanations": explanations}
        if request.session_id:
            no_match_details["session_id"] = request.session_id  # Preserve session so frontend continues same conversation
        constraints_out = [
            ConstraintDetail(
                code="NO_MATCHING_PRODUCTS",
                message=msg,
                details=no_match_details,
                suggested_actions=suggested_actions,
            )
        ]
    response = SearchProductsResponse(
        status=ResponseStatus.OK,
        data=SearchResultsData(
            products=product_summaries,
            total_count=total_count,
            next_cursor=next_cursor
        ),
        constraints=constraints_out,
        trace=create_trace(request_id, cache_hit, timings, sources, trace_metadata),
        version=create_version_info()
    )
    
    # Event logging for research replay
    log_mcp_event(db, request_id, "search_products", "/api/search-products", request, response)
    return response


# 
# GetProduct - Detail Retrieval Tool
# 

def get_product(
    request: GetProductRequest,
    db: Session
) -> GetProductResponse:
    """
    Get detailed information about a single product.
    
    Uses cache-aside pattern:
    1. Check Redis cache first
    2. If miss, query Postgres
    3. Update cache with fresh data
    
    Returns: Full product details or NOT_FOUND
    """
    request_id = str(uuid.uuid4())
    start_time = time.time()
    
    # Structured logging: log request
    log_request("get_product", request_id, params={"product_id": request.product_id, "fields": request.fields})
    
    timings = {}
    sources = []
    cache_hit = False
    
    # Try cache first
    cache_start = time.time()
    cached_summary = cache_client.get_product_summary(request.product_id)
    cached_price = cache_client.get_price(request.product_id)
    cached_inventory = cache_client.get_inventory(request.product_id)
    timings["cache"] = (time.time() - cache_start) * 1000
    
    if cached_summary and cached_price and cached_inventory:
        # Full cache hit
        cache_hit = True
        sources = ["redis"]
        
        # Build response from cache
        product_detail = ProductDetail(
            product_id=cached_summary["product_id"],
            name=cached_summary["name"],
            description=cached_summary.get("description"),
            category=cached_summary.get("category"),
            brand=cached_summary.get("brand"),
            price_cents=cached_price["price_cents"],
            currency=cached_price.get("currency", "USD"),
            available_qty=cached_inventory["available_qty"],
            source=cached_summary.get("source"),
            color=cached_summary.get("color"),
            scraped_from_url=cached_summary.get("scraped_from_url"),
            reviews=cached_summary.get("reviews"),
            created_at=datetime.fromisoformat(cached_summary["created_at"]),
            updated_at=datetime.fromisoformat(cached_summary["updated_at"])
        )
        
        # Apply field projection if requested
        if request.fields:
            product_detail = apply_field_projection(product_detail, request.fields)
        
        timings["db"] = 0
        timings["total"] = (time.time() - start_time) * 1000
        
        # Record metrics
        record_request_metrics("get_product", timings["total"], cache_hit, is_error=False)
        
        # Structured logging: log response
        log_response("get_product", request_id, "OK", timings["total"], cache_hit=cache_hit)
        
        response = GetProductResponse(
            status=ResponseStatus.OK,
            data=product_detail,
            constraints=[],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info()
        )
        
        # Event logging for research replay
        log_mcp_event(db, request_id, "get_product", "/api/get-product", request, response)
        
        return response
    
    # Cache miss - query database
    sources.append("postgres")
    db_start = time.time()
    
    product = db.query(Product).filter(
        Product.product_id == request.product_id
    ).first()
    
    timings["db"] = (time.time() - db_start) * 1000
    
    if not product:
        # Product not found
        timings["total"] = (time.time() - start_time) * 1000
        
        # Record metrics (not an error, just not found)
        record_request_metrics("get_product", timings["total"], cache_hit, is_error=False)
        
        # Structured logging: log response
        log_response("get_product", request_id, "NOT_FOUND", timings["total"], cache_hit=cache_hit)
        
        response = GetProductResponse(
            status=ResponseStatus.NOT_FOUND,
            data=None,
            constraints=[
                ConstraintDetail(
                    code="PRODUCT_NOT_FOUND",
                    message=f"Product with ID '{request.product_id}' does not exist",
                    details={"product_id": request.product_id},
                    allowed_fields=None,
                    suggested_actions=["SearchProducts to find available products"]
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info()
        )
        
        # Event logging for research replay
        log_mcp_event(db, request_id, "get_product", "/api/get-product", request, response)
        
        return response
    
    # Build product detail from database
    product_detail = ProductDetail(
        product_id=product.product_id,
        name=product.name,
        description=product.description,
        category=product.category,
        brand=product.brand,
        price_cents=product.price_info.price_cents,
        currency=product.price_info.currency,
        available_qty=product.inventory_info.available_qty,
        source=getattr(product, 'source', None),
        color=getattr(product, 'color', None),
        scraped_from_url=getattr(product, 'scraped_from_url', None),
        reviews=getattr(product, 'reviews', None),
        created_at=product.created_at,
        updated_at=product.updated_at
    )
    
    cache_client.set_product_summary(
        product.product_id,
        {
            "product_id": product.product_id,
            "name": product.name,
            "description": product.description,
            "category": product.category,
            "brand": product.brand,
            "source": getattr(product, 'source', None),
            "color": getattr(product, 'color', None),
            "scraped_from_url": getattr(product, 'scraped_from_url', None),
            "reviews": getattr(product, 'reviews', None),
            "created_at": product.created_at.isoformat(),
            "updated_at": product.updated_at.isoformat()
        }
    )
    
    cache_client.set_price(
        product.product_id,
        {
            "price_cents": product.price_info.price_cents,
            "currency": product.price_info.currency
        }
    )
    
    cache_client.set_inventory(
        product.product_id,
        {
            "available_qty": product.inventory_info.available_qty
        }
    )
    
    # Apply field projection if requested
    if request.fields:
        product_detail = apply_field_projection(product_detail, request.fields)
    
    timings["total"] = (time.time() - start_time) * 1000
    
    # Record metrics
    record_request_metrics("get_product", timings["total"], cache_hit, is_error=False)
    
    # Structured logging: log response
    log_response("get_product", request_id, "OK", timings["total"], cache_hit=cache_hit)
    
    response = GetProductResponse(
        status=ResponseStatus.OK,
        data=product_detail,
        constraints=[],
        trace=create_trace(request_id, cache_hit, timings, sources),
        version=create_version_info()
    )
    
    # Event logging for research replay
    log_mcp_event(db, request_id, "get_product", "/api/get-product", request, response)
    
    return response


# 
# AddToCart - Execution Tool (IDs Only!)
# 

def add_to_cart(
    request: AddToCartRequest,
    db: Session
) -> AddToCartResponse:
    """
    Add a product to a cart.
    
    CRITICAL: IDs-only execution rule enforced here.
    Only accepts product_id, never product name.
    
    Validates:
    - Product exists
    - Sufficient inventory
    - Cart exists (creates if not)
    
    Returns: Updated cart or constraint (OUT_OF_STOCK, NOT_FOUND)
    """
    request_id = str(uuid.uuid4())
    start_time = time.time()
    
    timings = {}
    sources = ["postgres"]
    cache_hit = False
    
    # Verify product exists and check inventory
    db_start = time.time()
    
    product = db.query(Product).filter(
        Product.product_id == request.product_id
    ).first()
    
    if not product:
        timings["db"] = (time.time() - db_start) * 1000
        timings["total"] = (time.time() - start_time) * 1000
        
        # Record metrics
        record_request_metrics("add_to_cart", timings["total"], cache_hit, is_error=False)
        
        # Structured logging: log response
        log_response("add_to_cart", request_id, "NOT_FOUND", timings["total"], cache_hit=cache_hit)
        
        response = AddToCartResponse(
            status=ResponseStatus.NOT_FOUND,
            data=None,
            constraints=[
                ConstraintDetail(
                    code="PRODUCT_NOT_FOUND",
                    message=f"Product with ID '{request.product_id}' does not exist",
                    details={"product_id": request.product_id},
                    allowed_fields=None,
                    suggested_actions=["SearchProducts to find valid product IDs"]
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info()
        )
        
        # Event logging for research replay
        log_mcp_event(db, request_id, "add_to_cart", "/api/add-to-cart", request, response)
        
        return response
    
    # Check inventory availability
    inventory = product.inventory_info
    if inventory.available_qty < request.qty:
        timings["db"] = (time.time() - db_start) * 1000
        timings["total"] = (time.time() - start_time) * 1000
        
        # Record metrics
        record_request_metrics("add_to_cart", timings["total"], cache_hit, is_error=False)
        
        # Structured logging: log response
        log_response("add_to_cart", request_id, "OUT_OF_STOCK", timings["total"], cache_hit=cache_hit)
        
        response = AddToCartResponse(
            status=ResponseStatus.OUT_OF_STOCK,
            data=None,
            constraints=[
                ConstraintDetail(
                    code="OUT_OF_STOCK",
                    message=f"Insufficient inventory for product '{product.name}'",
                    details={
                        "product_id": request.product_id,
                        "requested_qty": request.qty,
                        "available_qty": inventory.available_qty
                    },
                    allowed_fields=None,
                    suggested_actions=[
                        f"ReduceQty to {inventory.available_qty} or less",
                        "SearchProducts to find alternative products"
                    ]
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info()
        )
        
        # Event logging for research replay
        log_mcp_event(db, request_id, "add_to_cart", "/api/add-to-cart", request, response)
        
        return response
    
    # Get or create cart
    cart = db.query(Cart).filter(Cart.cart_id == request.cart_id).first()
    if not cart:
        cart = Cart(cart_id=request.cart_id, status="active")
        db.add(cart)
        db.flush()  # Get cart ID without committing
    
    # Check if product already in cart
    existing_item = db.query(CartItem).filter(
        CartItem.cart_id == request.cart_id,
        CartItem.product_id == request.product_id
    ).first()
    
    if existing_item:
        # Update quantity
        existing_item.quantity += request.qty
    else:
        # Add new item
        cart_item = CartItem(
            cart_id=request.cart_id,
            product_id=request.product_id,
            quantity=request.qty
        )
        db.add(cart_item)
    
    db.commit()
    
    # Build cart response
    cart_items = db.query(CartItem).filter(CartItem.cart_id == request.cart_id).all()
    
    cart_items_data = []
    total_cents = 0
    
    for item in cart_items:
        item_product = item.product
        item_price = item_product.price_info.price_cents
        
        cart_item_data = CartItemData(
            cart_item_id=item.cart_item_id,
            product_id=item.product_id,
            product_name=item_product.name,
            quantity=item.quantity,
            price_cents=item_price,
            currency=item_product.price_info.currency
        )
        cart_items_data.append(cart_item_data)
        total_cents += item_price * item.quantity
    
    timings["db"] = (time.time() - db_start) * 1000
    timings["total"] = (time.time() - start_time) * 1000
    
    cart_data = CartData(
        cart_id=cart.cart_id,
        status=cart.status,
        items=cart_items_data,
        total_cents=total_cents,
        currency="USD"
    )
    
    # Record metrics
    record_request_metrics("add_to_cart", timings["total"], cache_hit, is_error=False)
    
    # Structured logging: log response
    log_response("add_to_cart", request_id, "OK", timings["total"], cache_hit=cache_hit)
    
    response = AddToCartResponse(
        status=ResponseStatus.OK,
        data=cart_data,
        constraints=[],
        trace=create_trace(request_id, cache_hit, timings, sources),
        version=create_version_info()
    )
    
    # Event logging for research replay
    log_mcp_event(db, request_id, "add_to_cart", "/api/add-to-cart", request, response)
    
    return response


# 
# Checkout - Execution Tool (IDs Only!)
# 

def checkout(
    request: CheckoutRequest,
    db: Session
) -> CheckoutResponse:
    """
    Complete checkout for a cart.
    
    IDs-only execution: accepts cart_id, payment_method_id, address_id.
    (In production, these IDs would be validated against user's saved info)
    
    Validates:
    - Cart exists and is active
    - Cart has items
    - All items still in stock
    
    Returns: Order confirmation or constraints
    """
    request_id = str(uuid.uuid4())
    start_time = time.time()
    
    timings = {}
    sources = ["postgres"]
    cache_hit = False
    
    db_start = time.time()
    
    # Verify cart exists
    cart = db.query(Cart).filter(Cart.cart_id == request.cart_id).first()
    
    if not cart:
        timings["db"] = (time.time() - db_start) * 1000
        timings["total"] = (time.time() - start_time) * 1000
        
        # Record metrics
        record_request_metrics("checkout", timings["total"], cache_hit, is_error=False)
        
        # Structured logging: log response
        log_response("checkout", request_id, "NOT_FOUND", timings["total"], cache_hit=cache_hit)
        
        response = CheckoutResponse(
            status=ResponseStatus.NOT_FOUND,
            data=None,
            constraints=[
                ConstraintDetail(
                    code="CART_NOT_FOUND",
                    message=f"Cart with ID '{request.cart_id}' does not exist",
                    details={"cart_id": request.cart_id},
                    allowed_fields=None,
                    suggested_actions=["AddToCart to create a cart with items"]
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info()
        )
        
        # Event logging for research replay
        log_mcp_event(db, request_id, "checkout", "/api/checkout", request, response)
        
        return response
    
    # Verify cart has items (eager load product + inventory for validation and decrement)
    cart_items = (
        db.query(CartItem)
        .filter(CartItem.cart_id == request.cart_id)
        .options(
            selectinload(CartItem.product).selectinload(Product.price_info),
            selectinload(CartItem.product).selectinload(Product.inventory_info),
        )
        .all()
    )
    
    if not cart_items:
        timings["db"] = (time.time() - db_start) * 1000
        timings["total"] = (time.time() - start_time) * 1000
        
        # Record metrics
        record_request_metrics("checkout", timings["total"], cache_hit, is_error=False)
        
        # Structured logging: log response
        log_response("checkout", request_id, "INVALID", timings["total"], cache_hit=cache_hit)
        
        response = CheckoutResponse(
            status=ResponseStatus.INVALID,
            data=None,
            constraints=[
                ConstraintDetail(
                    code="CART_EMPTY",
                    message="Cannot checkout an empty cart",
                    details={"cart_id": request.cart_id},
                    allowed_fields=None,
                    suggested_actions=["AddToCart to add items before checkout"]
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info()
        )
        
        # Event logging for research replay
        log_mcp_event(db, request_id, "checkout", "/api/checkout", request, response)
        
        return response
    
    # Lock inventory rows for concurrency-safe decrement (avoid oversell race)
    product_ids = [item.product_id for item in cart_items]
    list(db.query(Inventory).filter(Inventory.product_id.in_(product_ids)).with_for_update().all())
    
    # Verify all items are still in stock
    total_cents = 0
    out_of_stock_items = []
    
    for item in cart_items:
        product = item.product
        inventory = product.inventory_info
        
        if inventory.available_qty < item.quantity:
            out_of_stock_items.append({
                "product_id": product.product_id,
                "product_name": product.name,
                "requested_qty": item.quantity,
                "available_qty": inventory.available_qty
            })
        
        total_cents += product.price_info.price_cents * item.quantity
    
    if out_of_stock_items:
        timings["db"] = (time.time() - db_start) * 1000
        timings["total"] = (time.time() - start_time) * 1000
        
        # Record metrics
        record_request_metrics("checkout", timings["total"], cache_hit, is_error=False)
        
        # Structured logging: log response
        log_response("checkout", request_id, "OUT_OF_STOCK", timings["total"], cache_hit=cache_hit)
        
        response = CheckoutResponse(
            status=ResponseStatus.OUT_OF_STOCK,
            data=None,
            constraints=[
                ConstraintDetail(
                    code="CHECKOUT_OUT_OF_STOCK",
                    message="Some items in cart are out of stock",
                    details={"out_of_stock_items": out_of_stock_items},
                    allowed_fields=None,
                    suggested_actions=[
                        "Remove out-of-stock items from cart",
                        "Reduce quantities to available levels"
                    ]
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info()
        )
        
        # Event logging for research replay
        log_mcp_event(db, request_id, "checkout", "/api/checkout", request, response)
        
        return response
    
    # Create order (with synthetic shipping per week4notes.txt)
    order_id = f"order-{uuid.uuid4()}"
    order = Order(
        order_id=order_id,
        cart_id=request.cart_id,
        payment_method_id=request.payment_method_id,
        address_id=request.address_id,
        total_cents=total_cents,
        status="pending",
        shipping_method="standard",
        estimated_delivery_days=5,
        shipping_cost_cents=599,
        shipping_region="US",
    )
    db.add(order)
    
    # Update cart status
    cart.status = "checked_out"
    
    # Decrement inventory for all items
    for item in cart_items:
        inventory = item.product.inventory_info
        inventory.available_qty -= item.quantity
    
    db.commit()
    
    # Invalidate cache for all products in the cart (inventory changed)
    for item in cart_items:
        cache_client.invalidate_product(item.product_id)
    
    timings["db"] = (time.time() - db_start) * 1000
    timings["total"] = (time.time() - start_time) * 1000
    
    shipping_info = None
    if getattr(order, "shipping_method", None) or getattr(order, "estimated_delivery_days", None) or getattr(order, "shipping_cost_cents", None) or getattr(order, "shipping_region", None):
        shipping_info = ShippingInfo(
            shipping_method=getattr(order, "shipping_method", None),
            estimated_delivery_days=getattr(order, "estimated_delivery_days", None),
            shipping_cost_cents=getattr(order, "shipping_cost_cents", None),
            shipping_region=getattr(order, "shipping_region", None),
        )
    order_data = OrderData(
        order_id=order.order_id,
        cart_id=order.cart_id,
        total_cents=order.total_cents,
        currency=order.currency,
        status=order.status,
        created_at=order.created_at,
        shipping=shipping_info,
    )
    
    # Record metrics
    record_request_metrics("checkout", timings["total"], cache_hit, is_error=False)
    
    # Structured logging: log response
    log_response("checkout", request_id, "OK", timings["total"], cache_hit=cache_hit)
    
    response = CheckoutResponse(
        status=ResponseStatus.OK,
        data=order_data,
        constraints=[],
        trace=create_trace(request_id, cache_hit, timings, sources),
        version=create_version_info()
    )
    
    # Event logging for research replay
    log_mcp_event(db, request_id, "checkout", "/api/checkout", request, response)
    
    return response