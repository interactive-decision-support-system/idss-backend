#!/usr/bin/env python3
"""
Conference Demo Query Tester
============================
Sends all 43 demo queries to the local agent (POST /chat on port 8001),
checks the response, and prints a formatted report.

Usage:
    # Make sure the server is running first:
    #   cd mcp-server && uvicorn app.main:app --port 8001
    python scripts/test_demo_queries.py

Options:
    --url   Base URL (default: http://localhost:8001)
    --query N   Run only query N (1-indexed, e.g. --query 26)
    --group LABEL   Run only queries in a group (e.g. "brand exclusion")
    --verbose  Print full response JSON for every query
"""

import argparse
import json
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)


# ============================================================================
# 43 Conference Demo Queries
# ============================================================================

QUERIES: List[Dict[str, Any]] = [
    # ── Expert / Technical ──────────────────────────────────────────────────
    {
        "id": 1,
        "group": "expert",
        "label": "AI researcher (RTX 4060, 32GB RAM, 1TB, <$2k)",
        "message": (
            "I need a laptop for PyTorch deep learning and fine-tuning LLMs locally. "
            "Must have NVIDIA RTX 4060 or better, 32GB RAM, 1TB NVMe SSD, under $2,000, "
            "and weigh under 5 lbs for carrying to lab."
        ),
        "expect_domain": "laptops",
        "expect_filters": ["min_ram_gb", "budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": True,  # all criteria in one message → should go direct
    },
    {
        "id": 2,
        "group": "expert",
        "label": "UI/UX designer (16in, 16GB, $1500)",
        "message": (
            "I'm a UI/UX designer using Figma and Webflow with occasional video editing "
            "in Premiere Pro. I want a 16-inch display with accurate colors, 16GB RAM, "
            "512GB storage, good battery, and a budget of $1,500."
        ),
        "expect_domain": "laptops",
        "expect_filters": ["min_ram_gb", "budget", "screen_size"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": True,
    },
    {
        "id": 3,
        "group": "expert",
        "label": "Developer (Linux, ThinkPad, 32GB, 8h battery, <$1500)",
        "message": (
            "I need a developer laptop for React, Docker with 10 containers, and "
            "PostgreSQL locally. Must run Linux, have a ThinkPad-style keyboard, "
            "32GB RAM, 8+ hours battery, and cost under $1,500."
        ),
        "expect_domain": "laptops",
        "expect_filters": ["min_ram_gb", "budget", "os"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": True,
    },
    {
        "id": 4,
        "group": "expert",
        "label": "Student gamer (16GB, <$700, light, 8h battery)",
        "message": (
            "I'm a broke college student who wants to play Valorant and do Python "
            "assignments. Must last 8 hours unplugged, weigh under 4 lbs, have 16GB "
            "RAM, and cost under $700."
        ),
        "expect_domain": "laptops",
        "expect_filters": ["min_ram_gb", "budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": True,
    },
    {
        "id": 5,
        "group": "expert",
        "label": "High-end gamer (RTX 4070, 32GB, 1440p 165Hz, $2k-$2.5k)",
        "message": (
            "I want to play Cyberpunk 2077 on high settings and stream on Twitch. "
            "Need RTX 4070 or better, 32GB RAM, a 1440p 165Hz display, and a "
            "budget between $2,000 and $2,500."
        ),
        "expect_domain": "laptops",
        "expect_filters": ["min_ram_gb", "budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": True,
    },
    # ── Typos / Casual ──────────────────────────────────────────────────────
    {
        "id": 6,
        "group": "typos",
        "label": "Typo: 'latop', 'arond', 'nothin'",
        "message": "need latop for school. budget arond 500 bucks. nothin too heavy",
        "expect_domain": "laptops",
        "expect_filters": ["budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,  # under-specified, may ask q
    },
    {
        "id": 7,
        "group": "typos",
        "label": "Typo: 'lookign', 'somthing', MacBook alternative",
        "message": "lookign for somthing like macbook but cheaper, dont need apple tax",
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 8,
        "group": "typos",
        "label": "ALL CAPS frustrated user, battery complaint",
        "message": (
            "LAPTOP WITH GOOD BATTRY AND NOT SLOW. CAPS BECAUSE IM FRUSTRATED MY "
            "CURRENT ONE DIES IN 2 HOURS"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 9,
        "group": "typos",
        "label": "ThinkPad, Zoom calls, 8hrs, coffee shop",
        "message": "thinkpad? or something. need it for zoom calls 8hrs a day from coffee shop",
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── Contradictory / Impossible ──────────────────────────────────────────
    {
        "id": 10,
        "group": "contradictory",
        "label": "Impossible: RTX 4090, 64GB, 20h battery, 2lbs, <$800",
        "message": (
            "I want RTX 4090, 64GB RAM, 4K OLED display, 20-hour battery, "
            "weighs 2 lbs, under $800. Is that possible?"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget", "min_ram_gb"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": True,
    },
    {
        "id": 11,
        "group": "contradictory",
        "label": "Contradiction: gaming RTX 4080 + fanless + looks like MacBook",
        "message": (
            "Need a gaming laptop with RTX 4080, but it HAS to be fanless and silent. "
            "Also no bulky design, must look like a MacBook."
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 12,
        "group": "contradictory",
        "label": "Indecisive: 32GB vs 16GB, Blender, <$1200",
        "message": (
            "Want 32GB RAM but I've heard more RAM drains battery so actually "
            "maybe 16GB but then can I still run Blender? Idk just find me "
            "something good under $1,200"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── Zero Technical Knowledge ─────────────────────────────────────────────
    {
        "id": 13,
        "group": "no_tech",
        "label": "Non-technical: Netflix, homework, video calls, 'doesn't freeze'",
        "message": (
            "idk what specs are. my old dell broke. i just watch netflix, do "
            "homework, and video call grandma. something that doesnt freeze"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 14,
        "group": "no_tech",
        "label": "Non-technical: 'power user', 40 Chrome tabs, Excel",
        "message": (
            "what's a good one for a 'power user' (my IT guy called me that). "
            "i mostly have like 40 chrome tabs open and excel files"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 15,
        "group": "no_tech",
        "label": "Gift for data scientist boyfriend, $1800",
        "message": (
            "my boyfriend does 'data science' stuff and his birthday is next week, "
            "budget $1,800, what should i get him"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 16,
        "group": "no_tech",
        "label": "Non-technical: 'the cloud', no storage needed?",
        "message": (
            "need something for 'the cloud'. my company is moving everything to cloud. "
            "does that mean i dont need storage?"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── Overly Specific / Forum ──────────────────────────────────────────────
    {
        "id": 17,
        "group": "forum",
        "label": "r/SuggestALaptop: Framework 16 alternative, $1400",
        "message": (
            "r/SuggestALaptop told me to get a Framework 16 with AMD 7945HX but "
            "my budget is $1400 not $1600. are there alternatives with same iGPU TDP?"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 18,
        "group": "forum",
        "label": "Ultra-spec: PCIe 5.0, DDR5-5600, MUX switch, Wi-Fi 7",
        "message": (
            "looking for laptop with PCIe 5.0 NVMe, DDR5-5600 dual channel, "
            "MUX switch, per-key RGB, Thunderbolt 4, MIL-SPEC 810H, "
            "and Wi-Fi 7. must not throttle under sustained cinebench load"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 19,
        "group": "forum",
        "label": "CPU comparison: i7-13700H vs Ryzen 7 7745HX, AutoCAD",
        "message": (
            "i7-13700H or Ryzen 7 7745HX? for single-threaded performance only, "
            "not multicore. doing lots of AutoCAD 2D drafting"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── Budget Anchoring ─────────────────────────────────────────────────────
    {
        "id": 20,
        "group": "budget",
        "label": "Budget shock: Chromebook was $200, replacing it",
        "message": (
            "Chromebook was $200, now they want $1,200 for a 'real laptop'?? "
            "just need something to replace it. why is everything so expensive"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 21,
        "group": "budget",
        "label": "Facebook marketplace RTX 4060 $400 — cheapest new one?",
        "message": (
            "found an RTX 4060 laptop on facebook marketplace for $400, "
            "seems too good. is that legit or should i buy new from you? "
            "whats the cheapest new one with 4060?"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 22,
        "group": "budget",
        "label": "Spend all of $3000 work budget — best possible",
        "message": (
            "my work gives me $3,000 for a laptop. i want to spend all of it. "
            "what's the absolute best possible laptop money can buy right now"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── Domain Switching ─────────────────────────────────────────────────────
    {
        "id": 23,
        "group": "domain_switch",
        "label": "Pivot from laptop to iPad/Surface/Chromebook",
        "message": (
            "actually forget laptops i want an ipad with a keyboard instead. "
            "does that count? or maybe a surface pro? or chromebook? "
            "im confused just help me pick"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 24,
        "group": "domain_switch",
        "label": "Laptop + 4K 144Hz monitor, $2000 total",
        "message": (
            "need a laptop AND a monitor. the monitor should be 4K 144Hz. "
            "total budget $2,000 for both"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 25,
        "group": "domain_switch",
        "label": "5 identical developer laptops, <$1200 each",
        "message": (
            "im buying this for my startup. we need 5 laptops, all the same, "
            "developer-grade, under $1,200 each"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── Brand Exclusions (HIGH RISK — our main regression fixes) ─────────────
    {
        "id": 26,
        "group": "brand_exclusion",
        "label": "No HP, no Acer — $900, 16GB RAM",
        "message": (
            "anything but HP. had 3 HP laptops and all died. also no Acer, "
            "heard bad things. budget $900, 16GB RAM"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["excluded_brands", "budget", "min_ram_gb"],
        "must_not_contain_brands": ["HP", "hp", "Acer", "acer"],
        "expect_recs_on_first": True,
        "check_brand_exclusion": True,
    },
    {
        "id": 27,
        "group": "brand_exclusion",
        "label": "No Windows 11, must have Windows 10 or Linux, 16GB, SSD, <$1000",
        "message": (
            "i refuse to use Windows 11. must come with Windows 10 or Linux. "
            "16GB RAM, SSD, under $1,000"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["min_ram_gb", "budget", "storage_type", "os"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": True,
        "check_os_filter": True,
    },
    {
        "id": 28,
        "group": "brand_exclusion",
        "label": "No gaming aesthetic, no RGB, professional look, runs DaVinci",
        "message": (
            "no gaming aesthetic. no RGB. no big logos. just looks "
            "professional for client meetings but can also run DaVinci Resolve"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── Time / Urgency ───────────────────────────────────────────────────────
    {
        "id": 29,
        "group": "urgency",
        "label": "Need today/tomorrow, Prime 2-day, in-stock, $700",
        "message": (
            "need it TODAY or tomorrow at latest. amazon prime 2-day. "
            "in-stock only. $700 budget. student"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 30,
        "group": "urgency",
        "label": "APO shipping, internship starts Monday",
        "message": (
            "shipping to military APO address. are any of these available "
            "with that? internship starts monday"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── Cross-Product Confusion ──────────────────────────────────────────────
    {
        "id": 31,
        "group": "cross_product",
        "label": "Final Cut Pro — Mac mini alternative?",
        "message": (
            "can any of these run Final Cut Pro? i edit YouTube videos. "
            "or should i just get a mac mini instead"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 32,
        "group": "cross_product",
        "label": "Microsoft Office required by school — factor into budget",
        "message": (
            "does this come with Microsoft Office? my school requires it. "
            "or do i have to buy it separately. factor that into budget"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 33,
        "group": "cross_product",
        "label": "3 external monitors — needs docking station?",
        "message": (
            "i need to connect 3 external monitors. does any laptop here "
            "actually support that or do i need a docking station"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── Vague Lifestyle ──────────────────────────────────────────────────────
    {
        "id": 34,
        "group": "lifestyle",
        "label": "Business traveler, airports, bad wifi, Tokyo flights",
        "message": (
            "i travel a lot for work. always in airports, hotels, bad wifi. "
            "just needs to be reliable and not die on a flight to tokyo"
        ),
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 35,
        "group": "lifestyle",
        "label": "Nurse: rugged, drop/spill-proof, 12h shifts, <$800",
        "message": (
            "im a nurse working 12-hour shifts. need something rugged, "
            "maybe drop-proof? spill-proof? hospital wifi. under $800"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 36,
        "group": "lifestyle",
        "label": "10-year-old kid, Minecraft, survive backpack, $500",
        "message": (
            "my kid is going to destroy this. 10 years old, minecraft and "
            "youtube, needs to survive being thrown in a backpack. $500 max"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── Partially Wrong Info ─────────────────────────────────────────────────
    {
        "id": 37,
        "group": "wrong_info",
        "label": "8GB VRAM confusion (system RAM vs VRAM), <$1500",
        "message": (
            "i need at least 8GB VRAM for stable diffusion. heard you need "
            "that for AI image gen. 16GB RAM total, under $1,500"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget", "min_ram_gb"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": True,
    },
    {
        "id": 38,
        "group": "wrong_info",
        "label": "Brand bias: 'i7 is better than Ryzen', video editing, <$1000",
        "message": (
            "i7 is better than ryzen right? get me the fastest i7 laptop "
            "under $1,000 for video editing"
        ),
        "expect_domain": "laptops",
        "expect_filters": ["budget"],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    {
        "id": 39,
        "group": "wrong_info",
        "label": "Obvious typo: '1TB RAM' — should treat as GB",
        "message": "need 1TB RAM for my ML models",
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── One-Liners ───────────────────────────────────────────────────────────
    {
        "id": 40,
        "group": "one_liner",
        "label": "Single word: 'laptop'",
        "message": "laptop",
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
        "expect_question": True,
    },
    {
        "id": 41,
        "group": "one_liner",
        "label": "3 words: 'good laptop cheap'",
        "message": "good laptop cheap",
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
        "expect_question": True,
    },
    {
        "id": 42,
        "group": "one_liner",
        "label": "'best laptop 2024'",
        "message": "best laptop 2024",
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
        "expect_question": True,
    },
    {
        "id": 43,
        "group": "one_liner",
        "label": "ThinkPad X1 Carbon alternative, cheaper",
        "message": "something like a thinkpad x1 carbon but cheaper",
        "expect_domain": "laptops",
        "expect_filters": [],
        "must_not_contain_brands": [],
        "expect_recs_on_first": False,
    },
    # ── Brand Exclusions - Exam Scenarios (6 NEW) ─────────────────────────────
    {
        "id": 44,
        "group": "brand_exclusion_exam",
        "label": "no mac - alias resolution test",
        "message": "I want a laptop, no mac",
        "expect_domain": "laptops",
        "expect_filters": ["excluded_brands"],
        "must_not_contain_brands": ["Apple", "apple"],
        "expect_recs_on_first": True,
        "check_brand_exclusion": True,
    },
    {
        "id": 45,
        "group": "brand_exclusion_exam",
        "label": "we hate ASUS - direct negation test",
        "message": "we hate ASUS, find me a gaming laptop",
        "expect_domain": "laptops",
        "expect_filters": ["excluded_brands", "good_for_gaming"],
        "must_not_contain_brands": ["ASUS", "asus"],
        "expect_recs_on_first": True,
        "check_brand_exclusion": True,
    },
    {
        "id": 46,
        "group": "brand_exclusion_exam",
        "label": "steer clear of HP - indirect phrasing test",
        "message": "steer clear of HP, bad experience",
        "expect_domain": "laptops",
        "expect_filters": ["excluded_brands"],
        "must_not_contain_brands": ["HP", "hp"],
        "expect_recs_on_first": True,
        "check_brand_exclusion": True,
    },
    {
        "id": 47,
        "group": "brand_exclusion_exam",
        "label": "I don't want a 14 inch screen - screen exclusion test",
        "message": "I don't want a 14 inch screen",
        "expect_domain": "laptops",
        "expect_filters": ["excluded_screen_size"],
        "must_not_contain_screen_sizes": ["14"],
        "check_screen_exclusion": True,
        "expect_recs_on_first": False,
    },
    {
        "id": 48,
        "group": "brand_exclusion_exam",
        "label": "brand override - no Apple then show me Apple (multi-turn)",
        "multi_turn": ["I want a laptop, no mac", "actually show me Apple"],
        "expect_domain": "laptops",
        "expect_filters": ["brand"],
        "must_contain_brands": ["Apple", "apple"],
        "expect_recs_on_first": True,
    },
]


# ============================================================================
# Test Runner
# ============================================================================

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m⚠\033[0m"
INFO = "\033[36m·\033[0m"


def send_chat(client: httpx.Client, base_url: str, message: str, session_id: str) -> Dict[str, Any]:
    """POST /chat and return parsed JSON."""
    resp = client.post(
        f"{base_url}/chat",
        json={"message": message, "session_id": session_id},
        timeout=90,  # gpt-5-nano is slower than gpt-4o-mini; 3 sequential LLM calls can take ~60s
    )
    resp.raise_for_status()
    return resp.json()


def check_query(
    query: Dict[str, Any],
    response: Dict[str, Any],
    verbose: bool = False,
) -> Tuple[List[str], List[str], List[str]]:
    """
    Check a single query response against expectations.
    Returns (passes, failures, warnings).
    """
    passes: List[str] = []
    failures: List[str] = []
    warnings: List[str] = []

    rtype = response.get("response_type", "")
    domain = response.get("domain")
    filters = response.get("filters", {})
    message = response.get("message", "")
    recs = response.get("recommendations") or []
    quick_replies = response.get("quick_replies") or []

    # 1. Domain check
    if query.get("expect_domain"):
        if domain == query["expect_domain"]:
            passes.append(f"Domain: {domain}")
        elif domain is None and rtype == "question":
            warnings.append(f"Domain not yet set (still in interview) — may be OK")
        else:
            failures.append(f"Domain: expected '{query['expect_domain']}', got '{domain}'")

    # 2. Expected filter keys
    # response.filters uses search-compatible keys (price_max_cents, min_screen_size, etc.)
    # while expect_filters uses agent slot names (budget, screen_size, etc.).
    _FILTER_ALIASES = {
        "budget":      ["price_max_cents", "price_min_cents", "price"],
        "screen_size": ["min_screen_size", "max_screen_size", "screen_size", "excluded_screen_size"],
        "use_case":    ["good_for_gaming", "good_for_ml", "good_for_creative", "good_for_web_dev", "_soft_preferences"],
    }
    for key in query.get("expect_filters", []):
        search_keys = _FILTER_ALIASES.get(key, [key])
        actual_val = next((filters[k] for k in search_keys if k in filters), None)
        if actual_val is not None:
            passes.append(f"Filter '{key}' extracted: {actual_val!r}")
        else:
            failures.append(f"Filter '{key}' NOT extracted — missing from response.filters")

    # 3. Response type — recs on first message?
    if query.get("expect_recs_on_first"):
        if rtype == "recommendations":
            passes.append(f"Got recommendations on first message")
        elif rtype == "question":
            warnings.append(
                f"Got question instead of recommendations — "
                f"possible: not enough criteria extracted. Q: {message[:80]!r}"
            )
        else:
            failures.append(f"Expected recommendations, got: {rtype!r}")

    # 4. One-liner check: should ask a question, not hallucinate recs
    if query.get("expect_question"):
        if rtype == "question":
            passes.append(f"Correctly asked a clarifying question")
        else:
            failures.append(f"Expected question (interview start), got {rtype!r}")

    # 5. Brand exclusion: check recs don't contain excluded brands
    must_not = query.get("must_not_contain_brands", [])
    if must_not and recs:
        for row in recs:
            for product in row:
                p_brand = (product.get("brand") or "").lower()
                p_name = (product.get("name") or "").lower()
                for bad_brand in must_not:
                    if bad_brand.lower() in p_brand or bad_brand.lower() in p_name:
                        failures.append(
                            f"Brand exclusion FAILED: '{bad_brand}' found in result "
                            f"(brand='{product.get('brand')}', name='{product.get('name', '')[:40]}')"
                        )

    # 6. Screen size exclusion: check recs don't contain excluded screen sizes
    must_not_screen = query.get("must_not_contain_screen_sizes", [])
    if must_not_screen and recs:
        for row in recs:
            for product in row:
                attrs = product.get("attributes", {})
                screen = attrs.get("screen_size")
                if screen:
                    screen_str = str(screen)
                    for bad_size in must_not_screen:
                        if bad_size.lower() in screen_str.lower():
                            failures.append(
                                f"Screen exclusion FAILED: '{bad_size}' found in result "
                                f"(screen_size='{screen}', name='{product.get('name', '')[:40]}')"
                            )

    # 7. Brand exclusion extracted into filters
    if query.get("check_brand_exclusion"):
        excl = filters.get("excluded_brands", [])
        if excl:
            passes.append(f"excluded_brands in filters: {excl}")
        else:
            failures.append(f"excluded_brands NOT in filters — brand exclusion not extracted")

    # 8. Screen exclusion extracted into filters
    if query.get("check_screen_exclusion"):
        excl = filters.get("excluded_screen_size", [])
        if excl:
            passes.append(f"excluded_screen_size in filters: {excl}")
        else:
            failures.append(f"excluded_screen_size NOT in filters — screen exclusion not extracted")

    # 9. Brand required
    must_contain = query.get("must_contain_brands", [])
    if must_contain and recs:
        found_any = False
        for row in recs:
            for product in row:
                p_brand = (product.get("brand") or "").lower()
                p_name = (product.get("name") or "").lower()
                for needed_brand in must_contain:
                    if needed_brand.lower() in p_brand or needed_brand.lower() in p_name:
                        found_any = True
                        break
            if found_any:
                break
        if found_any:
            passes.append(f"Brand required: found {must_contain} in results")
        else:
            failures.append(f"Brand required FAILED: {must_contain} NOT in results")

    # 10. OS filter extracted
    if query.get("check_os_filter"):
        os_val = filters.get("os")
        if os_val:
            passes.append(f"os filter extracted: {os_val!r}")
        else:
            warnings.append(f"os filter NOT in response.filters — may only be in agent-internal state")

    # 11. Not empty response
    if not message:
        failures.append("Empty message in response")
    else:
        passes.append(f"Non-empty response ({len(message)} chars)")

    # 12. Quick replies present for question type
    if rtype == "question" and not quick_replies:
        warnings.append("No quick_replies provided with question response")

    return passes, failures, warnings


def run_queries(
    base_url: str,
    selected_ids: Optional[List[int]] = None,
    selected_group: Optional[str] = None,
    verbose: bool = False,
) -> None:
    queries = QUERIES
    if selected_ids:
        queries = [q for q in queries if q["id"] in selected_ids]
    if selected_group:
        queries = [q for q in queries if selected_group.lower() in q["group"].lower()]

    total = len(queries)
    total_passes = 0
    total_failures = 0
    total_warnings = 0
    failed_ids = []

    print(f"\n{'='*70}")
    print(f"  IDSS Conference Demo Query Test — {total} queries against {base_url}")
    print(f"{'='*70}\n")

    with httpx.Client(timeout=90) as client:
        for i, query in enumerate(queries, 1):
            session_id = str(uuid.uuid4())
            qid = query["id"]
            label = query["label"]

            print(f"[{qid:2d}/{total:2d}] {label}")
            print(f"       Group: {query['group']}")
            messages = query.get("multi_turn", [query.get("message", "")])
            msg_display = " → ".join(m[:50] + ("..." if len(m) > 50 else "") for m in messages)
            print(f"       Msg:   {msg_display}")

            t0 = time.perf_counter()
            response = None
            try:
                for msg in messages:
                    response = send_chat(client, base_url, msg, session_id)
            except httpx.ConnectError:
                print(f"       {FAIL} Cannot connect to {base_url} — is the server running?")
                print(f"            Start it with: uvicorn app.main:app --port 8001 --reload")
                sys.exit(1)
            except Exception as e:
                print(f"       {FAIL} Request error: {e}")
                total_failures += 1
                failed_ids.append(qid)
                print()
                continue
            elapsed = (time.perf_counter() - t0) * 1000

            rtype = response.get("response_type", "?")
            domain = response.get("domain", "?")
            filters = response.get("filters", {})

            passes, failures, warnings = check_query(query, response, verbose)
            total_passes += len(passes)
            total_failures += len(failures)
            total_warnings += len(warnings)

            # Status line
            status = PASS if not failures else FAIL
            print(f"       {status} [{elapsed:.0f}ms] type={rtype!r} domain={domain!r} filters={list(filters.keys())}")
            print(f"       {INFO} Msg: {response.get('message', '')[:100]!r}")

            for p in passes:
                print(f"       {PASS} {p}")
            for w in warnings:
                print(f"       {WARN} {w}")
            for f in failures:
                print(f"       {FAIL} {f}")
                failed_ids.append(qid)

            if verbose:
                print(f"       Full response:")
                print(json.dumps(response, indent=8, default=str)[:2000])

            print()

    # Summary
    print(f"{'='*70}")
    print(f"  RESULTS: {total_passes} checks passed | {total_failures} failures | {total_warnings} warnings")
    if failed_ids:
        unique_failed = sorted(set(failed_ids))
        print(f"  Failed query IDs: {unique_failed}")
        print(f"\n  Re-run only failures with:")
        print(f"    python scripts/test_demo_queries.py --query {' --query '.join(str(x) for x in unique_failed)}")
    else:
        print(f"  All queries passed! 🎉")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Test 43 conference demo queries against the IDSS agent.")
    parser.add_argument("--url", default="http://localhost:8001", help="Base URL of the agent server")
    parser.add_argument("--query", type=int, action="append", dest="queries", metavar="N", help="Run only query N (can repeat)")
    parser.add_argument("--group", type=str, default=None, help="Run only queries in this group (e.g. brand_exclusion, one_liner)")
    parser.add_argument("--verbose", action="store_true", help="Print full response JSON")
    args = parser.parse_args()

    run_queries(
        base_url=args.url,
        selected_ids=args.queries,
        selected_group=args.group,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
