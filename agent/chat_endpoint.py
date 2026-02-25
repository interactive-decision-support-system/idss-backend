"""
Chat endpoint for MCP server - compatible with IDSS /chat API.

Provides a unified /chat endpoint that:
1. Accepts the same request format as IDSS /chat
2. Uses UniversalAgent (LLM-driven) for domain detection, criteria extraction, and question generation
3. Routes search to IDSS (vehicles) or PostgreSQL (laptops/books)
4. Returns the same response format as IDSS /chat
"""

import re
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
from agent.comparison_agent import detect_post_rec_intent, generate_comparison_narrative
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

    # Latency instrumentation — step-level timings in milliseconds
    timings_ms: Optional[Dict[str, float]] = Field(default=None, description="Per-step latency breakdown (ms)")


# ============================================================================
# Chat Endpoint Logic
# ============================================================================

async def process_chat(request: ChatRequest) -> ChatResponse:
    import time
    timings = {}
    t_start = time.perf_counter()

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
            quick_replies=["See similar items", "Compare items", "Different category"],
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
    t_agent = time.perf_counter()
    # Restore agent from session or create new
    if session.active_domain:
        agent = UniversalAgent.restore_from_session(session_id, session)
    else:
        agent = UniversalAgent(session_id=session_id, max_questions=request.k if request.k is not None else 3)

    # Override max_questions if k=0 (skip interview)
    if request.k == 0:
        agent.max_questions = 0

    previous_domain = session.active_domain
    agent_response = agent.process_message(msg)
    timings["agent_total_ms"] = (time.perf_counter() - t_agent) * 1000

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
            timings_ms={**agent_response.get("timings_ms", {}), **timings, "total_backend_ms": (time.perf_counter() - t_start) * 1000}
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
        elif domain in ("laptops", "books", "phones"):
            category = "Books" if domain == "books" else "electronics"
            product_type = "book" if domain == "books" else ("phone" if domain == "phones" else "laptop")
            search_filters["category"] = category
            search_filters["product_type"] = product_type
            # Extract hardware specs (RAM, storage, battery, screen) from user query
            try:
                from app.query_parser import enhance_search_request
                _, spec_filters = enhance_search_request(request.message, search_filters)
                search_filters.update(spec_filters)
            except Exception:
                pass  # Non-critical: spec extraction failure shouldn't block search
            return await _search_and_respond_ecommerce(
                search_filters, category, domain, session_id, session, session_manager,
                n_rows=request.n_rows or 2, n_per_row=request.n_per_row or 3,
                question_count=agent_response.get("question_count", 0),
                agent=agent,
            )

    # Fallback
    return ChatResponse(
        response_type="question",
        message="I can help with Cars, Laptops, Books, or Phones. What are you looking for today?",
        session_id=session_id,
        quick_replies=["Cars", "Laptops", "Books", "Phones"],
        filters={},
        preferences={},
        question_count=0,
        domain=None,
    )


# ============================================================================
# Best-Value Scoring
# ============================================================================

