"""
Chat endpoint for MCP server - compatible with IDSS /chat API.

Provides a unified /chat endpoint that:
1. Accepts the same request format as IDSS /chat
2. Uses UniversalAgent (LLM-driven) for domain detection, criteria extraction, and question generation
3. Routes search to IDSS (vehicles) or PostgreSQL (laptops/books)
4. Returns the same response format as IDSS /chat
"""

import uuid
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from agent.interview.session_manager import (
    get_session_manager,
    STAGE_INTERVIEW,
    STAGE_RECOMMENDATIONS,
)
from agent.universal_agent import UniversalAgent, AgentState
from agent.domain_registry import get_domain_schema
from app.structured_logger import StructuredLogger

logger = StructuredLogger("chat_endpoint")


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

async def process_chat(request: ChatRequest) -> ChatResponse:
    """
    Process a chat message using UniversalAgent for all domains.

    Flow:
    1. Session init + user action processing
    2. Post-recommendation handlers (if applicable)
    3. Reset/greeting check
    4. UniversalAgent.process_message() — domain detection, criteria extraction, question generation
    5. If recommendations_ready → dispatch to search (vehicles: direct IDSS import, laptops/books: PostgreSQL)
    """
    # Get or create session
    session_id = request.session_id or str(uuid.uuid4())
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)

    # Process user actions (favorites, clicks)
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

    msg = request.message.strip()
    msg_lower = msg.lower()

    # --- Handle rating responses (accept regardless of session state) ---
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

    # --- Post-recommendation handlers ---
    if session.stage == STAGE_RECOMMENDATIONS and session.active_domain:
        post_rec_response = await _handle_post_recommendation(request, session, session_id, session_manager)
        if post_rec_response:
            return post_rec_response

    # --- Reset / greeting check ---
    reset_keywords = ['reset', 'restart', 'start over', 'new search', 'clear', 'different category']
    is_explicit_reset = any(keyword == msg_lower or keyword in msg_lower for keyword in reset_keywords)
    greeting_words = ['hi', 'hello', 'hey', 'yo', 'sup']
    is_standalone_greeting = msg_lower in greeting_words and session.active_domain

    if is_explicit_reset or is_standalone_greeting:
        session_manager.reset_session(session_id)
        return ChatResponse(
            response_type="question",
            message="What are you looking for today?",
            session_id=session_id,
            quick_replies=["Vehicles", "Laptops", "Books"],
            filters={},
            preferences={},
            question_count=0,
            domain=None,
        )

    # --- UniversalAgent processing ---
    # Restore agent from session or create new
    if session.active_domain and session.agent_history:
        agent = UniversalAgent.restore_from_session(session_id, session)
    else:
        agent = UniversalAgent(session_id=session_id, max_questions=request.k if request.k is not None else 3)

    # Override max_questions if k=0 (skip interview)
    if request.k == 0:
        agent.max_questions = 0

    previous_domain = session.active_domain
    agent_response = agent.process_message(msg)

    # Detect domain switch: if domain changed, reset old filters
    new_domain = agent.domain
    if previous_domain and new_domain and previous_domain != new_domain:
        logger.info("domain_switch_detected", f"Domain switched from {previous_domain} to {new_domain}, resetting filters", {
            "old_domain": previous_domain, "new_domain": new_domain,
        })
        # Clear stale filters from old domain — agent's extraction already has the new ones
        agent.filters = {k: v for k, v in agent.filters.items()
                        if k in [s.name for s in (get_domain_schema(new_domain).slots if get_domain_schema(new_domain) else [])]}
        agent.questions_asked = []
        agent.question_count = 0

    # Persist agent state back to session
    agent_state = agent.get_state()
    session.agent_filters = agent_state["filters"]
    session.agent_questions_asked = agent_state["questions_asked"]
    session.agent_history = agent_state["history"]
    session.question_count = agent_state["question_count"]
    if agent_state["domain"]:
        session_manager.set_active_domain(session_id, agent_state["domain"])
    session_manager.update_filters(session_id, agent.get_search_filters())
    session_manager._persist(session_id)

    response_type = agent_response.get("response_type")

    # --- Question response ---
    if response_type == "question":
        session_manager.set_stage(session_id, STAGE_INTERVIEW)
        if agent_response.get("domain"):
            session_manager.add_question_asked(session_id, agent_response.get("topic", "general"))
        return ChatResponse(
            response_type="question",
            message=agent_response["message"],
            session_id=session_id,
            quick_replies=agent_response.get("quick_replies"),
            filters=agent.get_search_filters(),
            preferences={},
            question_count=agent_response.get("question_count", session.question_count),
            domain=agent_response.get("domain"),
        )

    # --- Error response ---
    if response_type == "error":
        return ChatResponse(
            response_type="question",
            message=agent_response.get("message", "Something went wrong. Please try again."),
            session_id=session_id,
            quick_replies=["Vehicles", "Laptops", "Books"],
            filters={},
            preferences={},
            question_count=0,
            domain=None,
        )

    # --- Recommendations ready: dispatch to search ---
    if response_type == "recommendations_ready":
        domain = agent_response.get("domain", agent.domain)
        search_filters = agent.get_search_filters()

        if domain == "vehicles":
            return await _search_and_respond_vehicles(
                search_filters, session_id, session, session_manager,
                n_rows=request.n_rows or 3, n_per_row=request.n_per_row or 3,
                method=request.method or "embedding_similarity",
                question_count=agent_response.get("question_count", 0),
                agent=agent,
            )
        elif domain in ("laptops", "books"):
            category = "Electronics" if domain == "laptops" else "Books"
            product_type = "laptop" if domain == "laptops" else "book"
            search_filters["category"] = category
            search_filters["product_type"] = product_type
            return await _search_and_respond_ecommerce(
                search_filters, category, domain, session_id, session, session_manager,
                n_rows=request.n_rows or 2, n_per_row=request.n_per_row or 3,
                question_count=agent_response.get("question_count", 0),
                agent=agent,
            )

    # Fallback
    return ChatResponse(
        response_type="question",
        message="I can help with Cars, Laptops, or Books. What are you looking for today?",
        session_id=session_id,
        quick_replies=["Vehicles", "Laptops", "Books"],
        filters={},
        preferences={},
        question_count=0,
        domain=None,
    )


