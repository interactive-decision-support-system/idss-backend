#!/usr/bin/env python3
"""
Multi-Turn G-Eval Harness — 5-Way Fair Comparison
===================================================
Tests IDSS, GPT-4o-mini (catalog-bound), Gemini-2.0-flash (catalog-bound),
Perplexity sonar (⚠ web-augmented, informational), and Sajjad's idss-mcp on 10
scripted 3–5 turn conversation scenarios covering constraint accumulation,
mind-changes, brand exclusion persistence, and preference tracking.

Systems:
  IDSS        sequential /chat calls to live server (session state enforces constraints)
  Sajjad      same /chat API, different server (localhost:9003 by default)
  GPT         catalog-bound gpt-4o-mini — before EACH turn, searches the IDSS product
              catalog via /chat and injects top-10 results so GPT recommends from the
              SAME product pool as IDSS (fair comparison).
  Gemini      catalog-bound gemini-2.0-flash — same catalog injection design as GPT.
              Uses GPT-4o-mini as judge (not Gemini) to avoid self-preference bias.
              Uses role "model" (not "assistant") in conversation history per Gemini API.
  Perplexity  ⚠ sonar (web-augmented) — informational only. All Perplexity API
              models use live web search; no offline variant exists. Per mentor:
              "The baseline shouldn't use web search." Excluded from primary ranking.

Fairness design (per mentor feedback — Negin Golrezaei + Hannah Clay):
  "If we don't give [GPT] our database, how can we even compare?" — answered by
  catalog injection for GPT and Gemini. Perplexity cannot be made fair (web search is
  mandatory in their API). Primary fair comparison: IDSS vs GPT vs Gemini vs Sajjad.

Scoring (v3 — catalog-bound GPT/Gemini + judge-hallucination-resistant):
  45% judge       GPT-4o-mini evaluates full transcript; explicitly told not to penalize
                  silent constraint enforcement (DB/search filtering).
                  All systems judged by the SAME GPT-4o-mini judge for comparability.
  55% constraint  Deterministic checks: brand exclusions, brand presence, budget.
                  GPT/Gemini have structured products from catalog matching.
                  Text-based checks only for Perplexity (no structured output).

Usage:
    python scripts/run_multiturn_geval.py --url http://localhost:8001
    python scripts/run_multiturn_geval.py --systems idss,gpt,gemini,sajjad
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

# google-genai is optional — only required when --systems includes "gemini"
try:
    from google import genai as _genai_lib
    from google.genai import types as _genai_types
    _GEMINI_AVAILABLE = True
except ImportError:
    _GEMINI_AVAILABLE = False

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

# ── ANSI colours ───────────────────────────────────────────────────────────
GREEN = "\033[92m"; RED = "\033[91m"; YEL = "\033[93m"; CYN = "\033[96m"
BOLD  = "\033[1m";  DIM = "\033[2m";  RST = "\033[0m"

PASS_THRESHOLD = 0.5
GEMINI_MODEL   = "gemini-2.5-flash-lite"   # 4K RPM / Unlimited RPD on paid tier — higher throughput than 2.5-flash (1K RPM / 10K RPD)

# ============================================================================
# Scripted multi-turn scenarios
# ============================================================================

MULTITURN_SCENARIOS: List[Dict] = [
    {
        "id": 1,
        "name": "constraint_accumulation_4turn",
        "description": "User adds constraints one by one over 4 turns",
        "category": "constraint_persistence",
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
        # per_turn_constraints: specifies which hard constraints are active in each turn.
        # Empty dict = no checkable hard constraint established yet for that turn.
        # Used by check_constraint_drift_all_turns() to check EVERY turn, not just the last.
        "per_turn_constraints": [
            {},                                                          # turn 1 — "I need a laptop"
            {},                                                          # turn 2 — gaming (qualitative, not checkable)
            {"excluded_brands": ["HP"], "budget_max_usd": 1000},        # turn 3 — HP exclusion + $1000 stated
            {"excluded_brands": ["HP"], "budget_max_usd": 1000},        # turn 4 — all prior constraints still active
        ],
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
        "category": "intent_pivot",
        "turns": [
            "Show me gaming laptops",
            "Actually, I just started a freelance video editing business. I need something for that instead — not gaming.",
            "Budget is $1500",
        ],
        "final_constraints": {
            "use_case": "video editing",
            "budget_max_usd": 1500,
        },
        # Budget set only in turn 3; turns 1-2 have no checkable hard numerical constraint.
        "per_turn_constraints": [
            {},                          # turn 1 — gaming (qualitative, not budget/brand checkable)
            {},                          # turn 2 — pivot (qualitative)
            {"budget_max_usd": 1500},    # turn 3 — $1500 budget stated
        ],
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
        "category": "brand_exclusion",
        "turns": [
            "Show me laptops — I don't want any Dell products",
            "What about something with a dedicated GPU for machine learning?",
            "What's the typical price range for those?",
        ],
        "final_constraints": {
            "excluded_brands": ["Dell"],
        },
        # Dell exclusion is active from turn 1 and must persist in EVERY subsequent turn.
        # This is the key scenario for constraint drift — exclusion must not "fade out".
        "per_turn_constraints": [
            {"excluded_brands": ["Dell"]},    # turn 1 — Dell excluded immediately
            {"excluded_brands": ["Dell"]},    # turn 2 — must still exclude Dell
            {"excluded_brands": ["Dell"]},    # turn 3 — must still exclude Dell
        ],
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
        "category": "clarification",
        "turns": [
            "laptop",
            "for college, I'm a CS major — lots of coding and some data science",
            "max $800",
        ],
        "final_constraints": {
            "use_case": "college/CS/coding",
            "budget_max_usd": 800,
        },
        # Budget only established in turn 3.
        "per_turn_constraints": [
            {},                       # turn 1 — "laptop" (no constraints)
            {},                       # turn 2 — CS use case (qualitative)
            {"budget_max_usd": 800},  # turn 3 — $800 budget stated
        ],
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
        "category": "comparison",
        "turns": [
            "I need a business laptop under $1200",
            "Can you compare the top 2 options in more detail?",
            "Which one has better battery life for travel?",
        ],
        "final_constraints": {
            "budget_max_usd": 1200,
        },
        # Budget $1200 stated in turn 1 — should be active for all subsequent turns.
        "per_turn_constraints": [
            {"budget_max_usd": 1200},    # turn 1 — budget stated immediately
            {"budget_max_usd": 1200},    # turn 2 — comparison (same pool, same budget)
            {"budget_max_usd": 1200},    # turn 3 — follow-up Q (same products referenced)
        ],
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
        "category": "budget_refinement",
        "turns": [
            "Show me the best laptops for software developers",
            "Those are too expensive. I need something under $700",
            "Of those, which is best for running Docker containers?",
        ],
        "final_constraints": {
            "budget_max_usd": 700,
            "use_case": "developer/Docker",
        },
        # Budget $700 introduced in turn 2; must persist in turn 3.
        # Turn 1 has no budget constraint (testing that unconstrained → constrained transition works).
        "per_turn_constraints": [
            {},                       # turn 1 — no budget set yet
            {"budget_max_usd": 700},  # turn 2 — "$700" stated; refinement applies immediately
            {"budget_max_usd": 700},  # turn 3 — Docker Q in context of <$700 options
        ],
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
        "category": "budget_overwrite",
        "turns": [
            "MacBook recommendations under $1000",
            "I can stretch to $1400 if it makes a meaningful difference in performance",
            "What about the latest model options?",
        ],
        "final_constraints": {
            "brand_preference": "Apple/MacBook",
            "budget_max_usd": 1400,
        },
        # Budget CHANGES: $1000 in turn 1 → overwritten to $1400 in turn 2.
        # The drift check must use the CURRENT budget for each turn (no double-counting).
        # A product priced at $1200 is fine in all turns: passes $1000*1.05=1050? No.
        # Actually $1200 > $1000*1.05=$1050, so it FAILS turn 1 budget check.
        # This tests that the budget update is correctly tracked turn-by-turn.
        "per_turn_constraints": [
            {"budget_max_usd": 1000},    # turn 1 — "$1000" stated
            {"budget_max_usd": 1400},    # turn 2 — overwritten to "$1400"
            {"budget_max_usd": 1400},    # turn 3 — $1400 still active
        ],
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
        "category": "exclusion_reversal",
        "turns": [
            "Show me laptops, no HP please",
            "Actually, HP is fine. I changed my mind about that.",
            "Can you show me HP laptop options specifically?",
        ],
        "final_constraints": {
            "brand_preference": "HP",  # should APPEAR in final turn
        },
        # HP exclusion active only in turn 1.
        # After turn 2 (un-exclusion), HP constraint is cleared — no brand check applies.
        # Turn 3: HP should be PRESENT (checked by check_final_brand_presence, not drift).
        "per_turn_constraints": [
            {"excluded_brands": ["HP"]},    # turn 1 — "no HP please"
            {},                              # turn 2 — HP un-excluded, no constraint active
            {},                              # turn 3 — HP preferred; drift check N/A
        ],
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
        "category": "long_session",
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
        # Budget $600 only set in turn 4; must hold in turn 5 (warranty Q).
        # "No Chromebook" is a category exclusion, not a brand — not directly checkable
        # by brand name match (Chromebooks don't have a single brand). Skip in drift.
        "per_turn_constraints": [
            {},                       # turn 1 — use case (qualitative)
            {},                       # turn 2 — use case detail (qualitative)
            {},                       # turn 3 — Chromebook exclusion (no brand check available)
            {"budget_max_usd": 600},  # turn 4 — "$600" stated
            {"budget_max_usd": 600},  # turn 5 — warranty Q; budget still applies to refs
        ],
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
        "category": "dense_constraint",
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
        # Budget $1300 stated in turn 1 — must persist across all 3 turns.
        # Many other constraints (weight, screen size, style) are qualitative — not checkable
        # via product price/brand fields. Only budget is deterministically checkable here.
        "per_turn_constraints": [
            {"budget_max_usd": 1300},    # turn 1 — full dense spec stated, $1300 checkable
            {"budget_max_usd": 1300},    # turn 2 — clarifying Q; budget still applies
            {"budget_max_usd": 1300},    # turn 3 — refurbished ask; budget still applies
        ],
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

# Independent copy for Gemini — same content ensures same playing field as GPT.
# Kept as a separate constant (not imported) so both scripts remain self-contained.
GEMINI_MULTITURN_SYSTEM = """\
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
# Gemini multi-turn runner (catalog-bound, full context each turn)
# ============================================================================

