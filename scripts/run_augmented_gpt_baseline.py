#!/usr/bin/env python3
"""
Augmented GPT Baseline Evaluator
==================================
Fair ARCHITECTURE comparison: GPT-4o-mini given the EXACT SAME products IDSS would show.

This addresses the core problem with the plain GPT baseline:
  Plain GPT:     no database → hallucinates products → judge rewards fluent hallucinations
  Augmented GPT: receives IDSS's actual search results → writes narrative from REAL products
                 judge PENALIZES if GPT invents products not in the catalog

What this comparison measures:
  "Does IDSS's agent pipeline (interview + KG + explanation) produce better
   recommendation quality than GPT writing about an identical product list?"
  → Pure architecture comparison, database advantage removed.

For each query:
  1. Call IDSS live → get the products IDSS would recommend
  2. Give those same products to GPT as context
  3. Score GPT's response with a catalog-grounding-aware judge
  4. Compare: IDSS quality vs. augmented GPT quality

If IDSS quality > augmented GPT: IDSS's architecture wins on the explanation layer.
If augmented GPT > IDSS: IDSS's response text generation needs improvement.

Usage:
    python scripts/run_augmented_gpt_baseline.py --url http://localhost:8001
    python scripts/run_augmented_gpt_baseline.py --url http://localhost:8001 --save scripts/geval_augmented_gpt.json
    python scripts/run_augmented_gpt_baseline.py --url http://localhost:8001 --group expert --verbose
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

# ── Augmented GPT system prompt ────────────────────────────────────────────
AUGMENTED_SYSTEM = """\
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

# ── Catalog-aware quality judge ─────────────────────────────────────────────
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
    """Format a list of product dicts into a readable catalog string for GPT."""
    if not products:
        return "  (no products available)"
    lines = []
    for i, p in enumerate(products[:max_items], 1):
        brand = (p.get("brand") or "").strip()
        name  = (p.get("name")  or "")[:65].strip()
        # price may be 'price' (live) or 'price_value' (stored)
        price = p.get("price") or p.get("price_value")

        # Avoid repeating brand in name
        display_name = name
        if brand and name.lower().startswith(brand.lower()):
            display_name = name[len(brand):].strip(" -–")

        line = f"{i}. {brand} {display_name}"
        if price:
            line += f"  —  ${price}"
        lines.append(line)

        # Key specs
        specs = []
        attrs = p.get("laptop", {}).get("attributes", {}) if isinstance(p.get("laptop"), dict) else {}
        ram_gb = attrs.get("ram_gb") or p.get("ram_gb")
        storage = (p.get("laptop", {}) or {}).get("specs", {}).get("storage") if isinstance(p.get("laptop"), dict) else None
        gpu = attrs.get("gpu_model") or p.get("gpu_model") or ""
        os_  = (p.get("laptop", {}) or {}).get("specs", {}).get("os", "") if isinstance(p.get("laptop"), dict) else ""
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
    return f"{catalog_str}\n\nCustomer request: {query_message}"


