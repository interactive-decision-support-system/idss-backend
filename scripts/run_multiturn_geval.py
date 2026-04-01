#!/usr/bin/env python3
"""
Multi-Turn G-Eval Harness — 4-Way Fair Comparison
===================================================
Tests IDSS, GPT-4o-mini (catalog-bound), Perplexity sonar (⚠ web-augmented, informational), and Sajjad's idss-mcp on 10 scripted
3–5 turn conversation scenarios covering constraint accumulation, mind-changes,
brand exclusion persistence, and preference tracking.

Systems:
  IDSS        sequential /chat calls to live server (session state enforces constraints)
  Sajjad      same /chat API, different server (localhost:9003 by default)
  GPT         catalog-bound gpt-4o-mini — before EACH turn, searches the IDSS product
              catalog via /api/search-products and injects top-10 results so GPT
              recommends from the SAME product pool as IDSS (fair comparison).
              Structured products are matched back from GPT's text for budget/brand checks.
  Perplexity  ⚠ sonar (web-augmented) — informational only. All Perplexity API
              models use live web search; no offline variant exists. Per mentor:
              "The baseline shouldn't use web search." Excluded from primary ranking.

Fairness design (per mentor feedback — Negin Golrezaei + Hannah Clay):
  "If we don't give [GPT] our database, how can we even compare?" — answered by
  catalog injection for GPT. Perplexity cannot be made fair (web search is
  mandatory in their API). Primary fair comparison: IDSS vs GPT-catalog-bound vs Sajjad.

Scoring (v3 — catalog-bound GPT + judge-hallucination-resistant):
  45% judge       GPT-4o-mini evaluates full transcript; explicitly told not to penalize
                  silent constraint enforcement (DB/search filtering)
  55% constraint  Deterministic checks: brand exclusions, brand presence, budget.
                  GPT now has structured products from catalog matching.
                  Text-based checks only for Perplexity (still no structured output).

Key fix over v2:
  GPT was comparing against its training data (hallucinated products, no real prices).
  Now: GPT gets the actual IDSS catalog (same search results) before each turn.
  This makes budget checks, brand exclusion checks, and quality scoring fair.

Usage:
    python scripts/run_multiturn_geval.py --url http://localhost:8001
    python scripts/run_multiturn_geval.py --systems idss,gpt,perplexity,sajjad
    python scripts/run_multiturn_geval.py --systems idss,gpt --save scripts/geval_multiturn.json
    python scripts/run_multiturn_geval.py --scenario 1
    python scripts/run_multiturn_geval.py --sajjad-url http://otherhost:9003
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
    print("ERROR: httpx not installed.")
    sys.exit(1)

try:
    from openai import AsyncOpenAI
except ImportError:
    print("ERROR: openai not installed.")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

# ── ANSI colours ───────────────────────────────────────────────────────────
GREEN = "\033[92m"; RED = "\033[91m"; YEL = "\033[93m"; CYN = "\033[96m"
BOLD  = "\033[1m";  DIM = "\033[2m";  RST = "\033[0m"

PASS_THRESHOLD = 0.5

# ============================================================================
# Scripted multi-turn scenarios
# ============================================================================

MULTITURN_SCENARIOS: List[Dict] = [
    {
        "id": 1,
        "name": "constraint_accumulation_4turn",
        "description": "User adds constraints one by one over 4 turns",
        "turns": [
            "I need a laptop",
            "It's mainly for gaming — needs to handle modern AAA games",
            "Budget is under $1000, and please no HP laptops",
            "Also needs at least 16GB RAM",
        ],
        "final_constraints": {
            "use_case": "gaming",
            "budget_max_usd": 1000,
            "excluded_brands": ["HP"],
            "min_ram_gb": 16,
        },
        "quality_note": (
            "User adds constraints across 4 turns: gaming → budget $1000 → no HP → 16GB RAM. "
            "Final recommendations MUST satisfy ALL four constraints. "
            "Any HP laptop in final recommendations is a critical failure. "
            "Any laptop over $1000 or under 16GB RAM is a failure."
        ),
        "check_final_brand_exclusion": ["HP"],
    },
    {
        "id": 2,
        "name": "mind_change_pivot_gaming_to_video",
        "description": "User starts with gaming, explicitly pivots to video editing",
        "turns": [
            "Show me gaming laptops",
            "Actually, I just started a freelance video editing business. I need something for that instead — not gaming.",
            "Budget is $1500",
        ],
        "final_constraints": {
            "use_case": "video editing",
            "budget_max_usd": 1500,
        },
        "quality_note": (
            "User explicitly pivots from gaming to video editing in turn 2. "
            "Final recommendations must be for VIDEO EDITING, not gaming. "
            "Strong failure: recommending gaming laptops in turn 3. "
            "Final recs should mention display quality, color accuracy, CPU/RAM for rendering."
        ),
        "check_final_brand_exclusion": [],
    },
    {
        "id": 3,
        "name": "brand_exclusion_persistence_dell",
        "description": "Dell excluded in turn 1; subsequent turns must still exclude Dell",
        "turns": [
            "Show me laptops — I don't want any Dell products",
            "What about something with a dedicated GPU for machine learning?",
            "What's the typical price range for those?",
        ],
        "final_constraints": {
            "excluded_brands": ["Dell"],
        },
        "quality_note": (
            "Dell must be excluded from ALL turns, not just the first. "
            "Turn 2 should show GPU-capable laptops, still without Dell. "
            "Turn 3 should answer the price question in context of previously shown products. "
            "Any Dell recommendation across any turn is a critical failure."
        ),
        "check_final_brand_exclusion": ["Dell"],
    },
    {
        "id": 4,
        "name": "vague_to_specific_college_cs",
        "description": "One-word first query, agent guides user to specific recommendation",
        "turns": [
            "laptop",
            "for college, I'm a CS major — lots of coding and some data science",
            "max $800",
        ],
        "final_constraints": {
            "use_case": "college/CS/coding",
            "budget_max_usd": 800,
        },
        "quality_note": (
            "Starting from a single word ('laptop'), system should ask a clarifying question, "
            "then progressively narrow to a CS student laptop under $800. "
            "Final recs should be appropriate for coding + data science (decent CPU/RAM), "
            "all priced under $800."
        ),
        "check_final_brand_exclusion": [],
    },
    {
        "id": 5,
        "name": "comparison_request_with_follow_up",
        "description": "User requests comparison of shown products, then asks specific follow-up",
        "turns": [
            "I need a business laptop under $1200",
            "Can you compare the top 2 options in more detail?",
            "Which one has better battery life for travel?",
        ],
        "final_constraints": {
            "budget_max_usd": 1200,
        },
        "quality_note": (
            "Turn 1: system shows business laptop recommendations under $1200. "
            "Turn 2: system must compare the TOP TWO from turn 1 — without re-searching. "
            "Turn 3: system answers battery life question IN CONTEXT of the two products from turn 2. "
            "Critical failure: recommending new products in turn 2 instead of comparing prior ones."
        ),
        "check_final_brand_exclusion": [],
    },
    {
        "id": 6,
        "name": "price_refinement_downward",
        "description": "User refines price downward mid-conversation; new budget must apply",
        "turns": [
            "Show me the best laptops for software developers",
            "Those are too expensive. I need something under $700",
            "Of those, which is best for running Docker containers?",
        ],
        "final_constraints": {
            "budget_max_usd": 700,
            "use_case": "developer/Docker",
        },
        "quality_note": (
            "After price refinement in turn 2, ALL recommendations must be under $700. "
            "Turn 3 should answer the Docker question in context of the <$700 options. "
            "Failure: showing laptops over $700 in turns 2 or 3."
        ),
        "check_final_brand_exclusion": [],
    },
    {
        "id": 7,
        "name": "budget_increase_overwrite",
        "description": "User raises budget — must OVERWRITE not accumulate old budget",
        "turns": [
            "MacBook recommendations under $1000",
            "I can stretch to $1400 if it makes a meaningful difference in performance",
            "What about the latest model options?",
        ],
        "final_constraints": {
            "brand_preference": "Apple/MacBook",
            "budget_max_usd": 1400,
        },
        "quality_note": (
            "Budget must UPDATE to $1400 in turn 2, not accumulate ($1000-$1400 is wrong). "
            "MacBook/Apple preference must persist through all turns. "
            "Turn 3 should discuss latest Apple models in context of $1400 budget."
        ),
        "check_final_brand_exclusion": [],
    },
    {
        "id": 8,
        "name": "exclusion_then_unexclude_hp",
        "description": "User excludes HP, then changes mind and asks for HP specifically",
        "turns": [
            "Show me laptops, no HP please",
            "Actually, HP is fine. I changed my mind about that.",
            "Can you show me HP laptop options specifically?",
        ],
        "final_constraints": {
            "brand_preference": "HP",  # should APPEAR in final turn
        },
        "quality_note": (
            "Turn 1: HP is excluded — no HP in recommendations. "
            "Turn 2: user reverses the exclusion — HP is now allowed. "
            "Turn 3: user explicitly asks for HP options. "
            "Critical failure: HP still excluded in turn 3. "
            "Success: HP products shown prominently in final turn."
        ),
        "check_final_brand_exclusion": [],  # no exclusions by final turn
        "check_final_brand_presence": ["HP"],  # HP should BE present
    },
    {
        "id": 9,
        "name": "5turn_med_school_session",
        "description": "Full 5-turn realistic session for medical student",
        "turns": [
            "I need a laptop for medical school",
            "Mainly for note-taking, reading PDFs, and some light research online",
            "No Chromebooks — I need a real OS like Windows or macOS",
            "Budget is max $600",
            "Is it worth getting the extended warranty on a laptop in this price range?",
        ],
        "final_constraints": {
            "use_case": "medical student / note-taking",
            "excluded_types": ["Chromebook"],
            "os": "Windows or macOS",
            "budget_max_usd": 600,
        },
        "quality_note": (
            "5-turn session: med student → note-taking/PDF → no Chromebook/real OS → $600 max → warranty Q. "
            "System must accumulate ALL stated constraints. "
            "Turn 5 (warranty Q) should be answered gracefully in context of the $600 laptops shown. "
            "Failure: Chromebook recommendations, laptops over $600, or ignoring OS requirement."
        ),
        "check_final_brand_exclusion": [],
    },
    {
        "id": 10,
        "name": "dense_first_turn_then_refinement",
        "description": "Many constraints in turn 1, then user refines and asks about refurbished",
        "turns": [
            "I want a lightweight laptop — under 4 lbs, 13 or 14 inch, at least 16GB RAM, no gaming aesthetics, professional look, under $1300",
            "Does it need to be brand new or can it be refurbished?",
            "Refurbished is totally fine. Show me the best value option from that.",
        ],
        "final_constraints": {
            "budget_max_usd": 1300,
            "min_ram_gb": 16,
            "style": "professional (no gaming)",
        },
        "quality_note": (
            "All first-turn constraints must persist: <4lbs, 13-14in, 16GB+, no gaming look, <$1300. "
            "Turn 2-3 clarify that refurbished is acceptable. "
            "Final recommendation should be a compact, professional laptop satisfying ALL first-turn specs. "
            "Failure: gaming-style laptop, over $1300, under 16GB, or wrong screen size."
        ),
        "check_final_brand_exclusion": [],
    },
]


# ============================================================================
# Multi-turn judge
# ============================================================================

MULTITURN_JUDGE_SYSTEM = """\
You are evaluating a MULTI-TURN laptop shopping assistant conversation.