# ============================================================================
# Post-Recommendation Handlers
# ============================================================================

async def _handle_post_recommendation(
    request: ChatRequest, session, session_id: str, session_manager
) -> Optional[ChatResponse]:
    """Handle post-recommendation follow-ups. Returns None if not a post-rec action."""
    msg_lower = request.message.strip().lower()
    active_domain = session.active_domain

    if "see similar" in msg_lower or "similar items" in msg_lower:
        session_manager.add_message(session_id, "user", request.message)
        # Directly show diverse results (drop brand filter to fix brand overfitting)
        if active_domain in ("laptops", "books", "phones"):
            category = _domain_to_category(active_domain)
            exclude_ids = list(session.last_recommendation_ids or [])
            diversified_filters = dict(session.explicit_filters)
            diversified_filters.pop("brand", None)
            recs, labels = await _search_ecommerce_products(
                diversified_filters, category, n_rows=2, n_per_row=3,
                exclude_ids=exclude_ids if exclude_ids else None,
            )
            if recs:
                new_ids = []
                for row in recs:
                    for item in row:
                        pid = item.get("product_id") or item.get("id") or (item.get("_product") or {}).get("product_id")
                        if pid and pid not in new_ids:
                            new_ids.append(pid)
                if new_ids:
                    accumulated = list(exclude_ids) + [p for p in new_ids if p not in exclude_ids]
                    session_manager.set_last_recommendations(session_id, accumulated[:24])
                return ChatResponse(
                    response_type="recommendations",
                    message="Here are similar items from different brands:",
                    session_id=session_id,
                    recommendations=recs,
                    bucket_labels=labels or [],
                    filters=diversified_filters,
                    preferences={},
                    question_count=session.question_count,
                    domain=active_domain,
                    quick_replies=["See similar items", "Compare items", "Broaden search", "Help with checkout"],
                )
        return ChatResponse(
            response_type="question",
            message="I can show you more options. Would you like to broaden the search or try a different category?",
            session_id=session_id,
            quick_replies=["Broaden search", "Different category", "Show more like these"],
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=active_domain,
        )

    if "broaden search" in msg_lower:
        session_manager.add_message(session_id, "user", request.message)
        relaxed = dict(session.explicit_filters)
        relaxed.pop("brand", None)
        if relaxed.get("price_max_cents"):
            relaxed["price_max_cents"] = min(int(relaxed["price_max_cents"] * 1.5), 999999)
        relaxed.pop("price_min_cents", None)
        session_manager.update_filters(session_id, relaxed)
        if active_domain in ("laptops", "books"):
            category = _domain_to_category(active_domain)
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
                    domain=active_domain,
                    quick_replies=["See similar items", "Anything else?", "Compare items", "Help with checkout"],
                )
        return ChatResponse(
            response_type="question",
            message="I couldn't find more options. Would you like to try a different category?",
            session_id=session_id,
            quick_replies=["Vehicles", "Laptops", "Books"],
            filters=relaxed,
            preferences={},
            question_count=session.question_count,
            domain=active_domain,
        )

    if "different category" in msg_lower:
        session_manager.add_message(session_id, "user", request.message)
        session_manager.reset_session(session_id)
        return ChatResponse(
            response_type="question",
            message="What are you looking for today?",
            session_id=session_id,
            quick_replies=["Vehicles", "Laptops", "Books"],
            filters={},
            preferences={},
            question_count=0,
            domain=None,
        )

    if "show more like these" in msg_lower:
        session_manager.add_message(session_id, "user", request.message)
        if active_domain in ("laptops", "books", "phones"):
            category = _domain_to_category(active_domain)
            exclude_ids = list(session.last_recommendation_ids or [])
            # Drop brand filter to ensure brand diversity
            diversified_filters = dict(session.explicit_filters)
            diversified_filters.pop("brand", None)
            recs, labels = await _search_ecommerce_products(
                diversified_filters, category, n_rows=2, n_per_row=3,
                exclude_ids=exclude_ids if exclude_ids else None,
            )
        else:
            recs, labels = [], []
        if recs:
            new_ids = []
            for row in recs:
                for item in row:
                    pid = item.get("product_id") or item.get("id") or (item.get("_product") or {}).get("product_id")
                    if pid and pid not in new_ids:
                        new_ids.append(pid)
            if new_ids:
                accumulated = list(exclude_ids) + [p for p in new_ids if p not in exclude_ids]
                session_manager.set_last_recommendations(session_id, accumulated[:24])
            return ChatResponse(
                response_type="recommendations",
                message="Here are more options like the ones you saw:",
                session_id=session_id,
                recommendations=recs,
                bucket_labels=labels or [],
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=active_domain,
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
            domain=active_domain,
        )

    if "anything else" in msg_lower or ("help" in msg_lower and "checkout" not in msg_lower):
        session_manager.add_message(session_id, "user", request.message)
        return ChatResponse(
            response_type="question",
            message="I'm here to help! You can: see similar items, compare products, rate these recommendations, or get help with checkout. What would you like to do?",
            session_id=session_id,
            quick_replies=["See similar items", "Compare items", "Rate recommendations", "Help with checkout"],
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=active_domain,
        )

    # Research
    if any(k in msg_lower for k in ["research", "explain features", "check compatibility", "summarize reviews"]):
        session_manager.add_message(session_id, "user", request.message)
        from app.research_compare import build_research_summary
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
                domain=active_domain,
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
                domain=active_domain,
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
            domain=active_domain,
        )

    # Compare
    if any(k in msg_lower for k in ["compare", "vs", "against", "versus"]):
        session_manager.add_message(session_id, "user", request.message)
        category = _domain_to_category(active_domain)
        from app.research_compare import build_comparison_table, parse_compare_by

        # Parse "compare by price and brand" → ["price", "brand"]
        compare_by = parse_compare_by(request.message)

        is_generic_compare = (
            "compare these" in msg_lower
            or "compare items" in msg_lower
            or compare_by is not None
            or ("compare" in msg_lower and "mac" not in msg_lower and "dell" not in msg_lower)
        )
        if is_generic_compare:
            product_ids = list(dict.fromkeys(
                list(session.favorite_product_ids or []) +
                list(session.clicked_product_ids or [])
            ))[:4]
            if not product_ids and getattr(session, "last_recommendation_ids", None):
                product_ids = session.last_recommendation_ids[:4]
            if product_ids:
                products = _fetch_products_by_ids(product_ids)
                if products:
                    comparison = build_comparison_table(products, compare_by=compare_by)
                    fmt_domain = "books" if category == "Books" else "laptops"
                    from app.formatters import format_product
                    formatted = [format_product(p, fmt_domain).model_dump(mode="json", exclude_none=True) for p in products]
                    rec_rows = [formatted]
                    labels = [p.get("name", "Product")[:20] for p in products]
                    attr_desc = ", ".join(compare_by) if compare_by else "all attributes"
                    return ChatResponse(
                        response_type="compare",
                        message=f"Side-by-side comparison by {attr_desc}:",
                        session_id=session_id,
                        comparison_data=comparison,
                        recommendations=rec_rows,
                        bucket_labels=labels,
                        quick_replies=["Compare by price", "Compare by brand", "Compare by rating", "See similar items"],
                        filters=session.explicit_filters,
                        preferences={},
                        question_count=session.question_count,
                        domain=active_domain,
                    )
            return ChatResponse(
                response_type="question",
                message="To compare items, please click on 2-4 products from the recommendations first (or add them to favorites), then say \"Compare these\" or \"Compare by price\".",
                session_id=session_id,
                quick_replies=["Compare by price", "Compare by brand", "Compare by rating", "See similar items"],
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=active_domain,
            )
        if "mac" in msg_lower and "dell" in msg_lower:
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
                    domain=active_domain,
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
                    domain=active_domain,
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
            domain=active_domain,
        )

    if "checkout" in msg_lower or "pay" in msg_lower or "transaction" in msg_lower:
        session_manager.add_message(session_id, "user", request.message)
        return ChatResponse(
            response_type="question",
            message="I can help with checkout! For now, you can view the full listing for any product by clicking \"View Details\" on a recommendation. Would you like to see more options or have questions about a specific item?",
            session_id=session_id,
            quick_replies=["See similar items", "Compare items", "Back to recommendations"],
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=active_domain,
        )

    if "rate" in msg_lower and ("recommendation" in msg_lower or "these" in msg_lower):
        session_manager.add_message(session_id, "user", request.message)
        return ChatResponse(
            response_type="question",
            message="Thanks for your interest in rating! Your feedback helps us improve. How would you rate these recommendations overall? (1-5 stars)",
            session_id=session_id,
            quick_replies=["5 stars", "4 stars", "3 stars", "Could be better"],
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=active_domain,
        )

    # --- Agent-driven refinement for unmatched post-rec messages ---
    # Instead of returning None and losing the message, let the agent classify it.
    agent = UniversalAgent.restore_from_session(session_id, session)
    refinement = agent.process_refinement(request.message.strip())

    if refinement.get("response_type") == "domain_switch":
        # Reset session and re-route to new domain
        new_domain = refinement.get("new_domain")
        session_manager.reset_session(session_id)
        if new_domain and new_domain != "unknown":
            # Create fresh agent for the new domain and process the original message
            new_agent = UniversalAgent(session_id=session_id, max_questions=request.k if request.k is not None else 3)
            new_agent.domain = new_domain
            new_agent.state = AgentState.INTERVIEW
            agent_response = new_agent.process_message(request.message.strip())
            # Persist new agent state
            agent_state = new_agent.get_state()
            new_session = session_manager.get_session(session_id)
            new_session.agent_filters = agent_state["filters"]
            new_session.agent_questions_asked = agent_state["questions_asked"]
            new_session.agent_history = agent_state["history"]
            new_session.question_count = agent_state["question_count"]
            session_manager.set_active_domain(session_id, new_domain)
            session_manager.update_filters(session_id, new_agent.get_search_filters())
            session_manager._persist(session_id)
            if agent_response.get("response_type") == "question":
                return ChatResponse(
                    response_type="question",
                    message=agent_response["message"],
                    session_id=session_id,
                    quick_replies=agent_response.get("quick_replies"),
                    filters=new_agent.get_search_filters(),
                    preferences={},
                    question_count=agent_response.get("question_count", 0),
                    domain=new_domain,
                )
        # Fallback: ask what they want
        return ChatResponse(
            response_type="question",
            message="Sure! What are you looking for?",
            session_id=session_id,
            quick_replies=["Vehicles", "Laptops", "Books"],
            filters={},
            preferences={},
            question_count=0,
            domain=None,
        )

    if refinement.get("response_type") == "recommendations_ready":
        # Refinement or new search — re-run search with updated filters
        session_manager.add_message(session_id, "user", request.message)
        search_filters = agent.get_search_filters()
        # Persist updated agent state
        agent_state = agent.get_state()
        session.agent_filters = agent_state["filters"]
        session.agent_questions_asked = agent_state["questions_asked"]
        session.agent_history = agent_state["history"]
        session_manager.update_filters(session_id, search_filters)
        session_manager._persist(session_id)

        if active_domain == "vehicles":
            return await _search_and_respond_vehicles(
                search_filters, session_id, session, session_manager,
                n_rows=request.n_rows or 3, n_per_row=request.n_per_row or 3,
                method=request.method or "embedding_similarity",
                question_count=session.question_count,
                agent=agent,
            )
        elif active_domain in ("laptops", "books"):
            category = _domain_to_category(active_domain)
            search_filters["category"] = category
            return await _search_and_respond_ecommerce(
                search_filters, category, active_domain, session_id, session, session_manager,
                n_rows=request.n_rows or 2, n_per_row=request.n_per_row or 3,
                question_count=session.question_count,
                agent=agent,
            )

    # Not a refinement — return None to continue to agent processing
    return None


