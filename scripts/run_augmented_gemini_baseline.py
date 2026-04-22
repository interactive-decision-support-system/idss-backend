#!/usr/bin/env python3
"""
Augmented Gemini Baseline Evaluator
======================================
Fair ARCHITECTURE comparison: Gemini-2.0-flash given the EXACT SAME products IDSS would show.

This addresses the core problem with the plain GPT baseline:
  Plain GPT/Gemini: no database → hallucinates products → judge rewards fluent hallucinations
  Augmented Gemini: receives IDSS's actual search results → writes narrative from REAL products
                    judge PENALIZES if Gemini invents products not in the catalog

What this comparison measures:
  "Does IDSS's agent pipeline (interview + KG + explanation) produce better
   recommendation quality than Gemini writing about an identical product list?"
  → Pure architecture comparison, database advantage removed.
  → Provider comparison: Gemini-2.0-flash vs GPT-4o-mini under identical conditions.

For each query:
  1. Call IDSS live → get the products IDSS would recommend
  2. Give those same products to Gemini-2.0-flash as context
  3. Score Gemini's response with the catalog-grounding-aware GPT-4o-mini judge
  4. Compare: IDSS quality vs. augmented Gemini quality

Key design choice — JUDGE = GPT-4o-mini for ALL systems (including Gemini's output):
  Gemini judging its own output inflates scores due to self-preference bias.
  Using GPT-4o-mini as the judge across IDSS, GPT+Catalog, AND Gemini+Catalog
  ensures all three systems are scored on an identical scale for paper comparison.

Scoring formula (identical to run_augmented_gpt_baseline.py for direct comparison):
  40%  response type   (deterministic — did it ask/recommend correctly?)
  15%  catalog grounding (heuristic — are catalog products referenced?)
  45%  quality          (GPT-4o-mini judge — needs matching, explanation, tone)

If IDSS quality > augmented Gemini: IDSS's architecture wins on explanation layer.
If augmented Gemini > IDSS: IDSS's response text generation needs improvement.

Usage:
    python scripts/run_augmented_gemini_baseline.py --url http://localhost:8001
    python scripts/run_augmented_gemini_baseline.py --url http://localhost:8001 --save scripts/geval_augmented_gemini.json
    python scripts/run_augmented_gemini_baseline.py --url http://localhost:8001 --group expert --verbose

Prerequisites:
    pip install google-genai
    Add GEMINI_API_KEY=<key> to .env  (get key from https://aistudio.google.com/app/apikey)
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)

try:
    from openai import AsyncOpenAI
except ImportError:
    print("ERROR: openai not installed. Run: pip install openai")
    sys.exit(1)

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    print("ERROR: google-genai not installed. Run: pip install google-genai")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from run_geval import (
        QUERIES,
        PASS_THRESHOLD,
        compute_final_score,
        check_disclosure,
    )
except ImportError as e:
    print(f"ERROR: Cannot import from run_geval.py: {e}")
    sys.exit(1)


# ── ANSI colours ───────────────────────────────────────────────────────────
GREEN = "\033[92m"; RED = "\033[91m"; YEL = "\033[93m"; CYN = "\033[96m"
BOLD  = "\033[1m";  RST = "\033[0m"

def color_score(s: float) -> str:
    c = GREEN if s >= PASS_THRESHOLD else (YEL if s >= 0.4 else RED)
    return f"{c}{s:.3f}{RST}"


# ── Gemini model constant ──────────────────────────────────────────────────
# gemini-2.0-flash is the "mini" tier — fast, cheap, comparable to gpt-4o-mini.
# Update this constant to benchmark a different Gemini model (e.g. gemini-1.5-pro).
GEMINI_MODEL = "gemini-2.5-flash-lite"   # 4K RPM / Unlimited RPD on paid tier


# ── Augmented Gemini system prompt ────────────────────────────────────────
# Identical content to run_augmented_gpt_baseline.py AUGMENTED_SYSTEM so
# both models receive the same instructions — any quality gap is model capability,
# not prompt differences.
AUGMENTED_GEMINI_SYSTEM = """\
You are a helpful online shopping assistant for laptops.
You will be given a list of laptops CURRENTLY AVAILABLE IN THE STORE CATALOG,
followed by a customer's shopping request.

CRITICAL RULES:
1. You MUST only recommend products from the provided catalog — do NOT invent, hallucinate,
   or suggest products not explicitly listed below.
2. Reference products by their actual names and prices from the catalog.
3. Recommend 3–5 products with specific reasoning for each.
4. If the customer's request is too vague (one or two words with no context), ask ONE
   focused clarifying question instead of recommending.