CRITICAL FAIRNESS RULE — READ BEFORE SCORING:
Systems may enforce constraints SILENTLY through database or search filtering rather than
verbally. Do NOT deduct points for lack of verbal acknowledgment.
Check only whether the final PRODUCTS LIST complies with stated constraints.
Example: User said "no HP" → no HP products appear in Products list → FULL marks for that
constraint regardless of whether the assistant said "I'm excluding HP as requested."

SCORE FLOOR: If the Products list shows all recommendations comply with stated constraints
(no excluded brands, prices within budget, correct use-case), Constraint Satisfaction
score MUST be at least 3 out of 4. Reserve 0 for complete failure on a constraint.

Score the entire conversation from 0–10 based on these criteria:

1. CONSTRAINT SATISFACTION (0–4 points):
   Did the assistant honor ALL constraints stated across all turns in the final response?
   Constraints include: budget limits, brand exclusions, OS requirements, use-case specs,
   RAM/size requirements. Budget/spec updates must OVERWRITE prior values (not accumulate).
   Mind-changes (e.g., "actually HP is fine") must reverse the prior constraint.
   − 2 pts per violated hard constraint FOUND IN THE PRODUCTS LIST.
   − Do NOT deduct for constraints not echoed verbally if products comply.

2. PREFERENCE TRACKING (0–3 points):
   Did the assistant maintain preferences stated in earlier turns throughout the conversation?
   Did it correctly handle preference changes (pivots, exclusion reversals, budget updates)?
   Did it maintain context from prior recommendations when asked for comparison/follow-up?
   Give partial credit (1–2 pts) when some but not all preferences were tracked.

