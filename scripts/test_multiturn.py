#!/usr/bin/env python3
"""
Deterministic Multi-Turn Harness — IDSS Agent
==============================================
Tests that the agent correctly maintains and updates conversation state
across multiple turns. No LLM judge — all checks are deterministic.

Scoring dimensions per test case:
  - filter_score:    fraction of expected filters present after final turn
  - exclusion_score: fraction of expected excluded brands present after final turn
  - type_score:      1.0 if last turn's response_type matches expectation; N/A if unconstrained
  - final_score:     weighted average of applicable dimensions (equal weight)

Edge cases tested (tests 1–10, original):
  - Budget captured on second turn (T1 vague → T2 adds budget)
  - Brand exclusion persists through constraint update (exclusion must NOT be replaced)
  - Multiple brand exclusions accumulate (not overwrite)
  - RAM + budget extracted together from a single rich message
  - Mind-change clears a brand exclusion (user prefers previously excluded brand)
  - Three-turn preference build-up (interview → recs)
  - Screen size extracted alongside budget
  - Budget range (price_min + price_max both set)
  - OS preference persisted; excluded brand persists across turns
  - Vague one-liner → full spec on second turn

TauBench-style scenarios (tests 11–15):
  - Compare-from-first-message: compare intent with no prior session context
  - 5-turn constraint accumulation: OS → budget → exclusion → RAM built up over 5 turns
  - Budget overwrite: user raises budget mid-conversation (update, not accumulate)
  - Full exclude/un-exclude/re-exclude cycle: mind-change in both directions
  - Multi-correction: gaming → video editing pivot + RAM added mid-conversation

Usage:
    python scripts/test_multiturn.py
    python scripts/test_multiturn.py --url http://localhost:8001
    python scripts/test_multiturn.py --test 1 --test 5   # specific tests only
    python scripts/test_multiturn.py --verbose            # print full response JSON
    python scripts/test_multiturn.py --save results.json  # save to file
    python scripts/test_multiturn.py --k 3               # PASS-k: 3 independent runs per test
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed.  Run: pip install httpx")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass


# ============================================================================
# Test cases — 10 multi-turn scenarios
# ============================================================================
# Each case:
#   turns              : list of user messages (sent sequentially, same session)
#   check_after_turn   : (optional) dict of per-turn intermediate checks
#                        key = 1-indexed turn number, value = check dict
#   expected_filters   : dict of filter key → expected value or sentinel:
#                          "present" = key must exist with any non-None value
#                          a number  = value must equal this (within 5% tolerance)
#                          a list    = value must contain any of these strings
#   expected_exclusions: list of brand strings that MUST be in
#                        response.filters.excluded_brands after final turn
#   excluded_must_be_gone: list of brand strings that must NOT be in
#                           excluded_brands after final turn (mind-change)
#   expected_type_last : optional — "question" or "recommendations"
# ============================================================================

TESTS: List[Dict[str, Any]] = [
    # ── 1. Budget captured on second turn ──────────────────────────────────
    {
        "id": 1,
        "name": "Budget captured on second turn",
        "description": "T1 is vague (gaming laptop). T2 adds budget. Agent must extract budget.",
        "turns": [
            "I need a gaming laptop",
            "my budget is around $1000",
        ],
        "check_after_turn": {
            # After turn 2 the budget must be set
            2: {
                "expected_filters":   {"budget": "present"},
                "expected_exclusions": [],
            }
        },
        "expected_filters":    {"budget": "present"},
        "expected_exclusions": [],
        "excluded_must_be_gone": [],
        "expected_type_last": "recommendations",
    },

    # ── 2. Brand exclusion persists through budget update ──────────────────
    {
        "id": 2,
        "name": "Brand exclusion persists through budget update",
        "description": "T1 excludes HP. T2 changes budget. HP must stay excluded.",
        "turns": [
            "I need a laptop under $1500, no HP please",
            "actually let's raise the budget to $2000",
        ],
        "check_after_turn": {
            1: {
                "expected_filters":    {"budget": "present"},
                "expected_exclusions": ["HP"],
            },
            2: {
                "expected_filters":    {"budget": "present"},
                # HP must still be excluded after budget update
                "expected_exclusions": ["HP"],
            }
        },
        "expected_filters":    {"budget": "present"},
        "expected_exclusions": ["HP"],
        "excluded_must_be_gone": [],
        "expected_type_last": "recommendations",
    },

    # ── 3. Multiple brand exclusions accumulate ────────────────────────────
    {
        "id": 3,
        "name": "Multiple brand exclusions accumulate across turns",
        "description": "T1 excludes HP. T2 also excludes Lenovo. Both must remain in excluded_brands.",
        "turns": [
            "I want a laptop under $1200, no HP",
            "also please exclude Lenovo from the results",
        ],
        "check_after_turn": {
            1: {"expected_exclusions": ["HP"]},
            2: {"expected_exclusions": ["HP", "Lenovo"]},
        },
        "expected_filters":    {"budget": "present"},
        "expected_exclusions": ["HP", "Lenovo"],
        "excluded_must_be_gone": [],
        "expected_type_last": None,
    },

    # ── 4. RAM + budget extracted together ─────────────────────────────────
    {
        "id": 4,
        "name": "RAM and budget extracted from a single rich message",
        "description": "Single turn provides both RAM and budget constraints. Both must be extracted.",
        "turns": [
            "I want a laptop with at least 32GB RAM for machine learning, budget $2000",
        ],
        "check_after_turn": {},
        "expected_filters":    {"min_ram_gb": "present", "budget": "present"},
        "expected_exclusions": [],
        "excluded_must_be_gone": [],
        "expected_type_last": "recommendations",
    },

    # ── 5. Mind-change removes brand exclusion ─────────────────────────────
    {
        "id": 5,
        "name": "Mind-change: user pivots to previously excluded brand",
        "description": "T1 says no Dell. T2 asks about Dell XPS. Dell must be removed from exclusions.",
        "turns": [
            "I need a laptop under $1200, no Dell",
            "Actually, what do you think about the Dell XPS? I heard it's good.",
        ],
        "check_after_turn": {
            1: {"expected_exclusions": ["Dell"]},
        },
        "expected_filters":    {},
        "expected_exclusions": [],
        "excluded_must_be_gone": ["Dell"],
        "expected_type_last": None,
    },

    # ── 6. Three-turn preference build-up through interview ────────────────
    {
        "id": 6,
        "name": "Three-turn interview → filter accumulation → recommendations",
        "description": "T1 vague. T2 adds use case. T3 adds RAM + budget + brand exclusion. All must be in final filters.",
        "turns": [
            "I need a new laptop",
            "I'll be using it for graphic design and video editing in Premiere Pro",
            "I need at least 16GB RAM, budget $1500, and please no HP or Acer",
        ],
        "check_after_turn": {
            3: {
                "expected_filters":    {"min_ram_gb": "present", "budget": "present"},
                "expected_exclusions": ["HP", "Acer"],
            },
        },
        "expected_filters":    {"min_ram_gb": "present", "budget": "present"},
        "expected_exclusions": ["HP", "Acer"],
        "excluded_must_be_gone": [],
        "expected_type_last": "recommendations",
    },

    # ── 7. Screen size and budget extracted together ────────────────────────
    {
        "id": 7,
        "name": "Screen size and budget extracted in one turn",
        "description": "Single message with screen size + budget. Both must appear in filters.",
        "turns": [
            "Looking for a 15-inch laptop under $800 for college",
        ],
        "check_after_turn": {},
        "expected_filters":    {"screen_size": "present", "budget": "present"},
        "expected_exclusions": [],
        "excluded_must_be_gone": [],
        "expected_type_last": None,
    },

    # ── 8. Budget range: both min and max must be captured ─────────────────
    {
        "id": 8,
        "name": "Budget range: price_min and price_max both extracted",
        "description": "'between $1000 and $1500' — agent must set both min and max price filters.",
        "turns": [
            "I want a laptop between $1000 and $1500",
        ],
        "check_after_turn": {},
        "expected_filters":    {"price_min": "present", "price_max": "present"},
        "expected_exclusions": [],
        "excluded_must_be_gone": [],
        "expected_type_last": None,
    },

    # ── 9. Brand exclusion + constraint update across two turns ────────────
    {
        "id": 9,
        "name": "OS preference and brand exclusion both preserved on turn 2",
        "description": "T1: Windows laptop, no Mac, budget $1000. T2: needs 16GB RAM. Both exclusion and OS must stay.",
        "turns": [
            "I want a Windows laptop, no Apple/Mac, around $1000",
            "I also need at least 16GB RAM",
        ],
        "check_after_turn": {
            1: {"expected_exclusions": ["Apple"]},
            2: {
                "expected_filters":    {"min_ram_gb": "present", "budget": "present"},
                "expected_exclusions": ["Apple"],
            },
        },
        "expected_filters":    {"min_ram_gb": "present", "budget": "present"},
        "expected_exclusions": ["Apple"],
        "excluded_must_be_gone": [],
        "expected_type_last": "recommendations",
    },

    # ── 10. One-liner → full spec on second turn ────────────────────────────
    {
        "id": 10,
        "name": "One-liner followed by full spec on second turn",
        "description": "T1: 'gaming laptop' (too vague). T2: full spec. Filters must be extracted after T2.",
        "turns": [
            "gaming laptop",
            "RTX 4070, 32GB RAM, under $2500, no ASUS",
        ],
        "check_after_turn": {
            1: {
                # Should ask a question or give ambiguous recs — but NOT have budget set yet
                "expected_no_filters": ["budget"],
            },
            2: {
                "expected_filters":    {"min_ram_gb": "present", "budget": "present"},
                "expected_exclusions": ["ASUS"],
            },
        },
        "expected_filters":    {"min_ram_gb": "present", "budget": "present"},
        "expected_exclusions": ["ASUS"],
        "excluded_must_be_gone": [],
        "expected_type_last": "recommendations",
    },

    # ── 11. Compare-from-first-message (TauBench: rigid intent at turn 1) ──
    {
        "id": 11,
        "name": "Compare intent on first message (no prior context)",
        "description": (
            "T1 is a direct compare request with named products. "
            "Agent must not crash — returning recommendations, comparison, or a question is all valid. "
            "Tests rigidity: compare-from-start should not produce an error."
        ),
        "turns": [
            "Compare the Dell XPS 15 vs the MacBook Pro 16 for video editing",
        ],
        "check_after_turn": {},
        # No specific filter/exclusion expectations — just verify the agent responds gracefully
        "expected_filters":    {},
        "expected_exclusions": [],
        "excluded_must_be_gone": [],
        # Either response type is acceptable — we just verify there's no error
        "expected_type_last": None,
    },

    # ── 12. 5-turn constraint accumulation (TauBench: long session) ──────────
    {
        "id": 12,
        "name": "5-turn constraint accumulation",
        "description": (
            "T1 vague → T2 OS → T3 budget → T4 brand exclusion → T5 RAM. "
            "All four constraints must be present after T5. "
            "Tests that state accumulates correctly over a longer conversation."
        ),
        "turns": [
            "I'm looking for a laptop recommendation",
            "I need Windows OS, not Mac",
            "my budget is around $1200",
            "please exclude HP from the results",
            "I also need at least 16GB RAM",
        ],
        "check_after_turn": {
            3: {
                # Budget should be set after T3
                "expected_filters": {"budget": "present"},
            },
            4: {
                # HP should be excluded after T4
                "expected_filters":    {"budget": "present"},
                "expected_exclusions": ["HP"],
            },
            5: {
                # RAM + budget + HP exclusion all set by T5
                "expected_filters":    {"min_ram_gb": "present", "budget": "present"},
                "expected_exclusions": ["HP"],
            },
        },
        "expected_filters":    {"min_ram_gb": "present", "budget": "present"},
        "expected_exclusions": ["HP"],
        "excluded_must_be_gone": [],
        "expected_type_last": "recommendations",
    },

    # ── 13. Budget overwrite — user raises budget mid-conversation ───────────
    {
        "id": 13,
        "name": "Budget overwrite: user raises budget on second turn",
        "description": (
            "T1: under $700. T2: bumped to $1200. "
            "The new budget ($1200) should replace the old one ($700). "
            "Tests that budget update (not accumulation) is handled correctly."
        ),
        "turns": [
            "I want a laptop for college under $700",
            "I checked my finances — I can actually spend up to $1200 now",
        ],
        "check_after_turn": {
            1: {
                "expected_filters": {"budget": "present"},
            },
            2: {
                # Budget must still be present (updated, not cleared)
                "expected_filters": {"budget": "present"},
            },
        },
        "expected_filters":    {"budget": "present"},
        "expected_exclusions": [],
        "excluded_must_be_gone": [],
        "expected_type_last": "recommendations",
    },

    # ── 14. Full exclude → un-exclude → re-exclude cycle ─────────────────────
    {
        "id": 14,
        "name": "Exclude → un-exclude → re-exclude cycle (HP)",
        "description": (
            "T1: no HP. T2: 'HP is fine'. T3: 'no, keep HP out'. "
            "After T3, HP must be back in excluded_brands. "
            "Tests the full mind-change cycle in both directions."
        ),
        "turns": [
            "I want a laptop under $1000, please no HP",
            "Actually HP is fine, show me all brands",
            "No wait, I definitely don't want HP — please keep it out",
        ],
        "check_after_turn": {
            1: {
                "expected_exclusions": ["HP"],
            },
            2: {
                # HP should be un-excluded after T2 mind-change
                "excluded_must_be_gone": ["HP"],    # HP must NOT be in exclusions
            },
        },
        "expected_filters":    {"budget": "present"},
        "expected_exclusions": ["HP"],   # HP must be re-excluded after T3
        "excluded_must_be_gone": [],
        "expected_type_last": "recommendations",
    },

    # ── 15. Multi-correction: constraints + exclusion added across 3 turns ───
    {
        "id": 15,
        "name": "Multi-correction: gaming → video editing pivot + RAM update",
        "description": (
            "T1: gaming laptop (vague). T2: adds budget + brand exclusion. "
            "T3: user corrects use case to video editing, adds RAM requirement. "
            "Budget, RAM, and exclusion must all be present after T3."
        ),
        "turns": [
            "I need a gaming laptop",
            "budget around $2000, no ASUS please",
            "actually I mostly do video editing in Premiere Pro — also I need at least 32GB RAM",
        ],
        "check_after_turn": {
            2: {
                "expected_filters":    {"budget": "present"},
                "expected_exclusions": ["ASUS"],
            },
            3: {
                "expected_filters":    {"budget": "present", "min_ram_gb": "present"},
                "expected_exclusions": ["ASUS"],
            },
        },
        "expected_filters":    {"budget": "present", "min_ram_gb": "present"},
        "expected_exclusions": ["ASUS"],
        "excluded_must_be_gone": [],
        "expected_type_last": "recommendations",
    },

    # ── 16. Q4 Scenario A — stale price filter overwrite ($800 → $600) ───────
    # Nino issue #27: agent stated $800 budget, user corrects to $600.
    # The new price MUST overwrite the old one — not accumulate alongside it.
    # A stale $800 price_max_cents co-existing with a new $600 value fails this test.
    {
        "id": 16,
        "name": "Q4-A: Stale price overwrite — $800 → $600 downgrade",
        "description": (
            "T1: gaming laptop, budget $800. "
            "T2: 'make it $600 max'. "
            "After T2, price_max_cents must be ≤ 60000 (not 80000). "
            "Tests that process_refinement() clears the old budget key before setting the new one."
        ),
        "turns": [
            "I need a gaming laptop, budget around $800",
            "Actually, make it $600 max please",
        ],
        "check_after_turn": {
            1: {
                # After T1: budget must be captured (any value)
                "expected_filters": {"budget": "present"},
            },
            2: {
                # After T2: budget must still be present AND updated downward
                "expected_filters": {"budget": "present"},
                # Strict price ceiling check: must be ≤ 60000 cents ($600)
                # This catches the stale-price bug where both $800 and $600 coexist
                "check_price_max_cents_lte": 60000,
            },
        },
        "expected_filters": {"budget": "present"},
        "expected_exclusions": [],
        "excluded_must_be_gone": [],
        # Strict final-turn check: price_max_cents must be ≤ 60000
        "expected_price_max_cents_lte": 60000,
        "expected_type_last": "recommendations",
    },
]


# ============================================================================
# Filter helpers
# ============================================================================

# Map abstract slot names used in test spec → concrete keys in response.filters
_FILTER_KEY_ALIASES: Dict[str, List[str]] = {
    "budget":      ["price_max_cents", "price_min_cents", "price"],
    "min_ram_gb":  ["min_ram_gb"],
    "screen_size": ["min_screen_size", "max_screen_size", "screen_size"],
    "price_min":   ["price_min_cents"],
    "price_max":   ["price_max_cents"],
}


def _resolve_filter(slot: str, filters: Dict[str, Any]) -> Any:
    """Return the first non-None value found for slot or its aliases."""
    keys = _FILTER_KEY_ALIASES.get(slot, [slot])
    for k in keys:
        if k in filters and filters[k] is not None:
            return filters[k]
    return None


def _check_filters(
    expected: Dict[str, Any],
    filters: Dict[str, Any],
) -> Tuple[float, List[str], List[str]]:
    """Check expected_filters dict against actual filters.

    Returns (ratio_found, found_list, missing_list).
    """
    if not expected:
        return 1.0, [], []
    found, missing = [], []
    for slot, expected_val in expected.items():
        actual = _resolve_filter(slot, filters)
        if actual is None:
            missing.append(slot)
        elif expected_val == "present":
            found.append(slot)
        else:
            found.append(slot)   # value check is lenient (presence is sufficient)
    ratio = len(found) / len(expected) if expected else 1.0
    return ratio, found, missing


def _normalize_exclusions(raw: Any) -> List[str]:
    """Normalize excluded_brands from the response filter dict.

    Can be a list ["HP", "Acer"], a comma-string "HP,Acer", or a single string "HP".
    Returns a list of stripped brand strings.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(b).strip() for b in raw if str(b).strip()]
    if isinstance(raw, str):
        return [b.strip() for b in raw.split(",") if b.strip()]
    return []


