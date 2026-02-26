"""
Comparison Agent — LLM-powered narrative comparison of recommended products.

Called when the user asks to compare recommendations (e.g. "compare my options",
"which one is better for gaming?", "pros and cons") after recommendations have
been shown.

Uses the same OpenAI client pattern as universal_agent.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

# Model configuration — single model for all LLM calls, set via environment
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
# Default is "" (disabled). Set OPENAI_REASONING_EFFORT=low in .env only if using an o-series model.
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "")
_REASONING_KWARGS = {"reasoning_effort": OPENAI_REASONING_EFFORT} if OPENAI_REASONING_EFFORT else {}

logger = logging.getLogger("comparison_agent")

# ---------------------------------------------------------------------------
# Intent detection helpers (fast, no LLM)
# ---------------------------------------------------------------------------

_COMPARE_KEYWORDS = {
    "compare", "comparison", "versus", " vs ", "vs.", "which is better",
    "which one", "differences", "pros and cons", "trade-offs", "tradeoffs",
    "side by side", "side-by-side", "pros and cons", "compared to",
    "compare my options", "compare these", "compare them",
}

_REFINE_KEYWORDS = {
    "show me more", "more options", "cheaper", "less expensive", "more expensive",
    "bigger screen", "smaller screen", "more ram", "more storage", "different brand",
    "under $", "below $", "budget", "change", "update", "refine",
    "show me similar", "similar items", "other options", "broaden",
}


async def detect_post_rec_intent(message: str) -> str:
    """
    LLM-based intent detection for post-recommendation messages.
    Returns: 'compare' | 'refine' | 'followup_qa'
    """
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        system_prompt = (
            "You are an intent routing assistant. The user is looking at a list of product recommendations.\n"
            "Classify their follow-up message into exactly ONE of these three categories:\n\n"
            "1. 'refine': The user explicitly wants to CHANGE the search filters and run a new search "
            "(e.g., 'show me cheaper ones', 'I want an Apple instead', 'different brand', "
            "'at least 16in screen', 'more ram', 'show me something else'). "
            "ANY request to add, change, or relax a specification MUST route to 'refine'.\n\n"
            "2. 'compare': The user explicitly asks for a side-by-side comparison or asks which one is "
            "better (e.g., 'compare X vs Y', 'which is better for gaming', 'pros and cons of each', "
            "'which should I buy').\n\n"
            "3. 'followup_qa': The user is asking a contextual question about the current recommendations "
            "— suitability for a use case, a specific attribute, worthiness, etc. "
            "(e.g., 'are these good enough for ML?', 'do any of these come in black?', "
            "'will this handle 4K video editing?', 'is 16GB enough for my needs?', "
            "'which one has the longest warranty?', 'are these worth the price?').\n\n"
            "CRITICAL RULES:\n"
            "- 'refine' if the user wants to change/add/relax ANY search criterion.\n"
            "- 'compare' only for explicit head-to-head comparisons or 'which one is better' questions.\n"
            "- 'followup_qa' for suitability questions, attribute queries, and anything else.\n"
            "- Default to 'followup_qa' when uncertain.\n\n"
            "Return valid JSON with a single key 'intent'."
        )

        completion = await client.chat.completions.create(
            model=OPENAI_MODEL,
            **_REASONING_KWARGS,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
        )

        data = json.loads(completion.choices[0].message.content)
        intent = data.get("intent", "followup_qa")

        if intent not in ("compare", "refine", "followup_qa"):
            intent = "followup_qa"

        return intent

    except Exception as e:
        logger.error(f"Intent router failed: {e}")
        lower = message.lower()
        if any(kw in lower for kw in _REFINE_KEYWORDS):
            return "refine"
        if any(kw in lower for kw in _COMPARE_KEYWORDS):
            return "compare"
        return "followup_qa"  # Default to Q&A


# ---------------------------------------------------------------------------
# Spec sheet builder
# ---------------------------------------------------------------------------

def _build_spec_sheet(products: List[Dict[str, Any]], domain: str) -> str:
    """
    Produce a structured plain-text spec sheet for the LLM prompt.
    Keeps it concise — only populated fields.
    """
    lines = []
    for i, p in enumerate(products, 1):
        name = p.get("name") or f"Product {i}"
        brand = p.get("brand", "")
        price = p.get("price")
        price_str = f"${price:,.0f}" if price else "N/A"
        bucket = p.get("bucket_label")

        product_id = p.get("id") or p.get("product_id", "")
        lines.append(f"[{i}] {name} ({brand})")
        lines.append(f"    PRODUCT_ID: {product_id}  ← copy this exactly into selected_ids")
        lines.append(f"    Price: {price_str}")
        if bucket:
            lines.append(f"    Tier/Bucket: {bucket}")

        if domain == "laptops":
            for label, key in [
                ("Processor", "processor"),
                ("RAM", "ram"),
                ("Storage", "storage"),
                ("Storage Type", "storage_type"),
                ("Screen", "screen_size"),
                ("Refresh Rate", "refresh_rate_hz"),
                ("Resolution", "resolution"),
                ("GPU", "gpu"),
                ("Battery", "battery_life"),
                ("OS", "os"),
                ("Weight", "weight"),
            ]:
                val = p.get(key)
                if val is not None:
                    suffix = '"' if key == "screen_size" else (" Hz" if key == "refresh_rate_hz" else "")
                    lines.append(f"    {label}: {val}{suffix}")

        elif domain == "vehicles":
            for label, key in [
                ("Year", "year"), ("Trim", "trim"), ("Mileage", "mileage"),
                ("Fuel Type", "fuel_type"), ("Drivetrain", "drivetrain"),
            ]:
                val = p.get(key)
                if val is not None:
                    lines.append(f"    {label}: {val}")

        elif domain == "books":
            for label, key in [
                ("Author", "author"), ("Genre", "genre"), ("Pages", "pages"),
            ]:
                val = p.get(key)
                if val is not None:
                    lines.append(f"    {label}: {val}")

        rating = p.get("rating")
        if rating:
            lines.append(f"    Rating: {float(rating):.1f} ★")
        lines.append("")  # blank line between products

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM narrative generation
# ---------------------------------------------------------------------------

async def generate_comparison_narrative(
    products: List[Dict[str, Any]],
    user_message: str,
    domain: str,
    mode: str = "compare",
) -> str:
    """
    Generate a rich Markdown narrative for the given products.

    mode="compare"  → side-by-side spec comparison with Best pick (existing)
    mode="features" → per-product feature bullet list + "Great for:" tags
                      (used by "Tell me more" / pros & cons flow)

    Returns a tuple of:
      1. A ready-to-display Markdown string.
      2. A list of product IDs that were actually compared.

    Or a fallback plain-text comparison if the LLM call fails.
    """
    if not products:
        return "I don't have any recommendations to compare yet. Let me search for some first!", [], []

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        n = len(products)

        domain_focus = {
            "laptops": (
                "Focus on: performance vs. price, processor speed for the stated use case, "
                "RAM adequacy for multitasking, storage speed/size, display quality for the task, "
                "battery life for portability, GPU for graphics/ML workloads."
            ),
            "vehicles": (
                "Focus on: reliability, fuel efficiency, total cost of ownership, "
                "comfort for the stated use case, cargo space, safety ratings."
            ),
            "books": (
                "Focus on: writing style, relevance to genre/interest, page count, author reputation."
            ),
        }.get(domain, "Focus on the most important differentiating attributes.")

        if mode == "features":
            # ---------------------------------------------------------------
            # "Tell me more" mode — parallel per-product LLM calls.
            #
            # WHY: A single call for N products generates ~120 tokens × N =
            # 720 tokens sequentially → 6-7 seconds.  N parallel calls each
            # produce ~120 tokens → all finish in parallel → ~1 second total.
            # ---------------------------------------------------------------

            async def _gen_one_product(p: Dict[str, Any]) -> str:
                """Generate feature bullets for a single product (parallel-safe)."""
                name = p.get("name") or "Product"
                price = p.get("price")
                price_str = f"${price:,.0f}" if price else ""
                one_spec = _build_spec_sheet([p], domain).strip()

                sys_p = (
                    "You are a product advisor. Write a SHORT, specific feature overview for this ONE product.\n"
                    "Format exactly:\n"
                    "- [key feature — ≤8 words, reference real specs]\n"
                    "- [key feature — ≤8 words]\n"
                    "- [key feature — ≤8 words]\n"
                    "Great for: [use case 1], [use case 2], [use case 3]\n"
                    "Pros: [1 clear strength]. Cons: [1 honest weakness].\n\n"
                    "Rules: Be specific (e.g. 'Apple M4 — up to 18-hr battery', not 'good performance'). "
                    "Start immediately with '- '. No intro sentence."
                )
                usr_p = (
                    f"Product:\n{one_spec}\n\n"
                    f"User question: \"{user_message}\"\n"
                    f"{domain_focus}"
                )

                try:
                    comp = await client.chat.completions.create(
                        model=OPENAI_MODEL,
                        **_REASONING_KWARGS,
                        messages=[
                            {"role": "system", "content": sys_p},
                            {"role": "user", "content": usr_p},
                        ],
                    )
                    body = comp.choices[0].message.content.strip()
                except Exception as ex:
                    logger.error(f"Feature gen failed for {name}: {ex}")
                    # Spec-based fallback so one failure doesn't blank the card
                    spec_lines = []
                    for lbl, k in [("CPU", "processor"), ("RAM", "ram"),
                                   ("Storage", "storage"), ("GPU", "gpu"),
                                   ("Battery", "battery_life"), ("Rating", "rating")]:
                        if p.get(k):
                            spec_lines.append(f"{lbl}: {p[k]}")
                    body = "\n".join(f"- {s}" for s in spec_lines) or "- No spec data available"

                header = f"**{name}**" + (f" ({price_str})" if price_str else "")
                return f"{header}\n{body}"

            # Fire all product calls in parallel — total time ≈ slowest single call
            results = await asyncio.gather(*[_gen_one_product(p) for p in products])
            narrative = "\n\n".join(str(r) for r in results)
            selected_ids = [str(p.get("id") or p.get("product_id", "")) for p in products]
            selected_names = [str(p.get("name", "")) for p in products]
            return narrative, selected_ids, selected_names

        # -----------------------------------------------------------------------
        # Default compare mode — single call, structured JSON with spec table
        # -----------------------------------------------------------------------
        spec_sheet = _build_spec_sheet(products, domain)
        system_prompt = (
            "You are a helpful product advisor. Compare the recommended products based strictly on what the user asked.\n\n"
            "OUTPUT: Valid JSON with exactly three keys:\n"
            "  'narrative': formatted comparison string (rules below)\n"
            f"  'selected_ids': array of PRODUCT_ID strings (copy verbatim from the spec sheet) for ALL {n} products in the spec sheet\n"
            f"  'selected_names': array of the product name strings for ALL {n} products (used as fallback)\n\n"
            "NARRATIVE FORMAT — one block per product:\n"
            "  '• **[Product Name]**\\n[Spec]: [value] | [Spec]: [value]\\n[1–2 sentence insight specific to the user's criteria]'\n"
            "Separate each product block with a blank line (\\n\\n).\n"
            "After the last product block, on its own line: 'Best pick: [one-sentence recommendation].'\n\n"
            "RULES:\n"
            f"- You MUST write one bullet block for EVERY product in the spec sheet. There are {n} products — include all {n}.\n"
            "- Start IMMEDIATELY with the first '•'. No intro sentence.\n"
            "- Pull spec values directly from the spec sheet. Only include the specs the user asked about.\n"
            "- NEVER include UUIDs or internal IDs in the narrative. Only use product name/brand.\n"
            "- Keep each insight 1–2 sentences, specific, and directly relevant to the user's question.\n"
            "- selected_ids MUST be the exact PRODUCT_ID values from the spec sheet (the UUID strings after 'PRODUCT_ID:').\n"
        )

        user_prompt = (
            f"User context/question: \"{user_message}\"\n\n"
            f"Available recommendations:\n{spec_sheet}\n"
            f"{domain_focus}\n\n"
            "Output the JSON response now."
        )

        completion = await client.chat.completions.create(
            model=OPENAI_MODEL,
            response_format={"type": "json_object"},
            **_REASONING_KWARGS,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        response_text = completion.choices[0].message.content.strip()
        data = json.loads(response_text)
        return (
            data.get("narrative", "Here's the comparison..."),
            data.get("selected_ids", []),
            data.get("selected_names", []),
        )

    except Exception as e:
        logger.error(f"Comparison LLM call failed: {e}")
        # Graceful fallback: structured plain-text comparison — include ALL products
        return (
            _fallback_comparison(products, domain),
            [p.get("id") or p.get("product_id") for p in products if p.get("id") or p.get("product_id")],
            [p.get("name", "") for p in products],
        )


async def generate_followup_answer(
    products: List[Dict[str, Any]],
    user_question: str,
    user_preferences: Dict[str, Any],
    domain: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Generate a conversational answer to a follow-up question about the recommendations.

    Unlike generate_comparison_narrative(), this function:
    - Answers the user's specific question directly (no forced per-product bullets)
    - Takes the user's stated preferences into account ("Given you need 16GB RAM for ML...")
    - Uses prior conversation history for full context
    - Returns a natural Markdown paragraph, not a structured comparison table

    Args:
        products:             Current recommendation pool from session.last_recommendation_data
        user_question:        The user's follow-up question (cleaned, no [ctx:] tag)
        user_preferences:     session.explicit_filters — what the user told us (budget, use_case, etc.)
        domain:               "laptops" | "vehicles" | "books"
        conversation_history: session.conversation_history (last few turns for context)

    Returns:
        A ready-to-display Markdown string answering the question.
    """
    if not products:
        return "I don't have any recommendations loaded yet. What are you looking for?"

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        # Build a human-readable summary of what the user told us
        pref_lines = []
        _PREF_LABELS = {
            "use_case": "Use case", "budget": "Budget",
            "price_max": "Max price", "price_min": "Min price",
            "price_max_cents": "Max price",
            "min_ram_gb": "Min RAM (GB)", "min_storage_gb": "Min storage (GB)",
            "min_screen_size": "Min screen (in)", "max_screen_size": "Max screen (in)",
            "min_screen_inches": "Min screen (in)",
            "brand": "Brand preference", "brands": "Brand preferences",
            "storage_type": "Storage type", "os": "OS preference",
            "good_for_gaming": "Gaming", "good_for_ml": "ML / AI",
            "good_for_creative": "Creative work", "good_for_web_dev": "Web dev",
        }
        for k, v in (user_preferences or {}).items():
            if v in (None, "", False, [], {}) or k.startswith("_"):
                continue
            label = _PREF_LABELS.get(k, k.replace("_", " ").title())
            if k == "price_max_cents":
                v = f"${int(v) // 100:,}"
            elif k == "price_min_cents":
                v = f"${int(v) // 100:,}"
            pref_lines.append(f"  {label}: {v}")
        user_context_str = (
            "**What the user told us:**\n" + "\n".join(pref_lines)
            if pref_lines
            else "No explicit preferences recorded yet."
        )

        # Spec sheet of the shown products
        spec_sheet = _build_spec_sheet(products, domain)

        # Recent conversation (last 6 turns = 3 exchanges) for additional context
        history_str = ""
        if conversation_history:
            recent = [m for m in conversation_history[-6:] if m.get("role") in ("user", "assistant")]
            if recent:
                history_str = "\n**Recent conversation:**\n" + "\n".join(
                    f"  {m['role'].capitalize()}: {m['content'][:200]}"
                    for m in recent
                )

        domain_guidance = {
            "laptops": (
                "Relevant specs for laptops: RAM (multitasking/ML), CPU (performance), "
                "GPU (graphics/ML), storage size/type (speed), battery (portability), screen size."
            ),
            "vehicles": (
                "Relevant factors: reliability, fuel efficiency, total cost of ownership, "
                "comfort, cargo space, safety ratings."
            ),
            "books": "Relevant factors: author credibility, writing style, pages, genre fit.",
        }.get(domain, "Focus on the most important attributes for the user's question.")

        system_prompt = (
            "You are a knowledgeable product advisor. The user has already been shown a set of "
            "recommendations and now has a follow-up question about them. "
            "Answer their question directly and conversationally, referencing the specific products "
            "and their specs where relevant. Leverage the user's stated preferences to personalise "
            "your answer (e.g. 'Since you mentioned needing 16GB RAM for ML work, ...').\n\n"
            "FORMAT RULES:\n"
            "- Answer the question in 2–5 sentences (or a short bullet list if comparing attributes).\n"
            "- Reference product names and key specs — be specific, not generic.\n"
            "- If the answer varies by product, call out the differences briefly.\n"
            "- End with ONE short follow-up suggestion (e.g., 'Want me to show only the ones that qualify?').\n"
            "- Do NOT re-list all products verbatim; answer the question, then stop.\n"
            f"- Domain: {domain}. {domain_guidance}"
        )

        user_prompt = (
            f"{user_context_str}\n"
            f"{history_str}\n\n"
            f"**Current recommendations:**\n{spec_sheet}\n\n"
            f"**User's question:** {user_question}"
        )

        completion = await client.chat.completions.create(
            model=OPENAI_MODEL,
            **_REASONING_KWARGS,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return completion.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"generate_followup_answer failed: {e}")
        # Plain fallback: just list product names with brief context
        names = [p.get("name", "Product") for p in products[:3]]
        return (
            f"Based on your recommendations ({', '.join(names)}), "
            "I wasn't able to generate a detailed answer right now. "
            "You can ask me to compare them, refine your search, or try again."
        )


def _fallback_comparison(products: List[Dict[str, Any]], domain: str) -> str:
    """Plain-text comparison table fallback when LLM is unavailable."""
    lines = ["Here's a quick comparison of your recommendations:\n"]
    for p in products:
        name = p.get("name", "Product")
        price = p.get("price")
        price_str = f"${price:,.0f}" if price else "N/A"
        lines.append(f"**{name}**")
        lines.append(f"  Price: {price_str}")
        if domain == "laptops":
            if p.get("processor"):
                lines.append(f"  CPU: {p['processor']}")
            if p.get("ram"):
                lines.append(f"  RAM: {p['ram']}")
            if p.get("storage"):
                lines.append(f"  Storage: {p['storage']}")
            if p.get("battery_life"):
                lines.append(f"  Battery: {p['battery_life']}")
        if p.get("rating"):
            lines.append(f"  Rating: {float(p['rating']):.1f} ★")
        lines.append("")
    return "\n".join(lines)