5. Be concise, warm, and match the customer's communication style.
"""


# ── Catalog-aware quality judge prompts ────────────────────────────────────
# IMPORTANT: The judge is GPT-4o-mini, NOT Gemini.
# Keeping the same judge across all systems (IDSS, GPT+Catalog, Gemini+Catalog)
# ensures all quality scores are on an identical scale for paper comparison.
# These prompts are copied verbatim from run_augmented_gpt_baseline.py.
AUGMENTED_JUDGE_SYSTEM = """\
You are an expert evaluator for a laptop shopping assistant.
The assistant was given a SPECIFIC LIST of products available in the store catalog
and was instructed to only recommend from that list.

Evaluate the response on these criteria (score 0–10):
1. CATALOG GROUNDING (critical, −3 per invented product):
   Does the response only recommend products that appear in the catalog list provided?
   Inventing or hallucinating products not in the list is a critical failure.
2. NEEDS MATCHING (0–4 points):
   How well do the recommended products match the user's stated requirements?
   Consider: use case, budget, specs, constraints.
3. EXPLANATION QUALITY (0–3 points):
   Are the reasons for each recommendation specific, accurate, and helpful?
4. TONE AND HELPFULNESS (0–3 points):
   Is the response appropriately concise, friendly, and matched to the user's style?

Write 2–3 sentences of reasoning.
Then output EXACTLY this JSON on the last line:
{"score": <0-10>, "reason": "<≤12 words summarizing key issue or strength>"}
"""

AUGMENTED_JUDGE_USER = """\
User query: {message}

Expected behavior: {quality_note}

Products available in catalog:
{catalog_str}

Assistant response:
  Type: {rtype}
  Full response:
{response_text}