def _pick_best_value(products: list) -> Optional[dict]:
    """
    Score each product by a weighted value formula and return the single best.

    Weights:
      35% price      — lower is better (normalized within the set)
      35% rating     — higher is better (0-5 → 0-1)
      10% review vol — more reviews = more trustworthy rating (capped at 0.10)
      20% specs      — RAM tier bonus for laptops; mileage bonus for vehicles
    """
    if not products:
        return None
    if len(products) == 1:
        return products[0]

    prices = []
    for p in products:
        raw = p.get("price") or p.get("price_value") or 0
        try:
            prices.append(float(raw))
        except (TypeError, ValueError):
            prices.append(0.0)

    valid_prices = [p for p in prices if p > 0]
    min_price = min(valid_prices) if valid_prices else 0.0
    max_price = max(valid_prices) if valid_prices else 1.0
    price_range = max(max_price - min_price, 1.0)

    scored = []
    for product, price in zip(products, prices):
        # --- Price score (lower → better) ---
        price_score = 1.0 - (price - min_price) / price_range if max_price > 0 else 0.5

        # --- Rating score ---
        try:
            rating = float(product.get("rating") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        rating_score = rating / 5.0

        # --- Review volume (confidence boost, max 0.10) ---
        try:
            reviews = int(product.get("reviews_count") or 0)
        except (TypeError, ValueError):
            reviews = 0
        review_boost = min(reviews / 200.0, 0.10)

        # --- Spec bonus (laptops: RAM tier) ---
        spec_score = 0.0
        attrs = product.get("attributes") or {}
        try:
            ram_gb = int(attrs.get("ram_gb") or 0)
            if ram_gb >= 32:
                spec_score += 0.20
            elif ram_gb >= 16:
                spec_score += 0.12
            elif ram_gb >= 8:
                spec_score += 0.06
        except (TypeError, ValueError):
            pass
        # Vehicles: penalise high mileage
        try:
            mileage = int(product.get("mileage") or product.get("vehicle", {}).get("mileage") or 0)
            if mileage and max_price > 0:
                spec_score -= min(mileage / 200_000, 0.15)
        except (TypeError, ValueError):
            pass

        if rating == 0:
            # No rating data: lean on price + specs
            total = price_score * 0.60 + spec_score * 0.30 + review_boost
        else:
            total = (price_score * 0.35) + (rating_score * 0.35) + review_boost + (spec_score * 0.20)

        scored.append((total, product))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _explain_best_value(product: dict, domain: str, all_products: Optional[list] = None) -> str:
    """
    Return a markdown bullet-point explanation (≥3 bullets) for why this
    product was chosen as the best value pick.
    Optionally receives all_products for price-comparison context.
    """
    name = product.get("name") or "This product"
    price = product.get("price") or product.get("price_value") or 0
    try:
        price_float = float(price)
        price_fmt = f"${int(price_float):,}"
    except (TypeError, ValueError):
        price_float = 0.0
        price_fmt = "a competitive price"

    bullets: list[str] = []
    attrs = product.get("attributes") or {}

    # --- 1. Price bullet — with context vs. other products when available ---
    if all_products and len(all_products) > 1:
        valid_prices = []
        for p in all_products:
            try:
                v = float(p.get("price") or p.get("price_value") or 0)
                if v > 0:
                    valid_prices.append(v)
            except (TypeError, ValueError):
                pass
        if valid_prices:
            avg_price = sum(valid_prices) / len(valid_prices)
            min_price = min(valid_prices)
            if price_float <= min_price:
                bullets.append(
                    f"**Lowest price** in your results at {price_fmt} "
                    f"— best affordability out of {len(valid_prices)} options"
                )
            elif price_float < avg_price:
                savings = int(avg_price - price_float)
                bullets.append(
                    f"**Priced at {price_fmt}** — ${savings:,} below the average "
                    f"of your results, great value for money"
                )
            else:
                bullets.append(
                    f"**Priced at {price_fmt}** — premium price justified by "
                    f"top-tier specs and rating"
                )
    if not bullets:
        bullets.append(f"**Priced at {price_fmt}** — strong value for the specs offered")

    # --- 2. Rating / reviews bullet ---
    try:
        rating = float(product.get("rating") or 0)
        reviews = int(product.get("reviews_count") or product.get("rating_count") or 0)
        reviews_str = f" from {reviews:,} verified reviews" if reviews > 0 else ""
        if rating >= 4.5:
            bullets.append(f"**Top-rated at {rating:.1f}/5**{reviews_str} — outstanding user satisfaction")
        elif rating >= 4.0:
            bullets.append(f"**Well-rated at {rating:.1f}/5**{reviews_str}")
        elif rating > 0:
            bullets.append(f"**Rated {rating:.1f}/5**{reviews_str}")
    except (TypeError, ValueError):
        pass

    # --- 3. RAM bullet ---
    try:
        ram_gb = int(attrs.get("ram_gb") or 0)
        if ram_gb >= 32:
            bullets.append(f"**{ram_gb}GB RAM** — handles heavy multitasking, video editing, and demanding workloads effortlessly")
        elif ram_gb >= 16:
            bullets.append(f"**{ram_gb}GB RAM** — smooth multitasking for coding, design tools, and everyday use")
        elif ram_gb >= 8:
            bullets.append(f"**{ram_gb}GB RAM** — sufficient for everyday tasks and light multitasking")
    except (TypeError, ValueError):
        pass

    # --- 4. Storage bullet ---
    try:
        storage_gb = int(attrs.get("storage_gb") or 0)
        storage_type = (attrs.get("storage_type") or "SSD").upper()
        if storage_gb >= 1000:
            bullets.append(f"**{storage_gb // 1000}TB {storage_type}** — massive storage for files, projects, and media")
        elif storage_gb >= 512:
            bullets.append(f"**{storage_gb}GB {storage_type}** — generous fast storage for most power users")
        elif storage_gb >= 256:
            bullets.append(f"**{storage_gb}GB {storage_type}** — solid fast storage for everyday use")
        elif storage_gb >= 128:
            bullets.append(f"**{storage_gb}GB {storage_type}**")
    except (TypeError, ValueError):
        pass

    # --- 5. Processor bullet ---
    cpu = (
        attrs.get("cpu") or attrs.get("processor")
        or product.get("processor") or product.get("cpu")
    )
    if cpu:
        bullets.append(f"**Processor: {cpu}** — capable performance for the price")

    # --- 6. Battery bullet ---
    try:
        battery_hours = float(attrs.get("battery_life_hours") or 0)
        if battery_hours >= 10:
            bullets.append(f"**{int(battery_hours)}-hour battery life** — all-day use without a charger")
        elif battery_hours >= 7:
            bullets.append(f"**{int(battery_hours)}-hour battery** — good for long sessions away from a desk")
    except (TypeError, ValueError):
        pass

    # --- Vehicles: mileage bullet instead of specs ---
    if domain == "vehicles":
        try:
            mileage = int(product.get("mileage") or product.get("vehicle", {}).get("mileage") or 0)
            if mileage:
                bullets.append(f"**{mileage:,} miles** on the odometer")
        except (TypeError, ValueError):
            pass

    # --- Ensure at least 3 bullets with fallbacks ---
    fallbacks = [
        "**Best overall score** across price, rating, and performance in your current results",
        "**Reliable brand** with strong user satisfaction based on available ratings",
        "**Balanced specs** — offers the best combination of performance and affordability",
    ]
    for fb in fallbacks:
        if len(bullets) >= 3:
            break
        bullets.append(fb)

    bullets_md = "\n".join(f"- {b}" for b in bullets[:6])  # cap at 6 to keep it clean
    return f"**{name}** is the best value pick:\n\n{bullets_md}"


# ============================================================================
# Post-Recommendation Handlers
# ============================================================================

async def _handle_post_recommendation(
    request: ChatRequest, session, session_id: str, session_manager
) -> Optional[ChatResponse]:
    """Handle post-recommendation follow-ups. Returns None if not a post-rec action."""
    active_domain = session.active_domain

    # -----------------------------------------------------------------------
    # Extract [ctx:id1,id2,...] tag injected by the frontend "Tell me more"
    # button. This encodes the EXACT products currently visible so we analyze
    # only those — not all historical session products.
    # The tag is stripped before any LLM call so the model never sees it.
    # -----------------------------------------------------------------------
    _ctx_match = re.search(r'\[ctx:([^\]]*)\]', request.message)
    context_product_ids: Optional[set] = (
        set(filter(None, _ctx_match.group(1).split(','))) if _ctx_match else None
    )
    # Message with the hidden tag removed — used for LLM calls and msg_lower
    clean_message: str = re.sub(r'\s*\[ctx:[^\]]*\]', '', request.message).strip()

    msg_lower = clean_message.lower()

    # -----------------------------------------------------------------------
    # Fast intent router: compare vs. refine vs. other
    # -----------------------------------------------------------------------
    # Keyword fast-path skips the LLM call for obvious fixed-button messages
    _FAST_BEST_VALUE_KWS = (
        "best value", "get best", "show me the best", "best pick",
    )
    # "Tell me more" and "pros and cons" → text-only response, NO product cards
    _FAST_PROS_CONS_KWS = (
        "tell me more about these",   # exact text from the action bar button
        "pros and cons",              # any pros/cons request
    )
    # Explicit compare (user named products or pressed Compare dialog) → show cards
    _FAST_COMPARE_KWS = (
        " vs ", "vs.", "compare my", "compare these", "compare them",
        "which is better", "which should i buy",
    )
    _FAST_REFINE_KWS = ("refine my search", "refine search", "change my criteria")
    if any(kw in msg_lower for kw in _FAST_BEST_VALUE_KWS):
        intent = "best_value"
    elif any(kw in msg_lower for kw in _FAST_PROS_CONS_KWS):
        intent = "pros_cons"
    elif any(kw in msg_lower for kw in _FAST_COMPARE_KWS):
        intent = "compare"
    elif any(kw in msg_lower for kw in _FAST_REFINE_KWS):
        intent = "refine"
    else:
        intent = await detect_post_rec_intent(clean_message)

    if intent == "best_value":
        session_manager.add_message(session_id, "user", request.message)
        products = list(getattr(session, "last_recommendation_data", []))
        if not products and getattr(session, "last_recommendation_ids", None):
            products = _fetch_products_by_ids(session.last_recommendation_ids[:12])
        # Deduplicate
        _seen_bv: set = set()
        _deduped_bv = []
        for _p in products:
            _pid = str(_p.get("id", ""))
            if _pid and _pid not in _seen_bv:
                _seen_bv.add(_pid)
                _deduped_bv.append(_p)
        products = _deduped_bv

        best = _pick_best_value(products)
        if best:
            explanation = _explain_best_value(best, active_domain or "laptops", products)
            from app.formatters import format_product
            fmt_domain = "books" if _domain_to_category(active_domain) == "Books" else (active_domain or "laptops")
            formatted = format_product(best, fmt_domain).model_dump(mode="json", exclude_none=True)
            return ChatResponse(
                response_type="recommendations",
                message=f"Here's the best value pick from your results:\n\n{explanation}",
                session_id=session_id,
                quick_replies=["See similar items", "Compare items", "Refine search"],
                recommendations=[[formatted]],
                bucket_labels=["Best Value Pick"],
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=active_domain,
            )
        return ChatResponse(
            response_type="question",
            message="I don't have any recommendations to evaluate yet. What are you looking for?",
            session_id=session_id,
            quick_replies=["Laptops", "Vehicles", "Books"],
            filters={},
            preferences={},
            question_count=0,
            domain=None,
        )

    if intent == "pros_cons":
        # "Tell me more" / "pros and cons" → narrative text only, NO product cards.
        # The cards are already visible above — re-rendering them creates duplicates.
        session_manager.add_message(session_id, "user", clean_message)
        products = list(getattr(session, "last_recommendation_data", []))
        if not products and getattr(session, "last_recommendation_ids", None):
            products = _fetch_products_by_ids(session.last_recommendation_ids[:12])
        _seen_pc: set = set()
        _deduped_pc = []
        for _p in products:
            _pid = str(_p.get("id", ""))
            if _pid and _pid not in _seen_pc:
                _seen_pc.add(_pid)
                _deduped_pc.append(_p)
        products = _deduped_pc
        if context_product_ids and products:
            _ctx_filtered = [
                p for p in products
                if str(p.get("id") or p.get("product_id", "")) in context_product_ids
            ]
            if _ctx_filtered:
                products = _ctx_filtered
        if products:
            try:
                narrative, _, _ = await generate_comparison_narrative(
                    products, clean_message, active_domain or "laptops"
                )
            except Exception as e:
                logger.error("pros_cons_failed", str(e), {})
                narrative = "Sorry, I had trouble analyzing these products. Try asking again."
            # response_type="question" → frontend shows text only, no product cards
            return ChatResponse(
                response_type="question",
                message=narrative,
                session_id=session_id,
                quick_replies=["Compare items", "Get best value", "Refine search"],
                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=active_domain,
            )
        return ChatResponse(
            response_type="question",
            message="I don't have any recommendations to analyze yet. What are you looking for?",
            session_id=session_id,
            quick_replies=["Laptops", "Vehicles", "Books"],
            filters={}, preferences={}, question_count=0, domain=None,
        )

    if intent == "compare":
        session_manager.add_message(session_id, "user", clean_message)
        # Use in-session product data first (no DB round-trip)
        products = list(getattr(session, "last_recommendation_data", []))
        if not products and getattr(session, "last_recommendation_ids", None):
            # Fallback: re-fetch from DB if session data not yet populated
            products = _fetch_products_by_ids(session.last_recommendation_ids[:6])
        # Deduplicate by product ID — same product can appear in multiple buckets
        _seen_pids: set = set()
        _deduped = []
        for _p in products:
            _pid = str(_p.get("id", ""))
            if _pid and _pid not in _seen_pids:
                _seen_pids.add(_pid)
                _deduped.append(_p)
        products = _deduped

        # If the frontend sent a [ctx:...] tag, filter to only those specific
        # products — this ensures "Tell me more" analyzes the exact products
        # the user was looking at, not all historical session products.
        if context_product_ids and products:
            _ctx_filtered = [
                p for p in products
                if str(p.get("id") or p.get("product_id", "")) in context_product_ids
            ]
            if _ctx_filtered:
                products = _ctx_filtered
                logger.info("compare_ctx_filter", f"Filtered to {len(products)} context products from [ctx:] tag", {})

        if products:
            selected_ids: list = []
            selected_names: list = []
            try:
                import time as _time
                t0 = _time.perf_counter()
                narrative, selected_ids, selected_names = await generate_comparison_narrative(
                    products, clean_message, active_domain or "laptops"
                )
                logger.info("comparison_generated", f"Comparison narrative in {(_time.perf_counter()-t0)*1000:.0f}ms", {})
            except Exception as e:
                logger.error("comparison_failed", str(e), {})
                narrative = "Sorry, I had trouble generating a comparison. Try asking again."

            # Filter products to only those selected by the LLM
            selected_products = []
            if selected_ids:
                str_selected_ids = [str(sid) for sid in selected_ids]
                logger.info("comparison_ids", f"LLM returned selected_ids: {str_selected_ids}, Available products: {[str(p.get('id') or p.get('product_id')) for p in products]}")
                selected_products = [
                    p for p in products
                    if str(p.get("id") or p.get("product_id", "")) in str_selected_ids
                ]
            # Name-based fallback: LLM sometimes returns product names instead of UUIDs.
            # For each target name the LLM returned, find the single best-matching
            # product by counting overlapping "distinctive" words (filtered for stop words).
            # Minimum 2 distinctive words must match to avoid false positives.
            if not selected_products and selected_names:
                _STOP = frozenset([
                    "laptop", "intel", "amd", "with", "and", "the", "for",
                    "gaming", "screen", "memory", "storage", "ssd", "hdd",
                    "ram", "gen", "inch", "series", "edition", "plus", "ultra",
                    "business", "computer", "notebook", "model", "new", "black",
                    "silver", "grey", "gray", "white", "blue", "nvidia", "geforce",
                    "ryzen", "core", "processor", "ghz", "display", "touch",
                ])
                def _distinctive_words(text: str) -> set:
                    return {
                        w.lower().strip('",.-()[]') for w in text.split()
                        if len(w) > 3 and w.lower().strip('",.-()[]') not in _STOP
                    }
                for target_name in selected_names:
                    if not target_name:
                        continue
                    target_words = _distinctive_words(target_name)
                    best_score, best_match = 0, None
                    for p in products:
                        p_words = _distinctive_words(p.get("name") or "")
                        score = len(target_words & p_words)
                        if score > best_score:
                            best_score, best_match = score, p
                    if best_match and best_score >= 2 and best_match not in selected_products:
                        selected_products.append(best_match)
                        logger.info("comparison_name_match", f"Matched '{best_match.get('name')}' for target '{target_name}' (score={best_score})", {})
                if not selected_products:
                    logger.warning("comparison_name_match", "Name fallback found no matches with score >= 2", {})
            if not selected_products:
                logger.warning("comparison_fallback", "No products matched selected_ids or names, falling back to all context products")
                selected_products = products
            # Deduplicate selected_products (LLM may return same ID twice in selected_ids)
            _seen_sel: set = set()
            _deduped_sel = []
            for _ps in selected_products:
                _ps_id = str(_ps.get("id", ""))
                if _ps_id not in _seen_sel:
                    _seen_sel.add(_ps_id)
                    _deduped_sel.append(_ps)
            selected_products = _deduped_sel

            from app.formatters import format_product
            fmt_domain = "books" if _domain_to_category(active_domain) == "Books" else (active_domain or "laptops")
            formatted_products = [
                format_product(p, fmt_domain).model_dump(mode="json", exclude_none=True) 
                for p in selected_products
            ]
                
            return ChatResponse(
                response_type="recommendations",
                message=narrative,
                session_id=session_id,
                quick_replies=["Show me the best value", "See similar items", "Refine search"],
                recommendations=[formatted_products] if formatted_products else [],
                bucket_labels=["Compared Items"] if formatted_products else [],

                filters=session.explicit_filters,
                preferences={},
                question_count=session.question_count,
                domain=active_domain,
            )
        # No product data at all
        return ChatResponse(
            response_type="question",
            message="I don't have any recommendations to compare yet. Let me search for some first! What are you looking for?",
            session_id=session_id,
            quick_replies=["Laptops", "Vehicles", "Books"],
            filters={},
            preferences={},
            question_count=0,
            domain=None,
        )

    # intent == 'refine' or None → fall through to the keyword handlers below,
    # which will either catch a specific keyword or return None to let the
    # UniversalAgent handle the message normally.
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
                    quick_replies=["See similar items", "Compare items", "Broaden search"],
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
                    quick_replies=["See similar items", "Anything else?", "Compare items"],
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
                quick_replies=["See similar items", "Anything else?", "Compare items"],
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

    # "help" alone is too broad — "can u help me find dell laptops" contains "help" but is
    # a new search request that should go to process_refinement, not this dead-end handler.
    if "anything else" in msg_lower:
        session_manager.add_message(session_id, "user", request.message)
        return ChatResponse(
            response_type="question",
            message="I'm here to help! You can: see similar items, compare products, or rate these recommendations. What would you like to do?",
            session_id=session_id,
            quick_replies=["See similar items", "Compare items", "Rate recommendations"],
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
            quick_replies=["Compare items", "See similar items"],
            filters=session.explicit_filters,
            preferences={},
            question_count=session.question_count,
            domain=active_domain,
        )



    if "checkout" in msg_lower or "pay" in msg_lower or "transaction" in msg_lower:
        session_manager.add_message(session_id, "user", request.message)
        fav_ids = list(session.favorite_product_ids or [])
        if fav_ids:
            fav_products = _fetch_products_by_ids(fav_ids)
            total = sum(p.get("price", 0) for p in fav_products)
            item_names = [p.get("name", "item")[:30] for p in fav_products[:5]]
            msg = (
                f"Your cart has {len(fav_products)} item(s) totaling ${total:,.2f}:\n"
                + "\n".join(f"- {name}" for name in item_names)
                + "\n\nClick the cart icon (top right) and then **Proceed to Checkout** to complete your purchase."
            )
        else:
            msg = "Your cart is empty. Add items to your cart by clicking the heart icon on products you like, then come back to checkout."
        return ChatResponse(
            response_type="question",
            message=msg,
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
            product_type = "laptop" if active_domain == "laptops" else "book"
            search_filters["category"] = category
            search_filters["product_type"] = product_type
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
    import time
    timings = {}
    t_search = time.perf_counter()
    try:
        from app.tools.vehicle_search import search_vehicles, VehicleSearchRequest
        result = search_vehicles(VehicleSearchRequest(
            filters=search_filters,
            preferences=search_filters.pop("_soft_preferences", {}),
            method=method,
            n_rows=n_rows,
            n_per_row=n_per_row,
        ))
        timings["vehicle_search_ms"] = (time.perf_counter() - t_search) * 1000
        t_format = time.perf_counter()
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
                timings_ms=timings
            )
        # Generate conversational explanation
        message = "Here are top vehicle recommendations. What would you like to do next?"
        if agent:
            try:
                message = agent.generate_recommendation_explanation(result.recommendations, "vehicles")
            except Exception as e:
                logger.error("rec_explanation_failed", f"Failed to generate explanation: {e}", {})
        from app.formatters import format_product
        formatted_recs = []
        flat_data = []
        for i, bucket in enumerate(result.recommendations):
            bucket_label = result.bucket_labels[i] if result.bucket_labels and i < len(result.bucket_labels) else None
            formatted_bucket = []
            for v in bucket:
                unified = format_product(v, "vehicles").model_dump(mode='json', exclude_none=True)
                formatted_bucket.append(unified)
                
                # Extract flat data for the comparison agent
                veh = unified.get("vehicle", {})
                flat_item = {
                    "id": unified.get("id"),
                    "name": unified.get("name"),
                    "price": unified.get("price"),
                    **veh
                }
                if bucket_label:
                    flat_item["bucket_label"] = bucket_label
                flat_data.append(flat_item)
                
            formatted_recs.append(formatted_bucket)
            
        session_manager.set_last_recommendation_data(session_id, flat_data)
        timings["vehicle_formatting_ms"] = (time.perf_counter() - t_format) * 1000
        return ChatResponse(
            response_type="recommendations",
            message=message,
            session_id=session_id,
            domain="vehicles",
            recommendations=formatted_recs,
            bucket_labels=result.bucket_labels,
            diversification_dimension=result.diversification_dimension,
            filters=search_filters,
            preferences={},
            question_count=question_count,
            quick_replies=["See similar items", "Research", "Compare items", "Rate recommendations"],
            timings_ms=timings
        )
    except Exception as e:
        logger.error("vehicle_search_failed", f"Vehicle search failed: {e}", {"error": str(e)})
        return ChatResponse(
            response_type="question",
            message=f"I'm having trouble searching vehicles. Please try again. Error: {str(e)[:100]}",
            session_id=session_id,
            quick_replies=["Try again"],
            domain="vehicles",
            timings_ms=timings
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
    import time
    timings = {}
    t_search = time.perf_counter()
    session_manager.add_message(session_id, "user", "")
    recs, labels = await _search_ecommerce_products(
        search_filters, category, n_rows=n_rows, n_per_row=n_per_row,
    )
    timings["ecommerce_search_ms"] = (time.perf_counter() - t_search) * 1000
    t_format = time.perf_counter()
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
            timings_ms=timings
        )
    # Store product IDs and full data for Research/Compare
    all_ids = []
    flat_data = []
    for i, row in enumerate(recs):
        bucket_label = labels[i] if labels and i < len(labels) else None
        for item in row:
            if bucket_label:
                item["bucket_label"] = bucket_label
            flat_data.append(item)
            pid = item.get("product_id") or item.get("id") or (item.get("_product") or {}).get("product_id")
            if pid and pid not in all_ids:
                all_ids.append(pid)
    session_manager.set_last_recommendations(session_id, all_ids)
    session_manager.set_last_recommendation_data(session_id, flat_data)

    product_label = "laptops" if domain == "laptops" else "books"
    # Generate conversational explanation
    message = f"Here are top {product_label} recommendations. What would you like to do next?"
    if agent:
        try:
            message = agent.generate_recommendation_explanation(recs, domain)
        except Exception as e:
            logger.error("rec_explanation_failed", f"Failed to generate explanation: {e}", {})
    timings["ecommerce_formatting_ms"] = (time.perf_counter() - t_format) * 1000
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
        ],
        timings_ms=timings
    )