3. RESPONSE APPROPRIATENESS (0–3 points):
   Was each turn's response appropriate to the conversational context?
   − Ask clarifying questions when appropriate (vague query), not repeatedly.
   − Give recommendations when user is ready, not more questions.
   − Answer follow-up questions (battery, price, warranty) in context of prior products.
   − Acknowledge and act on explicit preference changes.

SCORE ANCHORS:
  9–10: All constraints satisfied in products, preference tracking perfect, turns coherent.
  6–8:  Most constraints satisfied, minor omissions in tracking or coherence.
  4–5:  Some constraints violated or several preferences missed, but reasonable effort.
  2–3:  Major constraint violations (wrong brand, over budget) or completely off-topic.
  0–1:  System completely failed — wrong category, all exclusions violated, or crash/error.

Write 3–4 sentences of reasoning covering all criteria.
Then output EXACTLY this JSON on the last line:
{"score": <0-10>, "reason": "<≤15 words summarizing main strength or failure>"}
"""

MULTITURN_JUDGE_USER = """\
Scenario: {scenario_name}
Goal: {quality_note}

Full conversation transcript:
{transcript}

Score 0-10:"""


# ============================================================================
# IDSS multi-turn runner
# ============================================================================

async def run_idss_scenario(
    client:    httpx.AsyncClient,
    base_url:  str,
    scenario:  Dict,
) -> Tuple[List[Dict], float]:
    """
    Run a multi-turn scenario against IDSS.
    Returns (turn_results, total_elapsed_s).
    Each turn_result: {user, assistant, response_type, n_recs, products_flat}.
    """
    session_id = str(uuid.uuid4())
    turn_results: List[Dict] = []
    t_total = time.perf_counter()

    for turn_msg in scenario["turns"]:
        t0 = time.perf_counter()
        try:
            resp = await client.post(
                f"{base_url}/chat",
                json={"message": turn_msg, "session_id": session_id},
                timeout=90,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            data = {"response_type": "error", "message": f"[ERROR: {e}]",
                    "recommendations": None}

        recs = data.get("recommendations") or []
        products_flat = [p for group in recs if isinstance(group, list) for p in group]
        elapsed = time.perf_counter() - t0

        turn_results.append({
            "user":          turn_msg,
            "assistant":     data.get("message", ""),
            "response_type": data.get("response_type", "unknown"),
            "n_recs":        len(products_flat),
            "products":      [
                {
                    "brand": p.get("brand", ""),
                    "name":  (p.get("name") or "")[:60],
                    "price": p.get("price") or p.get("price_value"),
                }
                for p in products_flat[:6]
            ],
            "elapsed_s": round(elapsed, 2),
        })

    return turn_results, time.perf_counter() - t_total


# ============================================================================
# Catalog search helpers — gives GPT the same product pool as IDSS
# ============================================================================

async def search_catalog_for_gpt(
    client:   httpx.AsyncClient,
    base_url: str,
    query:    str,
    n:        int = 10,
) -> List[Dict]:
    """
    Fetch products from the IDSS catalog via /chat with k=0 (skip interview).
    Returns up to N product dicts normalised to {brand, name, price_cents}.

    Uses /chat (not /api/search-products) because the chat endpoint routes
    through the full IDSS search pipeline (embedding similarity, ranking, MMR)
    — giving GPT the identical product pool that IDSS itself returns, so the
    comparison is fair: both systems choose from the same catalog snapshot.
    Silently returns [] on any error so GPT degrades to free-form gracefully.
    """
    import uuid as _uuid
    try:
        resp = await client.post(
            f"{base_url}/chat",
            json={"message": query, "session_id": str(_uuid.uuid4()), "k": 0},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        recs = data.get("recommendations") or []
        flat = [p for group in recs if isinstance(group, list) for p in group]
        result: List[Dict] = []
        for p in flat[:n]:
            price_raw = p.get("price") or p.get("price_value")
            try:
                price_usd = float(price_raw) if price_raw else None
            except (TypeError, ValueError):
                price_usd = None
            result.append({
                "brand":       (p.get("brand") or "").strip(),
                "name":        (p.get("name") or "")[:80].strip(),
                "price_cents": int(price_usd * 100) if price_usd else 0,
            })
        return result
    except Exception:
        return []


def _format_catalog_for_gpt(products: List[Dict]) -> str:
    """Format catalog products as a numbered list for injection into GPT's system prompt."""
    if not products:
        return "(no products available)"
    lines: List[str] = []
    for i, p in enumerate(products, 1):
        brand = (p.get("brand") or "").strip()
        name  = (p.get("name")  or "Unknown")[:70].strip()
        cents = p.get("price_cents") or 0
        price = f"${cents / 100:.0f}" if cents else "price N/A"
        lines.append(f"{i}. {brand} — {name} ({price})")
    return "\n".join(lines)