# ── Catalog grounding check (deterministic heuristic) ─────────────────────
def check_catalog_grounding(response_text: str, products: List[Dict]) -> Tuple[float, str]:
    """
    Heuristic: does GPT's response reference products from the catalog or hallucinate?

    Returns (score 0-1, note).
    """
    if not products:
        return 1.0, "no catalog to check"

    text_lower = response_text.lower()

    # Build catalog keyword set: brand names + significant product name words
    catalog_keywords: set = set()
    for p in products:
        brand = (p.get("brand") or "").lower().strip()
        name  = (p.get("name")  or "").lower()
        if brand and brand not in ("new", "recertified", "used", "renewed"):
            catalog_keywords.add(brand)
        # significant words from name
        words = [w for w in name.split() if len(w) > 4 and w.isalpha()]
        catalog_keywords.update(words[:6])

    # Count how many catalog keywords appear in response
    hits = sum(1 for kw in catalog_keywords if kw in text_lower)

    # Flag well-known product lines NOT in our catalog (hallucination signals)
    known_hallucinations = [
        "macbook pro 16", "macbook air m2", "dell xps 15", "thinkpad x1 carbon",
        "asus zenbook 14", "microsoft surface laptop", "lg gram 17",
        "razer blade 15", "alienware m15",
    ]
    catalog_all_names = " ".join(
        (p.get("name") or "").lower() for p in products
    )
    potential_hallucinations = [
        s for s in known_hallucinations if s not in catalog_all_names and s in text_lower
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
    "not HP", "without HP", "except HP", "excluding HP").

    Returns:
        (None,   note)  – no exclusions specified for this query
        (0.0,    note)  – excluded brand found in recommendation context
        (1.0,    note)  – no excluded-brand violations detected

    Design notes:
    - We look for the brand name as a whole word (\\b) so "HP" won't match "HDPI".
    - Negation window: up to 4 tokens before the brand name.
    - Case-insensitive throughout.
    - When the same brand appears multiple times we stop at the first positive hit.
    """
    if not must_not_brands:
        return None, "no exclusions to check"

    text = response_text.lower()
    violations: List[str] = []

    # Tokens within 4 words before the brand name that signal "not recommending".
    # Both present and past tense forms are included to catch "excluding HP" AND
    # "excluded HP" (past tense when echoing back the user's constraint).
    _NEGATION_WORDS = frozenset({
        "no", "not", "avoid", "avoiding",
        "without", "except",
        "excluding", "excluded",    # present + past tense
        "skip", "skipping", "skipped",
        "omit", "omitting", "omitted",
        "drop", "dropping", "dropped",
        "never", "none",
        "want",                     # "don't want HP"
    })

    for brand in must_not_brands:
        brand_l = brand.lower()
        # Find every match of the brand name in the text.
        for m in re.finditer(rf'\b{re.escape(brand_l)}\b', text):
            start = m.start()
            # Check for "non-<brand>" pattern: hyphen immediately before the brand
            # (e.g. "non-HP alternatives").  This is a negation, not a recommendation.
            is_non_prefix = start > 0 and text[start - 1] == "-"
            if is_non_prefix:
                break  # treat "non-<brand>" as negated, stop scanning this brand

            # Look at the preceding 40 characters for negation tokens.
            window = text[max(0, start - 40): start]
            window_tokens = window.split()[-5:]  # last 5 words (was 4; +1 for contractions)
            # Expand contractions so "don't" → includes "don" with negation sense.
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
    """
    Call IDSS chat endpoint, extract recommended products.
    Returns (products_flat_list, response_type, elapsed_s).
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
        except Exception as e:
            return [], "error", time.perf_counter() - t0

        elapsed = time.perf_counter() - t0
        rtype = data.get("response_type", "unknown")
        recs   = data.get("recommendations") or []
        products_flat: List[Dict] = []
        for group in recs:
            if isinstance(group, list):
                products_flat.extend(group)
        return products_flat, rtype, elapsed


# ── Augmented GPT call ──────────────────────────────────────────────────────
async def call_augmented_gpt_async(
    oai: AsyncOpenAI,
    message: str,
    products: List[Dict],
    sem: asyncio.Semaphore,
) -> Tuple[str, int, str]:
    """
    Send query + product catalog to GPT-4o-mini.
    Returns (response_text, elapsed_ms, inferred_type).
    """
    async with sem:
        catalog_str = format_catalog(products)
        user_msg    = build_augmented_user_message(message, catalog_str)

        t0 = time.perf_counter()
        try:
            completion = await oai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": AUGMENTED_SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.3,
                max_tokens=600,
            )
            text = completion.choices[0].message.content.strip()
        except Exception as e:
            text = f"[ERROR: {e}]"
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Infer response type from text
        rtype = "recommendations"
        if text.rstrip().endswith("?") or re.search(r"(what|which|how|could you).{0,60}\?", text, re.I):
            if not re.search(r"^\s*[\-\*\d]+[\.\)]\s+\S", text, re.M):
                rtype = "question"

        return text, elapsed_ms, rtype


# ── Catalog-aware quality judge ─────────────────────────────────────────────
async def score_augmented_quality_async(
    oai: AsyncOpenAI,
    query: Dict,
    response_text: str,
    products: List[Dict],
    rtype: str,
) -> Tuple[float, str, Dict[str, int]]:
    """
    Score the augmented GPT response with catalog-grounding awareness.
    """
    catalog_str = format_catalog(products)
    quality_note = query.get("quality_note", "")

    prompt = AUGMENTED_JUDGE_USER.format(
        message=query["message"],
        quality_note=quality_note,
        catalog_str=catalog_str,
        rtype=rtype,
        response_text=response_text[:1500],
    )

    _zero: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
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
        raw = completion.choices[0].message.content.strip()
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                score_10 = float(data.get("score", 5))
                return max(0.0, min(10.0, score_10)) / 10.0, data.get("reason", ""), usage
        return 0.5, f"parse error: {raw[-60:]}", _zero
    except Exception as e:
        return 0.5, f"judge error: {e}", _zero