async def run_gemini_scenario(
    gemini_client: Any,   # google.genai.Client — passed in from run_all_scenarios
    scenario:       Dict,
    sem:            asyncio.Semaphore,
    catalog_client: Optional[httpx.AsyncClient] = None,
    idss_url:       str = "http://localhost:8001",
) -> Tuple[List[Dict], float]:
    """
    Run a multi-turn scenario against Gemini-2.0-flash (catalog-bound, full context).

    Mirrors run_gpt_scenario() exactly, with two Gemini-specific differences:
      1. History uses role "model" instead of "assistant" (Gemini API convention).
         Mixing "assistant" with "user" causes a 400 error from the Gemini API.
      2. response.text can be None if the safety filter blocks the output — guarded
         with `(response.text or "").strip()`.

    Catalog injection follows the same design as GPT: before each turn, the IDSS
    catalog is fetched and prepended to the system instruction so Gemini recommends
    from the same product pool as IDSS. Products are matched back to catalog entries
    for deterministic budget/brand constraint checks.
    """
    history:      List[Dict] = []   # user/model history (no catalog text; role="model" for Gemini)
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

        # Build system instruction: base + catalog snapshot
        catalog_block = ""
        if catalog_products:
            catalog_block = (
                "\n\nCATALOG — recommend ONLY from these products (do not invent others):\n"
                + _format_catalog_for_gpt(catalog_products)
            )
        system_instruction = GEMINI_MULTITURN_SYSTEM + catalog_block

        # Build multi-turn contents list.
        # Gemini requires role "model" for assistant turns — NOT "assistant" (OpenAI convention).
        contents = []
        for msg in history:
            contents.append({"role": msg["role"], "parts": [{"text": msg["content"]}]})
        contents.append({"role": "user", "parts": [{"text": turn_msg}]})

        t0 = time.perf_counter()
        async with sem:
            reply = "[ERROR: not attempted]"
            for attempt in range(3):
                try:
                    response = await gemini_client.aio.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=contents,
                        config=_genai_types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.3,
                            max_output_tokens=600,
                        ),
                    )
                    # response.text is None when Gemini's safety filter blocks output
                    reply = (response.text or "").strip()
                    break
                except Exception as e:
                    if ("429" in str(e) or "quota" in str(e).lower()) and attempt < 2:
                        await asyncio.sleep(65)  # Gemini quota reset ~52s; 65s = 13s buffer
                        continue
                    reply = f"[ERROR: {e}]"
                    break
        elapsed = time.perf_counter() - t0

        # Append to clean history using Gemini's "model" role (not "assistant")
        history.append({"role": "user",  "content": turn_msg})
        history.append({"role": "model", "content": reply})

        # Match Gemini's response back to catalog entries for structured constraint checks
        gemini_products = _match_gpt_products_to_catalog(reply, catalog_products)

        # Infer response type (same heuristic as GPT runner)
        has_bullets  = bool(re.search(r"^\s*[\-\*\d]+[\.\)]\s+\S", reply, re.M))
        has_question = (bool(re.search(r"(what|which|how|could you).{0,60}\?", reply, re.I))
                        or reply.rstrip().endswith("?"))
        rtype = "recommendations" if has_bullets else ("question" if has_question else "unknown")

        turn_results.append({
            "user":          turn_msg,
            "assistant":     reply,
            "response_type": rtype,
            "n_recs":        len(gemini_products),
            "products":      gemini_products,
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
    oai:              AsyncOpenAI,
    scenario:         Dict,
    turn_results:     List[Dict],
    system_name:      str,
    constraint_notes: Optional[List[str]] = None,
) -> Tuple[float, str, Dict[str, int]]:
    """
    Judge the full conversation transcript using GPT-4o-mini (temp=0, deterministic).

    constraint_notes: pre-computed deterministic check results injected into the prompt
    so the judge cannot hallucinate constraint failures that were already verified as passing.
    Without this injection, the judge was systematically giving 0.0 when all constraints
    were actually satisfied (root cause of the suspicious floor-effect scores).
    """
    transcript = format_transcript(turn_results)

    # Build constraint context block from pre-computed deterministic check results.
    # If all checks pass, explicitly tell the judge so it doesn't score constraint
    # satisfaction as 0/4 when products are demonstrably within budget and brand-correct.
    constraint_ctx = ""
    if constraint_notes:
        ctx_lines = ["", "DETERMINISTIC CONSTRAINT CHECK RESULTS (pre-verified — use to calibrate score):"]
        for note in constraint_notes:
            ctx_lines.append(f"  {note}")
        # A note starting with ✗ is an explicit failure.
        # Notes like "no deterministic constraints to check" are NOT failures —
        # they mean no checkable constraints exist (e.g. final turn is an FAQ answer
        # with no product list).  Treat absence of ✗ as "all passed".
        has_failure = any(n.startswith("✗") for n in constraint_notes)
        if not has_failure:
            ctx_lines.append("ALL checks PASSED. Constraint Satisfaction MUST score ≥3 out of 4.")
        else:
            ctx_lines.append("Some checks FAILED (see ✗ above). Penalise constraint satisfaction accordingly.")
        constraint_ctx = "\n".join(ctx_lines) + "\n"

    prompt = MULTITURN_JUDGE_USER.format(
        scenario_name=scenario["name"],
        quality_note=scenario["quality_note"],
        transcript=transcript,
    ) + constraint_ctx

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


def check_constraint_drift_all_turns(
    scenario:     Dict,
    turn_results: List[Dict],
) -> Dict:
    """
    Check brand exclusion and budget constraints on EVERY turn, not just the last.

    A constraint is 'active' for a turn when the scenario's per_turn_constraints
    list specifies it for that turn index. Only turns that (a) have an active
    constraint AND (b) returned at least one structured product are counted as
    'applicable'. Turns with empty product lists are skipped so that clarifying-
    question turns don't inflate the denominator.

    Returns a dict:
        drift_rate:   float or None — total_violations / n_applicable.
                      None when no applicable (turn, constraint) pairs exist.
        per_turn:     list of per-turn check result dicts.
        violations:   list of human-readable violation strings.
        n_applicable: int — how many (turn, constraint) pairs were evaluated.
        n_violations: int — how many violations were found.

    Budget tolerance: 5% (matching check_final_turn_constraints).
    Brand matching: case-insensitive; checks both brand field and product name.
    """
    ptc = scenario.get("per_turn_constraints")
    if not ptc or not turn_results:
        # Scenario has no per-turn constraint spec — nothing to check.
        return {"drift_rate": None, "per_turn": [], "violations": [], "n_applicable": 0, "n_violations": 0}

    per_turn: List[Dict] = []
    all_violations: List[str] = []
    n_applicable = 0
    n_violations = 0

    for turn_idx, constraints in enumerate(ptc):
        if turn_idx >= len(turn_results):
            break

        turn = turn_results[turn_idx]
        products = turn.get("products") or []

        # Skip turns with no structured products (e.g. clarifying questions, follow-up Q&A).
        if not products or not constraints:
            per_turn.append({"turn": turn_idx + 1, "skipped": True, "reason": "no products or no constraints"})
            continue

        turn_notes: List[str] = []
        turn_violations = 0

        # ── Brand exclusion check ──────────────────────────────────────────
        excluded = constraints.get("excluded_brands") or []
        for brand in excluded:
            brand_lower = brand.lower()
            for p in products:
                p_brand = (p.get("brand") or "").lower()
                p_name  = (p.get("name")  or "").lower()
                if brand_lower in p_brand or brand_lower in p_name:
                    msg = (f"✗ Turn {turn_idx+1}: excluded brand '{brand}' found "
                           f"in '{p.get('brand','')} {p.get('name','')[:30]}'")
                    turn_notes.append(msg)
                    all_violations.append(msg)
                    turn_violations += 1
                    n_violations += 1
                    break   # count once per excluded brand per turn
            else:
                turn_notes.append(f"✓ Turn {turn_idx+1}: '{brand}' correctly absent")
            n_applicable += 1

        # ── Budget check ───────────────────────────────────────────────────
        budget = constraints.get("budget_max_usd")
        if budget:
            over = [
                p for p in products
                if p.get("price") is not None and float(p["price"]) > budget * 1.05
            ]
            if over:
                msg = (f"✗ Turn {turn_idx+1}: {len(over)} product(s) over "
                       f"${budget} budget (e.g. '{over[0].get('name','?')[:30]}' "
                       f"${over[0].get('price','?')})")
                turn_notes.append(msg)
                all_violations.append(msg)
                turn_violations += 1
                n_violations += 1
            else:
                turn_notes.append(f"✓ Turn {turn_idx+1}: all {len(products)} products within ${budget}")
            n_applicable += 1

        per_turn.append({
            "turn":       turn_idx + 1,
            "skipped":    False,
            "violations": turn_violations,
            "notes":      turn_notes,
        })

    drift_rate = (n_violations / n_applicable) if n_applicable > 0 else None
    return {
        "drift_rate":   round(drift_rate, 4) if drift_rate is not None else None,
        "per_turn":     per_turn,
        "violations":   all_violations,
        "n_applicable": n_applicable,
        "n_violations": n_violations,
    }


# ============================================================================
# Main runner
# ============================================================================

async def run_all_scenarios(
    scenarios:      List[Dict],
    base_url:       str,
    systems:        List[str],
    save_path:      Optional[str],
    verbose:        bool,
    sajjad_url:     str   = "http://localhost:9003",
    pplx_key:       Optional[str] = None,
    gemini_key:     Optional[str] = None,
    pass_threshold: float = PASS_THRESHOLD,
) -> Dict:
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        print("ERROR: OPENAI_API_KEY not set.")
        sys.exit(1)

    oai        = AsyncOpenAI(api_key=openai_key)
    gpt_sem    = asyncio.Semaphore(4)
    pplx_sem   = asyncio.Semaphore(2)   # Perplexity free tier: ~3 req/min
    gemini_sem = asyncio.Semaphore(8)   # Gemini paid tier: 4K RPM (flash-lite) — 8 concurrent is safe

    # Initialise Perplexity client if needed
    pplx = None
    if "perplexity" in systems:
        _pplx_key = pplx_key or os.getenv("PERPLEXITY_API_KEY")
        if not _pplx_key:
            print("ERROR: PERPLEXITY_API_KEY not set (required for --systems perplexity).")
            sys.exit(1)
        pplx = AsyncOpenAI(api_key=_pplx_key, base_url="https://api.perplexity.ai")

    # Initialise Gemini client if needed (lazy — google-genai only required for this system)
    gemini_client = None
    if "gemini" in systems:
        if not _GEMINI_AVAILABLE:
            print("ERROR: google-genai not installed. Run: pip install google-genai")
            sys.exit(1)
        _gemini_key = gemini_key or os.getenv("GEMINI_API_KEY")
        if not _gemini_key:
            print("ERROR: GEMINI_API_KEY not set (required for --systems gemini).")
            sys.exit(1)
        gemini_client = _genai_lib.Client(api_key=_gemini_key)

    total = len(scenarios)
    print(f"\n{BOLD}Multi-Turn G-Eval Harness{RST}")
    print(f"  Scenarios: {total}")
    print(f"  Systems:   {', '.join(systems)}")
    print(f"  IDSS URL:  {base_url}")
    if "sajjad" in systems:
        print(f"  Sajjad URL: {sajjad_url}")
    if "gpt" in systems:
        print(f"  GPT:       catalog-bound (searches IDSS catalog before each turn — fair comparison)")
    if "gemini" in systems:
        print(f"  Gemini:    {GEMINI_MODEL}, catalog-bound (same catalog injection as GPT — fair comparison)")
        print(f"             Judge: GPT-4o-mini (NOT Gemini — avoids self-preference bias)")
    if "perplexity" in systems:
        print(f"  Perplexity: sonar (⚠ WEB-AUGMENTED — all Perplexity models use web search)")
        print(f"  {YEL}  Perplexity results are INFORMATIONAL ONLY — not a fair comparison.")
        print(f"     Primary fair comparison: IDSS vs GPT vs Gemini (catalog-bound) vs Sajjad.{RST}")
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
                elif system == "gemini":
                    # Catalog-bound, same design as GPT: IDSS catalog injected before each turn.
                    # Uses role "model" (not "assistant") per Gemini API convention.
                    assert gemini_client is not None
                    turn_results, elapsed = await run_gemini_scenario(
                        gemini_client, sc, gemini_sem,
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

                # Run deterministic checks FIRST so we can inject results into the judge prompt.
                # Previously judge ran before checks → judge hallucinated 0/10 even when
                # all constraints were satisfied, because it had no proof they were met.
                constraint_score, constraint_notes = check_final_turn_constraints(sc, turn_results)

                # Constraint drift rate — checks EVERY turn, not just the last.
                # drift_result["drift_rate"] is None when no per_turn_constraints are defined.
                drift_result = check_constraint_drift_all_turns(sc, turn_results)

                # Judge full transcript — now receives constraint check results so it
                # cannot score constraint satisfaction as 0 when products are verified correct.
                judge_score, judge_reason, judge_usage = await judge_transcript(
                    oai, sc, turn_results, system,
                    constraint_notes=constraint_notes,
                )
                total_prompt_tokens     += judge_usage.get("prompt_tokens",     0)
                total_completion_tokens += judge_usage.get("completion_tokens", 0)

                # Combined final score: 45% judge + 55% constraint
                final_score = 0.45 * judge_score + 0.55 * constraint_score
                passed = final_score >= pass_threshold

                drift_str = (f"{drift_result['drift_rate']:.3f}"
                             if drift_result["drift_rate"] is not None else "N/A")
                drift_color = (RED if (drift_result["drift_rate"] or 0) > 0 else GREEN)

                status = f"{GREEN}PASS{RST}" if passed else f"{RED}FAIL{RST}"
                print(f"\n  [{system.upper()}] {status}  "
                      f"final={final_score:.3f}  "
                      f"judge={judge_score:.3f}  "
                      f"constraint={constraint_score:.3f}  "
                      f"drift={drift_color}{drift_str}{RST}  "
                      f"elapsed={elapsed:.1f}s")
                print(f"  [{system.upper()}] reason: {judge_reason}")
                for note in constraint_notes:
                    print(f"  [{system.upper()}] constraint: {note}")
                if drift_result["violations"]:
                    for v in drift_result["violations"]:
                        print(f"  [{system.upper()}] drift: {v}")

                sc_result["systems"][system] = {
                    "turn_results":      turn_results,
                    "judge_score":       round(judge_score, 4),
                    "constraint_score":  round(constraint_score, 4),
                    "final_score":       round(final_score, 4),
                    "passed":            passed,
                    "judge_reason":      judge_reason,
                    "constraint_notes":  constraint_notes,
                    "drift_rate":        drift_result["drift_rate"],
                    "drift_details":     drift_result,
                    "total_elapsed_s":   round(elapsed, 2),
                }

            # Delta IDSS − each baseline
            if "idss" in sc_result["systems"]:
                idss_score = sc_result["systems"]["idss"]["final_score"]
                for baseline in ("gpt", "gemini", "sajjad", "perplexity"):
                    if baseline in sc_result["systems"]:
                        delta = idss_score - sc_result["systems"][baseline]["final_score"]
                        delta_str = (f"{GREEN}+{delta:.3f}{RST}" if delta > 0
                                     else f"{RED}{delta:.3f}{RST}")
                        print(f"\n  DELTA (IDSS − {baseline.upper()}): {delta_str}")
                sc_result.setdefault("deltas", {})
                for baseline in ("gpt", "gemini", "sajjad", "perplexity"):
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
        for baseline in [s for s in ("gpt", "gemini", "sajjad", "perplexity") if s in systems]:
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
            for baseline in [s for s in ("gpt", "gemini", "sajjad", "perplexity") if s in r["systems"]]:
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
        for baseline in [s for s in ("gpt", "gemini", "sajjad", "perplexity") if s in systems]:
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
        pass_n = sum(1 for s in scores if s >= pass_threshold)
        # Compute avg drift_rate across scenarios (skip None — no per_turn_constraints)
        drift_vals = [
            r["systems"][sys]["drift_rate"]
            for r in all_results
            if sys in r["systems"] and r["systems"][sys].get("drift_rate") is not None
        ]
        drift_str = f"  drift={sum(drift_vals)/len(drift_vals):.3f}" if drift_vals else ""
        print(f"  {sys.upper():<12} avg={avg:.3f}  pass={pass_n}/{len(scores)} ({100*pass_n//max(1,len(scores))}%){drift_str}")

    print(f"\n  {YEL}Scoring: 45% LLM judge (full transcript) + 55% deterministic constraint checks.{RST}")
    print(f"  {YEL}Judge does NOT penalize silent constraint enforcement (DB/search filtering).{RST}")
    if "gpt" in systems:
        print(f"  {YEL}GPT: catalog-bound — each turn searches IDSS catalog so GPT picks from same products.{RST}")
    if "gemini" in systems:
        print(f"  {YEL}Gemini: {GEMINI_MODEL}, catalog-bound. Judge is GPT-4o-mini (not Gemini — avoids self-bias).{RST}")
    if "perplexity" in systems:
        print(f"  {YEL}⚠ Perplexity: WEB-AUGMENTED (sonar). No offline Perplexity API model exists.")
        print(f"     Excluded from primary fair ranking. Shown as informational reference.{RST}")

    cost_usd = (total_prompt_tokens * 0.150 + total_completion_tokens * 0.600) / 1_000_000
    print(f"\n  Judge cost: ${cost_usd:.4f} USD  ({total_prompt_tokens:,} prompt + {total_completion_tokens:,} completion tokens)")

    # ── Build output dict (always — needed for multi-run pass@K aggregation) ─
    output = {
            "version":     "multiturn_geval_v2",
            "systems":     systems,
            "idss_url":    base_url,
            "sajjad_url":  sajjad_url if "sajjad" in systems else None,
            "n_scenarios": total,
            "threshold":   pass_threshold,
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
                "GPT: catalog-bound gpt-4o-mini — each turn calls IDSS /chat to get products, "
                "same pool as IDSS (per mentor Negin Golrezaei: 'if we don't give it our database, how can we compare?'). "
                "Gemini: catalog-bound gemini-2.0-flash — same injection design as GPT. "
                "Judge: GPT-4o-mini for ALL systems (avoids Gemini self-preference bias). "
                "Perplexity: sonar (⚠ web-augmented) — INFORMATIONAL ONLY. "
                "All Perplexity API models use web search; no offline model exists. "
                "Primary fair comparison: IDSS vs GPT vs Gemini (catalog-bound) vs Sajjad."
            ),
            "summary": {
                sys: {
                    "avg_score":  round(sum(system_scores[sys]) / max(1, len(system_scores[sys])), 4),
                    "pass_count": sum(1 for s in system_scores[sys] if s >= pass_threshold),
                    "n":          len(system_scores[sys]),
                    # avg_drift_rate: mean over scenarios with per_turn_constraints defined.
                    # None means no scenarios had per_turn_constraints (unexpected).
                    "avg_drift_rate": (
                        round(
                            sum(
                                r["systems"][sys]["drift_rate"]
                                for r in all_results
                                if sys in r["systems"]
                                and r["systems"][sys].get("drift_rate") is not None
                            ) / max(1, sum(
                                1 for r in all_results
                                if sys in r["systems"]
                                and r["systems"][sys].get("drift_rate") is not None
                            )),
                            4,
                        )
                        if any(
                            r["systems"].get(sys, {}).get("drift_rate") is not None
                            for r in all_results
                        )
                        else None
                    ),
                    # Per-scenario breakdown (S1–S10)
                    "per_scenario": [
                        {
                            "id":     r["id"],
                            "name":   r["name"],
                            "score":  round(r["systems"][sys]["final_score"], 4) if sys in r["systems"] else None,
                            "passed": r["systems"][sys].get("passed", False) if sys in r["systems"] else False,
                            "judge_score":      round(r["systems"][sys].get("judge_score", 0), 4) if sys in r["systems"] else None,
                            "constraint_score": round(r["systems"][sys].get("constraint_score", 0), 4) if sys in r["systems"] else None,
                        }
                        for r in all_results
                    ],
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
    if save_path:
        with open(save_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\n  Saved to: {save_path}")

    # ── Per-scenario breakdown table ──────────────────────────────────────
    _sys_col = {
        "idss": "IDSS", "gpt": "GPT+Cat", "gemini": "Gemini",
        "sajjad": "BaselineB", "perplexity": "Pplx⚠",
    }
    _active = [s for s in systems if s != "perplexity"]
    _col_w = 9
    _name_w = 36
    _hdr = f"  {'S#':<4} {'Scenario':<{_name_w}}" + "".join(f"  {_sys_col.get(s, s):>{_col_w}}" for s in _active)
    print(f"\n{'='*74}")
    print(f"  Per-Scenario Scores (all {len(all_results)} scenarios)")
    print(f"{'='*74}")
    print(_hdr)
    print(f"  {'─'*74}")
    for r in all_results:
        _row = f"  S{r['id']:<3} {r['name']:<{_name_w}}"
        for s in _active:
            _sc = r["systems"].get(s, {}).get("final_score")
            _passed = r["systems"].get(s, {}).get("passed", False)
            if _sc is None:
                _row += f"  {'N/A':>{_col_w}}"
            else:
                _mark = "✓" if _passed else "✗"
                _row += f"  {_mark}{_sc:>{_col_w - 2}.3f}"
        print(_row)
    print(f"  {'─'*74}")
    _mean_row = f"  {'---':<4} {'Mean':<{_name_w}}"
    for s in _active:
        _scores = [r["systems"][s]["final_score"] for r in all_results if s in r["systems"]]
        _mean_row += f"  {(sum(_scores)/len(_scores)):>{_col_w}.3f}" if _scores else f"  {'N/A':>{_col_w}}"
    print(_mean_row)
    print()

    return output


# ============================================================================
# Pass@K and category breakdown — multi-run aggregation
# ============================================================================

def compute_pass_at_k(
    all_run_outputs: List[Dict],
    systems:         List[str],
    scenarios:       List[Dict],
    threshold:       float,
) -> Dict:
    """
    Compute pass@K statistics from K independent runs of run_all_scenarios().

    For each (system, scenario): collect K final_scores, then compute:
      pass_at_k   = fraction of K runs where score >= threshold
      mean_score  = mean of K scores
      sd_score    = std-dev of K scores (0.0 when K=1)

    Aggregated per system:
      macro_pass_at_k = mean of per-scenario pass_at_k values
      mean_avg_score  = mean of per-scenario mean_scores
      sd_avg_score    = std-dev of per-scenario mean_scores (scenario-level variance)

    Returns: {system: {K, macro_pass_at_k, mean_avg_score, sd_avg_score,
                        per_scenario: [{id, name, category, pass_at_k,
                                        mean_score, sd_score, per_run_scores}]}}
    """
    import math as _math
    import statistics as _stat
    K = len(all_run_outputs)
    result: Dict = {}

    for sys_name in systems:
        scenario_stats = []
        for sc in scenarios:
            sc_id = sc["id"]
            scores: List[float] = []

            # Collect this scenario's final_score from every run
            for run_out in all_run_outputs:
                for sc_r in run_out.get("results", []):
                    if sc_r["id"] == sc_id and sys_name in sc_r.get("systems", {}):
                        scores.append(sc_r["systems"][sys_name]["final_score"])

            if not scores:
                continue

            n_sc = len(scores)
            n_pass = sum(1 for s in scores if s >= threshold)
            _sc_sd = _stat.stdev(scores) if n_sc > 1 else 0.0
            scenario_stats.append({
                "id":             sc_id,
                "name":           sc["name"],
                "category":       sc.get("category", "unknown"),
                "pass_at_k":      round(n_pass / n_sc, 3),
                "mean_score":     round(sum(scores) / n_sc, 4),
                "sd_score":       round(_sc_sd, 4),
                "se_score":       round(_sc_sd / _math.sqrt(n_sc) if n_sc > 1 else 0.0, 4),
                "per_run_scores": [round(s, 4) for s in scores],
            })

        if not scenario_stats:
            result[sys_name] = {"K": K, "macro_pass_at_k": 0.0, "mean_avg_score": 0.0,
                                 "sd_avg_score": 0.0, "se_avg_score": 0.0, "per_scenario": []}
            continue

        macro = sum(s["pass_at_k"] for s in scenario_stats) / len(scenario_stats)
        mean_avgs = [s["mean_score"] for s in scenario_stats]
        n_sc_total = len(mean_avgs)
        _sys_sd = _stat.stdev(mean_avgs) if n_sc_total > 1 else 0.0
        result[sys_name] = {
            "K":               K,
            "macro_pass_at_k": round(macro, 4),
            "mean_avg_score":  round(sum(mean_avgs) / n_sc_total, 4),
            "sd_avg_score":    round(_sys_sd, 4),
            "se_avg_score":    round(_sys_sd / _math.sqrt(n_sc_total) if n_sc_total > 1 else 0.0, 4),
            "per_scenario":    scenario_stats,
        }

    return result


def compute_category_breakdown(
    all_run_outputs: List[Dict],
    systems:         List[str],
    scenarios:       List[Dict],
    threshold:       float,
) -> Dict:
    """
    Group scenario scores by category across all K runs, per system.

    Categories (from scenario["category"]):
      constraint_persistence, intent_pivot, brand_exclusion, clarification,
      comparison, budget_refinement, budget_overwrite, exclusion_reversal,
      long_session, dense_constraint

    Returns: {system: {category: {n_scenarios, n_evals, avg_score, pass_rate}}}
    """
    # Map category → list of scenario IDs
    cat_to_ids: Dict[str, List[int]] = {}
    for sc in scenarios:
        cat = sc.get("category", "unknown")
        cat_to_ids.setdefault(cat, []).append(sc["id"])

    result: Dict = {}
    for sys_name in systems:
        cat_stats: Dict = {}
        for cat, sc_ids in sorted(cat_to_ids.items()):
            scores: List[float] = []
            for run_out in all_run_outputs:
                for sc_r in run_out.get("results", []):
                    if sc_r["id"] in sc_ids and sys_name in sc_r.get("systems", {}):
                        scores.append(sc_r["systems"][sys_name]["final_score"])

            if scores:
                cat_stats[cat] = {
                    "n_scenarios": len(sc_ids),
                    "n_evals":     len(scores),
                    "avg_score":   round(sum(scores) / len(scores), 4),
                    "pass_rate":   round(sum(1 for s in scores if s >= threshold) / len(scores), 4),
                }
        result[sys_name] = cat_stats

    return result


# Friendly display labels for system names used in printed tables
_SYS_LABELS = {
    "idss":       "IDSS",
    "gpt":        "GPT+Cat",
    "gemini":     "Gemini",
    "sajjad":     "MrgSajjad",
    "perplexity": "Pplx⚠",
}


def print_pass_at_k_table(
    passatk:   Dict,
    systems:   List[str],
    K:         int,
    threshold: float,
) -> None:
    """
    Print per-scenario pass@K table with mean±SD for each system.
    Shows which scenarios are borderline (low pass_at_k) and which are robust.
    """
    active = [s for s in systems if s in passatk]
    col_w = 12

    print(f"\n{'═'*80}")
    print(f"  {BOLD}Pass@{K} Results  (threshold={threshold})  "
          f"— fraction of {K} runs where score ≥ {threshold}{RST}")
    print(f"{'═'*80}")

    # Header
    hdr = f"  {'Scenario':<36} {'Cat':<22}"
    for s in active:
        hdr += f"  {_SYS_LABELS.get(s, s):>{col_w}}"
    print(hdr)
    print(f"  {'─'*80}")

    # Per-scenario rows
    for sc_data in (passatk.get(active[0], {}).get("per_scenario", []) if active else []):
        sc_id   = sc_data["id"]
        sc_name = sc_data["name"][:34]
        sc_cat  = sc_data.get("category", "")[:20]
        row = f"  S{sc_id:<2} {sc_name:<34} {sc_cat:<22}"
        for s in active:
            per_sc = {d["id"]: d for d in passatk.get(s, {}).get("per_scenario", [])}
            d = per_sc.get(sc_id)
            if d is None:
                row += f"  {'N/A':>{col_w}}"
            else:
                mean_s = d["mean_score"]
                sd_s   = d["sd_score"]
                pat_k  = d["pass_at_k"]
                c = GREEN if pat_k >= 0.8 else (YEL if pat_k >= 0.4 else RED)
                row += f"  {c}{pat_k:.1f}({mean_s:.3f}±{sd_s:.3f}){RST}"
        print(row)

    print(f"  {'─'*80}")

    # Macro pass@K row
    macro_row = f"  {'Macro Pass@'+str(K):<36} {'':<22}"
    for s in active:
        mp = passatk.get(s, {}).get("macro_pass_at_k", 0.0)
        c = GREEN if mp >= 0.8 else (YEL if mp >= 0.4 else RED)
        macro_row += f"  {c}{mp:>{col_w}.3f}{RST}"
    print(macro_row)

    # Mean avg score row (shows ±SE for paper tables)
    avg_row = f"  {'Mean Avg Score':<36} {'':<22}"
    for s in active:
        ma = passatk.get(s, {}).get("mean_avg_score", 0.0)
        se = passatk.get(s, {}).get("se_avg_score", 0.0)
        avg_row += f"  {BOLD}{ma:.3f}±{se:.3f}(SE){RST}{' '*(col_w-16)}"
    print(avg_row)
    print()


def print_category_table(
    cat_breakdown: Dict,
    systems:       List[str],
) -> None:
    """
    Print per-category avg score and pass rate for each system.
    Reveals which scenario types each system handles well vs. poorly.
    """
    active = [s for s in systems if s in cat_breakdown]
    if not active:
        return

    # Collect all categories across systems
    all_cats = sorted({cat for s in active for cat in cat_breakdown.get(s, {})})
    col_w = 16

    print(f"{'═'*80}")
    print(f"  {BOLD}Category Breakdown  (avg score / pass%){RST}")
    print(f"{'═'*80}")

    hdr = f"  {'Category':<26}"
    for s in active:
        hdr += f"  {_SYS_LABELS.get(s, s):>{col_w}}"
    print(hdr)
    print(f"  {'─'*80}")

    for cat in all_cats:
        row = f"  {cat:<26}"
        for s in active:
            d = cat_breakdown.get(s, {}).get(cat)
            if d is None:
                row += f"  {'N/A':>{col_w}}"
            else:
                avg  = d["avg_score"]
                pct  = d["pass_rate"] * 100
                n_sc = d["n_scenarios"]
                c = GREEN if pct >= 80 else (YEL if pct >= 50 else RED)
                row += f"  {c}{avg:.3f}/{pct:4.0f}% (n={n_sc}){RST}"
        print(row)

    print(f"  {'─'*80}")
    print()


def build_merged_output(
    all_run_outputs: List[Dict],
    passatk:         Dict,
    cat_breakdown:   Dict,
    systems:         List[str],
    threshold:       float,
) -> Dict:
    """
    Merge K per-run result dicts into one summary JSON for saving.
    Includes pass@K stats, category breakdown, and all raw per-run results.
    """
    K = len(all_run_outputs)
    return {
        "version":          "multiturn_geval_passatk_v1",
        "K":                K,
        "systems":          systems,
        "threshold":        threshold,
        "pass_at_k":        passatk,
        "category_breakdown": cat_breakdown,
        "per_run_summaries": [
            r.get("summary", {}) for r in all_run_outputs
        ],
        "per_run_results": [
            r.get("results", []) for r in all_run_outputs
        ],
    }


# ============================================================================
# CLI
# ============================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "Multi-turn G-Eval: 5-way fair comparison of IDSS, GPT-4o-mini (catalog-bound), "
            "Gemini-2.0-flash (catalog-bound), and Sajjad's idss-mcp. "
            "Perplexity sonar is informational only (web-augmented, no offline API model exists)."
        )
    )
    parser.add_argument("--url",       default="http://localhost:8001",
                        help="IDSS agent URL (default: http://localhost:8001)")
    parser.add_argument("--sajjad-url", default="http://localhost:9003",
                        help="Sajjad idss-mcp URL (default: http://localhost:9003)")
    parser.add_argument("--perplexity-key", default=None,
                        help="Perplexity API key (or set PERPLEXITY_API_KEY env var)")
    parser.add_argument("--gemini-key", default=None,
                        help="Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--save",      help="Path to save JSON results")
    parser.add_argument("--scenario",  type=int, help="Run only this scenario ID")
    parser.add_argument("--systems",   default="idss,gpt",
                        help="Comma-separated systems to run: idss,gpt,gemini,perplexity,sajjad "
                             "(default: idss,gpt)")
    parser.add_argument("--verbose",   action="store_true",
                        help="Print full turn text")
    parser.add_argument("--runs",      type=int, default=1,
                        help="Number of independent runs for pass@K statistics "
                             "(default: 1 = single run, no pass@K). "
                             "Recommend --runs 5 for reliable pass@5 reporting.")
    parser.add_argument("--threshold", type=float, default=PASS_THRESHOLD,
                        help=f"Pass threshold 0.0–1.0 (default: {PASS_THRESHOLD}). "
                             "Recommend 0.65 for multi-turn to overcome the scoring floor: "
                             "with constraint=1.0 and judge=0.0 the score is 0.55, so "
                             "threshold=0.5 auto-passes all compliant systems regardless of quality.")
    args = parser.parse_args()

    valid_systems = {"idss", "gpt", "gemini", "perplexity", "sajjad"}
    systems = [s.strip().lower() for s in args.systems.split(",")]
    for s in systems:
        if s not in valid_systems:
            print(f"ERROR: unknown system '{s}'. Valid: {', '.join(sorted(valid_systems))}")
            sys.exit(1)

    scenarios = list(MULTITURN_SCENARIOS)
    if args.scenario:
        scenarios = [sc for sc in scenarios if sc["id"] == args.scenario]

    if not scenarios:
        print("No scenarios match given filters.")
        sys.exit(1)

    threshold = args.threshold
    K         = args.runs

    if K > 1:
        print(f"\n{BOLD}Multi-run mode: K={K}, threshold={threshold}{RST}")
        print(f"  Will run all {len(scenarios)} scenario(s) × {len(systems)} system(s) "
              f"× {K} run(s) = {len(scenarios)*len(systems)*K} total evaluations.")
        if threshold <= PASS_THRESHOLD:
            print(f"  {YEL}WARNING: threshold={threshold} ≤ scoring floor (0.55 when constraint=1.0, judge=0.0). "
                  f"Consider --threshold 0.65 for more discriminating results.{RST}")

    all_run_outputs: List[Dict] = []

    for run_i in range(K):
        if K > 1:
            print(f"\n{'▓'*74}")
            print(f"  RUN {run_i + 1} / {K}")
            print(f"{'▓'*74}")
        # Pass save_path=None for intermediate runs; we save the merged output at end.
        # For a single run (K=1) we save directly via run_all_scenarios.
        run_save = args.save if K == 1 else None
        out = asyncio.run(run_all_scenarios(
            scenarios=scenarios,
            base_url=args.url,
            systems=systems,
            save_path=run_save,
            verbose=args.verbose,
            sajjad_url=args.sajjad_url,
            pplx_key=args.perplexity_key,
            gemini_key=args.gemini_key,
            pass_threshold=threshold,
        ))
        all_run_outputs.append(out)

    # After all runs: compute pass@K and category breakdown, print summary tables, save merged JSON.
    if K > 1:
        passatk       = compute_pass_at_k(all_run_outputs, systems, scenarios, threshold)
        cat_breakdown = compute_category_breakdown(all_run_outputs, systems, scenarios, threshold)
        print_pass_at_k_table(passatk, systems, K, threshold)
        print_category_table(cat_breakdown, systems)

        if args.save:
            merged = build_merged_output(all_run_outputs, passatk, cat_breakdown, systems, threshold)
            with open(args.save, "w") as f:
                json.dump(merged, f, indent=2)
            print(f"  Merged pass@{K} results saved to: {args.save}")


if __name__ == "__main__":
    main()
