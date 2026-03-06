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
    Returns: 'compare' | 'targeted_qa' | 'refine' | 'new_search'

    targeted_qa: user asks which product(s) are best at a specific criterion
                 (e.g. "Which has the best build quality?", "Which is most durable?").
                 Answer = 1-2 winners with detailed reasoning, NOT a table of all products.
    compare:     user wants a side-by-side breakdown of all shown products
                 (e.g. "compare these", "pros and cons of each", "how do they differ?").
    """
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        system_prompt = (
            "You are an intent routing assistant. The user is viewing a list of product recommendations.\n"
            "Classify their follow-up message into one of four categories:\n"
            "1. 'new_search': The user is starting COMPLETELY FRESH — self-contained query unrelated to shown products. "
            "Signals: detailed spec list from scratch, entirely different use case, no anaphoric references to "
            "'these'/'them'/'those' products. Key: message could stand alone as a brand-new search.\n"
            "2. 'refine': The user wants to CHANGE or ADD to the current search filters. "
            "e.g. 'show me cheaper ones', 'I want Apple instead', 'at least 16in screen', 'more RAM'.\n"
            "3. 'targeted_qa': The user asks which product(s) are BEST at a specific dimension or criterion — "
            "expects 1-2 direct picks with reasoning, NOT a rundown of every product. "
            "Signals: 'which has the best X', 'which is most X', 'which is the most X', 'which is best for X', "
            "'which one would you recommend', 'which should I get/pick/choose', 'what has the best X'. "
            "Examples: 'Which has the best build quality?', 'Which is most durable?', "
            "'Which one would you recommend for college?', 'Which has the best display?'.\n"
            "4. 'compare': The user wants a SIDE-BY-SIDE breakdown of all shown products. "
            "e.g. 'compare these', 'how do they compare?', 'what are the differences?', 'specs of each', "
            "'pros and cons of each', 'which is better — A or B?'.\n\n"
            "CRITICAL rules:\n"
            "- 'targeted_qa' → asking for THE BEST one or two; only 1-2 products will be highlighted.\n"
            "- 'compare' → asking for ALL products to be shown side by side.\n"
            "- Use 'new_search' only for fully self-contained new queries.\n"
            "- Default to 'compare' when unsure between compare and targeted_qa.\n"
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
            max_completion_tokens=20,
        )

        data = json.loads(completion.choices[0].message.content)
        intent = data.get("intent", "compare")

        if intent not in ("compare", "targeted_qa", "refine", "new_search"):
            intent = "compare"

        return intent

    except Exception as e:
        logger.error(f"Intent router failed: {e}")
        lower = message.lower()
        if any(kw in lower for kw in _REFINE_KEYWORDS):
            return "refine"
        # targeted_qa fast fallback
        _TARGETED_SIGNALS = (
            "which has the best", "which is the most", "which is most",
            "which has the most", "which would you recommend", "which one should i",
            "which should i get", "which should i pick", "which should i choose",
            "best build quality", "most durable", "most reliable",
        )
        if any(sig in lower for sig in _TARGETED_SIGNALS):
            return "targeted_qa"
        _no_anaphora = not any(ref in lower for ref in ("these", " them", "those", "current", "shown"))
        _has_specs = any(sig in lower for sig in ("rtx ", "gtx ", "ryzen", "i7", "i9", "i5", "32gb", "16gb", "ram", "budget"))
        _has_new_intent = any(sig in lower for sig in ("i want to play", "i need a laptop for", "looking for a laptop that", "need rtx", "gaming laptop with"))
        if _no_anaphora and (_has_new_intent or (_has_specs and ("$" in lower or "budget" in lower))):
            return "new_search"
        return "compare"


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
            # 1000 tokens: worst-case 6 products × ~80 tok narrative each (~480)
            # + JSON structure + UUIDs (~150) + product names (~120) = ~750 tok.
            # No previous cap allowed the model to be verbose (observed 13 s).
            max_completion_tokens=1000,
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


async def generate_targeted_answer(
    products: List[Dict[str, Any]],
    user_message: str,
    domain: str,
) -> tuple[str, list, list]:
    """
    Answer a "which has the best X?" question by identifying the top 1-2 products
    that excel at the user's specific criterion.

    Unlike generate_comparison_narrative (compare mode), this does NOT write a block
    for every product — it selects only the 1-2 winners and explains in detail why
    they win on that specific dimension.

    Returns:
        (narrative_str, selected_ids_list, selected_names_list)
        selected_ids contains only 1-2 product UUIDs, not all products.
    """
    if not products:
        return "I don't have any recommendations to evaluate yet.", [], []

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        spec_sheet = _build_spec_sheet(products, domain)
        n = len(products)

        domain_quality_hints = {
            "laptops": (
                "Build quality signals: chassis material (aluminum/magnesium > plastic), "
                "weight, keyboard travel/feedback, hinge durability, MIL-SPEC certification, "
                "brand reliability (ThinkPad/MacBook/XPS historically strong). "
                "Display signals: resolution, nit brightness, OLED vs IPS vs TN, color gamut. "
                "Performance signals: CPU tier, GPU model, RAM speed, SSD type (NVMe > SATA). "
                "Battery signals: WHr capacity, battery_life_hours, efficiency (ARM > x86)."
            ),
            "vehicles": (
                "Reliability signals: brand/model reliability history, mileage, condition. "
                "Value signals: price vs. average market, fuel efficiency, total cost of ownership."
            ),
            "books": (
                "Quality signals: author reputation, ratings, review sentiment, genre fit."
            ),
        }.get(domain, "Focus on the most important differentiating attributes for the user's criterion.")

        system_prompt = (
            "You are a knowledgeable product advisor. The user is asking which product(s) are BEST at a "
            "specific criterion. Your job is to identify the top 1-2 winners — not to describe every product.\n\n"
            "OUTPUT: Valid JSON with exactly three keys:\n"
            "  'narrative': your recommendation (format below)\n"
            f"  'selected_ids': array of PRODUCT_ID strings for ONLY the top 1-2 winning products "
            f"(copy verbatim from spec sheet). Do NOT list all {n} products.\n"
            "  'selected_names': array of those product names (used as fallback)\n\n"
            "NARRATIVE FORMAT:\n"
            "  Start with the winner:\n"
            "  '**[Product Name]** — [Price]\n"
            "  • [Specific reason 1 — reference real spec value or brand fact]\n"
            "  • [Specific reason 2 — reference real spec value or brand fact]\n"
            "  • [Specific reason 3 if needed]\n'\n"
            "  If there's a genuine close runner-up (not just the second-best by default), add:\n"
            "  '**Runner-up: [Product Name]** — [Price]\n"
            "  • [Why it's close but #2]\n'\n"
            "  End with: 'Best for [user criterion]: [Product Name] — [one direct sentence why].'\n\n"
            "RULES:\n"
            "- selected_ids must contain ONLY 1-2 UUIDs — the actual winners. Do NOT include all products.\n"
            "- If one product clearly wins, return only 1 product in selected_ids.\n"
            "- Only add a runner-up if it genuinely competes on the user's criterion.\n"
            "- Reference real spec values from the spec sheet (not vague claims like 'high quality').\n"
            "- Start the narrative IMMEDIATELY with '**'. No intro sentence.\n"
            "- NEVER include UUID strings in the narrative text — only product names.\n"
            "- Keep bullets concise (1 sentence each), specific, and directly relevant to the criterion.\n"
        )

        user_prompt = (
            f"User question: \"{user_message}\"\n\n"
            f"Available products:\n{spec_sheet}\n"
            f"{domain_quality_hints}\n\n"
            "Identify the top 1-2 products that best answer the user's question. Output the JSON now."
        )

        completion = await client.chat.completions.create(
            model=OPENAI_MODEL,
            response_format={"type": "json_object"},
            **_REASONING_KWARGS,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # 1-2 products × ~150 tokens each + JSON overhead = ~500 tokens max
            max_completion_tokens=600,
        )

        data = json.loads(completion.choices[0].message.content.strip())
        return (
            data.get("narrative", ""),
            data.get("selected_ids", []),
            data.get("selected_names", []),
        )

    except Exception as e:
        logger.error(f"generate_targeted_answer failed: {e}")
        # Minimal fallback: just return the top-rated product
        best = max(products, key=lambda p: float(p.get("rating") or 0), default=None)
        if best:
            name = best.get("name", "Top pick")
            price = best.get("price")
            price_str = f"${price:,.0f}" if price else ""
            fb = f"**{name}**" + (f" — {price_str}" if price_str else "")
            fb += f"\n• Highest-rated option in your results ({float(best.get('rating') or 0):.1f} ★)"
            fb += f"\n\nBest pick: {name} based on overall user ratings."
            return fb, [str(best.get("id") or best.get("product_id", ""))], [name]
        return "I couldn't determine a winner from the current results.", [], []


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