# ============================================================================
# Search Dispatchers
# ============================================================================

async def _search_and_respond_vehicles(
    search_filters: Dict[str, Any],
    session_id: str,
    session,
    session_manager,
    n_rows: int = 3,
    n_per_row: int = 3,
    method: str = "embedding_similarity",
    question_count: int = 0,
    agent: Optional["UniversalAgent"] = None,
) -> ChatResponse:
    """Search vehicles using direct IDSS import and return response."""
    try:
        from app.tools.vehicle_search import search_vehicles, VehicleSearchRequest

        result = search_vehicles(VehicleSearchRequest(
            filters=search_filters,
            preferences=search_filters.pop("_soft_preferences", {}),
            method=method,
            n_rows=n_rows,
            n_per_row=n_per_row,
        ))

        session_manager.set_stage(session_id, STAGE_RECOMMENDATIONS)

        if not result.recommendations:
            return ChatResponse(
                response_type="question",
                message="I couldn't find vehicles matching your criteria. Try adjusting your budget or preferences.",
                session_id=session_id,
                quick_replies=["Broaden search", "Different category"],
                filters=search_filters,
                preferences={},
                question_count=question_count,
                domain="vehicles",
            )

        # Generate conversational explanation
        message = "Here are top vehicle recommendations. What would you like to do next?"
        if agent:
            try:
                message = agent.generate_recommendation_explanation(result.recommendations, "vehicles")
            except Exception as e:
                logger.error("rec_explanation_failed", f"Failed to generate explanation: {e}", {})

        return ChatResponse(
            response_type="recommendations",
            message=message,
            session_id=session_id,
            domain="vehicles",
            recommendations=result.recommendations,
            bucket_labels=result.bucket_labels,
            diversification_dimension=result.diversification_dimension,
            filters=search_filters,
            preferences={},
            question_count=question_count,
            quick_replies=["See similar items", "Research", "Compare items", "Rate recommendations", "Help with checkout"],
        )
    except Exception as e:
        logger.error("vehicle_search_failed", f"Vehicle search failed: {e}", {"error": str(e)})
        return ChatResponse(
            response_type="question",
            message=f"I'm having trouble searching vehicles. Please try again. Error: {str(e)[:100]}",
            session_id=session_id,
            quick_replies=["Try again"],
            domain="vehicles",
        )