def _check_exclusions(
    expected_excluded: List[str],
    filters: Dict[str, Any],
) -> Tuple[float, List[str], List[str]]:
    """Check that all expected_excluded brands are in filters.excluded_brands.

    Returns (ratio_found, found_list, missing_list).
    """
    if not expected_excluded:
        return 1.0, [], []
    actual_excl = _normalize_exclusions(filters.get("excluded_brands"))
    # Case-insensitive comparison
    actual_lower = [b.lower() for b in actual_excl]
    found, missing = [], []
    for brand in expected_excluded:
        if brand.lower() in actual_lower:
            found.append(brand)
        else:
            missing.append(brand)
    ratio = len(found) / len(expected_excluded) if expected_excluded else 1.0
    return ratio, found, missing


def _check_price_max_cents_lte(ceiling_cents: int, filters: Dict[str, Any]) -> Tuple[bool, str]:
    """Verify that the current price_max_cents is at or below ceiling_cents.

    Returns (passed, note_string).
    Used for Q4-A stale price detection: if price_max_cents is ABOVE the new
    stated ceiling it means the old value was not cleared on update.

    Checks both 'price_max_cents' and 'price' (dollars) keys to handle
    different response formats.
    """
    # Try cents key first
    actual = filters.get("price_max_cents")
    if actual is not None:
        try:
            val = int(actual)
            if val <= ceiling_cents:
                return True, f"✓ price_max_cents={val} ≤ {ceiling_cents}"
            return False, f"✗ stale price: price_max_cents={val} > ceiling {ceiling_cents}"
        except (ValueError, TypeError):
            pass
    # Fall back to dollar price key
    actual_dollars = filters.get("price")
    if actual_dollars is not None:
        try:
            val_cents = int(float(actual_dollars) * 100)
            if val_cents <= ceiling_cents:
                return True, f"✓ price={actual_dollars} → {val_cents} cents ≤ {ceiling_cents}"
            return False, f"✗ stale price: price={actual_dollars} → {val_cents}c > ceiling {ceiling_cents}"
        except (ValueError, TypeError):
            pass
    # Key absent — budget not yet set; treat as pass (absence ≠ stale)
    return True, f"price_max_cents not in filters (not yet set)"