Score 0-10:"""


# ── Product formatting ──────────────────────────────────────────────────────
def format_catalog(products: List[Dict], max_items: int = 8) -> str:
    """Format a list of product dicts into a readable catalog string.

    Identical to run_augmented_gpt_baseline.format_catalog — both models receive
    the same catalog text so any quality gap is model capability, not presentation.

    Args:
        products:  List of product dicts from IDSS API response.
        max_items: Cap at this many products to keep prompt size manageable.

    Returns:
        Multi-line string like "1. Dell Inspiron 15  —  $799\n   [RAM:8GB, ...]"
    """
    if not products:
        return "  (no products available)"
    lines = []
    for i, p in enumerate(products[:max_items], 1):
        brand = (p.get("brand") or "").strip()
        name  = (p.get("name")  or "")[:65].strip()
        # price may come as 'price' (live response) or 'price_value' (stored result)
        price = p.get("price") or p.get("price_value")

        # Avoid "Dell Dell Inspiron" — strip brand prefix from name if present
        display_name = name
        if brand and name.lower().startswith(brand.lower()):
            display_name = name[len(brand):].strip(" -–")

        line = f"{i}. {brand} {display_name}"
        if price:
            line += f"  —  ${price}"
        lines.append(line)

        # Append key specs as a compact bracketed line below the product name
        specs = []
        attrs   = p.get("laptop", {}).get("attributes", {}) if isinstance(p.get("laptop"), dict) else {}
        ram_gb  = attrs.get("ram_gb") or p.get("ram_gb")
        storage = (p.get("laptop", {}) or {}).get("specs", {}).get("storage") if isinstance(p.get("laptop"), dict) else None
        gpu     = attrs.get("gpu_model") or p.get("gpu_model") or ""
        os_     = (p.get("laptop", {}) or {}).get("specs", {}).get("os", "") if isinstance(p.get("laptop"), dict) else ""
        if ram_gb:
            specs.append(f"RAM:{ram_gb}GB")
        if storage:
            specs.append(f"Storage:{str(storage)[:10]}")
        if gpu:
            specs.append(f"GPU:{gpu[:20]}")
        if os_:
            specs.append(f"OS:{os_[:15]}")
        if specs:
            lines.append(f"   [{', '.join(specs)}]")

    return "\n".join(lines)


def build_augmented_user_message(query_message: str, catalog_str: str) -> str:
    """Combine the product catalog and the customer's request into a single user message.

    The catalog comes first so Gemini reads it before the request — same ordering
    as run_augmented_gpt_baseline.py to ensure identical context framing.
    """
    return f"{catalog_str}\n\nCustomer request: {query_message}"


# ── Catalog grounding check (deterministic heuristic) ─────────────────────
def check_catalog_grounding(response_text: str, products: List[Dict]) -> Tuple[float, str]:
    """Heuristic: does Gemini's response reference products from the catalog or hallucinate?

    Logic:
    - Builds a keyword set from brand names + significant product name words.
    - Counts how many catalog keywords appear in the response.
    - Checks for a hardcoded list of well-known products NOT in our catalog.
      Matching one of those is a hallucination signal.

    Returns:
        (score 0-1, note string)
        1.0  — three or more catalog keywords found (strong grounding)
        0.7  — one or two catalog keywords (weak grounding)
        0.4  — no catalog keywords (model may be ignoring context)
        0.0–0.6 — potential hallucination detected (−0.2 per hallucination)
    """
    if not products:
        # No products to check against — can't penalise
        return 1.0, "no catalog to check"

    text_lower = response_text.lower()

    # Build catalog keyword set: brand names + significant product name words (len > 4)
    catalog_keywords: set = set()
    for p in products:
        brand = (p.get("brand") or "").lower().strip()
        name  = (p.get("name")  or "").lower()
        if brand and brand not in ("new", "recertified", "used", "renewed"):
            catalog_keywords.add(brand)
        words = [w for w in name.split() if len(w) > 4 and w.isalpha()]
        catalog_keywords.update(words[:6])

    hits = sum(1 for kw in catalog_keywords if kw in text_lower)

    # Hardcoded hallucination signals: well-known laptop names that are unlikely
    # to be in our budget-oriented catalog at the exact spec/price mentioned.
    known_hallucinations = [
        "macbook pro 16", "macbook air m2", "dell xps 15", "thinkpad x1 carbon",
        "asus zenbook 14", "microsoft surface laptop", "lg gram 17",
        "razer blade 15", "alienware m15",
    ]
    catalog_all_names = " ".join((p.get("name") or "").lower() for p in products)
    potential_hallucinations = [
        s for s in known_hallucinations
        if s not in catalog_all_names and s in text_lower
    ]

    if potential_hallucinations:
        score = max(0.0, 0.6 - 0.2 * len(potential_hallucinations))
        note = f"⚠ possible hallucinations: {', '.join(potential_hallucinations)}"
    elif hits == 0:
        score = 0.4
        note = "no catalog keywords found in response (may not be using catalog)"
    elif hits >= 3:
        score = 1.0
        note = f"✓ {hits} catalog keywords matched"
    else:
        score = 0.7
        note = f"~ {hits} catalog keyword(s) matched"

    return score, note


# ── Brand exclusion check for free-text responses ─────────────────────────
def check_brand_from_text(
    response_text: str,
    must_not_brands: List[str],
) -> Tuple[Optional[float], str]:
    """
    Deterministic brand-exclusion check for text-only responses (GPT, Gemini).

    Scans the response for excluded brand names appearing in a recommendation
    context (i.e. NOT preceded by a negation like "no HP", "avoid HP",
    "not HP", "without HP", "except HP", "excluding HP", "excluded HP",
    "non-HP").

    Returns:
        (None,   note)  – no exclusions specified for this query
        (0.0,    note)  – excluded brand found in recommendation context
        (1.0,    note)  – no excluded-brand violations detected

    Design notes:
    - We look for the brand name as a whole word (\\b) so "HP" won't match "HDPI".
    - Negation window: up to 5 tokens before the brand name.
    - Both present- and past-tense negation forms are covered.
    - Case-insensitive throughout.
    """
    if not must_not_brands:
        return None, "no exclusions to check"

    text = response_text.lower()
    violations: List[str] = []

    # Tokens within 5 words before the brand name that signal "not recommending".
    # Both present and past tense forms are included (e.g. "excluding" and "excluded").
    _NEGATION_WORDS = frozenset({
        "no", "not", "avoid", "avoiding",
        "without", "except",
        "excluding", "excluded",
        "skip", "skipping", "skipped",
        "omit", "omitting", "omitted",
        "drop", "dropping", "dropped",
        "never", "none",
        "want",                     # handles "don't want HP"
    })

    for brand in must_not_brands:
        brand_l = brand.lower()
        for m in re.finditer(rf'\b{re.escape(brand_l)}\b', text):
            start = m.start()
            # "non-<brand>" pattern — hyphen immediately before brand is a negation.
            if start > 0 and text[start - 1] == "-":
                break  # negated; stop scanning this brand

            # Look at up to 5 words before the brand for negation tokens.
            window = text[max(0, start - 40): start]
            window_tokens = window.split()[-5:]
            expanded = " ".join(window_tokens)
            has_dont = bool(re.search(r"\bdon'?t\b", expanded))
            is_negated = has_dont or any(tok in _NEGATION_WORDS for tok in window_tokens)
            if not is_negated:
                violations.append(brand)
                break  # one positive violation per brand is enough

    if violations:
        return 0.0, f"✗ Excluded brand in recommendation context: {', '.join(violations)}"
    return 1.0, (
        f"✓ No excluded brands ({', '.join(must_not_brands)}) in recommendation context"
    )


# ── IDSS call ───────────────────────────────────────────────────────────────
async def get_idss_products_async(
    client: httpx.AsyncClient,
    base_url: str,
    message: str,
    sem: asyncio.Semaphore,
) -> Tuple[List[Dict], str, float]:
    """Call IDSS chat endpoint and extract the recommended products.

    Returns:
        (products_flat_list, response_type, elapsed_s)
        products_flat_list: all products across recommendation groups, flattened.
        response_type:      "recommendations", "question", "research", etc.
        elapsed_s:          wall-clock seconds for the IDSS call.
    """
    async with sem:
        t0 = time.perf_counter()
        session_id = str(uuid.uuid4())
        try:
            resp = await client.post(
                f"{base_url}/chat",
                json={"message": message, "session_id": session_id},
                timeout=90,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return [], "error", time.perf_counter() - t0

        elapsed = time.perf_counter() - t0
        rtype   = data.get("response_type", "unknown")
        recs    = data.get("recommendations") or []
        products_flat: List[Dict] = []
        for group in recs:
            if isinstance(group, list):
                products_flat.extend(group)
        return products_flat, rtype, elapsed


# ── Gemini call ─────────────────────────────────────────────────────────────
async def call_augmented_gemini_async(
    gemini_client: Any,   # google.genai.Client
    message: str,
    products: List[Dict],
    sem: asyncio.Semaphore,
) -> Tuple[str, int, str]:
    """Send query + product catalog to Gemini-2.0-flash.

    Gemini SDK differences from OpenAI:
    - system_instruction goes in GenerateContentConfig, not as a message role.
    - contents is a plain string (user message with catalog prepended).
    - response.text can be None if the safety filter blocks a response — guard with `or ""`.

    Retry logic:
    - On 429 (rate limit), backs off 15s then 30s before giving up.
    - Free tier limit: 15 RPM for gemini-2.0-flash. Keep --concurrency ≤ 4.

    Returns:
        (response_text, elapsed_ms, inferred_type)
    """
    async with sem:
        catalog_str = format_catalog(products)
        user_msg    = build_augmented_user_message(message, catalog_str)

        # Gemma models don't support system_instruction — prepend system prompt to contents.
        is_gemma = "gemma" in GEMINI_MODEL.lower()
        if is_gemma:
            contents       = AUGMENTED_GEMINI_SYSTEM + "\n\n" + user_msg
            gen_config     = genai_types.GenerateContentConfig(temperature=0.3, max_output_tokens=600)
        else:
            contents       = user_msg
            gen_config     = genai_types.GenerateContentConfig(
                system_instruction=AUGMENTED_GEMINI_SYSTEM, temperature=0.3, max_output_tokens=600
            )

        t0   = time.perf_counter()
        text = "[ERROR: not attempted]"
        for attempt in range(5):   # up to 5 attempts on rate-limit errors
            try:
                response = await asyncio.wait_for(
                    gemini_client.aio.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=contents,
                        config=gen_config,
                    ),
                    timeout=60,  # 60s per call — prevents indefinite hangs
                )
                # response.text is None when the safety filter blocks output
                text = (response.text or "").strip()
                break
            except asyncio.TimeoutError:
                text = "[ERROR: TIMEOUT — Gemini call exceeded 60s]"
                break
            except Exception as e:
                err_str = str(e)
                # Distinguish daily quota exhaustion from per-minute rate limit.
                # Daily quota: "exceeded your current quota" (billing/plan limit) — no point retrying.
                # RPM rate limit: "RATE_LIMIT_EXCEEDED" or generic 429 — retry with back-off.
                is_daily_quota = "exceeded your current quota" in err_str or "check your plan" in err_str
                is_rpm_limit   = ("429" in err_str or "quota" in err_str.lower()) and not is_daily_quota
                if is_daily_quota:
                    text = f"[ERROR: DAILY_QUOTA_EXHAUSTED — enable billing at https://aistudio.google.com]"
                    break  # fail fast, don't waste time retrying
                elif is_rpm_limit and attempt < 4:
                    # Back-off: 15s, 30s, 60s, 120s — per-minute rate limit recovers quickly
                    wait = [15, 30, 60, 120][attempt]
                    await asyncio.sleep(wait)
                    continue
                text = f"[ERROR: {e}]"
                break

        # Throttle: stay under 15 RPM free tier limit (4.5s between calls ≈ 13 RPM).
        # Sleep INSIDE the semaphore context so the slot stays locked during the delay,
        # preventing the next queued request from immediately firing.
        await asyncio.sleep(4.5)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Infer response type from text — same regex as GPT version for comparability
        rtype = "recommendations"
        if text.rstrip().endswith("?") or re.search(
            r"(what|which|how|could you).{0,60}\?", text, re.I
        ):
            if not re.search(r"^\s*[\-\*\d]+[\.\)]\s+\S", text, re.M):
                rtype = "question"

        return text, elapsed_ms, rtype


# ── GPT-4o-mini judge for Gemini's response ────────────────────────────────
async def score_gemini_quality_async(
    oai: AsyncOpenAI,         # GPT-4o-mini judge (NOT the Gemini client)
    query: Dict,
    gemini_text: str,         # the response text Gemini produced
    products: List[Dict],
    rtype: str,
) -> Tuple[float, str, Dict[str, int]]:
    """Score Gemini's response using the GPT-4o-mini judge.

    Intentionally uses GPT-4o-mini as judge (same as all other baselines) so
    IDSS, GPT+Catalog, and Gemini+Catalog are all scored on an identical scale.
    Uses the same catalog-grounding-aware judge prompt from run_augmented_gpt_baseline.py.

    Returns:
        (quality_score 0-1, reason_str, usage_token_counts)
    """
    catalog_str  = format_catalog(products)
    quality_note = query.get("quality_note", "")

    prompt = AUGMENTED_JUDGE_USER.format(
        message=query["message"],
        quality_note=quality_note,
        catalog_str=catalog_str,
        rtype=rtype,
        response_text=gemini_text[:1500],
    )

    _zero: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    for _attempt in range(4):
        try:
            completion = await oai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": AUGMENTED_JUDGE_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.1,
                max_tokens=200,
            )
            usage: Dict[str, int] = {
                "prompt_tokens":     getattr(completion.usage, "prompt_tokens",     0) or 0,
                "completion_tokens": getattr(completion.usage, "completion_tokens", 0) or 0,
            }
            raw = (completion.choices[0].message.content or "").strip()
            # The judge outputs reasoning followed by a JSON line — take the last JSON line
            for line in reversed(raw.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    data = json.loads(line)
                    score_10 = float(data.get("score", 5))
                    return max(0.0, min(10.0, score_10)) / 10.0, data.get("reason", ""), usage
            return 0.5, f"parse error: {raw[-60:]}", _zero
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "rate_limit" in err_str.lower() or "RateLimitError" in type(e).__name__
            if is_rate_limit and _attempt < 3:
                wait = (2 ** _attempt) * 15  # 15s, 30s, 60s
                await asyncio.sleep(wait)
                continue
            return 0.5, f"judge error: {e}", _zero
    return 0.5, "max retries exceeded", _zero


# ── Type score ─────────────────────────────────────────────────────────────
def check_response_type_augmented(
    query: Dict,
    rtype: str,
    has_products: bool,
) -> Tuple[float, str]:
    """Check whether Gemini's response type matches what was expected.

    Mirrors run_augmented_gpt_baseline.check_response_type_augmented exactly.
    When IDSS returned no products (interview mode), type checking is N/A.

    Returns:
        (type_score 0-1, note_str)
    """
    exp_recs = query.get("expect_recs_on_first", False)
    exp_q    = query.get("expect_question", False)

    if not has_products:
        # IDSS was in interview mode — we have no products to give Gemini,
        # so a recommendation vs question distinction isn't meaningful here.
        return 0.75, "~ IDSS returned no products (interview query); type check N/A"

    if exp_recs and not exp_q:
        if rtype == "recommendations":
            return 1.0, "✓ Gave recommendations as expected"
        else:
            return 0.0, f"✗ Expected recommendations, got {rtype!r}"
    elif exp_q and not exp_recs:
        if rtype == "question":
            return 1.0, "✓ Asked clarifying question as expected"
        else:
            return 0.0, f"✗ Expected question, got {rtype!r}"
    else:
        # Neither expected — either is fine
        return 1.0, f"~ Either acceptable; got {rtype}"


# ── Main evaluation loop ────────────────────────────────────────────────────
async def run_augmented_gemini(
    queries:     List[Dict],
    base_url:    str,
    save_path:   Optional[str],
    concurrency: int,
    verbose:     bool,
) -> List[Dict]:
    """Run the full augmented Gemini evaluation pipeline.

    Three phases:
      Phase 1 — Fetch products from IDSS for every query (parallel).
      Phase 2 — Send each query + products to Gemini-2.0-flash (parallel).
      Phase 3 — Score each Gemini response with the GPT-4o-mini judge (parallel).
    """
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        print("ERROR: OPENAI_API_KEY not set (needed for GPT-4o-mini judge).")
        sys.exit(1)

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        print("ERROR: GEMINI_API_KEY not set. Add GEMINI_API_KEY=<key> to your .env")
        print("       Get a key at https://aistudio.google.com/app/apikey")
        sys.exit(1)

    # Two separate clients: Gemini for generation, OpenAI for judging
    oai           = AsyncOpenAI(api_key=openai_key)
    gemini_client = genai.Client(api_key=gemini_key)

    idss_sem   = asyncio.Semaphore(concurrency)
    # Gemini free tier: 15 RPM. Serial + 4.5s delay ≈ 13 RPM — stays safely under limit.
    # Independent of --concurrency so IDSS/judge phases still run at full concurrency.
    gemini_sem = asyncio.Semaphore(1)

    total = len(queries)
    print(f"\n{BOLD}Augmented Gemini Baseline Evaluator{RST}")
    print(f"  Generator  : {GEMINI_MODEL}")
    print(f"  Judge      : gpt-4o-mini  (same judge as all other baselines)")
    print(f"  Queries    : {total}")
    print(f"  IDSS URL   : {base_url}")
    print(f"  Concurrency: {concurrency}")
    print(f"  Purpose    : Fair architecture comparison (same catalog, different model)\n")

    # ── Phase 1: Collect IDSS products for all queries ──────────────────────
    print(f"  {CYN}Phase 1 — Calling IDSS to get products for {total} queries…{RST}")
    t1 = time.perf_counter()
    async with httpx.AsyncClient(timeout=90) as client:
        tasks = [
            get_idss_products_async(client, base_url, q["message"], idss_sem)
            for q in queries
        ]
        idss_results = await asyncio.gather(*tasks)
    print(f"  Done in {time.perf_counter()-t1:.1f}s\n")

    # ── Phase 2: Call Gemini with each query + products ──────────────────────
    print(f"  {CYN}Phase 2 — Calling {GEMINI_MODEL} with IDSS products…{RST}")
    t2 = time.perf_counter()
    gemini_results = await asyncio.gather(*[
        call_augmented_gemini_async(
            gemini_client, q["message"], idss_result[0], gemini_sem
        )
        for q, idss_result in zip(queries, idss_results)
    ])
    print(f"  Done in {time.perf_counter()-t2:.1f}s\n")

    # ── Phase 3: Score all Gemini responses ────────────────────────────────
    print(f"  {CYN}Phase 3 — Scoring {total} responses (GPT-4o-mini judge)…{RST}")
    t3 = time.perf_counter()

    async def score_one(idx: int) -> Dict:
        """Score a single Gemini response: type check + grounding check + quality judge."""
        q                                      = queries[idx]
        products, idss_rtype, idss_elapsed     = idss_results[idx]
        gemini_text, gemini_elapsed_ms, gemini_rtype = gemini_results[idx]

        has_products = len(products) > 0

        # 1. Type score (deterministic)
        type_score, type_note = check_response_type_augmented(
            q, gemini_rtype, has_products
        )

        # 2. Catalog grounding score (deterministic heuristic)
        grounding_score, grounding_note = check_catalog_grounding(
            gemini_text, products
        )

        # 3. Brand exclusion check — text-parsed for this free-text baseline.
        # Returns None when the query has no brand exclusions (no penalty applied).
        must_not = q.get("must_not_contain_brands", [])
        brand_score_text, brand_note_text = check_brand_from_text(gemini_text, must_not)

        # 4. Quality score (GPT-4o-mini judge)
        quality_score, reason, usage = await score_gemini_quality_async(
            oai, q, gemini_text, products, gemini_rtype
        )

        # Disclosure check — only active for catalog_impossible group queries.
        # Gemini receives IDSS's product list; check if it explicitly discloses
        # the constraint gap or silently recommends over-budget products.
        _resp_for_disclosure = {"message": gemini_text, "recommendations": products}
        disclosure_score, disclosure_note = check_disclosure(q, _resp_for_disclosure)

        # Final composite score — unchanged from the existing formula.
        # brand_score_text is a separate diagnostic field; it does NOT change the
        # composite score so historical results remain directly comparable.
        if disclosure_score is not None:
            final = 0.40 * type_score + 0.15 * grounding_score + 0.20 * disclosure_score + 0.25 * quality_score
        else:
            final = 0.40 * type_score + 0.15 * grounding_score + 0.45 * quality_score

        return {
            "id":                 q["id"],
            "group":              q["group"],
            "label":              q["label"],
            "message":            q["message"],
            "score":              round(final, 4),
            "type_score":         type_score,
            "grounding_score":    round(grounding_score, 4),
            # brand_score_text: None = no exclusion; 0.0 = violation; 1.0 = clean.
            "brand_score_text":   brand_score_text,
            "brand_note_text":    brand_note_text,
            "quality_score":      quality_score,
            "disclosure_score":   disclosure_score,
            "disclosure_note":    disclosure_note,
            "type_note":          type_note,
            "grounding_note":     grounding_note,
            "reason":             reason,
            "n_products_given":   len(products),
            "idss_rtype":         idss_rtype,
            "gemini_rtype":       gemini_rtype,
            "idss_elapsed_s":     round(idss_elapsed, 2),
            "gemini_elapsed_ms":  gemini_elapsed_ms,
            # Judge token counts for GPT-4o-mini (used for cost calculation)
            "prompt_tokens":      usage.get("prompt_tokens",     0),
            "completion_tokens":  usage.get("completion_tokens", 0),
            "gemini_text_preview": gemini_text[:300],
        }

    # Limit concurrent judge calls to 8 to avoid 429 rate-limit bursts
    _judge_sem = asyncio.Semaphore(8)
    async def score_one_limited(idx: int) -> Dict:
        async with _judge_sem:
            return await score_one(idx)
    scored = await asyncio.gather(*[score_one_limited(i) for i in range(total)])
    scored = sorted(scored, key=lambda r: r["id"])
    print(f"  Done in {time.perf_counter()-t3:.1f}s\n")

    # ── Per-query output ───────────────────────────────────────────────────
    print(f"{'─'*76}")
    for r in scored:
        status = f"{GREEN}PASS{RST}" if r["score"] >= PASS_THRESHOLD else f"{RED}FAIL{RST}"
        prods_note = f"+{r['n_products_given']}prods" if r["n_products_given"] else "no-prods"
        print(
            f"  Q{r['id']:3d} [{r['group']:15s}] {status}  "
            f"final={color_score(r['score'])}  "
            f"type={r['type_score']:.2f}  "
            f"gnd={r['grounding_score']:.2f}  "
            f"qual={r['quality_score']:.2f}  "
            f"[{prods_note}]"
        )
        print(f"       {r['type_note']}")
        print(f"       grounding: {r['grounding_note']}")
        if r["brand_score_text"] is not None:
            print(f"       brand(text): {r['brand_note_text']}")
        if verbose:
            print(f"       reason: {r['reason']}")
            print(f"       Gemini: {r['gemini_text_preview'][:120].replace(chr(10), ' ')}")
    print(f"{'─'*76}\n")

    # ── Summary ────────────────────────────────────────────────────────────
    def stats(subset: List[Dict]) -> Tuple[int, float, float, float, float, float]:
        """Return (n, avg_score, pass_pct, avg_quality, avg_grounding, avg_brand_text)."""
        if not subset:
            return 0, 0.0, 0.0, 0.0, 0.0, 0.0
        n      = len(subset)
        avg    = sum(r["score"]           for r in subset) / n
        pct    = 100.0 * sum(1 for r in subset if r["score"] >= PASS_THRESHOLD) / n
        qual   = sum(r["quality_score"]   for r in subset) / n
        ground = sum(r["grounding_score"] for r in subset) / n
        # Average brand_score_text only over queries that have brand exclusions.
        brand_checks = [r["brand_score_text"] for r in subset if r["brand_score_text"] is not None]
        brand_avg = sum(brand_checks) / len(brand_checks) if brand_checks else float("nan")
        return n, avg, pct, qual, ground, brand_avg

    all_  = scored
    recs_ = [r for r in scored if r["idss_rtype"] == "recommendations"]
    na_   = [r for r in scored if r["idss_rtype"] != "recommendations"]

    na, aa, pa, qa, ga, ba = stats(all_)
    nr, ar, pr, qr, gr, br = stats(recs_)

    print(f"\n{BOLD}  Augmented Gemini Baseline — Summary{RST}\n")
    print(f"  {'Category':<35} {'N':>4}  {'AvgScore':>8}  {'Pass%':>7}  {'AvgQual':>8}  {'AvgGround':>9}  {'BrandText':>9}")
    print(f"  {'─'*82}")
    brand_str   = f"{ba:>9.3f}" if ba == ba else "       N/A"  # nan guard
    brand_r_str = f"{br:>9.3f}" if br == br else "       N/A"
    print(f"  {'All queries':<35} {na:>4}  {aa:>8.3f}  {pa:>6.1f}%  {qa:>8.3f}  {ga:>9.3f}  {brand_str}")
    print(f"  {'Rec queries (IDSS gave products)':<35} {nr:>4}  {ar:>8.3f}  {pr:>6.1f}%  {qr:>8.3f}  {gr:>9.3f}  {brand_r_str}")
    if na_:
        print(f"  {'Interview queries (no products)':<35} {len(na_):>4}  (skipped — IDSS in interview mode)")

    # Per-group breakdown
    groups = sorted(set(r["group"] for r in scored))
    print(f"\n  Per-group (rec queries only):")
    print(f"  {'Group':<20} {'N':>4}  {'AvgScore':>8}  {'AvgQual':>8}  {'AvgGround':>9}")
    print(f"  {'─'*56}")
    for g in groups:
        gs = [r for r in recs_ if r["group"] == g]
        if gs:
            n, a, p, q_, gnd, br = stats(gs)
            print(f"  {g:<20} {n:>4}  {a:>8.3f}  {q_:>8.3f}  {gnd:>9.3f}")

    # Highlight key comparison — how does Gemini quality compare to IDSS?
    if recs_:
        print(f"\n  {BOLD}Key Comparison (recommendation queries only):{RST}")
        print(f"  Augmented Gemini avg quality : {qr:.3f}")
        print(f"  (Run compare_evals.py --augmented-gemini to see IDSS vs Gemini+Catalog side-by-side)")
        print(f"\n  {YEL}Scoring formula: type 40% + catalog_grounding 15% + quality 45%{RST}")
        print(f"  {YEL}Judge: gpt-4o-mini (same as GPT+Catalog for direct comparison){RST}")
        print(f"  {YEL}Compare this quality score ({qr:.3f}) to IDSS quality on same queries.{RST}")

    # Cost calculation (judge tokens from GPT-4o-mini, not Gemini generation tokens)
    total_prompt     = sum(r.get("prompt_tokens",     0) for r in scored)
    total_completion = sum(r.get("completion_tokens", 0) for r in scored)
    # GPT-4o-mini pricing: $0.150/M input, $0.600/M output (as of 2026)
    cost_usd = (total_prompt * 0.150 + total_completion * 0.600) / 1_000_000
    print(f"\n  {'─'*60}")
    print(f"  Judge tokens (gpt-4o-mini): {total_prompt:,} prompt + {total_completion:,} completion")
    print(f"  Judge cost:                 ${cost_usd:.4f} USD")
    avg_idss_ms   = sum(r["idss_elapsed_s"]   for r in scored) / len(scored) * 1000
    avg_gemini_ms = sum(r["gemini_elapsed_ms"] for r in scored) / len(scored)
    print(f"  Avg latency:  IDSS={avg_idss_ms:.0f}ms  Gemini={avg_gemini_ms:.0f}ms\n")

    # ── Save results ────────────────────────────────────────────────────────
    if save_path:
        output = {
            "version":  "augmented_gemini_baseline_v1",
            "model":    GEMINI_MODEL,
            "judge":    "gpt-4o-mini",   # explicit: judge ≠ generator
            "idss_url": base_url,
            "threshold": PASS_THRESHOLD,
            "note": (
                f"Augmented Gemini ({GEMINI_MODEL}): receives IDSS's actual product search results as context. "
                "Tests architecture quality, not database quality. "
                "Grounding score penalizes hallucinated products not in the IDSS catalog. "
                "Judge is GPT-4o-mini (same as GPT+Catalog baseline) for cross-system comparability. "
                "Scoring: type 40% + grounding 15% + quality 45%."
            ),
            "summary": {
                "all":             {"n": na, "avg_score": round(aa, 4), "pass_pct": round(pa, 1),
                                    "avg_quality": round(qa, 4), "avg_grounding": round(ga, 4)},
                "rec_queries":     {"n": nr, "avg_score": round(ar, 4), "pass_pct": round(pr, 1),
                                    "avg_quality": round(qr, 4), "avg_grounding": round(gr, 4)},
                "interview_queries": {"n": len(na_)},
            },
            "cost": {
                "judge_model":       "gpt-4o-mini",
                "prompt_tokens":     total_prompt,
                "completion_tokens": total_completion,
                "cost_usd":          round(cost_usd, 6),
            },
            "results": scored,
        }
        with open(save_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"  Saved to: {save_path}")

    return scored


# ── CLI ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            f"Augmented Gemini Baseline: fair architecture comparison "
            f"where {GEMINI_MODEL} is given the same products IDSS would show. "
            "Judge is GPT-4o-mini (same as all other baselines for comparability)."
        )
    )
    parser.add_argument(
        "--url", default="http://localhost:8001",
        help="IDSS agent URL (default: http://localhost:8001)",
    )
    parser.add_argument("--save",        help="Save JSON results to this path")
    parser.add_argument("--group",       help="Only run queries from this group")
    parser.add_argument("--query",       type=int, help="Only run this query ID")
    parser.add_argument("--max-id",      type=int, metavar="N",
                        help="Only run queries with ID <= N (e.g. --max-id 180)")
    parser.add_argument(
        "--concurrency", type=int, default=4,
        help="Max concurrent IDSS + Gemini calls (default 4; keep ≤4 on free tier)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print Gemini response text per query",
    )
    args = parser.parse_args()

    queries = list(QUERIES)
    if args.group:
        queries = [q for q in queries if q["group"] == args.group]
    if args.query:
        queries = [q for q in queries if q["id"] == args.query]
    if args.max_id is not None:
        queries = [q for q in queries if q["id"] <= args.max_id]

    if not queries:
        print("No queries match given filters.")
        sys.exit(1)

    asyncio.run(run_augmented_gemini(
        queries=queries,
        base_url=args.url,
        save_path=args.save,
        concurrency=args.concurrency,
        verbose=args.verbose,
    ))


if __name__ == "__main__":
    main()
