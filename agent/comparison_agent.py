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
import re
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
    "side by side", "side-by-side", "compared to",
    "compare my options", "compare these", "compare them",
    # Natural phrasing variants
    "difference between", "differences between",
    "how do they differ", "how do these differ",
    "contrast", "how does it compare",
    "lay them out", "break down the differences",
    "what sets them apart",
}

_REFINE_KEYWORDS = {
    "show me more", "more options", "cheaper", "less expensive", "more expensive",
    "bigger screen", "smaller screen", "more ram", "more storage", "different brand",
    "under $", "below $", "budget", "change", "update", "refine",
    "show me similar", "similar items", "other options", "broaden",
}


async def parse_compare_query(message: str) -> Dict[str, Any]:
    """
    LLM-based parser for any compare phrasing.
    Handles: "X vs Y", "compare X and Y", "compare X abd Y for Z",
             "X compared to Y focusing on Z", "compare X with Y in terms of Z", etc.

    Returns {"left": str, "right": str, "focus_features": str | None}
    Falls back to simple regex if the LLM call fails.
    """
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        completion = await client.chat.completions.create(
            model=OPENAI_MODEL,
            response_format={"type": "json_object"},
            **_REASONING_KWARGS,
            messages=[
                {"role": "system", "content": (
                    "Extract the two products being compared and any specific features the user wants to focus on.\n"
                    "Return JSON with exactly three keys:\n"
                    "  'left': first product name (string, clean — no feature qualifiers)\n"
                    "  'right': second product name (string, clean — no feature qualifiers)\n"
                    "  'focus_features': the dimensions to compare (string) or null if none specified\n\n"
                    "Handle all phrasings and typos:\n"
                    '  "compare MacBook Air and Dell XPS" → {"left":"MacBook Air","right":"Dell XPS","focus_features":null}\n'
                    '  "compare HP OmniBook 7 Flip abd HP Victus based on price and storage" → {"left":"HP OmniBook 7 Flip","right":"HP Victus","focus_features":"price and storage"}\n'
                    '  "MacBook Air vs Dell XPS for battery life and display" → {"left":"MacBook Air","right":"Dell XPS","focus_features":"battery life and display"}\n'
                    '  "compare iPhone 15 with Samsung S24 in terms of camera" → {"left":"iPhone 15","right":"Samsung S24","focus_features":"camera"}\n'
                    "Do NOT include feature phrases ('based on', 'for', 'focusing on') in the product names."
                )},
                {"role": "user", "content": message},
            ],
            max_completion_tokens=80,
        )
        data = json.loads(completion.choices[0].message.content)
        # Validate — both sides must be non-empty
        if data.get("left") and data.get("right"):
            return data
        raise ValueError("LLM returned empty left or right")

    except Exception as e:
        logger.warning(f"parse_compare_query LLM failed, using regex fallback: {e}")
        # Regex fallback — handles the common separators.
        # IMPORTANT: " abd " (typo for "and") must come before " and " so that
        # "X abd Y based on price and storage" doesn't split at "price AND storage".
        msg_lower = message.lower()
        for sep in (" vs ", " versus ", " vs.", " compared to ", " abd ", " and "):
            if sep in msg_lower:
                idx = msg_lower.index(sep)
                left = re.sub(r'^compare\s+', '', message[:idx], flags=re.IGNORECASE).strip()
                right = message[idx + len(sep):].strip()
                # Guard: if right side is < 2 words it's a feature word, not a product name.
                # Skip this separator and try the next one.
                if len(right.split()) < 2:
                    continue
                # Extract focus features from right side
                focus_m = re.search(
                    r'\s+(?:based on|for|with|focusing on|in terms of)\s+(.+?)$',
                    right, re.IGNORECASE
                )
                focus = focus_m.group(1).strip() if focus_m else None
                if focus_m:
                    right = right[:focus_m.start()].strip()
                return {"left": left, "right": right, "focus_features": focus}
        return {"left": message, "right": "", "focus_features": None}


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
    lower = message.lower().strip()

    # --- Pre-LLM guard: follow-up phrases that can NEVER be a standalone new_search ---
    # Phrases like "what about X", "does it need Y", "can it be Z" are context-dependent
    # follow-ups that reference the existing conversation. Return targeted_qa immediately
    # so the LLM cannot misclassify them as new_search and wipe the session.
    _FOLLOWUP_STARTERS = (
        "what about", "how about", "what if", "does it",
        "is it", "can it", "could it", "do they", "are they",
        "does the", "is the", "is there", "what's the",
        "what are the", "tell me more", "show me more",
        "show me the", "what model", "which model",
        "latest model", "newest model", "refurbished",
    )
    if any(lower.startswith(p) or lower == p for p in _FOLLOWUP_STARTERS):
        return "targeted_qa"
    # Short messages with question words are almost always follow-ups
    if len(lower.split()) <= 6 and lower.endswith("?"):
        return "targeted_qa"

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
            "- Use 'new_search' ONLY for fully self-contained new queries naming a completely different product or use case.\n"
            "- 'new_search' is NEVER correct for: 'what about X', 'how about X', 'what if X', 'does it need X', "
            "'can it be X', 'show me the latest', 'refurbished is fine', 'what model', or any question that depends on context.\n"
            "- Default to 'targeted_qa' when unsure between compare and targeted_qa.\n"
            "- Default to 'refine' when unsure between refine and targeted_qa for filter-change-like messages.\n"
            "- ONLY return 'compare' when the user explicitly asks for a side-by-side or asks about ALL products.\n"
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
        intent = data.get("intent", "targeted_qa")

        if intent not in ("compare", "targeted_qa", "refine", "new_search"):
            intent = "targeted_qa"

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
        # Word-boundary checks for all anaphoric tokens — "them" at sentence
        # start, "currently" (not "current"), "showing" (not "shown") are all
        # correctly excluded.  These patterns mirror the logic in
        # chat_endpoint._message_references_shown_recommendation_set; they are
        # intentionally inlined here to avoid a circular import (chat_endpoint
        # imports this module at the top level).
        _references_current_set = (
            re.search(r'\b(these|those|them)\b', lower) is not None
            or re.search(r'\b(current|shown)\b', lower) is not None
        )
        _has_specs = any(sig in lower for sig in ("rtx ", "gtx ", "ryzen", "i7", "i9", "i5", "32gb", "16gb", "ram", "budget"))
        _has_new_intent = any(sig in lower for sig in ("i want to play", "i need a laptop for", "looking for a laptop that", "need rtx", "gaming laptop with"))
        if not _references_current_set and (_has_new_intent or (_has_specs and ("$" in lower or "budget" in lower))):
            return "new_search"
        # Default to "other" (falls through to UniversalAgent) rather than
        # forcing a comparison table for ambiguous messages.
        return "other"


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
    focus_features: Optional[str] = None,
    brands: Optional[tuple] = None,
    selection_reasons: Optional[Dict[str, str]] = None,
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

        if mode == "brand_compare" and brands and len(brands) == 2:
            # ---------------------------------------------------------------
            # Brand-vs-brand mode: user said "compare Apple and Dell laptops"
            # Products are up to 3 samples per brand used as evidence.
            # Narrative describes brands broadly, NOT just the samples shown.
            # ---------------------------------------------------------------
            left_brand_name, right_brand_name = brands

            # Split samples by brand field
            left_prods = [p for p in products
                          if left_brand_name.lower() in (p.get("brand") or "").lower()][:3]
            right_prods = [p for p in products
                           if right_brand_name.lower() in (p.get("brand") or "").lower()][:3]
            # Fallback: split by position if brand field mismatch
            if not left_prods or not right_prods:
                mid = max(1, len(products) // 2)
                left_prods, right_prods = products[:mid], products[mid:]

            left_spec = _build_spec_sheet(left_prods, domain)
            right_spec = _build_spec_sheet(right_prods, domain)
            focus_note = f"\nFocus specifically on: {focus_features}." if focus_features else ""

            system_prompt = (
                f"You are a helpful product advisor comparing two laptop brands: "
                f"{left_brand_name} and {right_brand_name}.\n"
                "The user wants a BRAND-LEVEL overview, not a comparison of just these specific models.\n"
                "Use the sample products as evidence to describe each brand's typical characteristics.\n\n"
                "FORMAT — use exactly this structure:\n"
                f"**{left_brand_name} laptops**\n"
                "- [typical price range with $ figures from the samples]\n"
                "- [OS ecosystem and key platform strengths]\n"
                "- [typical performance tier and who they suit]\n\n"
                f"**{right_brand_name} laptops**\n"
                "- [typical price range with $ figures from the samples]\n"
                "- [OS ecosystem and key platform strengths]\n"
                "- [typical performance tier and who they suit]\n\n"
                "**Which should you choose?**\n"
                "[1-2 sentences: direct recommendation by use case]\n\n"
                "RULES:\n"
                "- Reference real prices and specs from the samples to back your claims.\n"
                "- Be direct — no filler like 'it depends on your needs'.\n"
                "- Keep each bullet under 20 words."
                + focus_note
            )
            _brand_selection_note = ""
            if selection_reasons:
                _brand_selection_note = "\n\nWhy these brands were selected for comparison:\n" + "\n".join(
                    f"- {name}: {reason}" for name, reason in selection_reasons.items()
                ) + "\nStart with one brief sentence explaining why these brands were chosen for comparison.\n"

            user_prompt = (
                f"User request: \"{user_message}\"\n\n"
                f"Sample {left_brand_name} products from our catalog:\n{left_spec}\n\n"
                f"Sample {right_brand_name} products from our catalog:\n{right_spec}"
                + _brand_selection_note
                + "\n\nWrite the brand comparison now."
            )

            try:
                completion = await client.chat.completions.create(
                    model=OPENAI_MODEL,
                    **_REASONING_KWARGS,
                    max_completion_tokens=500,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                narrative = completion.choices[0].message.content.strip()
            except Exception as _bc_err:
                logger.warning(f"brand_compare LLM failed: {_bc_err}")
                narrative = (
                    f"**{left_brand_name} laptops** — representative range in our catalog:\n"
                    + "\n".join(f"- {p.get('name','')[:60]} (${p.get('price', '?')})" for p in left_prods)
                    + f"\n\n**{right_brand_name} laptops** — representative range:\n"
                    + "\n".join(f"- {p.get('name','')[:60]} (${p.get('price', '?')})" for p in right_prods)
                )

            selected_ids = [str(p.get("id") or p.get("product_id", "")) for p in products]
            selected_names = [str(p.get("name", "")) for p in products]
            return narrative, selected_ids, selected_names

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
                    _asking_battery = "battery" in user_message.lower()
                    name_lower = name.lower()

                    for lbl, k in [("CPU", "processor"), ("RAM", "ram"),
                                   ("Storage", "storage"), ("GPU", "gpu"),
                                   ("Battery", "battery_life")]:
                        if p.get(k):
                            fallback_lines.append(f"{lbl}: {p[k]}")

                    # Battery inference — when user asks about battery and no data is in DB,
                    # infer from product name/OS so the answer stays on-topic.
                    if _asking_battery and not p.get("battery_life"):
                        if "chromebook" in name_lower:
                            fallback_lines.append("Battery: ~10–12 hrs typical (ChromeOS is very power-efficient)")
                        elif any(k in name_lower for k in ("stream", "celeron", "n4020", "n4000")):
                            fallback_lines.append("Battery: ~8–10 hrs typical (efficient low-power Celeron)")
                        elif any(k in name_lower for k in ("ryzen", "amd")):
                            fallback_lines.append("Battery: ~6–9 hrs typical (varies by workload)")
                        elif any(k in name_lower for k in ("macbook", "apple m")):
                            fallback_lines.append("Battery: 15–18 hrs (Apple Silicon is industry-leading)")
                        else:
                            fallback_lines.append("Battery: Not listed — check manufacturer spec sheet")

                    # Always add price context
                    if price_str:
                        fallback_lines.append(f"Price: {price_str}")
                    # Infer from product name keywords when specs are empty
                    if not any(k in ("processor", "ram") and p.get(k) for k in ("processor", "ram")):
                        if "framework" in name_lower:
                            fallback_lines += [
                                "Modular & fully repairable — swap any component",
                                "Right-to-repair friendly design",
                            ]
                        if "gaming" in name_lower or "rog" in name_lower or "strix" in name_lower:
                            fallback_lines.append("Gaming-grade GPU for high-frame-rate play")
                        if "chromebook" in name_lower and not _asking_battery:
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

        _focus_instruction = (
            f"\n\nCRITICAL: The user ONLY wants to compare: {focus_features}. "
            f"Show ONLY these dimensions in the narrative for each product. "
            f"Do NOT mention any other specs (no CPU, RAM, GPU, display, battery, etc.)."
        ) if focus_features else ""

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
            + _focus_instruction
        )

        _selection_note = ""
        if selection_reasons:
            _selection_note = "\n\nWhy these products were selected for comparison:\n" + "\n".join(
                f"- {name}: {reason}" for name, reason in selection_reasons.items()
            ) + "\nOpen your narrative with one brief sentence explaining why these products were chosen (e.g., 'We selected these because one has the best rating and the other the lowest price').\n"

        user_prompt = (
            f"User context/question: \"{user_message}\"\n\n"
            f"Available recommendations:\n{spec_sheet}\n"
            f"{domain_focus}"
            + _selection_note
            + "\n\nOutput the JSON response now."
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
            "You are a knowledgeable product advisor. The user may ask either:\n"
            "  (A) Which product is BEST at a specific criterion — identify 1-2 winners.\n"
            "  (B) A CONCEPTUAL question about a spec (e.g. 'real-world difference between 16GB and 64GB RAM',\n"
            "      'what does NVMe vs SATA mean', 'does 144Hz vs 60Hz matter') — explain the concept first,\n"
            "      then reference the most relevant products from the list to ground the answer.\n\n"
            "Detect which type it is and respond accordingly.\n\n"
            "OUTPUT: Valid JSON with exactly three keys:\n"
            "  'narrative': your answer (format below)\n"
            f"  'selected_ids': array of PRODUCT_ID strings for ONLY the 1-2 most relevant products "
            f"(copy verbatim from spec sheet). Do NOT list all {n} products.\n"
            "  'selected_names': array of those product names (used as fallback)\n\n"
            "NARRATIVE FORMAT FOR TYPE (A) — 'which is best at X?':\n"
            "  Start with the winner:\n"
            "  '**[Product Name]** — [Price]\n"
            "  • [Specific reason 1 — reference real spec value or brand fact]\n"
            "  • [Specific reason 2]\n'\n"
            "  If there's a genuine runner-up: '**Runner-up: [Product Name]** — [Price]\\n• [Why #2]\\n'\n"
            "  End with: 'Best for [criterion]: [Product Name] — [one direct sentence why].'\n\n"
            "NARRATIVE FORMAT FOR TYPE (B) — conceptual spec question:\n"
            "  Open with 2-3 sentences explaining the real-world meaning of the spec difference.\n"
            "  Be concrete: mention actual use cases (e.g. '16GB handles everyday tasks; 64GB is needed\n"
            "  for 4K video editing, running VMs, or heavy data work').\n"
            "  Then add: 'In your current results:' followed by 1-2 bullets referencing specific\n"
            "  products that illustrate the difference.\n\n"
            "RULES:\n"
            "- selected_ids must contain ONLY 1-2 UUIDs — the most relevant products. Never all products.\n"
            "- Reference real spec values from the spec sheet, not vague claims.\n"
            "- NEVER include UUID strings in the narrative text — only product names.\n"
            "- Keep bullets concise (1 sentence), specific, directly relevant to the question.\n"
            "- No intro filler ('Great question!', 'Sure!', etc.) — start the narrative immediately.\n"
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
        msg_lower = user_message.lower()

        # Helper: extract RAM GB from flat product dict
        def _get_ram_gb(p: Dict[str, Any]) -> int:
            raw = p.get("ram") or (p.get("attributes") or {}).get("ram_gb") or ""
            try:
                return int(float(str(raw).lower().replace("gb", "").strip().split()[0]))
            except (ValueError, IndexError, AttributeError):
                return 0

        # ── Conceptual RAM / memory question ───────────────────────────────
        _is_ram_conceptual = (
            any(kw in msg_lower for kw in ("real-world difference", "real world difference", "difference between"))
            and any(kw in msg_lower for kw in ("ram", "memory", "4gb", "8gb", "16gb", "gb"))
        )
        if _is_ram_conceptual:
            four_prods = [p for p in products if 0 < _get_ram_gb(p) <= 4]
            eight_prods = [p for p in products if _get_ram_gb(p) >= 8]
            body = (
                "**4GB vs 8GB RAM — Real-World Impact:**\n\n"
                "- **4GB**: Handles basic web browsing, email, Google Docs, and ChromeOS cloud tasks. "
                "Struggles with 10+ browser tabs, Office + Zoom open simultaneously, or any "
                "Windows 11 multitasking — expect slowdowns.\n"
                "- **8GB**: The minimum recommended for Windows 11 today. Comfortably handles "
                "everyday multitasking — 20+ browser tabs, Office, video calls, music streaming — "
                "without slowdowns. Future-proof for at least 3–4 years.\n\n"
            )
            if four_prods or eight_prods:
                body += "**In your current results:**\n"
                for p in four_prods[:2]:
                    n = (p.get("name") or "")[:65]
                    pr = f"${p['price']:,}" if p.get("price") else "N/A"
                    body += f"- **{n}** ({pr}) — 4 GB: fine for ChromeOS/cloud, limited on Windows\n"
                for p in eight_prods[:2]:
                    n = (p.get("name") or "")[:65]
                    pr = f"${p['price']:,}" if p.get("price") else "N/A"
                    body += f"- **{n}** ({pr}) — 8 GB: solid everyday multitasking\n"
            illustrate = (four_prods[:1] + eight_prods[:1]) or products[:2]
            return (
                body,
                [str(p.get("id") or p.get("product_id", "")) for p in illustrate],
                [p.get("name", "") for p in illustrate],
            )

        # ── Generic fallback: highest-rated with honest context ─────────────
        best = max(products, key=lambda p: float(p.get("rating") or 0), default=None)
        if best:
            name = best.get("name", "Top pick")
            price = best.get("price")
            price_str = f"${price:,.0f}" if price else ""
            rating = float(best.get("rating") or 0)
            fb = f"**{name}**" + (f" — {price_str}" if price_str else "")
            fb += f"\n• Highest-rated in your results ({rating:.1f} ★)"
            fb += f"\n\nBest pick: **{name}** — top user satisfaction score among current results."
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