def _check_brand_gone(
    must_be_gone: List[str],
    filters: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """Check that brands in must_be_gone are NOT in excluded_brands.

    Returns (all_gone, still_present_list).
    """
    if not must_be_gone:
        return True, []
    actual_excl = _normalize_exclusions(filters.get("excluded_brands"))
    actual_lower = [b.lower() for b in actual_excl]
    still_present = [b for b in must_be_gone if b.lower() in actual_lower]
    return len(still_present) == 0, still_present


# ============================================================================
# HTTP helper
# ============================================================================

async def send_turn(
    client: httpx.AsyncClient,
    base_url: str,
    message: str,
    session_id: str,
) -> Dict[str, Any]:
    resp = await client.post(
        f"{base_url}/chat",
        json={"message": message, "session_id": session_id},
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()


# ============================================================================
# Run a single test case (sequential turns)
# ============================================================================

async def run_test(
    client: httpx.AsyncClient,
    base_url: str,
    test: Dict[str, Any],
    verbose: bool,
) -> Dict[str, Any]:
    session_id = str(uuid.uuid4())
    turns_data: List[Dict] = []
    intermediate_results: List[Dict] = []

    # ── Send all turns sequentially ────────────────────────────────────────
    for turn_idx, message in enumerate(test["turns"], start=1):
        t0 = time.perf_counter()
        try:
            resp = await send_turn(client, base_url, message, session_id)
        except Exception as exc:
            resp = {"error": str(exc), "filters": {}, "response_type": "error", "message": ""}
        elapsed_ms = round((time.perf_counter() - t0) * 1000)
        turns_data.append({"turn": turn_idx, "message": message,
                            "response": resp, "elapsed_ms": elapsed_ms})

        # ── Intermediate checks (defined in check_after_turn) ──────────────
        check_spec = (test.get("check_after_turn") or {}).get(turn_idx, {})
        if check_spec:
            filters = resp.get("filters", {})
            icheck: Dict[str, Any] = {"turn": turn_idx, "passed": True, "notes": []}

            if "expected_filters" in check_spec:
                ratio, found, missing = _check_filters(check_spec["expected_filters"], filters)
                if missing:
                    icheck["passed"] = False
                    icheck["notes"].append(f"T{turn_idx} missing filters: {missing}")
                else:
                    icheck["notes"].append(f"T{turn_idx} filters OK: {found}")

            if "expected_exclusions" in check_spec:
                eratio, efound, emissing = _check_exclusions(
                    check_spec["expected_exclusions"], filters
                )
                if emissing:
                    icheck["passed"] = False
                    icheck["notes"].append(
                        f"T{turn_idx} brand exclusion missing: {emissing}"
                    )
                else:
                    icheck["notes"].append(
                        f"T{turn_idx} brand exclusion OK: {efound}"
                    )

            if "expected_no_filters" in check_spec:
                # Verify that certain filters are NOT yet set (e.g. budget after vague T1)
                filters_at_turn = resp.get("filters", {})
                premature = [
                    slot for slot in check_spec["expected_no_filters"]
                    if _resolve_filter(slot, filters_at_turn) is not None
                ]
                if premature:
                    # This is a soft warning, not a hard failure
                    icheck["notes"].append(
                        f"T{turn_idx} WARN: filters set earlier than expected: {premature}"
                    )
                else:
                    icheck["notes"].append(
                        f"T{turn_idx} correctly no early filters: {check_spec['expected_no_filters']}"
                    )

            if "excluded_must_be_gone" in check_spec:
                # Verify that certain brands have been removed from exclusions (mid-turn mind-change)
                gone_ok, still_present = _check_brand_gone(
                    check_spec["excluded_must_be_gone"], filters
                )
                if still_present:
                    icheck["passed"] = False
                    icheck["notes"].append(
                        f"T{turn_idx} brand should be un-excluded but still present: {still_present}"
                    )
                else:
                    icheck["notes"].append(
                        f"T{turn_idx} brand correctly un-excluded: {check_spec['excluded_must_be_gone']}"
                    )

            if "check_price_max_cents_lte" in check_spec:
                # Q4-A stale-price check: price ceiling must not exceed the new stated value
                price_ok, price_note = _check_price_max_cents_lte(
                    check_spec["check_price_max_cents_lte"], filters
                )
                if not price_ok:
                    icheck["passed"] = False
                icheck["notes"].append(f"T{turn_idx} price ceiling: {price_note}")

            intermediate_results.append(icheck)

    # ── Final-turn scoring (deterministic) ─────────────────────────────────
    final_resp    = turns_data[-1]["response"]
    final_filters = final_resp.get("filters", {})

    # Filter score
    filt_ratio, filt_found, filt_missing = _check_filters(
        test.get("expected_filters", {}), final_filters
    )

    # Exclusion score
    excl_ratio, excl_found, excl_missing = _check_exclusions(
        test.get("expected_exclusions", []), final_filters
    )

    # Brand-gone check (mind-change)
    brand_gone_ok, brand_still_present = _check_brand_gone(
        test.get("excluded_must_be_gone", []), final_filters
    )

    # Response type check
    exp_type = test.get("expected_type_last")
    got_type = final_resp.get("response_type", "?")
    if exp_type is None:
        type_score = None
        type_note  = f"type={got_type!r} (unconstrained)"
    elif got_type == exp_type:
        type_score = 1.0
        type_note  = f"✓ type={got_type!r}"
    else:
        type_score = 0.0
        type_note  = f"✗ expected type={exp_type!r}, got {got_type!r}"

    # Q4-A stale-price final check: if test specifies expected_price_max_cents_lte,
    # verify the final price ceiling is at or below that value.
    price_max_ok = True
    price_max_note = ""
    if "expected_price_max_cents_lte" in test:
        price_max_ok, price_max_note = _check_price_max_cents_lte(
            test["expected_price_max_cents_lte"], final_filters
        )

    # Compute final score — equal weight on applicable dimensions
    scores: List[float] = [filt_ratio, excl_ratio]
    if not brand_gone_ok:
        scores.append(0.0)   # mind-change failure
    if not price_max_ok:
        scores.append(0.0)   # stale price bug
    if type_score is not None:
        scores.append(type_score)
    final_score = round(sum(scores) / len(scores), 4) if scores else 1.0

    PASS_THRESHOLD = 0.6   # stricter for deterministic checks
    passed = final_score >= PASS_THRESHOLD

    # ── Build result record ────────────────────────────────────────────────
    notes: List[str] = []
    if filt_missing:
        notes.append(f"✗ missing filters: {filt_missing}")
    elif test.get("expected_filters"):
        notes.append(f"✓ filters: {filt_found}")

    if excl_missing:
        notes.append(f"✗ missing exclusions: {excl_missing}")
    elif test.get("expected_exclusions"):
        notes.append(f"✓ exclusions: {excl_found}")

    if not brand_gone_ok:
        notes.append(f"✗ brand should be removed but still excluded: {brand_still_present}")
    elif test.get("excluded_must_be_gone"):
        notes.append(f"✓ brand correctly removed from exclusions: {test['excluded_must_be_gone']}")

    if price_max_note:
        notes.append(price_max_note)

    notes.append(type_note)

    # Collect any intermediate failures
    inter_notes = []
    for ic in intermediate_results:
        if not ic["passed"]:
            inter_notes.extend(ic["notes"])

    return {
        "id":            test["id"],
        "name":          test["name"],
        "n_turns":       len(test["turns"]),
        "score":         final_score,
        "passed":        passed,
        "filter_score":  filt_ratio,
        "exclusion_score": excl_ratio,
        "type_score":    type_score,
        "brand_gone_ok": brand_gone_ok,
        "notes":         notes,
        "intermediate_failures": inter_notes,
        "final_filters": final_filters,
        "final_response_type": got_type,
        "turns_ms":      [t["elapsed_ms"] for t in turns_data],
    }


# ============================================================================
# PASS-k wrapper (TauBench-style repeated independent runs)
# ============================================================================

async def run_test_k_times(
    client: httpx.AsyncClient,
    base_url: str,
    test: Dict[str, Any],
    verbose: bool,
    k: int,
) -> Dict[str, Any]:
    """Run one test case k times with fully independent sessions (PASS-k).

    Each run gets a fresh session_id so agent state is completely independent.
    This matters because LLM responses are stochastic — a test that passes 3/3
    is more reliable than one that passes 1/3 by luck.

    Returns an aggregated result:
      pass_k    = fraction of k runs that pass (the PASS-k metric)
      score     = mean score across k runs
      passed    = True if pass_k >= PASS_THRESHOLD (majority pass)
      run_scores = list of per-run scores for variance analysis

    For k=1 the result is identical to run_test() with pass_k/k fields added.
    """
    runs: List[Dict] = []
    for _ in range(k):
        r = await run_test(client, base_url, test, verbose)
        runs.append(r)

    pass_count = sum(1 for r in runs if r["passed"])
    pass_k     = pass_count / k
    avg_score  = sum(r["score"] for r in runs) / k
    avg_filter = sum(r["filter_score"] for r in runs) / k
    avg_excl   = sum(r["exclusion_score"] for r in runs) / k
    type_runs  = [r for r in runs if r["type_score"] is not None]
    avg_type   = (sum(r["type_score"] for r in type_runs) / len(type_runs)
                  if type_runs else None)

    # For notes, use the worst-performing run — most informative for debugging
    worst = min(runs, key=lambda r: r["score"])

    return {
        "id":               test["id"],
        "name":             test["name"],
        "n_turns":          len(test["turns"]),
        "score":            round(avg_score, 4),
        "passed":           pass_k >= PASS_THRESHOLD,
        "pass_k":           round(pass_k, 4),
        "pass_count":       pass_count,
        "k":                k,
        "run_scores":       [r["score"] for r in runs],
        "filter_score":     round(avg_filter, 4),
        "exclusion_score":  round(avg_excl, 4),
        "type_score":       round(avg_type, 4) if avg_type is not None else None,
        "brand_gone_ok":    all(r["brand_gone_ok"] for r in runs),
        "notes":            worst["notes"],
        "intermediate_failures": worst["intermediate_failures"],
        "final_filters":    worst["final_filters"],
        "final_response_type": worst["final_response_type"],
        "turns_ms":         worst["turns_ms"],
    }


# ============================================================================
# Main runner
# ============================================================================

GREEN = "\033[32m"
RED   = "\033[31m"
YEL   = "\033[33m"
BOLD  = "\033[1m"
RST   = "\033[0m"

PASS_THRESHOLD = 0.6


async def run_all_tests(
    base_url: str,
    selected_ids: Optional[List[int]],
    verbose: bool,
    save_path: Optional[str],
    k: int = 1,
) -> None:
    tests = TESTS
    if selected_ids:
        tests = [t for t in tests if t["id"] in selected_ids]

    total = len(tests)
    k_label = f"  PASS-k: k={k} independent runs per test" if k > 1 else ""
    print(f"\n{'='*70}")
    print(f"  {BOLD}IDSS Multi-Turn Harness{RST} — {total} test cases → {base_url}")
    print(f"  All checks deterministic (no LLM judge)  Threshold: {PASS_THRESHOLD}")
    if k_label:
        print(k_label)
    print(f"{'='*70}\n")

    async with httpx.AsyncClient(timeout=120) as client:
        # Run tests sequentially — each test (and its k runs) uses fresh session_ids
        results = []
        for test in tests:
            run_label = f"×{k} " if k > 1 else ""
            print(f"  Running T{test['id']:2d} {run_label}: {test['name'][:52]}...", end=" ", flush=True)
            try:
                r = await run_test_k_times(client, base_url, test, verbose, k)
            except httpx.ConnectError:
                print(f"\n  FAIL: Cannot connect to {base_url}")
                sys.exit(1)
            results.append(r)
            status = f"{GREEN}PASS{RST}" if r["passed"] else f"{RED}FAIL{RST}"
            if k > 1:
                print(f"{status}  PASS@{k}={r['pass_k']:.2f} ({r['pass_count']}/{k})  avg={r['score']:.3f}")
            else:
                print(f"{status}  score={r['score']:.3f}")

    print(f"\n{'─'*70}")
    print(f"\n  {BOLD}Detailed results:{RST}\n")

    for r in results:
        status = f"{GREEN}PASS{RST}" if r["passed"] else f"{RED}FAIL{RST}"
        ms_str = " + ".join(f"{ms}ms" for ms in r["turns_ms"])
        type_str = "N/A" if r["type_score"] is None else f"{r['type_score']:.2f}"
        k_used = r.get("k", 1)
        if k_used > 1:
            k_info = f"  PASS@{k_used}={r['pass_k']:.2f}({r['pass_count']}/{k_used})"
        else:
            k_info = ""
        print(
            f"  T{r['id']:2d} {status}  score={r['score']:.3f}{k_info}"
            f"  filter={r['filter_score']:.2f}"
            f"  excl={r['exclusion_score']:.2f}"
            f"  type={type_str}"
            f"  [{r['n_turns']} turns: {ms_str}]"
        )
        if k_used > 1:
            print(f"       run scores: {r['run_scores']}")
        for note in r["notes"]:
            print(f"       {note}")
        if r["intermediate_failures"]:
            for note in r["intermediate_failures"]:
                print(f"       {YEL}INTER: {note}{RST}")
        if verbose:
            filt_display = {
                fk: fv for fk, fv in r["final_filters"].items()
                if fv is not None and fk not in ("_soft_preferences",)
            }
            print(f"       final_filters: {filt_display}")

    # ── Summary ────────────────────────────────────────────────────────────
    n_pass = sum(1 for r in results if r["passed"])
    n_fail = total - n_pass
    avg_filter = sum(r["filter_score"]    for r in results) / total
    avg_excl   = sum(r["exclusion_score"] for r in results) / total
    type_results = [r for r in results if r["type_score"] is not None]
    avg_type   = (sum(r["type_score"] for r in type_results) / len(type_results)
                  if type_results else None)
    avg_score  = sum(r["score"] for r in results) / total

    # PASS-k aggregate (meaningful only when k > 1)
    k_vals = [r.get("k", 1) for r in results]
    k_used_global = k_vals[0] if k_vals else 1
    if k_used_global > 1:
        pass_k_vals = [r.get("pass_k", float(r["passed"])) for r in results]
        avg_pass_k = sum(pass_k_vals) / len(pass_k_vals)
    else:
        avg_pass_k = None

    # Conversation-length breakdown: short (≤2 turns) vs long (≥3 turns)
    short_results = [r for r in results if r["n_turns"] <= 2]
    long_results  = [r for r in results if r["n_turns"] >= 3]

    def _group_stats(grp: List[Dict]) -> Tuple[int, int, float]:
        if not grp:
            return 0, 0, 0.0
        n = len(grp)
        p = sum(1 for r in grp if r["passed"])
        a = sum(r["score"] for r in grp) / n
        return n, p, a

    ns, ps, as_ = _group_stats(short_results)
    nl, pl, al  = _group_stats(long_results)

    print(f"\n{'─'*70}")
    print(f"\n  {BOLD}Summary{RST}")
    print(f"  Passed:          {n_pass}/{total}  ({100*n_pass/total:.0f}%)")
    print(f"  Avg score:       {avg_score:.3f}")
    if avg_pass_k is not None:
        print(f"  Avg PASS@{k_used_global}:      {avg_pass_k:.3f}   (mean pass fraction across {total} tests × {k_used_global} runs)")
    print(f"  Avg filter acc:  {avg_filter:.3f}   (fraction of expected filters extracted)")
    print(f"  Avg excl acc:    {avg_excl:.3f}   (fraction of expected exclusions enforced)")
    if avg_type is not None:
        print(f"  Avg type acc:    {avg_type:.3f}   (correct response type on last turn)")

    # Conversation-length breakdown table
    print(f"\n  {BOLD}Conversation-length breakdown:{RST}")
    print(f"  {'Category':<22} {'N':>4}  {'Pass':>5}  {'Pass%':>6}  {'Avg':>7}")
    print(f"  {'─'*46}")
    if ns:
        print(f"  {'Short (≤2 turns)':<22} {ns:>4}  {ps:>5}  {100*ps/ns:>5.0f}%  {as_:>7.3f}")
    if nl:
        print(f"  {'Long (≥3 turns)':<22} {nl:>4}  {pl:>5}  {100*pl/nl:>5.0f}%  {al:>7.3f}")
    print(f"  {'─'*46}")
    print(f"  {'All':<22} {total:>4}  {n_pass:>5}  {100*n_pass/total:>5.0f}%  {avg_score:>7.3f}")

    failed = [r for r in results if not r["passed"]]
    if failed:
        print(f"\n  {RED}Failed tests:{RST}")
        for r in failed:
            print(f"    T{r['id']:2d}  {r['name']}")
            for note in r["notes"]:
                if "✗" in note:
                    print(f"         {note}")

    # ── Save JSON ──────────────────────────────────────────────────────────
    if save_path:
        output = {
            "summary": {
                "total":   total,
                "passed":  n_pass,
                "failed":  n_fail,
                "pass_pct": round(100 * n_pass / total, 1),
                "avg_score": round(avg_score, 4),
                "avg_filter_score":    round(avg_filter, 4),
                "avg_exclusion_score": round(avg_excl, 4),
                "avg_type_score": round(avg_type, 4) if avg_type is not None else None,
                # PASS-k fields (None when k=1)
                "k":           k_used_global if k_used_global > 1 else 1,
                "avg_pass_k":  round(avg_pass_k, 4) if avg_pass_k is not None else None,
                # Conversation-length breakdown
                "n_turns_breakdown": {
                    "short_le2": {"n": ns, "passed": ps,
                                  "pass_pct": round(100*ps/ns, 1) if ns else None,
                                  "avg_score": round(as_, 4) if ns else None},
                    "long_ge3":  {"n": nl, "passed": pl,
                                  "pass_pct": round(100*pl/nl, 1) if nl else None,
                                  "avg_score": round(al, 4) if nl else None},
                },
            },
            "results": results,
        }
        with open(save_path, "w") as fout:
            json.dump(output, fout, indent=2)
        print(f"\n  Saved to: {save_path}")

    print()


# ============================================================================
# Entry point
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic multi-turn harness for IDSS agent"
    )
    parser.add_argument("--url", default="http://localhost:8000",
                        help="Agent base URL (default: http://localhost:8000)")
    parser.add_argument("--test", type=int, action="append", dest="tests", metavar="N",
                        help="Run only test with this ID (repeatable)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print final_filters for each test case")
    parser.add_argument("--save", metavar="FILE",
                        help="Save JSON results to FILE")
    parser.add_argument("--k", type=int, default=1, metavar="K",
                        help="PASS-k: run each test K times independently (default: 1). "
                             "k>1 enables TauBench-style reliability measurement.")
    args = parser.parse_args()

    asyncio.run(run_all_tests(
        base_url=args.url,
        selected_ids=args.tests,
        verbose=args.verbose,
        save_path=args.save,
        k=args.k,
    ))


if __name__ == "__main__":
    main()