# ── Type score (same as run_geval) ─────────────────────────────────────────
def check_response_type_augmented(query: Dict, rtype: str, has_products: bool) -> Tuple[float, str]:
    exp_recs = query.get("expect_recs_on_first", False)
    exp_q    = query.get("expect_question", False)

    # If IDSS had no products to give, this is a question-only comparison
    if not has_products:
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
        return 1.0, f"~ Either acceptable; got {rtype}"


# ── Main evaluation loop ────────────────────────────────────────────────────
async def run_augmented(
    queries:     List[Dict],
    base_url:    str,
    save_path:   Optional[str],
    concurrency: int,
    verbose:     bool,
) -> List[Dict]:
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        print("ERROR: OPENAI_API_KEY not set.")
        sys.exit(1)

    oai    = AsyncOpenAI(api_key=openai_key)
    idss_sem = asyncio.Semaphore(concurrency)
    gpt_sem  = asyncio.Semaphore(concurrency)

    total = len(queries)
    print(f"\n{BOLD}Augmented GPT Baseline Evaluator{RST}")
    print(f"  Queries    : {total}")
    print(f"  IDSS URL   : {base_url}")
    print(f"  Concurrency: {concurrency}")
    print(f"  Purpose    : Fair architecture comparison (same products, different agent)\n")

    # ── Phase 1: Collect IDSS products in parallel ─────────────────────────
    print(f"  {CYN}Phase 1 — Calling IDSS to get products for {total} queries…{RST}")
    t1 = time.perf_counter()
    async with httpx.AsyncClient(timeout=90) as client:
        tasks = [
            get_idss_products_async(client, base_url, q["message"], idss_sem)
            for q in queries
        ]
        idss_results = await asyncio.gather(*tasks)
    print(f"  Done in {time.perf_counter()-t1:.1f}s\n")

    # ── Phase 2: Call augmented GPT with those products ────────────────────
    print(f"  {CYN}Phase 2 — Calling augmented GPT with IDSS products…{RST}")
    t2 = time.perf_counter()
    gpt_results = await asyncio.gather(*[
        call_augmented_gpt_async(oai, q["message"], idss_result[0], gpt_sem)
        for q, idss_result in zip(queries, idss_results)
    ])
    print(f"  Done in {time.perf_counter()-t2:.1f}s\n")

    # ── Phase 3: Score all responses ───────────────────────────────────────
    print(f"  {CYN}Phase 3 — Scoring {total} augmented responses…{RST}")
    t3 = time.perf_counter()

    async def score_one(idx: int) -> Dict:
        q         = queries[idx]
        products, idss_rtype, idss_elapsed = idss_results[idx]
        gpt_text, gpt_elapsed_ms, gpt_rtype = gpt_results[idx]

        has_products = len(products) > 0

        type_score, type_note = check_response_type_augmented(q, gpt_rtype, has_products)

        grounding_score, grounding_note = check_catalog_grounding(gpt_text, products)

        # Brand exclusion check — text-parsed for this free-text baseline.
        # check_brand_from_text returns None when the query has no brand exclusions,
        # so this is always safe to call; it won't penalise queries that don't
        # specify brand constraints.
        must_not = q.get("must_not_contain_brands", [])
        brand_score_text, brand_note_text = check_brand_from_text(gpt_text, must_not)

        quality_score, reason, usage = await score_augmented_quality_async(
            oai, q, gpt_text, products, gpt_rtype
        )

        # Disclosure check — only active for catalog_impossible group queries.
        # GPT receives IDSS's product list; if IDSS relaxed budget, GPT gets
        # those products and must still add disclosure language to pass.
        # Build a minimal resp dict that check_disclosure expects.
        _resp_for_disclosure = {"message": gpt_text, "recommendations": products}
        disclosure_score, disclosure_note = check_disclosure(q, _resp_for_disclosure)

        # Composite score (text-only baseline formula — unchanged from before):
        #   type 40% + grounding 15% + quality 45%
        # brand_score_text is stored as a SEPARATE diagnostic field and does NOT
        # alter the composite score, so historical results remain directly comparable.
        if disclosure_score is not None:
            final = 0.40 * type_score + 0.15 * grounding_score + 0.20 * disclosure_score + 0.25 * quality_score
        else:
            final = 0.40 * type_score + 0.15 * grounding_score + 0.45 * quality_score

        return {
            "id":               q["id"],
            "group":            q["group"],
            "label":            q["label"],
            "message":          q["message"],
            "score":            round(final, 4),
            "type_score":       type_score,
            "grounding_score":  round(grounding_score, 4),
            # brand_score_text: None means no brand exclusion in this query;
            # 0.0 means excluded brand found in text; 1.0 means clean.
            "brand_score_text": brand_score_text,
            "brand_note_text":  brand_note_text,
            "quality_score":    quality_score,
            "disclosure_score": disclosure_score,
            "disclosure_note":  disclosure_note,
            "type_note":        type_note,
            "grounding_note":   grounding_note,
            "reason":           reason,
            "n_products_given": len(products),
            "idss_rtype":       idss_rtype,
            "gpt_rtype":        gpt_rtype,
            "idss_elapsed_s":   round(idss_elapsed, 2),
            "gpt_elapsed_ms":   gpt_elapsed_ms,
            "prompt_tokens":    usage.get("prompt_tokens",     0),
            "completion_tokens":usage.get("completion_tokens", 0),
            "gpt_text_preview": gpt_text[:300],
        }

    scored = await asyncio.gather(*[score_one(i) for i in range(total)])
    scored = sorted(scored, key=lambda r: r["id"])
    print(f"  Done in {time.perf_counter()-t3:.1f}s\n")

    # ── Per-query output ───────────────────────────────────────────────────
    print(f"{'─'*76}")
    rec_only = [r for r in scored if r["idss_rtype"] == "recommendations"]
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
            print(f"       GPT: {r['gpt_text_preview'][:120].replace(chr(10), ' ')}")
    print(f"{'─'*76}\n")

    # ── Summary ────────────────────────────────────────────────────────────
    def stats(subset: List[Dict]) -> Tuple[int, float, float, float, float, float]:
        """Return (n, avg_score, pass_pct, avg_quality, avg_grounding, avg_brand_text)."""
        if not subset:
            return 0, 0.0, 0.0, 0.0, 0.0, 0.0
        n      = len(subset)
        avg    = sum(r["score"] for r in subset) / n
        pct    = 100.0 * sum(1 for r in subset if r["score"] >= PASS_THRESHOLD) / n
        qual   = sum(r["quality_score"] for r in subset) / n
        ground = sum(r["grounding_score"] for r in subset) / n
        # brand_score_text is None when query has no brand exclusion — exclude those
        # from the average so we only report accuracy on brand-constrained queries.
        brand_checks = [r["brand_score_text"] for r in subset if r["brand_score_text"] is not None]
        brand_avg = sum(brand_checks) / len(brand_checks) if brand_checks else float("nan")
        return n, avg, pct, qual, ground, brand_avg

    all_  = scored
    recs_ = [r for r in scored if r["idss_rtype"] == "recommendations"]
    na_   = [r for r in scored if r["idss_rtype"] != "recommendations"]

    na, aa, pa, qa, ga, ba  = stats(all_)
    nr, ar, pr, qr, gr, br  = stats(recs_)

    print(f"\n{BOLD}  Augmented GPT Baseline — Summary{RST}\n")
    print(f"  {'Category':<35} {'N':>4}  {'AvgScore':>8}  {'Pass%':>7}  {'AvgQual':>8}  {'AvgGround':>9}  {'BrandText':>9}")
    print(f"  {'─'*82}")
    brand_str = f"{ba:>9.3f}" if ba == ba else "       N/A"  # nan guard
    print(f"  {'All queries':<35} {na:>4}  {aa:>8.3f}  {pa:>6.1f}%  {qa:>8.3f}  {ga:>9.3f}  {brand_str}")
    brand_r_str = f"{br:>9.3f}" if br == br else "       N/A"
    print(f"  {'Rec queries (IDSS gave products)':<35} {nr:>4}  {ar:>8.3f}  {pr:>6.1f}%  {qr:>8.3f}  {gr:>9.3f}  {brand_r_str}")
    if na_:
        print(f"  {'Interview queries (no products)':<35} {len(na_):>4}  (skipped — IDSS in interview mode)")

    # Per-group
    groups = sorted(set(r["group"] for r in scored))
    print(f"\n  Per-group (rec queries only):")
    print(f"  {'Group':<20} {'N':>4}  {'AvgScore':>8}  {'AvgQual':>8}  {'AvgGround':>9}")
    print(f"  {'─'*56}")
    for g in groups:
        gs = [r for r in recs_ if r["group"] == g]
        if gs:
            n, a, p, q_, gnd, br = stats(gs)
            print(f"  {g:<20} {n:>4}  {a:>8.3f}  {q_:>8.3f}  {gnd:>9.3f}")

    # Compare IDSS vs augmented GPT quality (rec queries)
    if recs_:
        print(f"\n  {BOLD}Key Comparison (recommendation queries only):{RST}")
        print(f"  Augmented GPT avg quality : {qr:.3f}")
        print(f"  (Run compare_evals.py with --augmented flag to see IDSS vs. augmented side-by-side)")
        print(f"\n  {YEL}Scoring formula: type 40% + catalog_grounding 15% + quality 45%{RST}")
        print(f"  {YEL}Grounding penalizes GPT for recommending products not in IDSS's catalog.{RST}")
        print(f"  {YEL}Compare this quality score ({qr:.3f}) to IDSS quality on same queries.{RST}")

    # Cost
    total_prompt     = sum(r.get("prompt_tokens",     0) for r in scored)
    total_completion = sum(r.get("completion_tokens", 0) for r in scored)
    cost_usd = (total_prompt * 0.150 + total_completion * 0.600) / 1_000_000
    print(f"\n  {'─'*60}")
    print(f"  Judge tokens: {total_prompt:,} prompt + {total_completion:,} completion")
    print(f"  Total cost:   ${cost_usd:.4f} USD")
    avg_idss_ms = sum(r["idss_elapsed_s"] for r in scored) / len(scored) * 1000
    avg_gpt_ms  = sum(r["gpt_elapsed_ms"]  for r in scored) / len(scored)
    print(f"  Avg latency:  IDSS={avg_idss_ms:.0f}ms  AugGPT={avg_gpt_ms:.0f}ms\n")

    # ── Save ───────────────────────────────────────────────────────────────
    if save_path:
        output = {
            "version": "augmented_gpt_baseline_v1",
            "model": "gpt-4o-mini",
            "idss_url": base_url,
            "threshold": PASS_THRESHOLD,
            "note": (
                "Augmented GPT: receives IDSS's actual product search results as context. "
                "Tests architecture quality, not database quality. "
                "Grounding score penalizes hallucinated products not in the IDSS catalog. "
                "Scoring: type 40% + grounding 15% + quality 45%."
            ),
            "summary": {
                "all":            {"n": na, "avg_score": round(aa, 4), "pass_pct": round(pa, 1),
                                   "avg_quality": round(qa, 4), "avg_grounding": round(ga, 4)},
                "rec_queries":    {"n": nr, "avg_score": round(ar, 4), "pass_pct": round(pr, 1),
                                   "avg_quality": round(qr, 4), "avg_grounding": round(gr, 4)},
                "interview_queries": {"n": len(na_)},
            },
            "cost": {
                "judge_model": "gpt-4o-mini",
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "cost_usd": round(cost_usd, 6),
            },
            "results": scored,
        }
        with open(save_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"  Saved to: {save_path}")

    return scored


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description=(
            "Augmented GPT Baseline: fair architecture comparison "
            "where GPT-4o-mini is given the same products IDSS would show."
        )
    )
    parser.add_argument("--url",         default="http://localhost:8001",
                        help="IDSS agent URL (default: http://localhost:8001)")
    parser.add_argument("--save",        help="Save JSON results to this path")
    parser.add_argument("--group",       help="Only run queries from this group")
    parser.add_argument("--query",       type=int, help="Only run this query ID")
    parser.add_argument("--max-id",      type=int, metavar="N",
                        help="Only run queries with ID <= N (e.g. --max-id 180)")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="Max concurrent IDSS + GPT calls (default 4)")
    parser.add_argument("--verbose",     action="store_true",
                        help="Print GPT response text per query")
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

    asyncio.run(run_augmented(
        queries=queries,
        base_url=args.url,
        save_path=args.save,
        concurrency=args.concurrency,
        verbose=args.verbose,
    ))


if __name__ == "__main__":
    main()