# ============================================================================
# Helper Functions
# ============================================================================

def _domain_to_category(active_domain: Optional[str]) -> str:
    """Map domain to database category for e-commerce search."""
    if not active_domain:
        return "electronics"
    m = {
        "laptops": "electronics",
        "books": "Books",
    }
    return m.get(active_domain, "electronics")


def _fetch_products_by_ids(product_ids: List[str]) -> List[Dict[str, Any]]:
    """Fetch product dicts by IDs."""
    if not product_ids:
        return []
    from app.database import SessionLocal
    from app.models import Product
    db = SessionLocal()
    try:
        products = db.query(Product).filter(Product.product_id.in_(product_ids)).all()
        id_order = {pid: i for i, pid in enumerate(product_ids)}
        products = sorted(products, key=lambda p: id_order.get(str(p.product_id), 999))
        result = []
        for product in products:
            price_dollars = float(product.price_value) if product.price_value else 0
            p_dict = {
                "id": str(product.product_id),
                "product_id": str(product.product_id),
                "name": product.name,
                "description": product.description,
                "category": product.category,
                "subcategory": getattr(product, "subcategory", None),
                "brand": product.brand,
                "price": round(price_dollars, 2),
                "price_cents": int(price_dollars * 100),
                "image_url": getattr(product, "image_url", None),
                "product_type": product.product_type,
                "gpu_vendor": getattr(product, "gpu_vendor", None),
                "gpu_model": getattr(product, "gpu_model", None),
                "color": getattr(product, "color", None),
                "tags": getattr(product, "tags", None),
                "reviews": getattr(product, "reviews", None),
                "available_qty": product.inventory or 0,
                "rating": float(product.rating) if product.rating else None,
                "rating_count": product.rating_count,
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
    if category.lower() == "electronics":
        parts.append("laptop")
    elif category.lower() == "books":
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
) -> tuple:
    """
    Search e-commerce products via Supabase REST API.
    Returns (buckets, bucket_labels) where buckets is a 2D list of formatted product dicts.
    """
    from app.formatters import format_product
    from app.tools.supabase_product_store import get_product_store

    # Normalise category / product_type defaults
    if category.lower() == "electronics" and not filters.get("product_type"):
        filters = {**filters, "product_type": "laptop"}
    elif category.lower() == "books" and not filters.get("product_type"):
        filters = {**filters, "product_type": "book"}

    # Always set category on the filters so the store can filter correctly
    search_filters = {**filters, "category": category}

    logger.info("search_ecommerce_start", "Searching products via Supabase", {
        "category": category, "filters": search_filters,
        "n_rows": n_rows, "n_per_row": n_per_row,
    })

    try:
        limit = n_rows * n_per_row * 3   # fetch a larger pool for bucketing
        store = get_product_store()
        product_dicts = store.search_products(
            search_filters,
            limit=limit,
            exclude_ids=exclude_ids,
        )

        if not product_dicts:
            logger.warning("search_ecommerce_empty", "No products returned from Supabase", {
                "category": category, "filters": search_filters,
            })
            return [], []

        # KG re-ranking (best-effort, non-blocking)
        kg_candidate_ids: List[str] = []
        try:
            from app.kg_service import get_kg_service
            kg = get_kg_service()
            if kg.is_available():
                kg_filters = {**search_filters}
                search_query = _build_kg_search_query(filters, category)
                kg_candidate_ids, _ = kg.search_candidates(
                    query=search_query, filters=kg_filters, limit=limit,
                )
                if kg_candidate_ids and exclude_ids:
                    exclude_set = set(exclude_ids)
                    kg_candidate_ids = [p for p in kg_candidate_ids if p not in exclude_set]
        except Exception as e:
            logger.warning("kg_search_skipped", f"KG search skipped: {e}", {"error": str(e)})

        # Sort: KG-ranked first, then by price
        if kg_candidate_ids:
            kg_id_to_idx = {pid: i for i, pid in enumerate(kg_candidate_ids)}
            product_dicts.sort(key=lambda p: (
                (0, kg_id_to_idx[p["id"]]) if p["id"] in kg_id_to_idx
                else (1, float(p.get("price", 0) or 0))
            ))
        else:
            product_dicts.sort(key=lambda x: float(x.get("price", 0) or 0))

        product_dicts = _diversify_by_brand(product_dicts)

        try:
            from app.research_compare import generate_recommendation_reasons
            generate_recommendation_reasons(product_dicts, filters=filters, kg_candidate_ids=kg_candidate_ids)
        except Exception:
            pass

        # Bucket into rows
        total = len(product_dicts)
        bucket_size = max(1, total // n_rows)
        buckets = []
        bucket_labels = []
        fmt_domain = "books" if category.lower() == "books" else "laptops"

        for i in range(n_rows):
            start = i * bucket_size
            end = start + n_per_row if i < n_rows - 1 else min(start + n_per_row, total)
            bucket_products = product_dicts[start:end]
            if bucket_products:
                formatted_bucket = [
                    format_product(p, fmt_domain).model_dump(mode="json", exclude_none=True)
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