_CATALOG_MATCH_STOP = frozenset([
    "with", "inch", "screen", "laptop", "for", "and", "the", "that",
    "from", "this", "also", "more", "best", "good", "great",
])


def _match_gpt_products_to_catalog(reply: str, catalog: List[Dict]) -> List[Dict]:
    """
    Match products mentioned in GPT's reply back to catalog entries.
    Returns structured dicts with brand/name/price for deterministic constraint checks.
    Strategy: brand must appear in reply AND at least 2 significant name-words must match.
    """
    if not catalog:
        return []
    reply_lower = reply.lower()
    matched: List[Dict] = []

    for p in catalog:
        brand  = (p.get("brand") or "").lower().strip()
        name   = (p.get("name")  or "").lower()
        cents  = p.get("price_cents") or 0

        if not brand or brand not in reply_lower:
            continue

        name_tokens = [
            w.strip(".,()[]'\"") for w in name.split()
            if len(w) >= 4 and w.strip(".,()[]'\"") not in _CATALOG_MATCH_STOP
        ]
        hits = sum(1 for tok in name_tokens[:8] if tok in reply_lower)
        if hits >= 2:
            matched.append({
                "brand": p.get("brand", ""),
                "name":  (p.get("name") or "")[:60],
                "price": cents / 100 if cents else None,
            })

    # Deduplicate by name prefix
    seen: set = set()
    result: List[Dict] = []
    for m in matched:
        key = m["name"][:25].lower()
        if key not in seen:
            seen.add(key)
            result.append(m)
    return result[:6]


# ============================================================================
# GPT multi-turn runner (catalog-bound, full context each turn)
# ============================================================================

GPT_MULTITURN_SYSTEM = """\
You are a helpful online shopping assistant specialising in laptops and computers.
When the user describes what they need, give them 3–5 concrete product recommendations
with a short reason for each. If the user's request is vague, ask ONE focused clarifying
question.

IMPORTANT: Track ALL preferences and constraints stated across the conversation.
If the user updates a preference (e.g., raises budget, removes a brand exclusion),
update your understanding — do not keep the old constraint.

IMPORTANT: When a CATALOG is provided below, you MUST recommend only from those
listed products. Do NOT invent or hallucinate products not in the catalog.
"""


async def run_gpt_scenario(
    oai:            AsyncOpenAI,
    scenario:       Dict,
    sem:            asyncio.Semaphore,
    catalog_client: Optional[httpx.AsyncClient] = None,
    idss_url:       str = "http://localhost:8001",
) -> Tuple[List[Dict], float]:
    """
    Run a multi-turn scenario against GPT-4o-mini (catalog-bound, full context).

    Catalog injection (fair comparison design):
      Before each turn, the IDSS /api/search-products endpoint is called with the
      accumulated user query. Top-10 results are injected into GPT's system prompt.
      GPT MUST recommend only from this catalog — same product pool as IDSS.
      After each reply, products are matched back to the catalog for structured
      budget/brand constraint checking.

    Returns (turn_results, total_elapsed_s).
    """
    history:    List[Dict] = []   # clean user/assistant history (no catalog text)
    turn_results: List[Dict] = []
    accumulated_query = ""
    t_total = time.perf_counter()

    for turn_msg in scenario["turns"]:
        accumulated_query = (accumulated_query + " " + turn_msg).strip()

        # Search catalog fresh each turn (captures preference refinements)
        catalog_products: List[Dict] = []
        if catalog_client:
            catalog_products = await search_catalog_for_gpt(
                catalog_client, idss_url, accumulated_query, n=10
            )

        # Build system prompt: base + catalog snapshot
        catalog_block = ""
        if catalog_products:
            catalog_block = (
                "\n\nCATALOG — recommend ONLY from these products (do not invent others):\n"
                + _format_catalog_for_gpt(catalog_products)
            )
        system_content = GPT_MULTITURN_SYSTEM + catalog_block

        # Rebuild full message list with updated catalog in system + clean history
        messages: List[Dict] = [{"role": "system", "content": system_content}]
        messages.extend(history)
        messages.append({"role": "user", "content": turn_msg})

        t0 = time.perf_counter()
        async with sem:
            try:
                completion = await oai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                    temperature=0.3,
                    max_tokens=600,
                )
                reply = completion.choices[0].message.content.strip()
            except Exception as e:
                reply = f"[ERROR: {e}]"
        elapsed = time.perf_counter() - t0

        # Append to clean history (no catalog injected — keeps conversation readable)
        history.append({"role": "user",      "content": turn_msg})
        history.append({"role": "assistant", "content": reply})

        # Match GPT's response back to catalog entries for structured constraint checks
        gpt_products = _match_gpt_products_to_catalog(reply, catalog_products)

        # Infer response type
        has_bullets  = bool(re.search(r"^\s*[\-\*\d]+[\.\)]\s+\S", reply, re.M))
        has_question = bool(re.search(r"(what|which|how|could you).{0,60}\?", reply, re.I)) or reply.rstrip().endswith("?")
        rtype = "recommendations" if has_bullets else ("question" if has_question else "unknown")

        turn_results.append({
            "user":          turn_msg,
            "assistant":     reply,
            "response_type": rtype,
            "n_recs":        len(gpt_products),
            "products":      gpt_products,
            "elapsed_s":     round(elapsed, 2),
        })

    return turn_results, time.perf_counter() - t_total


