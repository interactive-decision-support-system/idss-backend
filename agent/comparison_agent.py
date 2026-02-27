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
    Returns: 'compare' | 'refine' | 'new_search'
    """
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        system_prompt = (
            "You are an intent routing assistant. The user is viewing a list of product recommendations.\n"
            "Classify their follow-up message into one of three categories:\n"
            "1. 'new_search': The user is starting COMPLETELY FRESH with a self-contained query unrelated to the shown products. "
            "Signals: detailed spec list from scratch, entirely different use case, no anaphoric references to 'these'/'them'/'those' products, "
            "e.g. 'I want to play [game] and need RTX 4070, 32GB RAM, a 165Hz display, budget $2000-$2500'. "
            "Key: the message could stand alone as a brand-new search with no context from the current results.\n"
            "2. 'refine': The user wants to CHANGE or ADD to the current search filters (references current context implicitly). "
            "e.g. 'show me cheaper ones', 'I want Apple instead', 'at least 16in screen', 'more RAM'.\n"
            "3. 'compare': The user is asking a follow-up about the CURRENT shown products. "
            "e.g. 'why is Lenovo better?', 'which has better battery?', 'are you sure?'.\n\n"
            "CRITICAL: Use 'new_search' only when the message is a fully self-contained product query with specific new requirements "
            "that does NOT reference the currently shown products. Use 'refine' for adjustments to the existing search. "
            "Default to 'compare' for questions about current results.\n"
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
        intent = data.get("intent", "compare")

        # Guard against weird LLM outputs
        if intent not in ("compare", "refine", "new_search"):
            intent = "compare"

        return intent

    except Exception as e:
        logger.error(f"Intent router failed: {e}")
        # Ultra-fast keyword fallback
        lower = message.lower()
        if any(kw in lower for kw in _REFINE_KEYWORDS):
            return "refine"
        # Detect self-contained new search: explicit spec combo + no current-product references
        _no_anaphora = not any(ref in lower for ref in ("these", " them", "those", "current", "shown"))
        _has_specs = any(sig in lower for sig in ("rtx ", "gtx ", "ryzen", "i7", "i9", "i5", "32gb", "16gb", "ram", "budget"))
        _has_new_intent = any(sig in lower for sig in ("i want to play", "i need a laptop for", "looking for a laptop that", "need rtx", "gaming laptop with"))
        if _no_anaphora and (_has_new_intent or (_has_specs and ("$" in lower or "budget" in lower))):
            return "new_search"
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

                # Provide product description as extra context when specs are sparse.
                desc = (p.get("description") or "")[:250].strip()
                spec_context = one_spec
                if desc:
                    spec_context += f"\n    Description: {desc}"

                sys_p = (
                    "You are a knowledgeable product advisor. Write a brief, scannable overview for this ONE product.\n"
                    "Format exactly:\n"
                    "- [key spec or strength — reference real numbers/names, max 12 words]\n"
                    "- [second key feature — max 12 words]\n"
                    "Great for: [use case 1], [use case 2]\n"
                    "Pros: [one sharp sentence — most important strength, reference a real spec]. "
                    "Cons: [one honest sentence — key trade-off or limitation].\n\n"
                    "Rules:\n"
                    "- Reference actual specs when present (e.g. 'AMD Ryzen 7 5800H — ideal for multitasking and gaming').\n"
                    "- If specs are missing, INFER from product name, price, and brand "
                    "(e.g. '$298 Chromebook — cloud-first, lightweight, not for heavy apps').\n"
                    "- Pros and Cons must each be ONE concise sentence — specific and honest, not generic.\n"
                    "- Start immediately with the first '- '. No intro sentence, no product name header."
                )
                usr_p = (
                    f"Product:\n{spec_context}\n\n"
                    f"User question: \"{user_message}\"\n"
                    f"{domain_focus}"
                )

                try:
                    comp = await client.chat.completions.create(
                        model=OPENAI_MODEL,
                        **_REASONING_KWARGS,
                        max_completion_tokens=300,  # 2 bullets + Great for + 1-sentence Pros + 1-sentence Cons
                        messages=[
                            {"role": "system", "content": sys_p},
                            {"role": "user", "content": usr_p},
                        ],
                    )
                    body = comp.choices[0].message.content.strip()
                except Exception as ex:
                    logger.error(f"Feature gen failed for {name}: {ex}")
                    # Inference-based fallback — always produce 3-4 useful bullets even
                    # when the DB has no specs (e.g. modular/niche laptops like Framework).
                    fallback_lines: List[str] = []
                    for lbl, k in [("CPU", "processor"), ("RAM", "ram"),
                                   ("Storage", "storage"), ("GPU", "gpu"),
                                   ("Battery", "battery_life")]:
                        if p.get(k):
                            fallback_lines.append(f"{lbl}: {p[k]}")
                    # Always add price context
                    if price_str:
                        fallback_lines.append(f"Price: {price_str}")
                    # Infer from product name keywords when specs are empty
                    name_lower = name.lower()
                    if not any(k in ("processor", "ram") and p.get(k) for k in ("processor", "ram")):
                        if "framework" in name_lower:
                            fallback_lines += [
                                "Modular & fully repairable — swap any component",
                                "Right-to-repair friendly design",
                            ]
                        if "gaming" in name_lower or "rog" in name_lower or "strix" in name_lower:
                            fallback_lines.append("Gaming-grade GPU for high-frame-rate play")
                        if "chromebook" in name_lower:
                            fallback_lines += ["ChromeOS — lightweight and cloud-first",
                                               "Long battery life, fanless design"]
                        if "macbook" in name_lower or "apple" in (p.get("brand") or "").lower():
                            fallback_lines += ["Apple Silicon — exceptional perf/watt",
                                               "Tight hardware-software integration"]
                    # Pad to at least 3 bullets
                    if len(fallback_lines) < 3:
                        if price and price < 700:
                            fallback_lines.append("Budget-friendly entry-level option")
                        elif price and price >= 1500:
                            fallback_lines.append("Premium build quality and performance tier")
                        else:
                            fallback_lines.append("Mid-range value with capable everyday performance")
                    rating = p.get("rating")
                    if rating:
                        fallback_lines.append(f"User rating: {float(rating):.1f} ★")
                    body = "\n".join(f"- {s}" for s in fallback_lines[:5])

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
