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
from app.input_validator import should_reject_input, normalize_domain_keywords
from app.query_normalizer import normalize_query
from app.llm_validator import get_llm_validator
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

    # User actions (favorites, clicks) for preference refinement
    user_actions: Optional[List[Dict[str, str]]] = Field(default=None, description="List of {type, product_id}")


class ChatResponse(BaseModel):
    """Response model for chat endpoint - matches IDSS format."""
    response_type: str = Field(description="'question', 'recommendations', 'research', or 'compare'")
    message: str = Field(description="AI response message")
    session_id: str = Field(description="Session ID")

    # Question-specific fields
    quick_replies: Optional[List[str]] = Field(default=None, description="Quick reply options for questions")

    # Recommendation-specific fields
    recommendations: Optional[List[List[Dict[str, Any]]]] = Field(default=None, description="2D grid of products [rows][items]")
    bucket_labels: Optional[List[str]] = Field(default=None, description="Labels for each row/bucket")
    diversification_dimension: Optional[str] = Field(default=None, description="Dimension used for diversification")

    # Research: features, compatibility, review summary (kg.txt step intent)
    research_data: Optional[Dict[str, Any]] = Field(default=None, description="Product research: features, compatibility, review_summary")

    # Compare: side-by-side between options (kg.txt step intent)
    comparison_data: Optional[Dict[str, Any]] = Field(default=None, description="Side-by-side comparison: attributes, products with values")

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
    
    # Process user actions (favorites, clicks) for preference refinement
    if request.user_actions:
        for action in request.user_actions:
            act_type = (action or {}).get("type")
            product_id = (action or {}).get("product_id")
            if product_id:
                if act_type == "favorite":
                    session_manager.add_favorite(session_id, product_id)
                elif act_type == "unfavorite":
                    session_manager.remove_favorite(session_id, product_id)
                elif act_type == "click":
                    session_manager.add_click(session_id, product_id)
    
    # Fast bypass: exact domain quick-reply options are always valid (e.g. user clicked "Jewelry")
    _domain_options = ["vehicles", "laptops", "books", "jewelry", "accessories", "clothing", "beauty"]
    msg_stripped = request.message.strip()
    msg_lower = msg_stripped.lower()
    if msg_lower in _domain_options:
        validation_result = {
            "is_valid": True,
            "corrected_input": msg_lower,
            "confidence": 1.0,
            "detected_intent": "domain_selection",
            "suggestions": [],
            "error_message": None,
        }
    # Fast bypass: rating responses (always accept - user replying to "How would you rate?" prompt)
    elif msg_lower in ("5 stars", "4 stars", "3 stars", "2 stars", "1 star", "could be better"):
        validation_result = {
            "is_valid": True,
            "corrected_input": msg_stripped,
            "confidence": 1.0,
            "detected_intent": "rating_response",
            "suggestions": [],
            "error_message": None,
        }
    # Fast bypass: post-recommendation quick replies (Broaden search, Different category, etc.)
    # Include NO_MATCH quick replies so "Show me all X" / "Increase my budget" work when no results
    elif session.stage == STAGE_RECOMMENDATIONS and session.active_domain:
        _post_rec_answers = [
            "broaden search", "different category", "show more like these",
            "see similar items", "anything else", "compare items", "rate recommendations", "help with checkout",
            "research", "explain features", "check compatibility", "summarize reviews", "compare these",
            "5 stars", "4 stars", "3 stars", "2 stars", "1 star", "could be better",
            "show me all jewelry", "show me all accessories", "show me all clothing", "show me all beauty",
            "show me all laptops", "show me all books", "increase my budget", "try a different brand", "try a different type",
        ]
        if msg_lower in _post_rec_answers or any(a in msg_lower for a in _post_rec_answers):
            validation_result = {
                "is_valid": True,
                "corrected_input": msg_stripped,
                "confidence": 1.0,
                "detected_intent": "post_recommendation",
                "suggestions": [],
                "error_message": None,
            }
        else:
            llm_validator = get_llm_validator()
            context = f"Post-recommendation, domain: {session.active_domain}"
            validation_result = llm_validator.validate_and_correct(request.message, context)
    # Fast bypass: interview follow-up answers (brand, type, budget options) when in active session
    elif session.active_domain and session.stage == STAGE_INTERVIEW:
        _interview_answers = [
            "necklace", "earrings", "bracelet", "ring", "pendant",
            "scarf", "hat", "belt", "bag", "watch", "sunglasses",
            "lipstick", "eyeshadow", "mascara", "skincare", "foundation", "blush",
            "dress", "dresses", "shirt", "shirts", "pants", "jacket", "sweater", "top", "jeans", "skirt",
            "pandora", "tiffany", "swarovski", "kay jewelers", "zales", "jared",
            "tiffany & co", "tiffany and co", "mac", "nars", "colourpop", "fenty beauty",
            "nike", "patagonia", "uniqlo",
            "no preference", "specific brand",
            "under $50", "$50-$150", "$150-$300", "over $300",
            "under $20", "$20-$50", "$50-$100", "over $100", "$100-$200", "over $200",
            "under $15", "$15-$30", "any price",
            "under $20k", "$20k-$35k", "$35k-$50k", "over $50k",
            "gaming", "work", "school", "creative", "apple", "dell", "lenovo", "hp",
            "fiction", "mystery", "sci-fi", "hardcover", "paperback", "e-book", "audiobook",
            "show me all jewelry", "show me all accessories", "show me all clothing", "show me all beauty",
            "show me all laptops", "show me all books", "increase my budget", "try a different brand", "try a different type",
        ]
        _msg_normalized = " ".join(msg_lower.split())  # collapse spaces
        if any(ans in _msg_normalized or _msg_normalized == ans for ans in _interview_answers):
            validation_result = {
                "is_valid": True,
                "corrected_input": msg_stripped,
                "confidence": 1.0,
                "detected_intent": "filter_response",
                "suggestions": [],
                "error_message": None,
            }
        else:
            llm_validator = get_llm_validator()
            context = f"Active domain: {session.active_domain}"
            validation_result = llm_validator.validate_and_correct(request.message, context)
    else:
        # Accept known interview answers even without session (e.g. session_id lost, or first answer)
        _all_interview_answers = [
            "necklace", "earrings", "bracelet", "ring", "pendant",
            "scarf", "hat", "belt", "bag", "watch", "sunglasses",
            "lipstick", "eyeshadow", "mascara", "skincare", "foundation", "blush",
            "dress", "dresses", "shirt", "shirts", "pants", "jacket", "sweater", "top", "jeans", "skirt",
            "pandora", "tiffany", "swarovski", "kay jewelers", "zales", "jared",
            "tiffany & co", "tiffany and co", "mac", "nars", "colourpop", "fenty beauty",
            "nike", "patagonia", "uniqlo",
            "no preference", "specific brand",
            "under $50", "$50-$150", "$150-$300", "over $300",
            "under $20", "$20-$50", "$50-$100", "over $100", "$100-$200", "over $200",
            "under $15", "$15-$30", "any price",
            "under $20k", "$20k-$35k", "$35k-$50k", "over $50k",
            "gaming", "work", "school", "creative", "apple", "dell", "lenovo", "hp",
            "fiction", "mystery", "sci-fi", "hardcover", "paperback", "e-book", "audiobook",
            "show me all jewelry", "show me all accessories", "show me all clothing", "show me all beauty",
            "show me all laptops", "show me all books", "increase my budget", "try a different brand", "try a different type",
        ]
        _msg_normalized = " ".join(msg_lower.split())
        if any(ans in _msg_normalized or _msg_normalized == ans for ans in _all_interview_answers):
            validation_result = {
                "is_valid": True,
                "corrected_input": msg_stripped,
                "confidence": 1.0,
                "detected_intent": "filter_response",
                "suggestions": [],
                "error_message": None,
            }
        else:
            # Try LLM-based validation first (intelligent), fall back to hardcoded rules
            llm_validator = get_llm_validator()
            context = f"Active domain: {session.active_domain}" if session.active_domain else "No active conversation"
            validation_result = llm_validator.validate_and_correct(request.message, context)
    
    logger.info("input_validation", "Validated user input", {
        "original": request.message,
        "is_valid": validation_result["is_valid"],
        "corrected": validation_result["corrected_input"],
        "confidence": validation_result["confidence"],
        "intent": validation_result["detected_intent"],
    })
    
    # If LLM says invalid, reject
    if not validation_result["is_valid"]:
        error_msg = validation_result["error_message"] or "I didn't understand that. What are you looking for?"
        suggestions = validation_result["suggestions"] or ["Vehicles", "Laptops", "Books", "Jewelry", "Accessories", "Clothing", "Beauty"]
        return ChatResponse(
            response_type="question",
            message=error_msg,
            session_id=session_id,
            quick_replies=suggestions,
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=session.active_domain,
        )
    
    # Use corrected input if LLM made corrections
    if validation_result["corrected_input"] != request.message:
        logger.info("message_corrected_by_llm", f"Corrected '{request.message}' → '{validation_result['corrected_input']}'", {
            "original": request.message,
            "corrected": validation_result["corrected_input"],
            "confidence": validation_result["confidence"],
        })
        request.message = validation_result["corrected_input"]
    
    # Also apply hardcoded normalization as backup (belt and suspenders approach)
    normalized_message = normalize_domain_keywords(request.message)
    if normalized_message != request.message:
        logger.info("message_normalized_hardcoded", f"Further normalized '{request.message}' → '{normalized_message}'", {
            "original": request.message,
            "normalized": normalized_message,
        })
        request.message = normalized_message

    # Handle post-recommendation follow-ups (keep conversation going)
    session = session_manager.get_session(session_id)
    msg = request.message.strip()
    msg_lower = msg.lower()

    # Handle rating responses first (user replying to "How would you rate?" - accept regardless of session state)
    if msg_lower in ("5 stars", "4 stars", "3 stars", "2 stars", "1 star", "could be better"):
        session_manager.add_message(session_id, "user", msg)
        stars = "5" if "5" in msg_lower else "4" if "4" in msg_lower else "3" if "3" in msg_lower else "2" if "2" in msg_lower else "1" if "1" in msg_lower else "?"
        return ChatResponse(
            response_type="question",
            message=f"Thank you for your {stars}-star rating! Your feedback helps us improve. Is there anything else I can help you with?",
            session_id=session_id,
            quick_replies=["See similar items", "Compare items", "Help with checkout", "Different category"],
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=session.active_domain,
        )

    if session.stage == STAGE_RECOMMENDATIONS and session.active_domain:
        # Map quick-reply style intents to helpful responses
        if "see similar" in msg_lower or "similar items" in msg_lower:
            session_manager.add_message(session_id, "user", msg)
            return ChatResponse(
                response_type="question",
                message="I can show you more options in the same style. Would you like to broaden the search (e.g., different brands or price range), or try a different category?",
                session_id=session_id,
                quick_replies=["Broaden search", "Different category", "Show more like these"],
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=session.active_domain,
            )
        if "broaden search" in msg_lower:
            session_manager.add_message(session_id, "user", msg)
            # Relax filters: drop brand, widen price range (for e-commerce only)
            relaxed = dict(session.explicit_filters)
            relaxed.pop("brand", None)
            if relaxed.get("price_max_cents"):
                relaxed["price_max_cents"] = min(int(relaxed["price_max_cents"] * 1.5), 999999)  # Widen by 50%
            relaxed.pop("price_min_cents", None)
            session_manager.update_filters(session_id, relaxed)
            if session.active_domain in ("laptops", "books", "jewelry", "accessories", "clothing", "beauty"):
                category = _domain_to_category(session.active_domain)
                recs, labels = await _search_ecommerce_products(relaxed, category, n_rows=3, n_per_row=3)
                if recs:
                    return ChatResponse(
                        response_type="recommendations",
                        message="Here are more options with a broader search:",
                        session_id=session_id,
                        recommendations=recs,
                        bucket_labels=labels or [],
                        filters=relaxed,
                        preferences={},
                        question_count=session.question_count,
                        domain=session.active_domain,
                        quick_replies=["See similar items", "Anything else?", "Compare items", "Help with checkout"],
                    )
            return ChatResponse(
                response_type="question",
                message="I couldn't find more options. Would you like to try a different category?",
                session_id=session_id,
                quick_replies=["Vehicles", "Laptops", "Books", "Jewelry", "Accessories", "Clothing", "Beauty"],
                filters=relaxed,
                preferences={},
                question_count=session.question_count,
                domain=session.active_domain,
            )
        if "different category" in msg_lower:
            session_manager.add_message(session_id, "user", msg)
            session_manager.reset_session(session_id)
            return ChatResponse(
                response_type="question",
                message="What are you looking for today?",
                session_id=session_id,
                quick_replies=["Vehicles", "Laptops", "Books", "Jewelry", "Accessories", "Clothing", "Beauty"],
                filters={},
                preferences={},
                question_count=0,
                domain=None,
            )
        if "show more like these" in msg_lower:
            session_manager.add_message(session_id, "user", msg)
            if session.active_domain in ("laptops", "books", "jewelry", "accessories", "clothing", "beauty"):
                category = _domain_to_category(session.active_domain)
                exclude_ids = list(session.last_recommendation_ids or [])
                recs, labels = await _search_ecommerce_products(
                    session.explicit_filters, category, n_rows=2, n_per_row=3,
                    exclude_ids=exclude_ids if exclude_ids else None,
                )
            else:
                recs, labels = [], []
            if recs:
                # Accumulate shown IDs so next "Show more like these" excludes all we've shown
                new_ids = []
                for row in recs:
                    for item in row:
                        pid = item.get("product_id") or item.get("id") or (item.get("_product") or {}).get("product_id")
                        if pid and pid not in new_ids:
                            new_ids.append(pid)
                if new_ids:
                    accumulated = list(exclude_ids) + [p for p in new_ids if p not in exclude_ids]
                    session_manager.set_last_recommendations(session_id, accumulated[:24])  # Cap at 24
                return ChatResponse(
                    response_type="recommendations",
                    message="Here are more options like the ones you saw:",
                    session_id=session_id,
                    recommendations=recs,
                    bucket_labels=labels or [],
                    filters=session.explicit_filters,
                    preferences={},
                    question_count=session.question_count,
                    domain=session.active_domain,
                    quick_replies=["See similar items", "Anything else?", "Compare items", "Help with checkout"],
                )
            return ChatResponse(
                response_type="question",
                message="I've shown you the best matches. Would you like to broaden the search or try a different category?",
                session_id=session_id,
                quick_replies=["Broaden search", "Different category", "See similar items"],
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=session.active_domain,
            )
        if "anything else" in msg_lower or "help" in msg_lower and "checkout" not in msg_lower:
            session_manager.add_message(session_id, "user", msg)
            return ChatResponse(
                response_type="question",
                message="I'm here to help! You can: see similar items, compare products, rate these recommendations, or get help with checkout. What would you like to do?",
                session_id=session_id,
                quick_replies=["See similar items", "Compare items", "Rate recommendations", "Help with checkout"],
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=session.active_domain,
            )
        # Research: explain features, check compatibility, summarize reviews (kg.txt)
        if any(k in msg_lower for k in ["research", "explain features", "check compatibility", "summarize reviews"]):
            session_manager.add_message(session_id, "user", msg)
            from app.research_compare import build_research_summary
            # Get product: favorite > clicked > first from last recommendations
            product_ids = list(session.favorite_product_ids or []) + list(session.clicked_product_ids or [])
            if not product_ids and getattr(session, "last_recommendation_ids", None):
                product_ids = session.last_recommendation_ids[:1]
            if not product_ids:
                return ChatResponse(
                    response_type="question",
                    message="To research a product, please click on one from the recommendations first, or add it to favorites. Then say \"Research\" or \"Explain features\".",
                    session_id=session_id,
                    quick_replies=["See similar items", "Compare items", "Back to recommendations"],
                    filters=session.explicit_filters,
                    preferences={},
                    question_count=session.question_count,
                    domain=session.active_domain,
                )
            products = _fetch_products_by_ids(product_ids[:1])
            if not products:
                return ChatResponse(
                    response_type="question",
                    message="I couldn't find that product. Try selecting one from the recommendations first.",
                    session_id=session_id,
                    quick_replies=["See similar items", "Compare items"],
                    filters=session.explicit_filters,
                    preferences={},
                    question_count=session.question_count,
                    domain=session.active_domain,
                )
            research = build_research_summary(products[0])
            rev = research.get("review_summary", {})
            msg_parts = [f"**{research['name']}** ({research.get('brand') or ''})"]
            msg_parts.append(f"Price: ${float(research.get('price') or 0):,.2f}")
            if research.get("features"):
                msg_parts.append("**Features:** " + "; ".join(research["features"][:5]))
            msg_parts.append(f"**Compatibility:** {research.get('compatibility', '')}")
            msg_parts.append(f"**Reviews:** {rev.get('summary', 'No reviews')}")
            return ChatResponse(
                response_type="research",
                message="\n\n".join(str(p) for p in msg_parts if p),
                session_id=session_id,
                research_data=research,
                quick_replies=["Compare items", "See similar items", "Help with checkout"],
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=session.active_domain,
            )
        if "compare" in msg_lower or "vs" in msg_lower or "against" in msg_lower or "versus" in msg_lower:
            session_manager.add_message(session_id, "user", msg)
            category = _domain_to_category(session.active_domain)
            # Compare these / Compare items: use favorites + clicked, or last recommendations
            if "compare these" in msg_lower or "compare items" in msg_lower or ("compare" in msg_lower and "mac" not in msg_lower and "dell" not in msg_lower and "by price" not in msg_lower):
                from app.research_compare import build_comparison_table
                product_ids = list(dict.fromkeys(
                    list(session.favorite_product_ids or []) +
                    list(session.clicked_product_ids or [])
                ))[:4]
                if not product_ids and getattr(session, "last_recommendation_ids", None):
                    product_ids = session.last_recommendation_ids[:4]
                if product_ids:
                    products = _fetch_products_by_ids(product_ids)
                    if products:
                        comparison = build_comparison_table(products)
                        # Format products same as search (image.primary, retailListing, price) for frontend
                        fmt_domain = (
                            "books" if category == "Books"
                            else "jewelry" if category == "Jewelry"
                            else "accessories" if category == "Accessories"
                            else "laptops"
                        )
                        from app.formatters import format_product
                        formatted = [format_product(p, fmt_domain).model_dump(mode="json", exclude_none=True) for p in products]
                        rec_rows = [formatted]
                        labels = [p.get("name", "Product")[:20] for p in products]
                        return ChatResponse(
                            response_type="compare",
                            message="Side-by-side comparison of your selected items:",
                            session_id=session_id,
                            comparison_data=comparison,
                            recommendations=rec_rows,
                            bucket_labels=labels,
                            quick_replies=["See similar items", "Research", "Help with checkout"],
                            filters=session.explicit_filters,
                            preferences={},
                            question_count=session.question_count,
                            domain=session.active_domain,
                        )
                return ChatResponse(
                    response_type="question",
                    message="To compare items, please click on 2-4 products from the recommendations first (or add them to favorites), then say \"Compare these\".",
                    session_id=session_id,
                    quick_replies=["Compare by price", "Compare Mac vs Dell", "See similar items"],
                    filters=session.explicit_filters,
                    preferences={},
                    question_count=session.question_count,
                    domain=session.active_domain,
                )
            # Specific compare actions: run comparison search
            if "mac" in msg_lower and "dell" in msg_lower:
                # Compare Mac vs Dell - search each brand
                f_mac = dict(session.explicit_filters)
                f_mac["brand"] = "Apple"
                f_dell = dict(session.explicit_filters)
                f_dell["brand"] = "Dell"
                mac_recs, _ = await _search_ecommerce_products(f_mac, category, n_rows=1, n_per_row=2)
                dell_recs, _ = await _search_ecommerce_products(f_dell, category, n_rows=1, n_per_row=2)
                mac_row = mac_recs[0][:2] if mac_recs else []
                dell_row = dell_recs[0][:2] if dell_recs else []
                if mac_row or dell_row:
                    rec_rows = []
                    if mac_row:
                        rec_rows.append(mac_row)
                    if dell_row:
                        rec_rows.append(dell_row)
                    return ChatResponse(
                        response_type="recommendations",
                        message="Mac vs Dell comparison. Apple/Mac and Dell options:",
                        session_id=session_id,
                        recommendations=rec_rows,
                        bucket_labels=(["Apple/Mac"] if mac_row else []) + (["Dell"] if dell_row else []),
                        filters=session.explicit_filters,
                        preferences={},
                        question_count=session.question_count,
                        domain=session.active_domain,
                        quick_replies=["See similar items", "Anything else?", "Help with checkout"],
                    )
            if "by price" in msg_lower or "top 2" in msg_lower:
                recs, labels = await _search_ecommerce_products(
                    session.explicit_filters, category, n_rows=2, n_per_row=2,
                )
                if recs:
                    return ChatResponse(
                        response_type="recommendations",
                        message="Top options by price for comparison:",
                        session_id=session_id,
                        recommendations=recs,
                        bucket_labels=labels or ["Budget", "Premium"],
                        filters=session.explicit_filters,
                        preferences={},
                        question_count=session.question_count,
                        domain=session.active_domain,
                        quick_replies=["See similar items", "Anything else?", "Help with checkout"],
                    )
            return ChatResponse(
                response_type="question",
                message="I can compare items! Try \"Compare Mac vs Dell\" or \"Compare by price\" for a side-by-side view.",
                session_id=session_id,
                quick_replies=["Compare Mac vs Dell", "Compare by price", "See similar items"],
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=session.active_domain,
            )
        if "checkout" in msg_lower or "pay" in msg_lower or "transaction" in msg_lower:
            session_manager.add_message(session_id, "user", msg)
            return ChatResponse(
                response_type="question",
                message="I can help with checkout! For now, you can view the full listing for any product by clicking \"View Details\" on a recommendation. Would you like to see more options or have questions about a specific item?",
                session_id=session_id,
                quick_replies=["See similar items", "Compare items", "Back to recommendations"],
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=session.active_domain,
            )
        if "rate" in msg_lower and ("recommendation" in msg_lower or "these" in msg_lower):
            session_manager.add_message(session_id, "user", msg)
            return ChatResponse(
                response_type="question",
                message="Thanks for your interest in rating! Your feedback helps us improve. How would you rate these recommendations overall? (1-5 stars)",
                session_id=session_id,
                quick_replies=["5 stars", "4 stars", "3 stars", "Could be better"],
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=session.active_domain,
            )
    
    # Check if user explicitly wants to reset/restart
    message_lower = request.message.lower().strip()
    reset_keywords = ['reset', 'restart', 'start over', 'new search', 'clear', 'different category']
    is_explicit_reset = any(keyword == message_lower or keyword in message_lower for keyword in reset_keywords)
    
    # Check if it's a standalone greeting (hi, hello) with no other context
    # These should reset the conversation to domain selection
    # IMPORTANT: Only actual greetings, not brand names or answers!
    greeting_words = ['hi', 'hello', 'hey', 'yo', 'sup']
    is_standalone_greeting = (
        message_lower in greeting_words and  # Must be exact greeting word
        session.active_domain  # Has been in a conversation
    )
    
    if is_explicit_reset or is_standalone_greeting:
        logger.info("explicit_reset_requested", f"User requested reset: '{request.message}'", {
            "message": request.message,
            "is_explicit_reset": is_explicit_reset,
            "is_standalone_greeting": is_standalone_greeting,
        })
        session_manager.reset_session(session_id)
        session = session_manager.get_session(session_id)
        return ChatResponse(
            response_type="question",
            message="What are you looking for today?",
            session_id=session_id,
            quick_replies=["Vehicles", "Laptops", "Books", "Jewelry", "Accessories", "Clothing", "Beauty"],
            filters={},
            preferences={},
            question_count=0,
            domain=None,
        )
    
    # IMPORTANT: Don't detect domain switches for short answers in active sessions
    # When user is answering followup questions (e.g., "Paperback", "Dell", "Gaming"),
    # these are answers to the current question, NOT new domain selections
    is_likely_followup_answer = (
        session.active_domain and  # Has active domain
        len(request.message.strip()) < 40 and  # Short answer
        session.question_count > 0 and  # Has been asked questions
        session.stage == STAGE_INTERVIEW  # Still in interview, not after recommendations
    )
    
    if is_likely_followup_answer:
        # Keep current domain, don't re-detect
        detected_domain = Domain(session.active_domain) if session.active_domain else Domain.NONE
        route_reason = "followup_answer"
        logger.info("treating_as_followup_answer", f"Treating '{request.message}' as answer in {session.active_domain} domain", {
            "message": request.message,
            "active_domain": session.active_domain,
            "question_count": session.question_count,
        })
    else:
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
            quick_replies=["Vehicles", "Laptops", "Books", "Jewelry", "Accessories", "Clothing", "Beauty"],
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=None,
        )

    active_domain = detected_domain.value if detected_domain != Domain.NONE else session.active_domain or "vehicles"

    # HARD SAFEGUARD: Vehicle budget answers ("Over $50k", etc.) must ALWAYS go to IDSS.
    # Prevents book/laptop questions leaking into vehicle flow when session state is wrong.
    _vehicle_budget_answers = ("under $20k", "$20k-$35k", "$35k-$50k", "over $50k")
    if msg_lower in _vehicle_budget_answers:
        active_domain = "vehicles"

    if active_domain in ["laptops", "books", "jewelry", "accessories", "clothing", "beauty"]:
        category = _domain_to_category(active_domain)
        filters = dict(session.explicit_filters)
        filters["category"] = category

        # "Show me all X" - clear restrictive filters and show everything in category
        if msg_lower in ("show me all jewelry", "show me all accessories", "show me all clothing", "show me all beauty", "show me all laptops", "show me all books"):
            filters.pop("brand", None)
            filters.pop("subcategory", None)
            filters.pop("item_type", None)
            filters.pop("price_max_cents", None)
            filters.pop("price_min_cents", None)
            session_manager.update_filters(session_id, filters)
            session_manager.add_message(session_id, "user", request.message)
            recs, labels = await _search_ecommerce_products(filters, category, n_rows=request.n_rows or 2, n_per_row=request.n_per_row or 3)
            if recs:
                session_manager.set_stage(session_id, STAGE_RECOMMENDATIONS)
                all_ids = []
                for row in recs:
                    for item in row:
                        pid = item.get("product_id") or item.get("id") or (item.get("_product") or {}).get("product_id")
                        if pid and pid not in all_ids:
                            all_ids.append(pid)
                session_manager.set_last_recommendations(session_id, all_ids)
                product_label = active_domain
                return ChatResponse(
                    response_type="recommendations",
                    message=f"Here are all {product_label} we have. What would you like to do next?",
                    session_id=session_id,
                    domain=active_domain,
                    recommendations=recs,
                    bucket_labels=labels,
                    filters=filters,
                    preferences={},
                    question_count=session.question_count,
                    quick_replies=["See similar items", "Research", "Compare items", "Help with checkout"],
                )
            # Still no results - show all categories
            session_manager.reset_session(session_id)
            return ChatResponse(
                response_type="question",
                message=f"We don't have any {active_domain} in our catalog yet. Would you like to try another category?",
                session_id=session_id,
                quick_replies=["Vehicles", "Laptops", "Books", "Jewelry", "Accessories", "Clothing", "Beauty"],
                filters={},
                preferences={},
                question_count=0,
                domain=None,
            )

        # All e-commerce domains: run interview flow (laptops, books, jewelry, accessories)
        is_specific, extracted_info = is_specific_query(request.message, filters)
        product_type = (
            "book" if active_domain == "books"
            else "jewelry" if active_domain == "jewelry"
            else "accessory" if active_domain == "accessories"
            else "clothing" if active_domain == "clothing"
            else "beauty" if active_domain == "beauty"
            else str(extracted_info.get("product_type") or "laptop")
        )
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
        if extracted_info.get("genre"):
            filters["genre"] = extracted_info["genre"]
            filters["subcategory"] = extracted_info["genre"]  # Use genre as subcategory
        if extracted_info.get("format"):
            filters["format"] = extracted_info["format"]
        if extracted_info.get("subcategory") or extracted_info.get("item_type"):
            filters["subcategory"] = extracted_info.get("subcategory") or extracted_info.get("item_type")
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
                # Always multiply by 100 - prices stored in cents for ALL domains
                filters["price_min_cents"] = int(min_price * 100)
            if isinstance(max_price, (int, float)):
                # Always multiply by 100 - prices stored in cents for ALL domains
                filters["price_max_cents"] = int(max_price * 100)
        if extracted_info.get("soft_preferences"):
            filters["_soft_preferences"] = extracted_info["soft_preferences"]

        session_manager.update_filters(session_id, filters)
        session_manager.set_stage(session_id, STAGE_INTERVIEW)

        # CRITICAL: Pass product_type to ensure questions match the active domain.
        should_ask, missing_info = should_ask_followup(request.message, filters, product_type)
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
            n_rows=request.n_rows or 2,
            n_per_row=request.n_per_row or 3,
        )

        session_manager.set_stage(session_id, STAGE_RECOMMENDATIONS)
        
        # Handle no results case
        if not recs or len(recs) == 0:
            # Get filter descriptions for helpful message
            filter_desc = []
            if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
                filter_desc.append(f"{filters['brand']} brand")
            if filters.get("price_max_cents"):
                price_max_dollars = filters['price_max_cents'] / 100
                filter_desc.append(f"under ${price_max_dollars:.0f}")
            if filters.get("subcategory"):
                if active_domain in ("jewelry", "accessories", "clothing", "beauty"):
                    filter_desc.append(f"{filters['subcategory'].lower()}")
                else:
                    filter_desc.append(f"{filters['subcategory'].lower()} use")
            
            filter_text = " with " + ", ".join(filter_desc) if filter_desc else ""
            message = f"I couldn't find any {active_domain}{filter_text}. Try adjusting your filters or budget."
            no_results_replies = (
                ["Show me all jewelry", "Increase my budget", "Try a different brand"] if active_domain == "jewelry"
                else ["Show me all accessories", "Increase my budget", "Try a different brand"] if active_domain == "accessories"
                else ["Show me all clothing", "Increase my budget", "Try a different type"] if active_domain == "clothing"
                else ["Show me all beauty", "Increase my budget", "Try a different brand"] if active_domain == "beauty"
                else ["Show me all laptops", "Increase my budget", "Try a different brand"] if active_domain == "laptops"
                else ["Show me all books", "Increase my budget", "Try a different genre"] if active_domain == "books"
                else ["Show me all", "Increase my budget", "Try a different brand"]
            )
            return ChatResponse(
                response_type="question",
                message=message,
                session_id=session_id,
                domain=active_domain,
                quick_replies=no_results_replies,
                filters=filters,
                preferences=filters.get("_soft_preferences", {}),
                question_count=session.question_count,
            )
        
        # Store product IDs for Research/Compare (from last recommendations)
        all_ids = []
        for row in recs:
            for item in row:
                pid = item.get("product_id") or item.get("id") or (item.get("_product") or {}).get("product_id")
                if pid and pid not in all_ids:
                    all_ids.append(pid)
        session_manager.set_last_recommendations(session_id, all_ids)

        # Post-recommendation: keep conversation going with follow-up options
        product_label = (
            "laptops" if active_domain == "laptops"
            else "books" if active_domain == "books"
            else "jewelry" if active_domain == "jewelry"
            else "accessories" if active_domain == "accessories"
            else "clothing" if active_domain == "clothing"
            else "beauty" if active_domain == "beauty"
            else "vehicles"
        )
        return ChatResponse(
            response_type="recommendations",
            message=f"Here are top {product_label} recommendations. What would you like to do next?",
            session_id=session_id,
            domain=active_domain,
            recommendations=recs,
            bucket_labels=labels,
            filters=filters,
            preferences=filters.get("_soft_preferences", {}),
            question_count=session.question_count,
            quick_replies=[
                "See similar items",
                "Research",
                "Compare items",
                "Rate recommendations",
                "Help with checkout",
            ],
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
            quick_replies = idss_data.get("quick_replies")
            # Add post-recommendation follow-ups for vehicles if not present
            if response_type == "recommendations" and not quick_replies:
                quick_replies = ["See similar items", "Anything else?", "Compare items", "Rate recommendations", "Help with checkout"]
            # CRITICAL: Sync MCP session with IDSS response so next message is treated as followup
            # (prevents domain re-detection from "Over $50k" etc. and book questions leaking into vehicle flow)
            session_manager.set_active_domain(session_id, domain)
            if response_type == "question":
                session_manager.add_question_asked(session_id, "idss_question")
            if response_type == "recommendations":
                session_manager.set_stage(session_id, STAGE_RECOMMENDATIONS)

            return ChatResponse(
                response_type=response_type,
                message=idss_data.get("message", ""),
                session_id=idss_data.get("session_id", session_id),
                quick_replies=quick_replies,
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

def _domain_to_category(active_domain: Optional[str]) -> str:
    """Map domain to database category for e-commerce search."""
    if not active_domain:
        return "Electronics"
    m = {
        "laptops": "Electronics",
        "books": "Books",
        "jewelry": "Jewelry",
        "accessories": "Accessories",
        "clothing": "Clothing",
        "beauty": "Beauty",
    }
    return m.get(active_domain, "Electronics")


def _format_product_as_vehicle(product_dict: Dict[str, Any], category: str) -> Dict[str, Any]:
    """
    Format an e-commerce product using the unified product schema.

    New unified format includes:
    - productType: "laptop" | "book" | "vehicle"
    - Common fields: id, name, brand, price, image
    - Type-specific details: laptop{}, book{}, or vehicle{}
    - Legacy compatibility: vehicle{} and retailListing{} for backwards compat
    """
    product_id = product_dict.get("product_id", "")
    name = product_dict.get("name", "")
    description = product_dict.get("description", "")
    brand = product_dict.get("brand", "")
    price = product_dict.get("price", 0)
    image_url = product_dict.get("image_url")

    # Determine product type
    if category == "Electronics":
        product_type = "laptop"
        body_style = "Electronics"
    elif category == "Books":
        product_type = "book"
        body_style = "Books"
    elif category == "Jewelry":
        product_type = "jewelry"
        body_style = "Jewelry"
    elif category == "Accessories":
        product_type = "accessory"
        body_style = "Accessories"
    elif category == "Clothing":
        product_type = "clothing"
        body_style = "Clothing"
    elif category == "Beauty":
        product_type = "beauty"
        body_style = "Beauty"
    else:
        product_type = "generic"
        body_style = category

    # Build unified format
    result = {
        # ===== NEW UNIFIED SCHEMA =====
        # Common fields for all product types
        "id": product_id,
        "productType": product_type,
        "name": name,
        "brand": brand,
        "price": int(price),
        "currency": "USD",
        "description": description,
        "image": {
            "primary": image_url or "",
            "count": 1 if image_url else 0,
            "gallery": []
        },
        "url": "",  # Product detail page URL
        "available": True,

        # ===== LEGACY COMPATIBILITY (for existing frontend) =====
        "@id": product_id,
        "vin": product_id,
        "online": True,

        # Legacy vehicle object (frontend expects this structure)
        "vehicle": {
            "vin": product_id,
            "year": 2024,
            "make": brand,
            "model": name,
            "trim": "",
            "price": int(price),
            "mileage": 0,
            "bodyStyle": body_style,
            "drivetrain": product_type.capitalize(),
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
            "description": description,
            "category": category,
        },

        # Legacy retailListing (frontend expects this for images/price)
        "retailListing": {
            "price": int(price),
            "miles": 0,
            "dealer": brand,
            "city": "",
            "state": "",
            "zip": "",
            "vdp": "",
            "carfaxUrl": "",
            "primaryImage": image_url or "",
            "photoCount": 1 if image_url else 0,
            "used": False,
            "cpo": False,
        },

        # Keep original product data
        "_product": product_dict,
    }

    # Add type-specific details
    if product_type == "laptop":
        result["laptop"] = {
            "productType": product_dict.get("product_type", "laptop"),
            "specs": {
                "processor": "",
                "ram": "",
                "storage": "",
                "display": "",
                "graphics": product_dict.get("gpu_model", "")
            },
            "gpuVendor": product_dict.get("gpu_vendor", ""),
            "gpuModel": product_dict.get("gpu_model", ""),
            "color": product_dict.get("color", ""),
            "tags": product_dict.get("tags", []) or []
        }
    elif product_type == "book":
        result["book"] = {
            "author": "",  # Would need parsing from description
            "genre": product_dict.get("subcategory", ""),
            "format": "",
            "pages": None,
            "isbn": "",
            "publisher": "",
            "language": "English"
        }

    return result


def _fetch_products_by_ids(product_ids: List[str]) -> List[Dict[str, Any]]:
    """Fetch product dicts by IDs (same format as _search_ecommerce_products)."""
    if not product_ids:
        return []
    from app.database import SessionLocal
    from app.models import Product, Price, Inventory
    db = SessionLocal()
    try:
        products = db.query(Product).filter(Product.product_id.in_(product_ids)).all()
        id_order = {pid: i for i, pid in enumerate(product_ids)}
        products = sorted(products, key=lambda p: id_order.get(p.product_id, 999))
        result = []
        for product in products:
            price = db.query(Price).filter(Price.product_id == product.product_id).first()
            inv = db.query(Inventory).filter(Inventory.product_id == product.product_id).first()
            p_dict = {
                "id": product.product_id,
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "category": product.category,
                "subcategory": product.subcategory,
                "brand": product.brand,
                "price": round((price.price_cents / 100), 2) if price else 0,
                "price_cents": price.price_cents if price else 0,
                "image_url": getattr(product, "image_url", None),
                "product_type": product.product_type,
                "gpu_vendor": product.gpu_vendor,
                "gpu_model": product.gpu_model,
                "color": product.color,
                "tags": product.tags,
                "reviews": product.reviews,
                "available_qty": inv.available_qty if inv else 0,
            }
            result.append(p_dict)
        return result
    finally:
        db.close()


def _build_kg_search_query(filters: Dict[str, Any], category: str) -> str:
    """Build a search query string for KG from filters (e.g. 'gaming laptop Dell')."""
    parts = []
    if filters.get("subcategory"):
        parts.append(str(filters["subcategory"]))
    if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
        parts.append(str(filters["brand"]))
    if category == "Electronics":
        parts.append("laptop")
    elif category == "Books":
        parts.append("book")
    elif category == "Jewelry":
        parts.append("jewelry")
    elif category == "Accessories":
        parts.append("accessory")
    elif category == "Clothing":
        parts.append("clothing")
    elif category == "Beauty":
        parts.append("beauty")
    return " ".join(parts) if parts else ""


async def _search_ecommerce_products(
    filters: Dict[str, Any],
    category: str,
    n_rows: int = 3,
    n_per_row: int = 3,
    idss_preferences: Optional[Dict[str, Any]] = None,
    exclude_ids: Optional[List[str]] = None,
) -> tuple[List[List[Dict[str, Any]]], List[str]]:
    """
    Search e-commerce products from PostgreSQL database.
    Uses Neo4j knowledge graph when available to prioritize KG-ranked candidates.

    Returns products formatted to match IDSS vehicle structure for frontend compatibility.
    """
    from app.database import SessionLocal
    from app.models import Product, Price, Inventory
    from app.formatters import format_product

    logger.info("search_ecommerce_start", "Searching products", {
        "category": category,
        "filters": filters,
        "n_rows": n_rows,
        "n_per_row": n_per_row,
    })

    # Knowledge graph: get candidate IDs when available (prioritize KG-ranked products)
    kg_candidate_ids: List[str] = []
    try:
        from app.kg_service import get_kg_service
        kg = get_kg_service()
        if kg.is_available():
            kg_filters = {**filters, "category": category}
            search_query = _build_kg_search_query(filters, category)
            kg_candidate_ids, _ = kg.search_candidates(
                query=search_query,
                filters=kg_filters,
                limit=n_rows * n_per_row * 3,
            )
            if kg_candidate_ids:
                if exclude_ids:
                    exclude_set = set(exclude_ids)
                    kg_candidate_ids = [pid for pid in kg_candidate_ids if pid not in exclude_set]
                if kg_candidate_ids:
                    logger.info("kg_candidates_used", f"KG returned {len(kg_candidate_ids)} candidates", {"count": len(kg_candidate_ids)})
    except Exception as e:
        logger.warning("kg_search_skipped", f"KG search skipped: {e}", {"error": str(e)})

    db = SessionLocal()
    try:
        # Build query
        query = db.query(Product).filter(Product.category == category)

        # Exclude already-shown products (for "Show more like these" to return different items)
        if exclude_ids:
            query = query.filter(~Product.product_id.in_(exclude_ids))
            logger.info("search_exclude_ids", f"Excluding {len(exclude_ids)} already-shown products", {"exclude_count": len(exclude_ids)})

        # Debug: count products in category
        category_count = db.query(Product).filter(Product.category == category).count()
        logger.info("search_category_count", f"Products in category {category}: {category_count}", {})

        # First try: full filters
        query = db.query(Product).filter(Product.category == category)
        if exclude_ids:
            query = query.filter(~Product.product_id.in_(exclude_ids))
        if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
            query = query.filter(Product.brand == filters["brand"])
        if filters.get("subcategory"):
            query = query.filter(Product.subcategory == filters["subcategory"])
        if filters.get("product_type") and category not in ("Jewelry", "Accessories", "Clothing", "Beauty"):
            query = query.filter(Product.product_type == filters["product_type"])
        if filters.get("color"):
            query = query.filter(Product.color == filters["color"])
        if filters.get("gpu_vendor"):
            query = query.filter(Product.gpu_vendor == filters["gpu_vendor"])
        query = query.join(Price, Product.product_id == Price.product_id, isouter=True)
        if filters.get("price_min_cents"):
            query = query.filter(Price.price_cents >= filters["price_min_cents"])
        if filters.get("price_max_cents"):
            query = query.filter(Price.price_cents <= filters["price_max_cents"])
        query = query.order_by(Price.price_cents.asc())
        products = query.limit(n_rows * n_per_row * 2).all()

        # Fallback: relax price_min (e.g. user said $50-$100 but products are $20-$40)
        if not products and filters.get("price_min_cents") and category in ("Beauty", "Jewelry", "Accessories", "Clothing", "Books"):
            logger.info("search_relax_price_min", "No results with price_min, trying without", {"price_min": filters["price_min_cents"]})
            query = db.query(Product).filter(Product.category == category)
            if exclude_ids:
                query = query.filter(~Product.product_id.in_(exclude_ids))
            if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
                query = query.filter(Product.brand == filters["brand"])
            if filters.get("subcategory"):
                query = query.filter(Product.subcategory == filters["subcategory"])
            if filters.get("color"):
                query = query.filter(Product.color == filters["color"])
            query = query.join(Price, Product.product_id == Price.product_id, isouter=True)
            if filters.get("price_max_cents"):
                query = query.filter(Price.price_cents <= filters["price_max_cents"])
            query = query.order_by(Price.price_cents.asc())
            products = query.limit(n_rows * n_per_row * 2).all()

        # Fallback: relax subcategory (e.g. no Levi's Shirts & Blouses, but we have other Levi's)
        if not products and filters.get("subcategory") and category in ("Beauty", "Jewelry", "Accessories", "Clothing"):
            logger.info("search_relax_subcategory", "No results with subcategory, trying category+brand only", {"subcategory": filters["subcategory"]})
            query = db.query(Product).filter(Product.category == category)
            if exclude_ids:
                query = query.filter(~Product.product_id.in_(exclude_ids))
            if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
                query = query.filter(Product.brand == filters["brand"])
            query = query.join(Price, Product.product_id == Price.product_id, isouter=True)
            if filters.get("price_max_cents"):
                query = query.filter(Price.price_cents <= filters["price_max_cents"])
            if filters.get("price_min_cents"):
                query = query.filter(Price.price_cents >= filters["price_min_cents"])
            query = query.order_by(Price.price_cents.asc())
            products = query.limit(n_rows * n_per_row * 2).all()

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

            # Get inventory
            inventory = db.query(Inventory).filter(Inventory.product_id == product.product_id).first()
            available_qty = inventory.available_qty if inventory else 0

            # Get reviews from product.reviews field (it's a text field, not a separate table)
            reviews_text = product.reviews

            # Infer format from tags for books
            format_value = None
            if product.product_type == "book" and product.tags:
                for tag in product.tags:
                    tag_lower = tag.lower()
                    if "hardcover" in tag_lower or "hardback" in tag_lower:
                        format_value = "Hardcover"
                        break
                    elif "paperback" in tag_lower or "softcover" in tag_lower:
                        format_value = "Paperback"
                        break
                    elif "ebook" in tag_lower or "e-book" in tag_lower:
                        format_value = "E-book"
                        break
                    elif "audiobook" in tag_lower:
                        format_value = "Audiobook"
                        break

            product_dict = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "category": product.category,
                "subcategory": product.subcategory, # Genre/Subcategory
                "brand": product.brand,  # For books, this is the author
                "price": price_cents / 100,  # Convert to dollars for display
                "price_cents": price_cents,
                "image_url": getattr(product, 'image_url', None),
                # Extended fields for formatters
                "product_type": product.product_type,
                "gpu_vendor": product.gpu_vendor,
                "gpu_model": product.gpu_model,
                "color": product.color,
                "tags": product.tags,
                # Additional fields
                "reviews": reviews_text,
                "available_qty": available_qty,
                # Book-specific fields
                "format": format_value,
                "author": product.brand if product.product_type == "book" else None,
                "genre": product.subcategory if product.product_type == "book" else None,
            }
            product_dicts.append(product_dict)

        # Bucket into rows by price
        if len(product_dicts) == 0:
            return [], []

        # Reorder: KG candidates first (in KG order), then by price
        if kg_candidate_ids:
            kg_id_to_idx = {pid: i for i, pid in enumerate(kg_candidate_ids)}
            def sort_key(p):
                pid = p.get("product_id")
                if pid in kg_id_to_idx:
                    return (0, kg_id_to_idx[pid])
                return (1, float(p.get("price_cents", 0) or 0))
            product_dicts.sort(key=sort_key)
        else:
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
                # Convert products to unified format using helper
                # We determine domain based on category for the formatter
                fmt_domain = (
                    "laptops" if category == "Electronics"
                    else "books" if category == "Books"
                    else "jewelry" if category == "Jewelry"
                    else "accessories" if category == "Accessories"
                    else "laptops"
                )
                
                formatted_bucket = [
                    format_product(p, fmt_domain).model_dump(mode='json', exclude_none=True)
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