# ============================================================================
# Perplexity multi-turn runner (stateless web-augmented, full history each turn)
# ============================================================================

PERPLEXITY_MULTITURN_SYSTEM = """\
You are a helpful laptop shopping assistant with access to current product information.
When the user describes what they need, recommend 3–5 specific laptop models with model
names, approximate prices, key specs, and a brief reason for each.
If the request is vague, ask ONE focused clarifying question.

IMPORTANT: Track ALL preferences and constraints stated across the conversation.
If the user updates a preference (raises budget, removes brand exclusion, changes use-case),
update your understanding — do not keep the old constraint.
"""

PERPLEXITY_RETRY_DELAYS = [5, 15, 30]


async def run_perplexity_scenario(
    pplx:     "AsyncOpenAI",
    scenario: Dict,
    sem:      asyncio.Semaphore,
) -> Tuple[List[Dict], float]:
    """
    Run a multi-turn scenario against Perplexity (stateless, full context each turn).
    Returns (turn_results, total_elapsed_s).
    """
    messages: List[Dict] = [{"role": "system", "content": PERPLEXITY_MULTITURN_SYSTEM}]
    turn_results: List[Dict] = []
    t_total = time.perf_counter()

    for turn_msg in scenario["turns"]:
        messages.append({"role": "user", "content": turn_msg})
        t0 = time.perf_counter()
        reply = ""
        async with sem:
            for attempt, delay in enumerate([0] + PERPLEXITY_RETRY_DELAYS):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    completion = await pplx.chat.completions.create(
                        model="sonar",
                        messages=messages,
                        max_tokens=500,
                    )
                    reply = completion.choices[0].message.content.strip()
                    # r1-1776 may emit <think>...</think> chain-of-thought; strip it
                    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.DOTALL).strip()
                    break
                except Exception as exc:
                    if "429" in str(exc) and attempt < len(PERPLEXITY_RETRY_DELAYS):
                        continue
                    reply = f"[ERROR: {exc}]"
                    break
        elapsed = time.perf_counter() - t0
        messages.append({"role": "assistant", "content": reply})

        has_bullets  = bool(re.search(r"^\s*[\-\*\d]+[\.\)]\s+\S", reply, re.M))
        has_question = bool(re.search(r"(what|which|how|could you).{0,60}\?", reply, re.I)) or reply.rstrip().endswith("?")
        rtype = "recommendations" if has_bullets else ("question" if has_question else "unknown")

        turn_results.append({
            "user":          turn_msg,
            "assistant":     reply,
            "response_type": rtype,
            "n_recs":        0,
            "products":      [],   # no structured output from Perplexity
            "elapsed_s":     round(elapsed, 2),
        })

    return turn_results, time.perf_counter() - t_total


# ============================================================================
# Judge: evaluate full transcript
# ============================================================================

def format_transcript(turn_results: List[Dict]) -> str:
    lines = []
    for i, t in enumerate(turn_results, 1):
        lines.append(f"Turn {i}:")
        lines.append(f"  User:      {t['user']}")
        assistant = t["assistant"][:400]
        lines.append(f"  Assistant: {assistant}")
        if t.get("products"):
            prod_str = "; ".join(
                f"{p['brand']} {p['name'][:40]} ${p.get('price','?')}"
                for p in t["products"][:4]
            )
            lines.append(f"  Products:  {prod_str}")
        lines.append("")
    return "\n".join(lines)


async def judge_transcript(
    oai:           AsyncOpenAI,
    scenario:      Dict,
    turn_results:  List[Dict],
    system_name:   str,
) -> Tuple[float, str, Dict[str, int]]:
    transcript = format_transcript(turn_results)
    prompt = MULTITURN_JUDGE_USER.format(
        scenario_name=scenario["name"],
        quality_note=scenario["quality_note"],
        transcript=transcript,
    )

    _zero: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    try:
        completion = await oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": MULTITURN_JUDGE_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.0,
            max_tokens=300,
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


# ============================================================================
# Deterministic constraint checks on final turn
# ============================================================================

