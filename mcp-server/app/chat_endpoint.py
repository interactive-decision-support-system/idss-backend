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
from app.domain_registry import get_domain_schema, DOMAIN_REGISTRY
from app.structured_logger import StructuredLogger
from app.complex_query import is_complex_query
from app.universal_agent import UniversalAgent

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

# Richer KG / complex-query: known agent slot names that map to search filter keys
_AGENT_BUDGET_SLOT = "budget"
_AGENT_USE_CASE_SLOT = "use_case"
_AGENT_GENRE_SLOT = "genre"


def _domain_to_category_for_agent(domain: Optional[str]) -> str:
    """Map domain to category string for search (same logic as _domain_to_category, for use in agent filter mapping)."""
    if not domain:
        return "Electronics"
    m = {"vehicles": "Vehicles", "laptops": "Electronics", "books": "Books", "phones": "Electronics"}
    return m.get(domain, "Electronics")


def _agent_filters_to_search_filters(domain: str, agent_filters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map UniversalAgent slot-based filters to MCP search filters (category, price_max_cents, brand, subcategory, etc.).
    Used when is_complex_query → UniversalAgent → recommendations_ready; we need to run _search_ecommerce_products.
    """
    import re
    filters: Dict[str, Any] = {}
    category = _domain_to_category_for_agent(domain)
    if category:
        filters["category"] = category
    if not agent_filters:
        return filters
    schema = get_domain_schema(domain)
    for slot_name, value in agent_filters.items():
        if not value or not isinstance(value, str):
            continue
        value = value.strip()
        if slot_name == _AGENT_BUDGET_SLOT:
            # Parse "$500", "$1000-$2000", "Under $700", "Over $2000"
            value_clean = value.replace(",", "")
            numbers = re.findall(r"\$?\s*(\d+)", value_clean)
            if numbers:
                nums = [int(n) for n in numbers]
                if nums:
                    if "under" in value.lower() or "max" in value.lower() or len(nums) == 1:
                        filters["price_max_cents"] = max(nums) * 100
                    elif "over" in value.lower() or "min" in value.lower():
                        filters["price_min_cents"] = min(nums) * 100
                    else:
                        filters["price_min_cents"] = min(nums) * 100
                        filters["price_max_cents"] = max(nums) * 100
        elif slot_name == _AGENT_USE_CASE_SLOT:
            filters["subcategory"] = value
            filters["use_case"] = value
        elif slot_name == _AGENT_GENRE_SLOT:
            filters["genre"] = value
            filters["subcategory"] = value
        elif slot_name == "brand":
            filters["brand"] = value
        elif slot_name == "format":
            filters["format"] = value
        elif slot_name == "good_for_ml":
            filters["good_for_ml"] = value.lower() in ("yes", "true", "1", "y", "need it", "need")
        elif slot_name == "good_for_gaming":
            filters["good_for_gaming"] = value.lower() in ("yes", "true", "1", "y", "gaming")
        elif slot_name == "good_for_web_dev":
            filters["good_for_web_dev"] = value.lower() in ("yes", "true", "1", "y", "web dev", "coding")
        elif slot_name == "good_for_creative":
            filters["good_for_creative"] = value.lower() in ("yes", "true", "1", "y", "creative", "video", "photo")
        elif slot_name == "battery_life":
            # "8+ hours", "10+ hours", "6+ hours" -> battery_life_min_hours
            nums = re.findall(r"\d+", value)
            if nums:
                filters["battery_life_min_hours"] = int(nums[0])
        elif slot_name in ("body_style", "fuel_type", "condition"):
            # Vehicles: pass through if we ever support complex→search for vehicles
            filters[slot_name] = value
        elif slot_name in ("os", "screen_size", "item_type", "material", "color"):
            if slot_name == "item_type":
                filters["subcategory"] = value
            else:
                filters[slot_name] = value
        elif schema:
            slot = next((s for s in schema.slots if s.name == slot_name), None)
            if slot and slot.filter_key:
                filters[slot.filter_key] = value
    return filters


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

    # CRITICAL: "Show me all X" / "phones" / "laptops" / "books" - handle FIRST, before validation or any other logic
    # This catches clicks on no-results quick replies and simple domain selection
    _show_all = " ".join(request.message.strip().lower().split()).rstrip(".!?")
    if _show_all in ("show me all laptops", "show me all books", "show me all phones", "phones", "phone", "laptops", "books"):
        active_domain = "laptops" if "laptop" in _show_all else "books" if "book" in _show_all else "phones" if ("phone" in _show_all or _show_all == "phones") else "laptops"
        category = _domain_to_category(active_domain)
        filters = {"category": category}
        if active_domain == "laptops":
            filters["product_type"] = "laptop"
        elif active_domain == "phones":
            filters["product_type"] = "phone"
        session_manager.set_active_domain(session_id, active_domain)
        session_manager.update_filters(session_id, filters)
        session_manager.add_message(session_id, "user", request.message)
        recs, labels = await _search_ecommerce_products(
            filters, category, n_rows=request.n_rows or 2, n_per_row=request.n_per_row or 3
        )
        if recs:
            session_manager.set_stage(session_id, STAGE_RECOMMENDATIONS)
            all_ids = []
            for row in recs:
                for item in row:
                    pid = item.get("product_id") or item.get("id") or (item.get("_product") or {}).get("product_id")
                    if pid and pid not in all_ids:
                        all_ids.append(pid)
            session_manager.set_last_recommendations(session_id, all_ids)
            product_label = "laptops" if active_domain == "laptops" else "books" if active_domain == "books" else "phones"
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
        session_manager.reset_session(session_id)
        return ChatResponse(
            response_type="question",
            message=f"We don't have any {active_domain} in our catalog yet. Would you like to try another category?",
            session_id=session_id,
            quick_replies=["Cars", "Laptops", "Books", "Phones"],
            filters={},
            preferences={},
            question_count=0,
            domain=None,
        )
    
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
    
    # Fast bypass: exact domain quick-reply options are always valid (e.g. user clicked "Laptops")
    _domain_options = ["vehicles", "laptops", "books", "phones", "cars", "car"]
    msg_stripped = request.message.strip()
    msg_lower = msg_stripped.lower()
    # Normalize dashes (unicode en-dash/em-dash -> hyphen) so "Self–Help" matches "self-help"
    msg_lower = msg_lower.replace("\u2013", "-").replace("\u2014", "-")
    # Universal: accept any quick reply from any domain schema (Self-Help, Fiction, Non-Fiction, etc.)
    _is_quick_reply = False
    for schema in DOMAIN_REGISTRY.values():
        for slot in schema.slots:
            for r in (slot.example_replies or []):
                if r and msg_lower == (r or "").lower().strip().replace("\u2013", "-").replace("\u2014", "-"):
                    _is_quick_reply = True
                    validation_result = {
                        "is_valid": True,
                        "corrected_input": msg_stripped,
                        "confidence": 1.0,
                        "detected_intent": "filter_response",
                        "suggestions": [],
                        "error_message": None,
                    }
                    break
            if _is_quick_reply:
                break
        if _is_quick_reply:
            break
    if _is_quick_reply:
        pass  # validation_result already set above
    elif msg_lower in _domain_options:
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
            "show me all phones",
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
        _accepted = False
        # First: accept any example_reply from domain schema (guarantees quick replies like "Self-Help" work)
        schema = get_domain_schema(session.active_domain)
        if schema:
            for slot in schema.slots:
                for r in (slot.example_replies or []):
                    if r:
                        r_norm = (r or "").lower().strip().replace("\u2013", "-").replace("\u2014", "-")
                        if msg_lower == r_norm:
                            _accepted = True
                            break
                if _accepted:
                    break
        if not _accepted:
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
                "gaming", "work", "school", "creative", "apple", "dell", "lenovo", "hp",
                "fiction", "mystery", "sci-fi", "non-fiction", "nonfiction", "self-help", "selfhelp",
                "hardcover", "paperback", "e-book", "audiobook",
                "show me all phones",
                "show me all laptops", "show me all books", "increase my budget", "try a different brand", "try a different type",
                "linux", "ubuntu", "macos", "windows", "chromeos", "no preference",
            ]
            _msg_normalized = " ".join(msg_lower.split())
            _accepted = any(ans in _msg_normalized or _msg_normalized == ans for ans in _interview_answers)
        if _accepted:
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
            "gaming", "work", "school", "creative", "apple", "dell", "lenovo", "hp",
            "fiction", "mystery", "sci-fi", "non-fiction", "nonfiction", "self-help", "selfhelp",
            "hardcover", "paperback", "e-book", "audiobook",
            "show me all phones",
            "show me all laptops", "show me all books", "increase my budget", "try a different brand", "try a different type",
            "linux", "ubuntu", "macos", "windows", "chromeos", "no preference",
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
        suggestions = validation_result["suggestions"] or ["Cars", "Laptops", "Books", "Phones"]
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
        # "Show me all X" - clear filters and show everything in category (handles no-results recovery)
        if msg_lower in ("show me all laptops", "show me all books", "show me all phones"):
            session_manager.add_message(session_id, "user", msg)
            active_domain = "laptops" if "laptop" in msg_lower else "books" if "book" in msg_lower else "phones"
            category = _domain_to_category(active_domain)
            filters = {"category": category}
            if active_domain == "laptops":
                filters["product_type"] = "laptop"
            elif active_domain == "phones":
                filters["product_type"] = "phone"
            session_manager.set_active_domain(session_id, active_domain)
            session_manager.update_filters(session_id, filters)
            recs, labels = await _search_ecommerce_products(
                filters, category, n_rows=request.n_rows or 2, n_per_row=request.n_per_row or 3
            )
            if recs:
                all_ids = []
                for row in recs:
                    for item in row:
                        pid = item.get("product_id") or item.get("id") or (item.get("_product") or {}).get("product_id")
                        if pid and pid not in all_ids:
                            all_ids.append(pid)
                session_manager.set_last_recommendations(session_id, all_ids)
                product_label = "laptops" if active_domain == "laptops" else "books" if active_domain == "books" else "phones"
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
            session_manager.reset_session(session_id)
            return ChatResponse(
                response_type="question",
                message=f"We don't have any {active_domain} in our catalog yet. Would you like to try another category?",
                session_id=session_id,
                quick_replies=["Cars", "Laptops", "Books", "Phones"],
                filters={},
                preferences={},
                question_count=0,
                domain=None,
            )
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
            if session.active_domain in ("laptops", "books", "phones"):
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
                quick_replies=["Cars", "Laptops", "Books", "Phones"],
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
                quick_replies=["Cars", "Laptops", "Books", "Phones"],
                filters={},
                preferences={},
                question_count=0,
                domain=None,
            )
        if "show more like these" in msg_lower:
            session_manager.add_message(session_id, "user", msg)
            if session.active_domain in ("laptops", "books", "phones"):
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
                    try:
                        products = _fetch_products_by_ids(product_ids)
                        if products:
                            comparison = build_comparison_table(products)
                            # Format products same as search (image.primary, retailListing, price) for frontend
                            first_ptype = (products[0].get("product_type") or "").lower() if products else ""
                            fmt_domain = (
                                "books" if category == "Books"
                                else "phones" if category == "Electronics" and first_ptype in ("phone", "smartphone")
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
                    except Exception as e:
                        logger.error("compare_items_error", f"Compare items failed: {e}", {"error": str(e), "product_ids": product_ids})
                        return ChatResponse(
                            response_type="question",
                            message="I had trouble comparing those items. Please try selecting 2–4 products again and click Compare items.",
                            session_id=session_id,
                            quick_replies=["See similar items", "Compare by price", "Help with checkout"],
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
            quick_replies=["Cars", "Laptops", "Books", "Phones"],
            filters={},
            preferences={},
            question_count=0,
            domain=None,
        )

    # "Show me all X" / "phones" / "laptops" / "books" - ALWAYS bypass complex path; show all products in category
    msg_lower_early = request.message.strip().lower()
    if msg_lower_early in ("show me all laptops", "show me all books", "show me all phones", "phones", "phone", "laptops", "books"):
        active_domain = "laptops" if "laptop" in msg_lower_early else "books" if "book" in msg_lower_early else "phones" if ("phone" in msg_lower_early or msg_lower_early == "phones") else "laptops"
        category = _domain_to_category(active_domain)
        filters = {"category": category}
        if active_domain == "laptops":
            filters["product_type"] = "laptop"
        elif active_domain == "phones":
            filters["product_type"] = "phone"
        session_manager.set_active_domain(session_id, active_domain)
        session_manager.update_filters(session_id, filters)
        session_manager.add_message(session_id, "user", request.message)
        recs, labels = await _search_ecommerce_products(
            filters, category, n_rows=request.n_rows or 2, n_per_row=request.n_per_row or 3
        )
        if recs:
            session_manager.set_stage(session_id, STAGE_RECOMMENDATIONS)
            all_ids = []
            for row in recs:
                for item in row:
                    pid = item.get("product_id") or item.get("id") or (item.get("_product") or {}).get("product_id")
                    if pid and pid not in all_ids:
                        all_ids.append(pid)
            session_manager.set_last_recommendations(session_id, all_ids)
            product_label = "laptops" if active_domain == "laptops" else "books" if active_domain == "books" else "phones"
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
        session_manager.reset_session(session_id)
        return ChatResponse(
            response_type="question",
            message=f"We don't have any {active_domain} in our catalog yet. Would you like to try another category?",
            session_id=session_id,
            quick_replies=["Cars", "Laptops", "Books", "Phones"],
            filters={},
            preferences={},
            question_count=0,
            domain=None,
        )

    # Complex-query branch (§8): when message is complex, use UniversalAgent then MCP search with its filters
    if is_complex_query(request.message, session.explicit_filters):
        session = session_manager.get_session(session_id)
        history = getattr(session, "conversation_history", None) or []
        agent = active_agents.get(session_id)
        if agent is None:
            agent = UniversalAgent(session_id=session_id, history=history)
            active_agents[session_id] = agent
        else:
            agent.history = history
        try:
            agent_response = agent.process_message(request.message)
        except Exception as e:
            logger.warning("complex_query_agent_error", f"UniversalAgent failed: {e}", {"error": str(e)})
            agent_response = None
        if agent_response:
            response_type = agent_response.get("response_type") or "question"
            session_manager.add_message(session_id, "user", request.message)
            if response_type == "recommendations_ready":
                domain = agent_response.get("domain")
                filters_agent = agent_response.get("filters") or {}
                if domain and domain in ("laptops", "books", "phones"):
                    search_filters = _agent_filters_to_search_filters(domain, filters_agent)
                    search_filters["product_type"] = "laptop" if domain == "laptops" else "book" if domain == "books" else "phone"
                    category = _domain_to_category_for_agent(domain)
                    session_manager.set_active_domain(session_id, domain)
                    session_manager.update_filters(session_id, search_filters)
                    session_manager.set_stage(session_id, STAGE_RECOMMENDATIONS)
                    recs, labels = await _search_ecommerce_products(
                        search_filters,
                        category,
                        n_rows=request.n_rows or 2,
                        n_per_row=request.n_per_row or 3,
                    )
                    if recs:
                        all_ids = []
                        for row in recs:
                            for item in row:
                                pid = item.get("product_id") or item.get("id") or (item.get("_product") or {}).get("product_id")
                                if pid and pid not in all_ids:
                                    all_ids.append(pid)
                        session_manager.set_last_recommendations(session_id, all_ids)
                        session_manager.add_message(session_id, "assistant", agent_response.get("message", "Here are some options."))
                        product_label = "laptops" if domain == "laptops" else "books" if domain == "books" else "phones"
                        return ChatResponse(
                            response_type="recommendations",
                            message=agent_response.get("message") or f"Here are {product_label} that match your criteria.",
                            session_id=session_id,
                            domain=domain,
                            recommendations=recs,
                            bucket_labels=labels or [],
                            filters=search_filters,
                            preferences={},
                            question_count=agent_response.get("question_count", 0),
                            quick_replies=["See similar items", "Research", "Compare items", "Help with checkout"],
                        )
                    # No results: fall through to show a question or no-results message
                    session_manager.add_message(session_id, "assistant", "I couldn't find exact matches. Here are some options to consider.")
                    recs, labels = await _search_ecommerce_products(
                        {"category": category, "product_type": search_filters.get("product_type", "laptop")},
                        category,
                        n_rows=request.n_rows or 2,
                        n_per_row=request.n_per_row or 3,
                    )
                    if recs:
                        all_ids = []
                        for row in recs:
                            for item in row:
                                pid = item.get("product_id") or item.get("id")
                                if pid and pid not in all_ids:
                                    all_ids.append(pid)
                        session_manager.set_last_recommendations(session_id, all_ids)
                        return ChatResponse(
                            response_type="recommendations",
                            message="I couldn't find an exact match; here are some options you might like.",
                            session_id=session_id,
                            domain=domain,
                            recommendations=recs,
                            bucket_labels=labels or [],
                            filters=search_filters,
                            preferences={},
                            question_count=agent_response.get("question_count", 0),
                            quick_replies=["See similar items", "Research", "Compare items"],
                        )
                    # No results even with relaxed search
                    session_manager.add_message(session_id, "assistant", "I couldn't find matching options. Try broadening your criteria.")
                    domain_quick = (
                        ["Show me all laptops", "Show me all books", "Show me all phones", "Different category"]
                        if domain in ("laptops", "books", "phones")
                        else ["Show me all laptops", "Show me all books", "Different category"]
                    )
                    msg = (
                        "I couldn't find any options matching those criteria. Would you like to try a different budget, brand, or use case?"
                        if domain == "laptops"
                        else "I couldn't find any options matching those criteria. Would you like to try a different budget or brand?"
                        if domain == "phones"
                        else "I couldn't find any options matching those criteria. Would you like to try a different genre or budget?"
                        if domain == "books"
                        else "I couldn't find any options matching those criteria. Would you like to try a different budget, brand, or use case?"
                    )
                    return ChatResponse(
                        response_type="question",
                        message=msg,
                        session_id=session_id,
                        domain=domain,
                        quick_replies=domain_quick,
                        filters=search_filters,
                        preferences={},
                        question_count=agent_response.get("question_count", 0),
                    )
                # Vehicles or no laptops/books: fall through to normal detect_domain flow
            if response_type == "question":
                session_manager.add_message(session_id, "assistant", agent_response.get("message", ""))
                session_manager.set_active_domain(session_id, agent_response.get("domain") or session.active_domain or "laptops")
                return ChatResponse(
                    response_type="question",
                    message=agent_response.get("message", ""),
                    session_id=session_id,
                    quick_replies=agent_response.get("quick_replies"),
                    filters=agent_response.get("filters", session.explicit_filters),
                    preferences={},
                    question_count=agent_response.get("question_count", session.question_count),
                    domain=agent_response.get("domain") or session.active_domain,
                )
            if response_type == "error":
                return ChatResponse(
                    response_type="question",
                    message=agent_response.get("message", "Something went wrong. Please try again."),
                    session_id=session_id,
                    quick_replies=["Laptops", "Books", "Vehicles"],
                    filters=session.explicit_filters,
                    preferences={},
                    question_count=session.question_count,
                    domain=session.active_domain,
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
            quick_replies=["Cars", "Laptops", "Books", "Phones"],
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=None,
        )

    active_domain = detected_domain.value if detected_domain != Domain.NONE else session.active_domain or "vehicles"

    if active_domain in ["laptops", "books", "phones"]:
        category = _domain_to_category(active_domain)
        filters = dict(session.explicit_filters)
        filters["category"] = category

        # "Show me all X" - clear restrictive filters and show everything in category
        if msg_lower in ("show me all phones", "show me all laptops", "show me all books"):
            filters.pop("brand", None)
            filters.pop("subcategory", None)
            filters.pop("item_type", None)
            filters.pop("price_max_cents", None)
            filters.pop("price_min_cents", None)
            # Set product_type so we get laptops (not phones) or phones (not laptops)
            if active_domain == "laptops":
                filters["product_type"] = "laptop"
            elif active_domain == "phones":
                filters["product_type"] = "phone"
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
                quick_replies=["Cars", "Laptops", "Books", "Phones"],
                filters={},
                preferences={},
                question_count=0,
                domain=None,
            )

        # All e-commerce domains: run interview flow (laptops, books, phones)
        is_specific, extracted_info = is_specific_query(request.message, filters)
        product_type = (
            "book" if active_domain == "books"
            else "phone" if active_domain == "phones"
            else "laptop" if active_domain == "laptops"
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
        # OS quick reply (Linux, Ubuntu, macOS, Windows, ChromeOS) - map to filters
        if active_domain == "laptops":
            _os_msg = request.message.strip().lower()
            if _os_msg in ("linux", "ubuntu", "macos", "windows", "chromeos", "no preference"):
                filters["os"] = _os_msg.title() if _os_msg != "no preference" else "No preference"
                if _os_msg in ("linux", "ubuntu"):
                    filters["good_for_linux"] = True
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
                filter_desc.append(f"{filters['subcategory'].lower()} use")
            
            filter_text = " with " + ", ".join(filter_desc) if filter_desc else ""
            message = f"I couldn't find any {active_domain}{filter_text}. Try adjusting your filters or budget."
            no_results_replies = (
                ["Show me all laptops", "Different category", "Try a different brand"] if active_domain == "laptops"
                else ["Show me all books", "Different category", "Try a different genre"] if active_domain == "books"
                else ["Show me all phones", "Different category", "Try a different brand"] if active_domain == "phones"
                else ["Cars", "Laptops", "Books", "Phones"]
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
            else "phones" if active_domain == "phones"
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
            # CRITICAL: Always set active_domain (and stage) for vehicles so the next message stays in the car flow.
            # Previously we only set these on recommendations, so follow-up answers (e.g. "$35k-$50k") were routed to laptops.
            session_manager.set_active_domain(session_id, domain)
            session_manager.update_filters(session_id, {"category": "Vehicles"})  # So detect_domain doesn't use stale Electronics
            if response_type == "recommendations":
                session_manager.set_stage(session_id, STAGE_RECOMMENDATIONS)
            else:
                session_manager.set_stage(session_id, STAGE_INTERVIEW)

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
        "phones": "Electronics",  # Phones in Electronics with product_type=phone
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

    # Determine product type (only: laptops, books, phones - real scraped)
    pt = (product_dict.get("product_type") or "").lower()
    if category == "Electronics":
        product_type = "phone" if pt in ("phone", "smartphone") else "laptop"
        body_style = "Electronics"
    elif category == "Books":
        product_type = "book"
        body_style = "Books"
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
        # Real scraped products only (exclude Seed/fake) - must have scraped_from_url
        query = db.query(Product).filter(Product.category == category)
        query = query.filter(
            Product.scraped_from_url.isnot(None),
            ~Product.scraped_from_url.ilike("%test%"),
            ~Product.scraped_from_url.ilike("%example.com%"),
        )

        # Exclude already-shown products (for "Show more like these" to return different items)
        if exclude_ids:
            query = query.filter(~Product.product_id.in_(exclude_ids))
            logger.info("search_exclude_ids", f"Excluding {len(exclude_ids)} already-shown products", {"exclude_count": len(exclude_ids)})

        # Debug: count products in category
        category_count = db.query(Product).filter(Product.category == category).count()
        logger.info("search_category_count", f"Products in category {category}: {category_count}", {})

        # First try: full filters (real scraped only)
        query = db.query(Product).filter(Product.category == category)
        query = query.filter(Product.scraped_from_url.isnot(None), ~Product.scraped_from_url.ilike("%test%"), ~Product.scraped_from_url.ilike("%example.com%"))
        if exclude_ids:
            query = query.filter(~Product.product_id.in_(exclude_ids))
        if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
            query = query.filter(Product.brand == filters["brand"])
        if filters.get("subcategory"):
            query = query.filter(Product.subcategory == filters["subcategory"])
        if filters.get("product_type"):
            pt = filters["product_type"]
            if pt == "laptop":
                query = query.filter(Product.product_type.in_(["laptop", "gaming_laptop"]))
            elif pt in ("phone", "smartphone"):
                query = query.filter(Product.product_type.in_(["phone", "smartphone"]))
            else:
                query = query.filter(Product.product_type == pt)
        if filters.get("color"):
            query = query.filter(Product.color == filters["color"])
        if filters.get("gpu_vendor"):
            query = query.filter(Product.gpu_vendor == filters["gpu_vendor"])
        # Richer KG (§7): filter by kg_features when present (good_for_ml, good_for_gaming, battery_life_min, etc.)
        kg_col = getattr(Product, "kg_features", None)
        if kg_col is not None:
            try:
                if filters.get("good_for_ml"):
                    query = query.filter(kg_col["good_for_ml"].astext == "true")
                if filters.get("good_for_gaming"):
                    query = query.filter(kg_col["good_for_gaming"].astext == "true")
                if filters.get("good_for_web_dev"):
                    query = query.filter(kg_col["good_for_web_dev"].astext == "true")
                if filters.get("good_for_creative"):
                    query = query.filter(kg_col["good_for_creative"].astext == "true")
                if filters.get("good_for_linux"):
                    query = query.filter(kg_col["good_for_linux"].astext == "true")
                if filters.get("repairable"):
                    query = query.filter(kg_col["repairable"].astext == "true")
                if filters.get("refurbished"):
                    query = query.filter(kg_col["refurbished"].astext == "true")
                if filters.get("battery_life_min_hours") is not None:
                    from sqlalchemy import cast, Integer
                    min_hrs = int(filters["battery_life_min_hours"])
                    query = query.filter(cast(kg_col["battery_life_hours"].astext, Integer) >= min_hrs)
            except Exception:
                pass  # SQLite or missing JSON operators: skip kg_features filters
        query = query.join(Price, Product.product_id == Price.product_id, isouter=True)
        if filters.get("price_min_cents"):
            query = query.filter(Price.price_cents >= filters["price_min_cents"])
        if filters.get("price_max_cents"):
            query = query.filter(Price.price_cents <= filters["price_max_cents"])
        query = query.order_by(Price.price_cents.asc())
        products = query.limit(n_rows * n_per_row * 2).all()

        # Fallback: relax price_min (e.g. user said $50-$100 but products are $20-$40)
        # Keep product_type so phones never fall back to laptops
        if not products and filters.get("price_min_cents") and category in ("Books", "Electronics"):
            logger.info("search_relax_price_min", "No results with price_min, trying without", {"price_min": filters["price_min_cents"]})
            query = db.query(Product).filter(Product.category == category)
            query = query.filter(Product.scraped_from_url.isnot(None), ~Product.scraped_from_url.ilike("%test%"), ~Product.scraped_from_url.ilike("%example.com%"))
            if exclude_ids:
                query = query.filter(~Product.product_id.in_(exclude_ids))
            if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
                query = query.filter(Product.brand == filters["brand"])
            if filters.get("subcategory"):
                query = query.filter(Product.subcategory == filters["subcategory"])
            if filters.get("product_type"):
                pt = filters["product_type"]
                if pt == "laptop":
                    query = query.filter(Product.product_type.in_(["laptop", "gaming_laptop"]))
                elif pt in ("phone", "smartphone"):
                    query = query.filter(Product.product_type.in_(["phone", "smartphone"]))
                else:
                    query = query.filter(Product.product_type == pt)
            if filters.get("color"):
                query = query.filter(Product.color == filters["color"])
            query = query.join(Price, Product.product_id == Price.product_id, isouter=True)
            if filters.get("price_max_cents"):
                query = query.filter(Price.price_cents <= filters["price_max_cents"])
            query = query.order_by(Price.price_cents.asc())
            products = query.limit(n_rows * n_per_row * 2).all()

        # Fallback: relax subcategory (Electronics)
        if not products and filters.get("subcategory") and category == "Electronics":
            logger.info("search_relax_subcategory", "No results with subcategory, trying category+brand only", {"subcategory": filters["subcategory"]})
            query = db.query(Product).filter(Product.category == category)
            query = query.filter(Product.scraped_from_url.isnot(None), ~Product.scraped_from_url.ilike("%test%"), ~Product.scraped_from_url.ilike("%example.com%"))
            if exclude_ids:
                query = query.filter(~Product.product_id.in_(exclude_ids))
            if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
                query = query.filter(Product.brand == filters["brand"])
            if filters.get("product_type"):
                pt = filters["product_type"]
                if pt == "laptop":
                    query = query.filter(Product.product_type.in_(["laptop", "gaming_laptop"]))
                elif pt in ("phone", "smartphone"):
                    query = query.filter(Product.product_type.in_(["phone", "smartphone"]))
                else:
                    query = query.filter(Product.product_type == pt)
            query = query.join(Price, Product.product_id == Price.product_id, isouter=True)
            if filters.get("price_max_cents"):
                query = query.filter(Price.price_cents <= filters["price_max_cents"])
            if filters.get("price_min_cents"):
                query = query.filter(Price.price_cents >= filters["price_min_cents"])
            query = query.order_by(Price.price_cents.asc())
            products = query.limit(n_rows * n_per_row * 2).all()

        # Fallback: relax subcategory (Books - show all books when genre filter returns none)
        if not products and filters.get("subcategory") and category == "Books":
            logger.info("search_relax_subcategory_books", "No results with genre, showing all books", {"subcategory": filters["subcategory"]})
            query = db.query(Product).filter(Product.category == category)
            query = query.filter(Product.scraped_from_url.isnot(None), ~Product.scraped_from_url.ilike("%test%"), ~Product.scraped_from_url.ilike("%example.com%"))
            if exclude_ids:
                query = query.filter(~Product.product_id.in_(exclude_ids))
            query = query.join(Price, Product.product_id == Price.product_id, isouter=True)
            if filters.get("price_max_cents"):
                query = query.filter(Price.price_cents <= filters["price_max_cents"])
            if filters.get("price_min_cents"):
                query = query.filter(Price.price_cents >= filters["price_min_cents"])
            query = query.order_by(Price.price_cents.asc())
            products = query.limit(n_rows * n_per_row * 2).all()

        # Fallback: relax brand (laptops - user picked HP but we have System76, Framework, etc.)
        if not products and filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand") and category == "Electronics":
            logger.info("search_relax_brand", "No results with brand, showing all laptops in category", {"brand": filters["brand"]})
            query = db.query(Product).filter(Product.category == category)
            query = query.filter(Product.scraped_from_url.isnot(None), ~Product.scraped_from_url.ilike("%test%"), ~Product.scraped_from_url.ilike("%example.com%"))
            if exclude_ids:
                query = query.filter(~Product.product_id.in_(exclude_ids))
            if filters.get("product_type"):
                pt = filters["product_type"]
                if pt == "laptop":
                    query = query.filter(Product.product_type.in_(["laptop", "gaming_laptop"]))
                elif pt in ("phone", "smartphone"):
                    query = query.filter(Product.product_type.in_(["phone", "smartphone"]))
                else:
                    query = query.filter(Product.product_type == pt)
            query = query.join(Price, Product.product_id == Price.product_id, isouter=True)
            if filters.get("price_max_cents"):
                query = query.filter(Price.price_cents <= filters["price_max_cents"])
            if filters.get("price_min_cents"):
                query = query.filter(Price.price_cents >= filters["price_min_cents"])
            query = query.order_by(Price.price_cents.asc())
            products = query.limit(n_rows * n_per_row * 2).all()

        # Fallback: relax ALL filters - show any products in category (last resort)
        if not products and (filters.get("brand") or filters.get("subcategory") or filters.get("price_min_cents") or filters.get("price_max_cents") or filters.get("product_type")):
            logger.info("search_relax_all", "No results with filters, showing all in category", {"category": category})
            query = db.query(Product).filter(Product.category == category)
            query = query.filter(Product.scraped_from_url.isnot(None), ~Product.scraped_from_url.ilike("%test%"), ~Product.scraped_from_url.ilike("%example.com%"))
            if exclude_ids:
                query = query.filter(~Product.product_id.in_(exclude_ids))
            if filters.get("product_type"):
                pt = filters["product_type"]
                if pt == "laptop":
                    query = query.filter(Product.product_type.in_(["laptop", "gaming_laptop"]))
                elif pt in ("phone", "smartphone"):
                    query = query.filter(Product.product_type.in_(["phone", "smartphone"]))
                else:
                    query = query.filter(Product.product_type == pt)
            query = query.join(Price, Product.product_id == Price.product_id, isouter=True)
            query = query.filter(Price.price_cents > 0)
            query = query.order_by(Price.price_cents.asc())
            products = query.limit(n_rows * n_per_row * 2).all()

        # Final fallback for Electronics: drop product_type ONLY for laptops (never for phones)
        # When user asked for phones, we must only show phones - never fall back to laptops/accessories
        pt_filter = filters.get("product_type")
        if not products and category == "Electronics" and pt_filter and pt_filter not in ("phone", "smartphone"):
            logger.info("search_relax_product_type", "No results with product_type, showing all Electronics", {"product_type": pt_filter})
            query = db.query(Product).filter(Product.category == category)
            query = query.filter(Product.scraped_from_url.isnot(None), ~Product.scraped_from_url.ilike("%test%"), ~Product.scraped_from_url.ilike("%example.com%"))
            if exclude_ids:
                query = query.filter(~Product.product_id.in_(exclude_ids))
            query = query.join(Price, Product.product_id == Price.product_id, isouter=True)
            query = query.filter(Price.price_cents > 0)
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

        # Convert to product dicts (intermediate format), exclude $0 prices (real scraped only)
        product_dicts = []
        for product in products:
            # Get price (skip products with $0 - real scraped prices only)
            price = db.query(Price).filter(Price.product_id == product.product_id).first()
            price_cents = price.price_cents if price else 0
            # When showing "all" in category, include products without price (use 1 cent placeholder)
            if not price_cents or price_cents <= 0:
                if not filters.get("brand") and not filters.get("subcategory") and not filters.get("price_min_cents") and not filters.get("price_max_cents"):
                    price_cents = 1  # Placeholder so product shows (avoids empty "show all" results)
                else:
                    logger.info("search_skip_zero_price", f"Skipping product with no price", {"product_id": product.product_id})
                    continue

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
                    "phones" if filters.get("product_type") in ("phone", "smartphone")
                    else "laptops" if category == "Electronics"
                    else "books" if category == "Books"
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