async def _search_and_respond_ecommerce(
    search_filters: Dict[str, Any],
    category: str,
    domain: str,
    session_id: str,
    session,
    session_manager,
    n_rows: int = 2,
    n_per_row: int = 3,
    question_count: int = 0,
    agent: Optional["UniversalAgent"] = None,
) -> ChatResponse:
    """Search e-commerce products (laptops/books) and return response."""
    session_manager.add_message(session_id, "user", "")
    recs, labels = await _search_ecommerce_products(
        search_filters, category, n_rows=n_rows, n_per_row=n_per_row,
    )

    session_manager.set_stage(session_id, STAGE_RECOMMENDATIONS)

    if not recs:
        filter_desc = []
        if search_filters.get("brand") and str(search_filters["brand"]).lower() not in ("no preference", "specific brand"):
            filter_desc.append(f"{search_filters['brand']} brand")
        if search_filters.get("price_max_cents"):
            price_max_dollars = search_filters['price_max_cents'] / 100
            filter_desc.append(f"under ${price_max_dollars:.0f}")
        if search_filters.get("subcategory"):
            filter_desc.append(f"{search_filters['subcategory'].lower()}")

        filter_text = " with " + ", ".join(filter_desc) if filter_desc else ""
        message = f"I couldn't find any {domain}{filter_text}. Try adjusting your filters or budget."
        no_results_replies = (
            ["Show me all laptops", "Increase my budget", "Try a different brand"] if domain == "laptops"
            else ["Show me all books", "Increase my budget", "Try a different genre"] if domain == "books"
            else ["Broaden search", "Different category"]
        )
        return ChatResponse(
            response_type="question",
            message=message,
            session_id=session_id,
            domain=domain,
            quick_replies=no_results_replies,
            filters=search_filters,
            preferences=search_filters.get("_soft_preferences", {}),
            question_count=question_count,
        )

    # Store product IDs for Research/Compare
    all_ids = []
    for row in recs:
        for item in row:
            pid = item.get("product_id") or item.get("id") or (item.get("_product") or {}).get("product_id")
            if pid and pid not in all_ids:
                all_ids.append(pid)
    session_manager.set_last_recommendations(session_id, all_ids)

    product_label = "laptops" if domain == "laptops" else "books"

    # Generate conversational explanation
    message = f"Here are top {product_label} recommendations. What would you like to do next?"
    if agent:
        try:
            message = agent.generate_recommendation_explanation(recs, domain)
        except Exception as e:
            logger.error("rec_explanation_failed", f"Failed to generate explanation: {e}", {})

    return ChatResponse(
        response_type="recommendations",
        message=message,
        session_id=session_id,
        domain=domain,
        recommendations=recs,
        bucket_labels=labels,
        filters=search_filters,
        preferences=search_filters.get("_soft_preferences", {}),
        question_count=question_count,
        quick_replies=[
            "See similar items",
            "Research",
            "Compare items",
            "Rate recommendations",
            "Help with checkout",
        ],
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

    if category == "Electronics":
        product_type = "laptop"
        body_style = "Electronics"
    elif category == "Books":
        product_type = "book"
        body_style = "Books"
    else:
        product_type = "generic"
        body_style = category

    result = {
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
        "url": "",
        "available": True,
        "@id": product_id,
        "vin": product_id,
        "online": True,
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
        "_product": product_dict,
    }

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
            "author": "",
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
    """Build a search query string for KG from filters."""
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


def _diversify_by_brand(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Interleave products by brand to avoid showing all results from one brand."""
    if len(products) <= 2:
        return products
    from collections import OrderedDict
    brand_buckets: OrderedDict[str, list] = OrderedDict()
    for p in products:
        brand = (p.get("brand") or "Unknown").lower()
        brand_buckets.setdefault(brand, []).append(p)
    if len(brand_buckets) <= 1:
        return products
    result = []
    while any(brand_buckets.values()):
        for brand in list(brand_buckets.keys()):
            if brand_buckets[brand]:
                result.append(brand_buckets[brand].pop(0))
            else:
                del brand_buckets[brand]
    return result


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

    # Knowledge graph: get candidate IDs when available
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
        query = db.query(Product).filter(Product.category == category)

        if exclude_ids:
            query = query.filter(~Product.product_id.in_(exclude_ids))

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
        if filters.get("product_type"):
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

        # Fallback: relax price_min (but KEEP product_type)
        if not products and filters.get("price_min_cents"):
            query = db.query(Product).filter(Product.category == category)
            if exclude_ids:
                query = query.filter(~Product.product_id.in_(exclude_ids))
            if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
                query = query.filter(Product.brand == filters["brand"])
            if filters.get("product_type"):
                query = query.filter(Product.product_type == filters["product_type"])
            if filters.get("subcategory"):
                query = query.filter(Product.subcategory == filters["subcategory"])
            if filters.get("color"):
                query = query.filter(Product.color == filters["color"])
            query = query.join(Price, Product.product_id == Price.product_id, isouter=True)
            if filters.get("price_max_cents"):
                query = query.filter(Price.price_cents <= filters["price_max_cents"])
            query = query.order_by(Price.price_cents.asc())
            products = query.limit(n_rows * n_per_row * 2).all()

        # Fallback: relax subcategory (but KEEP product_type to avoid mixing laptops/watches/iPads)
        if not products and filters.get("subcategory"):
            query = db.query(Product).filter(Product.category == category)
            if exclude_ids:
                query = query.filter(~Product.product_id.in_(exclude_ids))
            if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
                query = query.filter(Product.brand == filters["brand"])
            if filters.get("product_type"):
                query = query.filter(Product.product_type == filters["product_type"])
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
            return [], []

        # Convert to product dicts
        product_dicts = []
        for product in products:
            price = db.query(Price).filter(Price.product_id == product.product_id).first()
            price_cents = price.price_cents if price else 0
            inventory = db.query(Inventory).filter(Inventory.product_id == product.product_id).first()
            available_qty = inventory.available_qty if inventory else 0
            reviews_text = product.reviews

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
                "subcategory": product.subcategory,
                "brand": product.brand,
                "price": price_cents / 100,
                "price_cents": price_cents,
                "image_url": getattr(product, 'image_url', None),
                "product_type": product.product_type,
                "gpu_vendor": product.gpu_vendor,
                "gpu_model": product.gpu_model,
                "color": product.color,
                "tags": product.tags,
                "reviews": reviews_text,
                "available_qty": available_qty,
                "format": format_value,
                "author": product.brand if product.product_type == "book" else None,
                "genre": product.subcategory if product.product_type == "book" else None,
            }
            product_dicts.append(product_dict)

        if len(product_dicts) == 0:
            return [], []

        # Reorder: KG candidates first, then by price
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

        # Brand diversification: interleave brands so results aren't all from one brand
        product_dicts = _diversify_by_brand(product_dicts)

        # Generate recommendation reasons based on ranking context
        from app.research_compare import generate_recommendation_reasons
        generate_recommendation_reasons(product_dicts, filters=filters, kg_candidate_ids=kg_candidate_ids)

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
                fmt_domain = (
                    "laptops" if category == "Electronics"
                    else "books" if category == "Books"
                    else "laptops"
                )

                formatted_bucket = [
                    format_product(p, fmt_domain).model_dump(mode='json', exclude_none=True)
                    for p in bucket_products
                ]
                buckets.append(formatted_bucket)

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