def check_final_turn_constraints(scenario: Dict, turn_results: List[Dict]) -> Tuple[float, List[str]]:
    """
    Deterministic checks on the final turn's products and response text.
    Returns (constraint_score 0-1, list_of_notes).
    """
    if not turn_results:
        return 0.0, ["no turns to check"]

    final = turn_results[-1]
    final_products = final.get("products") or []
    final_text = (final.get("assistant") or "").lower()
    notes: List[str] = []
    penalties = 0

    # Check brand exclusions
    # For text-only systems (GPT/Perplexity) with no structured products, fall back to text scan
    excluded = scenario.get("check_final_brand_exclusion") or []
    for brand in excluded:
        brand_lower = brand.lower()
        if final_products:
            # Structured products available → deterministic check
            if any(brand_lower in (p.get("brand") or "").lower() or
                   brand_lower in (p.get("name") or "").lower()
                   for p in final_products):
                notes.append(f"✗ FAIL: excluded brand '{brand}' found in final products")
                penalties += 1
            else:
                notes.append(f"✓ '{brand}' correctly absent from final products")
        else:
            # Text-only system: scan assistant text for brand mentions
            # Treat as violation if brand appears without "not"/"no"/"without" nearby
            import re as _re
            safe_brand = _re.escape(brand_lower)
            # Check for "no X / without X / not X / exclude X" patterns (legitimate mention)
            negated = bool(_re.search(
                rf"(no|not|without|excluding?|avoid|except)\s+\w*\s*{safe_brand}",
                final_text, _re.I
            ))
            if brand_lower in final_text and not negated:
                notes.append(f"✗ FAIL (text): excluded brand '{brand}' mentioned in final text without negation")
                penalties += 1
            else:
                notes.append(f"✓ '{brand}' not recommended in final text")

    # Check brand presence (for mind-change scenarios)
    expected = scenario.get("check_final_brand_presence") or []
    for brand in expected:
        brand_lower = brand.lower()
        if final_products:
            if any(brand_lower in (p.get("brand") or "").lower() or
                   brand_lower in (p.get("name") or "").lower()
                   for p in final_products):
                notes.append(f"✓ Expected brand '{brand}' present in final products")
            elif brand_lower in final_text:
                notes.append(f"~ '{brand}' mentioned in final text but not in structured products")
            else:
                notes.append(f"✗ FAIL: expected brand '{brand}' not found in final products after un-exclusion")
                penalties += 1
        else:
            # Text-only: pass if brand mentioned anywhere in final text
            if brand_lower in final_text:
                notes.append(f"✓ Expected brand '{brand}' mentioned in final text")
            else:
                notes.append(f"✗ FAIL (text): expected brand '{brand}' not mentioned after un-exclusion")
                penalties += 1

    # Budget check (if products available and budget specified)
    budget = scenario.get("final_constraints", {}).get("budget_max_usd")
    if budget and final_products:
        over_budget = [p for p in final_products
                       if p.get("price") and float(p["price"]) > budget * 1.05]  # 5% tolerance
        if over_budget:
            notes.append(f"✗ FAIL: {len(over_budget)} product(s) over ${budget} budget")
            penalties += 1
        else:
            notes.append(f"✓ All {len(final_products)} products within ${budget} budget")

    score = max(0.0, 1.0 - 0.33 * penalties)
    return score, notes if notes else ["no deterministic constraints to check"]


# ============================================================================
# Main runner
# ============================================================================

