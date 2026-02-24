"""
Comparison Agent — LLM-powered narrative comparison of recommended products.

Called when the user asks to compare recommendations (e.g. "compare my options",
"which one is better for gaming?", "pros and cons") after recommendations have
been shown.

Uses the same OpenAI client pattern as universal_agent.py.
"""
from __future__ import annotations

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
    Returns: 'compare' | 'refine'
    """
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()
        
        system_prompt = (
            "You are an intent routing assistant. The user is looking at a list of product recommendations.\n"
            "Classify their follow-up message into one of two categories:\n"
            "1. 'refine': The user explicitly wants to CHANGE the search filters and run a new search (e.g., 'show me cheaper ones', 'I want an Apple instead', 'different brand', 'at least 16in screen', 'more ram'). ANY request to add, change, or relax a specification MUST route to 'refine'.\n"
            "2. 'compare': The user is asking a follow-up question about the CURRENT recommendations, comparing them, or asking for details/justification (e.g., 'why is Lenovo better?', 'which has better battery?', 'are you sure?').\n\n"
            "CRITICAL: Default to 'compare' UNLESS there is an explicit request to add, change, or relax any product preference/specification, in which case you must output 'refine'.\n"
            "Return valid JSON with a single key 'intent' mapping to the category string."
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
        intent = data.get("intent", "compare")
        
        # Guard against weird LLM outputs
        if intent not in ("compare", "refine"):
            intent = "compare"
            
        return intent
        
    except Exception as e:
        logger.error(f"Intent router failed: {e}")
        # Ultra-fast keyword fallback
        lower = message.lower()
        if any(kw in lower for kw in _REFINE_KEYWORDS):
            return "refine"
        return "compare"  # Default to discussion on failure


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

        lines.append(f"[{i}] {name} ({brand})")
        lines.append(f"    ID: {p.get('id', '')}")
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
) -> str:
    """
    Generate a rich Markdown narrative comparison of the given products,
    tailored to the user's specific question/intent.

    Returns a tuple of:
      1. A ready-to-display Markdown string.
      2. A list of product IDs that were actually compared.
      
    Or a fallback plain-text comparison if the LLM call fails.
    """
    if not products:
        return "I don't have any recommendations to compare yet. Let me search for some first!", []

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        spec_sheet = _build_spec_sheet(products, domain)
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

        system_prompt = (
            "You are a helpful product advisor. Compare the recommended products based strictly on what the user asked.\n\n"
            "OUTPUT: Valid JSON with exactly two keys:\n"
            "  'narrative': formatted comparison string (rules below)\n"
            "  'selected_ids': array of ID strings for the 2–3 products you compared\n\n"
            "NARRATIVE FORMAT — one block per product:\n"
            "  '• **[Product Name]**\\n[Spec]: [value] | [Spec]: [value]\\n[1–2 sentence insight specific to the user's criteria]'\n"
            "Separate each product block with a blank line (\\n\\n).\n"
            "After the last product block, on its own line: 'Best pick: [one-sentence recommendation].'\n\n"
            "RULES:\n"
            "- Start IMMEDIATELY with the first '•'. No intro sentence.\n"
            "- Pull spec values directly from the spec sheet. Only include the specs the user asked about.\n"
            "- NEVER include UUIDs or internal IDs in the narrative. Only use product name/brand.\n"
            "- Keep each insight 1–2 sentences, specific, and directly relevant to the user's question.\n"
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
        return data.get("narrative", "Here's the comparison..."), data.get("selected_ids", [])

    except Exception as e:
        logger.error(f"Comparison LLM call failed: {e}")
        # Graceful fallback: structured plain-text comparison
        return _fallback_comparison(products, domain), [p.get("id") for p in products[:3] if p.get("id")]


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