async def run_all_scenarios(
    scenarios:   List[Dict],
    base_url:    str,
    systems:     List[str],
    save_path:   Optional[str],
    verbose:     bool,
    sajjad_url:  str = "http://localhost:9003",
    pplx_key:    Optional[str] = None,
) -> Dict:
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        print("ERROR: OPENAI_API_KEY not set.")
        sys.exit(1)

    oai      = AsyncOpenAI(api_key=openai_key)
    gpt_sem  = asyncio.Semaphore(4)
    pplx_sem = asyncio.Semaphore(2)  # Perplexity free tier: ~3 req/min

    # Initialise Perplexity client if needed
    pplx = None
    if "perplexity" in systems:
        _pplx_key = pplx_key or os.getenv("PERPLEXITY_API_KEY")
        if not _pplx_key:
            print("ERROR: PERPLEXITY_API_KEY not set (required for --systems perplexity).")
            sys.exit(1)
        pplx = AsyncOpenAI(api_key=_pplx_key, base_url="https://api.perplexity.ai")

    total = len(scenarios)
    print(f"\n{BOLD}Multi-Turn G-Eval Harness{RST}")
    print(f"  Scenarios: {total}")
    print(f"  Systems:   {', '.join(systems)}")
    print(f"  IDSS URL:  {base_url}")
    if "sajjad" in systems:
        print(f"  Sajjad URL: {sajjad_url}")
    if "gpt" in systems:
        print(f"  GPT:       catalog-bound (searches IDSS catalog before each turn — fair comparison)")
    if "perplexity" in systems:
        print(f"  Perplexity: sonar (⚠ WEB-AUGMENTED — all Perplexity models use web search)")
        print(f"  {YEL}  Perplexity results are INFORMATIONAL ONLY — not a fair comparison.")
        print(f"     Primary fair comparison: IDSS vs GPT (catalog-bound) vs Sajjad.{RST}")
    print(f"  Weights:   judge=45%  constraint=55%")

    all_results: List[Dict] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0

    async with httpx.AsyncClient(timeout=120) as client:
        for sc in scenarios:
            print(f"\n{'═'*74}")
            print(f"  {BOLD}Scenario {sc['id']}: {sc['name']}{RST}")
            print(f"  {DIM}{sc['description']}{RST}")
            print(f"  Turns: {len(sc['turns'])}")
            print(f"{'─'*74}")

            sc_result: Dict = {
                "id":          sc["id"],
                "name":        sc["name"],
                "description": sc["description"],
                "n_turns":     len(sc["turns"]),
                "turns":       sc["turns"],
                "systems":     {},
            }

            for system in systems:
                print(f"\n  [{system.upper()}] Running {len(sc['turns'])} turns…")
                t_sys = time.perf_counter()

                if system == "idss":
                    turn_results, elapsed = await run_idss_scenario(client, base_url, sc)
                elif system == "sajjad":
                    turn_results, elapsed = await run_idss_scenario(client, sajjad_url, sc)
                elif system == "perplexity":
                    assert pplx is not None
                    turn_results, elapsed = await run_perplexity_scenario(pplx, sc, pplx_sem)
                elif system == "gpt":
                    # Catalog-bound: pass the shared httpx client for catalog searches
                    turn_results, elapsed = await run_gpt_scenario(
                        oai, sc, gpt_sem,
                        catalog_client=client,
                        idss_url=base_url,
                    )
                else:
                    turn_results, elapsed = [], 0.0

                # Print turn summaries
                for i, t in enumerate(turn_results, 1):
                    print(f"    Turn {i}  [{t['response_type']:15s}]  {t['n_recs']} recs  "
                          f"{t['elapsed_s']:.1f}s")
                    if verbose:
                        print(f"           User: {t['user'][:80]}")
                        print(f"           Asst: {t['assistant'][:120].replace(chr(10),' ')}")

                # Judge full transcript
                judge_score, judge_reason, judge_usage = await judge_transcript(
                    oai, sc, turn_results, system
                )
                total_prompt_tokens     += judge_usage.get("prompt_tokens",     0)
                total_completion_tokens += judge_usage.get("completion_tokens", 0)

                # Deterministic constraint check
                constraint_score, constraint_notes = check_final_turn_constraints(sc, turn_results)

                # Combined final score: 45% judge + 55% constraint
                # Higher constraint weight prevents judge hallucination (0.0 judge + 1.0 constraint = 0.55 → PASS)
                final_score = 0.45 * judge_score + 0.55 * constraint_score
                passed = final_score >= PASS_THRESHOLD

                status = f"{GREEN}PASS{RST}" if passed else f"{RED}FAIL{RST}"
                print(f"\n  [{system.upper()}] {status}  "
                      f"final={final_score:.3f}  "
                      f"judge={judge_score:.3f}  "
                      f"constraint={constraint_score:.3f}  "
                      f"elapsed={elapsed:.1f}s")
                print(f"  [{system.upper()}] reason: {judge_reason}")
                for note in constraint_notes:
                    print(f"  [{system.upper()}] constraint: {note}")

                sc_result["systems"][system] = {
                    "turn_results":      turn_results,
                    "judge_score":       round(judge_score, 4),
                    "constraint_score":  round(constraint_score, 4),
                    "final_score":       round(final_score, 4),
                    "passed":            passed,
                    "judge_reason":      judge_reason,
                    "constraint_notes":  constraint_notes,
                    "total_elapsed_s":   round(elapsed, 2),
                }

            # Delta IDSS − each baseline
            if "idss" in sc_result["systems"]:
                idss_score = sc_result["systems"]["idss"]["final_score"]
                for baseline in ("gpt", "sajjad", "perplexity"):
                    if baseline in sc_result["systems"]:
                        delta = idss_score - sc_result["systems"][baseline]["final_score"]
                        delta_str = (f"{GREEN}+{delta:.3f}{RST}" if delta > 0
                                     else f"{RED}{delta:.3f}{RST}")
                        print(f"\n  DELTA (IDSS − {baseline.upper()}): {delta_str}")
                sc_result.setdefault("deltas", {})
                for baseline in ("gpt", "sajjad", "perplexity"):
                    if baseline in sc_result["systems"]:
                        sc_result["deltas"][f"idss_minus_{baseline}"] = round(
                            idss_score - sc_result["systems"][baseline]["final_score"], 4
                        )
                # Keep legacy key for backwards compat
                if "gpt" in sc_result["systems"]:
                    sc_result["delta_idss_minus_gpt"] = sc_result["deltas"]["idss_minus_gpt"]

            all_results.append(sc_result)

    # ── Summary ────────────────────────────────────────────────────────────
    col_w = 10
    print(f"\n\n{'═'*80}")
    print(f"\n{BOLD}  Multi-Turn G-Eval — Summary (judge=45% constraint=55%){RST}\n")
    header = f"  {'Scenario':<38} {'N':>2}"
    for sys in systems:
        header += f"  {sys.upper():>{col_w}}"
    if "idss" in systems:
        for baseline in [s for s in ("gpt", "sajjad", "perplexity") if s in systems]:
            header += f"  {'Δ vs '+baseline.upper():>{col_w}}"
    print(header)
    print(f"  {'─'*80}")

    system_scores: Dict[str, List[float]] = {s: [] for s in systems}

    for r in all_results:
        name_short = r["name"][:36]
        row = f"  {name_short:<38} {r['n_turns']:>2}"
        for sys in systems:
            sys_data = r["systems"].get(sys)
            if sys_data:
                sc_ = sys_data["final_score"]
                passed = sys_data["passed"]
                c = GREEN if passed else RED
                row += f"  {c}{sc_:>{col_w}.3f}{RST}"
                system_scores[sys].append(sc_)
            else:
                row += f"  {'N/A':>{col_w}}"
        if "idss" in r["systems"]:
            idss_sc = r["systems"]["idss"]["final_score"]
            for baseline in [s for s in ("gpt", "sajjad", "perplexity") if s in r["systems"]]:
                d = idss_sc - r["systems"][baseline]["final_score"]
                c = GREEN if d > 0 else RED
                row += f"  {c}{d:>+{col_w}.3f}{RST}"
        print(row)

    print(f"  {'─'*80}")
    avg_row = f"  {'AVERAGE':<40}"
    for sys in systems:
        scores = system_scores[sys]
        avg = sum(scores) / len(scores) if scores else 0.0
        avg_row += f"  {BOLD}{avg:>{col_w}.3f}{RST}"
    if "idss" in systems:
        idss_avg = (sum(system_scores["idss"]) / len(system_scores["idss"])
                    if system_scores["idss"] else 0.0)
        for baseline in [s for s in ("gpt", "sajjad", "perplexity") if s in systems]:
            bl_avg = (sum(system_scores[baseline]) / len(system_scores[baseline])
                      if system_scores[baseline] else 0.0)
            d = idss_avg - bl_avg
            c = GREEN if d > 0 else RED
            avg_row += f"  {c}{d:>+{col_w}.3f}{RST}"
    print(avg_row)
    print()
    for sys in systems:
        scores = system_scores[sys]
        avg = sum(scores) / len(scores) if scores else 0.0
        pass_n = sum(1 for s in scores if s >= PASS_THRESHOLD)
        print(f"  {sys.upper():<12} avg={avg:.3f}  pass={pass_n}/{len(scores)} ({100*pass_n//max(1,len(scores))}%)")

    print(f"\n  {YEL}Scoring: 45% LLM judge (full transcript) + 55% deterministic constraint checks.{RST}")
    print(f"  {YEL}Judge does NOT penalize silent constraint enforcement (DB/search filtering).{RST}")
    if "gpt" in systems:
        print(f"  {YEL}GPT: catalog-bound — each turn searches IDSS catalog so GPT picks from same products.{RST}")
    if "perplexity" in systems:
        print(f"  {YEL}⚠ Perplexity: WEB-AUGMENTED (sonar). No offline Perplexity API model exists.")
        print(f"     Excluded from primary fair ranking. Shown as informational reference.{RST}")

    cost_usd = (total_prompt_tokens * 0.150 + total_completion_tokens * 0.600) / 1_000_000
    print(f"\n  Judge cost: ${cost_usd:.4f} USD  ({total_prompt_tokens:,} prompt + {total_completion_tokens:,} completion tokens)")

    # ── Save ───────────────────────────────────────────────────────────────
    if save_path:
        output = {
            "version":     "multiturn_geval_v2",
            "systems":     systems,
            "idss_url":    base_url,
            "sajjad_url":  sajjad_url if "sajjad" in systems else None,
            "n_scenarios": total,
            "threshold":   PASS_THRESHOLD,
            "scoring": {
                "judge_weight":      0.45,
                "constraint_weight": 0.55,
                "judge_model":       "gpt-4o-mini",
                "judge_temperature": 0.0,
            },
            "note": (
                "Multi-turn evaluation: tests constraint accumulation, mind-change handling, "
                "and preference persistence across 3–5 turn conversations. "
                "Scoring: 45% judge (full transcript, silent-enforcement-aware) + "
                "55% constraint (deterministic checks). "
                "IDSS/Sajjad: session-state enforces constraints deterministically. "
                "GPT: catalog-bound — each turn calls /api/search-products so GPT "
                "recommends from same product pool as IDSS (per mentor Negin Golrezaei: "
                "'if we don't give it our database, how can we compare?'). "
                "Perplexity: sonar (⚠ web-augmented) — INFORMATIONAL ONLY. "
                "All Perplexity API models use web search; no offline model exists. "
                "Primary fair comparison: IDSS vs GPT-catalog-bound vs Sajjad."
            ),
            "summary": {
                sys: {
                    "avg_score":  round(sum(system_scores[sys]) / max(1, len(system_scores[sys])), 4),
                    "pass_count": sum(1 for s in system_scores[sys] if s >= PASS_THRESHOLD),
                    "n":          len(system_scores[sys]),
                }
                for sys in systems
            },
            "cost": {
                "judge_model":       "gpt-4o-mini",
                "prompt_tokens":     total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "cost_usd":          round(cost_usd, 6),
            },
            "results": all_results,
        }
        with open(save_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\n  Saved to: {save_path}")

    fallback = {
        "summary": {
            s: {"avg_score": round(sum(system_scores[s]) / max(1, len(system_scores[s])), 4)}
            for s in systems
        }
    }
    return output if save_path else fallback


# ============================================================================
# CLI
# ============================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "Multi-turn G-Eval: 3-way fair comparison of IDSS, GPT-4o-mini (catalog-bound), "
            "and Sajjad's idss-mcp. Perplexity sonar is informational only (web-augmented, "
            "no offline Perplexity API model exists)."
        )
    )
    parser.add_argument("--url",       default="http://localhost:8001",
                        help="IDSS agent URL (default: http://localhost:8001)")
    parser.add_argument("--sajjad-url", default="http://localhost:9003",
                        help="Sajjad idss-mcp URL (default: http://localhost:9003)")
    parser.add_argument("--perplexity-key", default=None,
                        help="Perplexity API key (or set PERPLEXITY_API_KEY env var)")
    parser.add_argument("--save",      help="Path to save JSON results")
    parser.add_argument("--scenario",  type=int, help="Run only this scenario ID")
    parser.add_argument("--systems",   default="idss,gpt",
                        help="Comma-separated systems to run: idss,gpt,perplexity,sajjad "
                             "(default: idss,gpt)")
    parser.add_argument("--verbose",   action="store_true",
                        help="Print full turn text")
    args = parser.parse_args()

    valid_systems = {"idss", "gpt", "perplexity", "sajjad"}
    systems = [s.strip().lower() for s in args.systems.split(",")]
    for s in systems:
        if s not in valid_systems:
            print(f"ERROR: unknown system '{s}'. Valid: {', '.join(sorted(valid_systems))}")
            sys.exit(1)

    scenarios = list(MULTITURN_SCENARIOS)
    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] == args.scenario]

    if not scenarios:
        print("No scenarios match given filters.")
        sys.exit(1)

    asyncio.run(run_all_scenarios(
        scenarios=scenarios,
        base_url=args.url,
        systems=systems,
        save_path=args.save,
        verbose=args.verbose,
        sajjad_url=args.sajjad_url,
        pplx_key=args.perplexity_key,
    ))


if __name__ == "__main__":
    main()
