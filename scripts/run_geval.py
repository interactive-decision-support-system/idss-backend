#!/usr/bin/env python3
"""
G-Eval v2 — IDSS Merchant Agent Evaluator
==========================================
Hybrid evaluation: deterministic rule-based checks (response type, brand
exclusions, filter presence) combined with an LLM quality judge that evaluates
BEHAVIOUR, not product-spec matching.

Key improvements over v1:
  - Deterministic checks for response type, brand exclusions, filter extraction
  - LLM judge scoped to response QUALITY only (not product catalog coverage)
  - Few-shot calibration examples anchoring 0.0 / 0.5 / 1.0
  - Chain-of-thought reasoning before score to reduce hallucinated numbers
  - Async parallel execution: all 160 agent queries run concurrently (semaphore=6)
  - All 160 scoring calls run concurrently after
  - Comparison mode: --baseline FILE shows delta vs previous run
  - Score distribution histogram in output
  - Difficulty split: "Specified" (expect recs on first msg) vs "Underspecified"

Usage:
    python scripts/run_geval.py
    python scripts/run_geval.py --url http://localhost:8000
    python scripts/run_geval.py --save results_v2.json
    python scripts/run_geval.py --baseline scripts/geval_results_20260314_161644.json
    python scripts/run_geval.py --verbose

Evaluating Sajjad's idss-mcp endpoint:
    python scripts/run_geval.py --sajjad-url                            # uses localhost:9003
    python scripts/run_geval.py --sajjad-url http://myhost:9003         # custom host
    python scripts/run_geval.py --sajjad-url --save geval_sajjad.json   # save results

  --sajjad-url is a convenience shortcut that overrides --url and implies --no-kg.
  Sajjad's /chat endpoint must accept the same JSON body:
    { "message": str, "session_id": str }
  and return the same response schema as IDSS:
    { "message": str, "response_type": str, "recommendations": [...],
      "filters": {...}, "kg_results": [...] }
  If the response format differs, run_geval.py will silently score missing fields as 0.

Four-system comparison (after running all evals):
    python scripts/compare_evals.py \\
        --idss    scripts/geval_results_ours.json \\
        --sajjad  scripts/geval_results_sajjad.json \\
        --gpt     scripts/geval_results_gpt.json \\
        --perplexity scripts/geval_results_pplx.json
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


# ============================================================================
# Evaluation Queries — 180 queries with full metadata
# ============================================================================
# Metadata fields used for deterministic scoring:
#   expect_recs_on_first: bool  — True if query has enough info for direct recs
#   expect_question: bool       — True if agent must ask a clarifying question
#   must_not_contain_brands: list — brand names that must not appear in recs
#   expect_filters: list        — filter keys that must be extracted
# ============================================================================

QUERIES: List[Dict[str, Any]] = [
    # ── Expert / Technical ──────────────────────────────────────────────────
    {
        "id": 1, "group": "expert",
        "label": "AI researcher (RTX 4060, 32GB RAM, 1TB, <$2k)",
        "message": (
            "I need a laptop for PyTorch deep learning and fine-tuning LLMs locally. "
            "Must have NVIDIA RTX 4060 or better, 32GB RAM, 1TB NVMe SSD, under $2,000, "
            "and weigh under 5 lbs for carrying to lab."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Should give laptop recs targeting ML use case; explain tradeoffs if GPU not available.",
    },
    {
        "id": 2, "group": "expert",
        "label": "UI/UX designer (16in, 16GB, $1500)",
        "message": (
            "I'm a UI/UX designer using Figma and Webflow with occasional video editing "
            "in Premiere Pro. I want a 16-inch display with accurate colors, 16GB RAM, "
            "512GB storage, good battery, and a budget of $1,500."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Should give design-focused recs; budget $1500, 16GB RAM are key constraints.",
    },
    {
        "id": 3, "group": "expert",
        "label": "Developer (Linux, ThinkPad, 32GB, 8h battery, <$1500)",
        "message": (
            "I need a developer laptop for React, Docker with 10 containers, and "
            "PostgreSQL locally. Must run Linux, have a ThinkPad-style keyboard, "
            "32GB RAM, 8+ hours battery, and cost under $1,500."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Should give developer recs; mention Linux compatibility; $1500 budget.",
    },
    {
        "id": 4, "group": "expert",
        "label": "Student gamer (16GB, <$700, light, 8h battery)",
        "message": (
            "I'm a broke college student who wants to play Valorant and do Python "
            "assignments. Must last 8 hours unplugged, weigh under 4 lbs, have 16GB "
            "RAM, and cost under $700."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Budget gaming laptop $700, 16GB RAM. Agent should give recs in this range.",
    },
    {
        "id": 5, "group": "expert",
        "label": "High-end gamer (RTX 4070, 32GB, 1440p 165Hz, $2k-$2.5k)",
        "message": (
            "I want to play Cyberpunk 2077 on high settings and stream on Twitch. "
            "Need RTX 4070 or better, 32GB RAM, a 1440p 165Hz display, and a "
            "budget between $2,000 and $2,500."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "High-end gaming recs $2k-$2.5k; best available GPU laptops.",
    },
    # ── Typos / Casual ──────────────────────────────────────────────────────
    {
        "id": 6, "group": "typos",
        "label": "Typo: 'latop', 'arond', 'nothin'",
        "message": "need latop for school. budget arond 500 bucks. nothin too heavy",
        "expect_recs_on_first": False,
        "expect_question": False,   # either recs or question acceptable
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Should understand typos; $500 budget for school; either ask about use case or show budget laptops.",
    },
    {
        "id": 7, "group": "typos",
        "label": "Typo: 'lookign', 'somthing', MacBook alternative",
        "message": "lookign for somthing like macbook but cheaper, dont need apple tax",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Should understand MacBook alternative request; ask about budget or show Windows alternatives.",
    },
    {
        "id": 8, "group": "typos",
        "label": "ALL CAPS frustrated user, battery complaint",
        "message": (
            "LAPTOP WITH GOOD BATTRY AND NOT SLOW. CAPS BECAUSE IM FRUSTRATED MY "
            "CURRENT ONE DIES IN 2 HOURS"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Should handle all-caps; empathize with frustration; ask about budget or show battery-focused laptops.",
    },
    {
        "id": 9, "group": "typos",
        "label": "ThinkPad, Zoom calls, 8hrs, coffee shop",
        "message": "thinkpad? or something. need it for zoom calls 8hrs a day from coffee shop",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "ThinkPad interest with battery/webcam focus; ask for budget is appropriate.",
    },
    # ── Contradictory / Impossible ──────────────────────────────────────────
    {
        "id": 10, "group": "contradictory",
        "label": "Impossible: RTX 4090, 64GB, 20h battery, 2lbs, <$800",
        "message": (
            "I want RTX 4090, 64GB RAM, 4K OLED display, 20-hour battery, "
            "weighs 2 lbs, under $800. Is that possible?"
        ),
        "expect_recs_on_first": True,  # has enough info; agent should show closest alternatives
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Should acknowledge impossibility of specs combo; show best available alternatives within budget.",
    },
    {
        "id": 11, "group": "contradictory",
        "label": "Contradiction: gaming RTX 4080 + fanless + looks like MacBook",
        "message": (
            "Need a gaming laptop with RTX 4080, but it HAS to be fanless and silent. "
            "Also no bulky design, must look like a MacBook."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "RTX 4080 needs cooling so fanless is impossible; should explain this and offer slim high-perf alternatives.",
    },
    {
        "id": 12, "group": "contradictory",
        "label": "Indecisive: 32GB vs 16GB, Blender, <$1200",
        "message": (
            "Want 32GB RAM but I've heard more RAM drains battery so actually "
            "maybe 16GB but then can I still run Blender? Idk just find me "
            "something good under $1,200"
        ),
        "expect_recs_on_first": True,  # has budget + use case
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Should address RAM/battery misconception; recommend Blender-capable laptops under $1200.",
    },
    # ── Zero Technical Knowledge ─────────────────────────────────────────────
    {
        "id": 13, "group": "no_tech",
        "label": "Non-technical: Netflix, homework, video calls, 'doesn't freeze'",
        "message": (
            "idk what specs are. my old dell broke. i just watch netflix, do "
            "homework, and video call grandma. something that doesnt freeze"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Basic use case; ask about budget in plain language, or show affordable reliable options.",
    },
    {
        "id": 14, "group": "no_tech",
        "label": "Non-technical: 'power user', 40 Chrome tabs, Excel",
        "message": (
            "what's a good one for a 'power user' (my IT guy called me that). "
            "i mostly have like 40 chrome tabs open and excel files"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Translate power user → 16GB+ RAM; ask budget in simple terms or show mid-range options.",
    },
    {
        "id": 15, "group": "no_tech",
        "label": "Gift for data scientist boyfriend, $1800",
        "message": (
            "my boyfriend does 'data science' stuff and his birthday is next week, "
            "budget $1,800, what should i get him"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Data science use + $1800 budget is enough; give high-RAM/GPU recommendations in accessible language.",
    },
    {
        "id": 16, "group": "no_tech",
        "label": "Non-technical: 'the cloud', no storage needed?",
        "message": (
            "need something for 'the cloud'. my company is moving everything to cloud. "
            "does that mean i dont need storage?"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Should explain local storage is still needed; ask about budget; recommend business laptops.",
    },
    # ── Overly Specific / Forum ──────────────────────────────────────────────
    {
        "id": 17, "group": "forum",
        "label": "r/SuggestALaptop: Framework 16 alternative, $1400",
        "message": (
            "r/SuggestALaptop told me to get a Framework 16 with AMD 7945HX but "
            "my budget is $1400 not $1600. are there alternatives with same iGPU TDP?"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Budget $1400, AMD GPU alternative to Framework 16; show best AMD-based options available.",
    },
    {
        "id": 18, "group": "forum",
        "label": "Ultra-spec: PCIe 5.0, DDR5-5600, MUX switch, Wi-Fi 7",
        "message": (
            "looking for laptop with PCIe 5.0 NVMe, DDR5-5600 dual channel, "
            "MUX switch, per-key RGB, Thunderbolt 4, MIL-SPEC 810H, "
            "and Wi-Fi 7. must not throttle under sustained cinebench load"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Ultra-specific request; ask for budget OR show best high-end options; engage technically.",
    },
    {
        "id": 19, "group": "forum",
        "label": "CPU comparison: i7-13700H vs Ryzen 7 7745HX, AutoCAD",
        "message": (
            "i7-13700H or Ryzen 7 7745HX? for single-threaded performance only, "
            "not multicore. doing lots of AutoCAD 2D drafting"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "CPU question + AutoCAD context; asking for budget is appropriate; engage with the comparison.",
    },
    # ── Budget Anchoring ─────────────────────────────────────────────────────
    {
        "id": 20, "group": "budget",
        "label": "Budget shock: Chromebook was $200, replacing it",
        "message": (
            "Chromebook was $200, now they want $1,200 for a 'real laptop'?? "
            "just need something to replace it. why is everything so expensive"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Empathize with budget shock; show affordable Windows alternatives ~$300-500.",
    },
    {
        "id": 21, "group": "budget",
        "label": "Facebook marketplace RTX 4060 $400 — cheapest new one?",
        "message": (
            "found an RTX 4060 laptop on facebook marketplace for $400, "
            "seems too good. is that legit or should i buy new from you? "
            "whats the cheapest new one with 4060?"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Should warn about marketplace risk; show cheapest available RTX 4060 or equivalent gaming laptops.",
    },
    {
        "id": 22, "group": "budget",
        "label": "Spend all of $3000 work budget — best possible",
        "message": (
            "my work gives me $3,000 for a laptop. i want to spend all of it. "
            "what's the absolute best possible laptop money can buy right now"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Show top-tier laptops at/near $3000 budget; workstation or high-end gaming.",
    },
    # ── Domain Switching ─────────────────────────────────────────────────────
    {
        "id": 23, "group": "domain_switch",
        "label": "Pivot from laptop to iPad/Surface/Chromebook",
        "message": (
            "actually forget laptops i want an ipad with a keyboard instead. "
            "does that count? or maybe a surface pro? or chromebook? "
            "im confused just help me pick"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "User confused about form factor; should help clarify or show Chromebook/2-in-1 options from catalog.",
    },
    {
        "id": 24, "group": "domain_switch",
        "label": "Laptop + 4K 144Hz monitor, $2000 total",
        "message": (
            "need a laptop AND a monitor. the monitor should be 4K 144Hz. "
            "total budget $2,000 for both"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Focus on laptop; acknowledge monitor need and budget split; ask which to prioritize.",
    },
    {
        "id": 25, "group": "domain_switch",
        "label": "5 identical developer laptops, <$1200 each",
        "message": (
            "im buying this for my startup. we need 5 laptops, all the same, "
            "developer-grade, under $1,200 each"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Developer laptops under $1200; acknowledge bulk purchase context.",
    },
    # ── Brand Exclusions ─────────────────────────────────────────────────────
    {
        "id": 26, "group": "brand_exclusion",
        "label": "No HP, no Acer — $900, 16GB RAM",
        "message": (
            "anything but HP. had 3 HP laptops and all died. also no Acer, "
            "heard bad things. budget $900, 16GB RAM"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["HP", "Acer"],
        "expect_filters": ["excluded_brands", "budget", "min_ram_gb"],
        "quality_note": "Clear brand exclusions + budget + RAM spec → should give recs with NO HP or Acer.",
    },
    {
        "id": 27, "group": "brand_exclusion",
        "label": "No Windows 11, must have Windows 10 or Linux, 16GB, SSD, <$1000",
        "message": (
            "i refuse to use Windows 11. must come with Windows 10 or Linux. "
            "16GB RAM, SSD, under $1,000"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "OS requirement (Win10/Linux) + 16GB + $1000; show compatible laptops.",
    },
    {
        "id": 28, "group": "brand_exclusion",
        "label": "No gaming aesthetic, no RGB, professional look, runs DaVinci",
        "message": (
            "no gaming aesthetic. no RGB. no big logos. just looks "
            "professional for client meetings but can also run DaVinci Resolve"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Professional aesthetic + DaVinci Resolve GPU needs; asking for budget is appropriate.",
    },
    # ── Time / Urgency ───────────────────────────────────────────────────────
    {
        "id": 29, "group": "urgency",
        "label": "Need today/tomorrow, Prime 2-day, in-stock, $700",
        "message": (
            "need it TODAY or tomorrow at latest. amazon prime 2-day. "
            "in-stock only. $700 budget. student"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Urgency + $700 budget; show in-stock student laptops; acknowledge urgency.",
    },
    {
        "id": 30, "group": "urgency",
        "label": "APO shipping, internship starts Monday",
        "message": (
            "shipping to military APO address. are any of these available "
            "with that? internship starts monday"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "APO shipping question + urgency; ask about requirements; recommend internship laptops.",
    },
    # ── Cross-Product Confusion ──────────────────────────────────────────────
    {
        "id": 31, "group": "cross_product",
        "label": "Final Cut Pro — Mac mini alternative?",
        "message": (
            "can any of these run Final Cut Pro? i edit YouTube videos. "
            "or should i just get a mac mini instead"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Should explain Final Cut Pro is Mac-only; recommend Mac options OR Windows alternatives for video editing.",
    },
    {
        "id": 32, "group": "cross_product",
        "label": "Microsoft Office required by school — factor into budget",
        "message": (
            "does this come with Microsoft Office? my school requires it. "
            "or do i have to buy it separately. factor that into budget"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Address Office question (usually separate purchase); ask budget; mention Office 365 student discount.",
    },
    {
        "id": 33, "group": "cross_product",
        "label": "3 external monitors — needs docking station?",
        "message": (
            "i need to connect 3 external monitors. does any laptop here "
            "actually support that or do i need a docking station"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Multi-monitor question; address Thunderbolt/USB-C support; ask budget; mention docking stations.",
    },
    # ── Vague Lifestyle ──────────────────────────────────────────────────────
    {
        "id": 34, "group": "lifestyle",
        "label": "Business traveler, airports, bad wifi, Tokyo flights",
        "message": (
            "i travel a lot for work. always in airports, hotels, bad wifi. "
            "just needs to be reliable and not die on a flight to tokyo"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Travel use case; battery life + light weight key; asking for budget is appropriate.",
    },
    {
        "id": 35, "group": "lifestyle",
        "label": "Nurse: rugged, drop/spill-proof, 12h shifts, <$800",
        "message": (
            "im a nurse working 12-hour shifts. need something rugged, "
            "maybe drop-proof? spill-proof? hospital wifi. under $800"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Budget $800 + rugged use case; show durable laptops (ThinkPad, MIL-SPEC) under $800.",
    },
    {
        "id": 36, "group": "lifestyle",
        "label": "10-year-old kid, Minecraft, survive backpack, $500",
        "message": (
            "my kid is going to destroy this. 10 years old, minecraft and "
            "youtube, needs to survive being thrown in a backpack. $500 max"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Budget $500 + durability + Minecraft; show affordable durable laptops under $500.",
    },
    # ── Partially Wrong Info ─────────────────────────────────────────────────
    {
        "id": 37, "group": "wrong_info",
        "label": "8GB VRAM confusion (system RAM vs VRAM), <$1500",
        "message": (
            "i need at least 8GB VRAM for stable diffusion. heard you need "
            "that for AI image gen. 16GB RAM total, under $1,500"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget", "min_ram_gb"],
        "quality_note": "VRAM for SD + 16GB RAM + $1500; show laptops with dedicated GPU (8GB VRAM if available).",
    },
    {
        "id": 38, "group": "wrong_info",
        "label": "Brand bias: 'i7 is better than Ryzen', video editing, <$1000",
        "message": (
            "i7 is better than ryzen right? get me the fastest i7 laptop "
            "under $1,000 for video editing"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Should gently note modern Ryzen is competitive; show best video editing laptops under $1000.",
    },
    {
        "id": 39, "group": "wrong_info",
        "label": "Obvious typo: '1TB RAM' — should treat as GB",
        "message": "need 1TB RAM for my ML models",
        "expect_recs_on_first": False,
        "expect_question": True,    # should clarify the typo
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "1TB RAM is impossible; should ask if they mean 1TB storage or 64/128GB RAM.",
    },
    # ── One-Liners ───────────────────────────────────────────────────────────
    {
        "id": 40, "group": "one_liner",
        "label": "Single word: 'laptop'",
        "message": "laptop",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Should ask a clarifying question; must NOT give unsolicited recommendations.",
    },
    {
        "id": 41, "group": "one_liner",
        "label": "3 words: 'good laptop cheap'",
        "message": "good laptop cheap",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Should ask about use case and budget; must NOT give unsolicited recommendations.",
    },
    {
        "id": 42, "group": "one_liner",
        "label": "'best laptop 2024'",
        "message": "best laptop 2024",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Should ask about intended use and budget; avoid 'best laptop' generic list.",
    },
    {
        "id": 43, "group": "one_liner",
        "label": "ThinkPad X1 Carbon alternative, cheaper",
        "message": "something like a thinkpad x1 carbon but cheaper",
        "expect_recs_on_first": False,
        "expect_question": False,   # can ask for budget or show alternatives
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "ThinkPad X1 as reference (ultrabook, premium); ask for budget or show affordable ultrabooks.",
    },
    # ── Comparison / Best-Value ──────────────────────────────────────────────
    {
        "id": 54, "group": "comparison",
        "label": "Side-by-side gaming laptops under $1500",
        "message": (
            "Can you show me a few gaming laptops under $1,500 so I can "
            "compare them? I want to understand the tradeoffs."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Comparison request + gaming + $1500; show 3+ options and explicitly discuss GPU/display tradeoffs.",
    },
    {
        "id": 55, "group": "comparison",
        "label": "Best bang-for-buck in catalog, no budget given",
        "message": "What's the best bang for the buck in your catalog right now?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Best-value without budget; ask for budget or show highly-rated value options with price-to-performance context.",
    },
    {
        "id": 56, "group": "comparison",
        "label": "Thin ultrabook vs powerful laptop — show both, $1000–$1500",
        "message": (
            "I'm torn between a thin ultrabook for travel and a more powerful "
            "laptop for heavy work. Can you show me good options in each "
            "category, around $1,000 to $1,500?"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Dual comparison: thin/light vs powerful, $1000-$1500; show 2-3 in each category and explain tradeoffs.",
    },
    # ── Refinement / Follow-up ───────────────────────────────────────────────
    {
        "id": 57, "group": "refine",
        "label": "Too expensive — video editing under $800",
        "message": "These are all too expensive. I need something under $800 that still does video editing.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Budget refinement: $800 cap + video editing. Show best value video-capable laptops under $800.",
    },
    {
        "id": 58, "group": "refine",
        "label": "Actually need 32GB RAM — show those only",
        "message": "Actually I need more RAM than that. Show me only laptops with at least 32GB.",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb"],
        "quality_note": "RAM refinement: 32GB+ only. Show all 32GB catalog options, or ask use case if no context.",
    },
    {
        "id": 59, "group": "refine",
        "label": "Changed mind — now wants HP, 16GB, under $1000",
        "message": "Actually forget what I said, I changed my mind. Show me HP laptops with 16GB RAM under $1,000.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget", "min_ram_gb"],
        "quality_note": "Brand pivot to HP + 16GB + $1000. Should give HP-specific recommendations, honoring the explicit change of mind.",
    },
    # ── Price Range (explicit min + max) ─────────────────────────────────────
    {
        "id": 60, "group": "price_range",
        "label": "Gaming, 16GB RAM, $1000–$1500 range",
        "message": "Gaming laptop, 16GB RAM. My budget is between $1,000 and $1,500.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Explicit price range $1000-$1500 + gaming + 16GB. Show best available gaming options within that band.",
    },
    {
        "id": 61, "group": "price_range",
        "label": "Mid-range coding — not cheap trash, not over $1200",
        "message": (
            "Not looking for the cheapest thing out there, but also can't go over $1,200. "
            "Something solid for software development."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Mid-range dev laptop ~$800-$1200; ask about RAM/OS or show popular developer laptops.",
    },
    {
        "id": 62, "group": "price_range",
        "label": "Flexible $1500–$2500 for ML/data science",
        "message": (
            "My budget is flexible — at least $1,500 but could stretch to $2,500 "
            "if it's genuinely worth it. I work in data science and run ML models."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget", "min_ram_gb"],
        "quality_note": "Flexible range $1500-$2500 for ML/DS. Show high-RAM GPU options; explain what extra spend buys.",
    },
    # ── Brand Preference ─────────────────────────────────────────────────────
    {
        "id": 63, "group": "brand_pref",
        "label": "ASUS only, best gaming under $1500",
        "message": "I only want ASUS. What's the best ASUS gaming laptop under $1,500?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["HP", "Dell", "Lenovo", "Acer"],
        "expect_filters": ["budget"],
        "quality_note": "ASUS-only + gaming + $1500. Recommendations must be ASUS only (ROG/TUF); explain model tier differences.",
    },
    {
        "id": 64, "group": "brand_pref",
        "label": "Only Lenovo or Dell, no HP/Acer, work, 16GB, $1200",
        "message": (
            "I've only ever trusted Lenovo or Dell. 16GB RAM, for office work, "
            "under $1,200. No HP, no Acer please."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["HP", "Acer"],
        "expect_filters": ["budget", "min_ram_gb"],
        "quality_note": "Brand preference (Lenovo/Dell) + hard exclusion (HP/Acer) + 16GB + $1200. Show only Lenovo/Dell results.",
    },
    {
        "id": 65, "group": "brand_pref",
        "label": "MacBook Pro equivalent in Windows, no budget given",
        "message": (
            "I want something that feels like a MacBook Pro — premium build, "
            "great display, thin — but I need Windows. What's the closest thing?"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Premium Windows ultrabook as MacBook alternative; ask budget or show Dell XPS/ASUS Zenbook class options.",
    },
    # ── Expert (additional) ──────────────────────────────────────────────────
    {
        "id": 66, "group": "expert",
        "label": "Music producer: Thunderbolt, 32GB, Ableton, <$2000",
        "message": (
            "I produce music in Ableton Live. Need ultra-low audio latency, "
            "32GB RAM, a Thunderbolt port for my audio interface, and good "
            "thermal performance so it doesn't throttle during recording. Budget $2,000."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Music production: Thunderbolt connectivity, 32GB RAM, $2000. Highlight Thunderbolt and sustained thermal stability.",
    },
    {
        "id": 67, "group": "expert",
        "label": "Architecture student: Revit+AutoCAD, 15in+, GPU, 32GB, <$2000",
        "message": (
            "I'm studying architecture — running Revit and AutoCAD for complex 3D models. "
            "Need at least 15-inch display, dedicated GPU, 32GB RAM, fast SSD, under $2,000."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "CAD/BIM workload: large screen, dedicated GPU, 32GB RAM, $2000. Show workstation-class laptops.",
    },
    {
        "id": 68, "group": "expert",
        "label": "Cybersecurity researcher: Kali Linux, 16GB, SSD, <$1200",
        "message": (
            "I do penetration testing and security research. Need a laptop "
            "that can dual-boot Kali Linux reliably, 16GB RAM, fast SSD, "
            "under $1,200."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Security researcher: Linux-compatible, 16GB, $1200. Mention Kali/Linux compatibility; ThinkPad or Dell class.",
    },
    # ── Urgency (additional) ─────────────────────────────────────────────────
    {
        "id": 69, "group": "urgency",
        "label": "Christmas in 3 days, $600 for teenager",
        "message": (
            "Christmas is literally in 3 days and I forgot to get a laptop "
            "for my teenager. $600 budget. Can it ship in time?"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Holiday urgency + $600 teen laptop. Acknowledge shipping constraints; show in-stock affordable options.",
    },
    {
        "id": 70, "group": "urgency",
        "label": "Laptop died, thesis presentation in 2 days, $800",
        "message": (
            "My laptop just completely died. I have a thesis presentation "
            "in 2 days and need a replacement immediately. $800 budget."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Emergency replacement: 2-day deadline + $800. Show fast-shipping reliable options; acknowledge the urgency.",
    },
    # ── Lifestyle (additional) ────────────────────────────────────────────────
    {
        "id": 71, "group": "lifestyle",
        "label": "Photographer: color-accurate display, 16GB, Lightroom, <$1800",
        "message": (
            "I'm a professional photographer. Color accuracy is non-negotiable — "
            "at least 95% DCI-P3. Also need 16GB RAM and fast SSD for editing "
            "RAW files in Lightroom. Budget $1,800."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Photography: display color accuracy (DCI-P3) is primary, 16GB, $1800. Prioritize display quality in recommendations.",
    },
    {
        "id": 72, "group": "lifestyle",
        "label": "Senior citizen, first laptop, video calls + photos, $300-400",
        "message": (
            "My grandson says I need a laptop. I'm 72 and have never owned one. "
            "I just want to video chat with family and look at photos. "
            "Nothing complicated. Budget around $300-$400."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Senior first-time user: $300-400, simplicity is critical. Use plain language; recommend easy-to-use reliable options.",
    },
    # ── Contradictory (additional) ────────────────────────────────────────────
    {
        "id": 73, "group": "contradictory",
        "label": "Ultra-thin <12mm + RTX 4090 + 20h battery",
        "message": (
            "I need the absolute thinnest laptop possible — under 12mm thick — "
            "but it also needs an RTX 4090 GPU and 20+ hours of battery. Does that exist?"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Physically impossible: RTX 4090 needs thick chassis + large cooling. Explain clearly and show best thin-and-light alternatives.",
    },
    {
        "id": 74, "group": "contradictory",
        "label": "4K OLED + 16h battery + under $600 — all required",
        "message": "Need a laptop with 4K OLED display, 16 hours battery life, under $600. All three are hard requirements.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Impossible combo at $600: 4K OLED + 16h battery. Explain market reality; show best available alternatives.",
    },
    # ── Zero Technical Knowledge (additional) ────────────────────────────────
    {
        "id": 75, "group": "no_tech",
        "label": "Son said get 16GB but doesn't know what it means, $600",
        "message": (
            "my son told me i absolutely have to buy something with '16GB'. "
            "i don't know what that means but he says it will be slow otherwise. "
            "$600 budget"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Non-technical + trusted family advice: confirm 16GB is sound, $600 budget. Show options in plain language.",
    },
    {
        "id": 76, "group": "no_tech",
        "label": "Laptop got slow over 5 years, wants durable replacement, <$700",
        "message": (
            "my laptop is 5 years old and started getting really slow. "
            "i don't know if it's the ram thing or what. just want a new one "
            "that will stay fast for years. under $700"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Durability concern + $700. Mention SSD and RAM as longevity factors in plain terms; show reliable mid-range options.",
    },
    # ── One-Liners (additional) ───────────────────────────────────────────────
    {
        "id": 77, "group": "one_liner",
        "label": "Single word: 'gaming'",
        "message": "gaming",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Single word 'gaming'; must ask clarifying question about budget and requirements before recommending.",
    },
    {
        "id": 78, "group": "one_liner",
        "label": "Single word: 'macbook'",
        "message": "macbook",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "MacBook request; show Mac options if in catalog, or Mac alternatives, or ask about budget.",
    },
    # ── Multi-Constraint (all specs given simultaneously) ─────────────────────
    {
        "id": 79, "group": "multi_constraint",
        "label": "14in, 16GB, <4 lbs, backlit KB, $700",
        "message": (
            "Looking for a 14-inch laptop, 16GB RAM, weighs under 4 pounds, "
            "must have a backlit keyboard, budget $700."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Multiple hard constraints: 14in + 16GB + weight + backlit KB + $700. Show best matches; note if any constraint can't be fully met.",
    },
    {
        "id": 80, "group": "multi_constraint",
        "label": "17in, 32GB, RTX 4060+, 144Hz, no HP, $1600",
        "message": (
            "I need a 17-inch gaming laptop: 32GB RAM, RTX 4060 or better, "
            "144Hz display, no HP brand, budget $1,600."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["HP"],
        "expect_filters": ["min_ram_gb", "budget", "excluded_brands"],
        "quality_note": "5 simultaneous constraints: 17in + 32GB + RTX 4060 + 144Hz + no HP at $1600. Must exclude HP; note any spec compromises needed.",
    },
    {
        "id": 91, "group": "multi_constraint",
        "label": "Touch 2-in-1, 16GB, 12h battery, <$900",
        "message": (
            "Need a 2-in-1 convertible with touchscreen, 16GB RAM, at least "
            "12 hours battery, and a price under $900."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Convertible form factor + 16GB + 12h battery + $900. Highlight whether all three constraints are achievable simultaneously.",
    },
    {
        "id": 92, "group": "multi_constraint",
        "label": "Thunderbolt 4, 32GB, OLED, <5 lbs, $1800",
        "message": (
            "Must have Thunderbolt 4 port, 32GB RAM, OLED display, weight under "
            "5 lbs, budget $1,800."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Premium multi-constraint: Thunderbolt 4 + 32GB + OLED + lightweight + $1800. All four are hard; honestly note catalog limitations.",
    },
    {
        "id": 93, "group": "multi_constraint",
        "label": "Compact student: 13in, 16GB, <3 lbs, 10h+, <$600",
        "message": (
            "College student needs: 13-inch screen, 16GB RAM, under 3 lbs, "
            "10+ hours battery, all under $600."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Tight $600 cap with 16GB + lightweight + long battery. Explain which constraints may require compromise at this price.",
    },
    {
        "id": 94, "group": "multi_constraint",
        "label": "No Lenovo/Acer, 32GB, dedicated GPU, 15in+, $1400",
        "message": (
            "I want a 15-inch or larger laptop with 32GB RAM, a discrete GPU, "
            "no Lenovo and no Acer, budget $1,400."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["Lenovo", "Acer"],
        "expect_filters": ["min_ram_gb", "budget", "excluded_brands"],
        "quality_note": "Hard exclusions + multi-spec: 32GB + dGPU + 15in+ + no Lenovo/Acer at $1400. Must not show Lenovo or Acer.",
    },
    {
        "id": 95, "group": "multi_constraint",
        "label": "MIL-SPEC, 16GB, SSD, fingerprint reader, $1100",
        "message": (
            "Needs to be MIL-SPEC 810H certified, 16GB RAM, SSD, fingerprint reader, "
            "budget $1,100."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Rugged enterprise checklist: MIL-SPEC + 16GB + biometrics at $1100. Show durable business laptops; acknowledge if MIL-SPEC coverage is limited in catalog.",
    },
    # ── Informal (internet slang / text-speak) ────────────────────────────────
    {
        "id": 96, "group": "informal",
        "label": "Gen-Z slang: 'goes hard fr fr', $500",
        "message": "yo i need a laptop that goes hard fr fr, budget like $500 no cap",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Gen-Z slang with $500 budget. Match casual tone; ask about use case or show solid affordable options without condescension.",
    },
    {
        "id": 97, "group": "informal",
        "label": "Abbreviations: 'ngl need smth 4 uni', $400",
        "message": "ngl i need smth 4 uni, broke af lmao, budget like $400?? pls help",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Heavy abbreviations + $400 student budget. Decode correctly (uni=university); ask about workload or show affordable reliable laptops.",
    },
    {
        "id": 98, "group": "informal",
        "label": "Emoji + slang: old HP died, $700",
        "message": "lol my old hp died rip \U0001f62d need a new 1, budget around $700 any recs??",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Emoji + internet speak + $700 replacement laptop. Empathize; ask use case or show reliable mid-range options.",
    },
    {
        "id": 99, "group": "informal",
        "label": "idk specs, just needs 2 b fast, $800ish",
        "message": "tbh idk anything abt specs lol, i just need it 2 b fast and not laggy, $800ish",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Spec-ignorant with ~$800 budget. Translate 'fast and not laggy' into RAM/SSD needs; ask or show options without jargon.",
    },
    {
        "id": 100, "group": "informal",
        "label": "Bestie said get a Mac, but idk, $1000",
        "message": "ok so my bestie said i HAVE to get a mac but idk?? budget is $1000, worth it??",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Peer-influenced Mac curiosity + $1000. Acknowledge both Mac and Windows options; ask about use case to give tailored advice.",
    },
    # ── Student-Specific ──────────────────────────────────────────────────────
    {
        "id": 101, "group": "student_specific",
        "label": "Pre-med: anatomy apps, 8h battery, $800",
        "message": (
            "I'm a pre-med student. I use anatomy visualization apps, take notes "
            "all day in class, and need at least 8 hours battery. Budget $800."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Pre-med: note-taking + 3D anatomy apps + battery longevity + $800. Prioritize battery and display; mention touchscreen as bonus.",
    },
    {
        "id": 102, "group": "student_specific",
        "label": "CS freshman: coding + casual gaming, 16GB, $900",
        "message": (
            "Starting CS degree next fall. Need something for coding (Java, Python, C++) "
            "and some gaming on weekends. 16GB RAM, budget $900."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "CS student dual-use: coding + casual gaming + 16GB + $900. Show gaming laptops with dev-friendly keyboards and Linux compatibility.",
    },
    {
        "id": 103, "group": "student_specific",
        "label": "Art school: stylus/pen input, color-accurate display, $1200",
        "message": (
            "I'm at art school and need a laptop with pen/stylus support for digital "
            "illustration. Color accuracy matters. Budget $1,200."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Art student: pen input + color-accurate display + $1200. Recommend 2-in-1 with stylus support; highlight display gamut specs.",
    },
    {
        "id": 104, "group": "student_specific",
        "label": "Community college: Office + Zoom, first laptop, $350",
        "message": (
            "I'm starting community college. Just need Microsoft Office and Zoom. "
            "This is my first laptop ever. Budget is $350."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Entry-level first laptop for basic school tasks + $350. Keep simple; mention Office 365 student deal; avoid over-speccing.",
    },
    {
        "id": 105, "group": "student_specific",
        "label": "MBA student: PowerPoint + Excel, travel between campuses, $1100",
        "message": (
            "MBA student here. Lots of PowerPoint presentations and Excel modeling, "
            "travel between campuses, need good build quality. Budget $1,100."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Business school: Office-heavy + portability + professional look + $1100. Prioritize build quality, battery, and keyboard.",
    },
    # ── Purchase-Ready (user has decided, wants final answer) ─────────────────
    {
        "id": 106, "group": "purchase_ready",
        "label": "Brand locked: best ASUS ROG under $1500",
        "message": (
            "I've already decided I want an ASUS ROG laptop. I just need to know "
            "which specific ROG model is the best one under $1,500."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["HP", "Dell", "Lenovo", "Acer"],
        "expect_filters": ["budget"],
        "quality_note": "Purchase-ready with brand locked in: ASUS ROG under $1500. Give a direct top pick; do not ask unnecessary questions. Must not show non-ASUS brands.",
    },
    {
        "id": 107, "group": "purchase_ready",
        "label": "Narrowed to 2: MacBook Air M2 vs Dell XPS 13 for traveling dev",
        "message": (
            "I've narrowed it down to two laptops: MacBook Air M2 or Dell XPS 13. "
            "Which one should I buy for a software developer who travels a lot?"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Direct comparison between two specific models for dev + travel. Give a clear recommendation; explain the key macOS vs Windows tradeoff.",
    },
    {
        "id": 108, "group": "purchase_ready",
        "label": "Final confirmation before checkout, $1200 gaming laptop",
        "message": (
            "I'm literally about to click buy on a $1,200 gaming laptop. "
            "Just confirm this is a fair price for a gaming laptop and I'll go for it."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Purchase validation at $1200 for gaming. Give direct confirmation (or flag if it seems overpriced); do not deflect with more questions.",
    },
    {
        "id": 109, "group": "purchase_ready",
        "label": "Single best pick only: 16GB, SSD, $700, buying today",
        "message": (
            "Don't give me a list. Just tell me the single best laptop under $700 "
            "with 16GB RAM and an SSD. I'll buy it today."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "User explicitly wants ONE recommendation. Give a single top pick with brief justification; do not list 5 options — that fails this query.",
    },
    # ── Follow-Up Q&A (service / product questions) ───────────────────────────
    {
        "id": 110, "group": "follow_up_qa",
        "label": "What is the return policy?",
        "message": "What is the return policy if I don't like the laptop after buying it?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Service question: return window and process. Answer clearly about the return policy; do not pivot to product recommendations unprompted.",
    },
    {
        "id": 111, "group": "follow_up_qa",
        "label": "How long does shipping take to California?",
        "message": "How long does shipping usually take? I'm in California.",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Shipping timeline question. Give a direct answer about estimated delivery; no unprompted product recs needed.",
    },
    {
        "id": 112, "group": "follow_up_qa",
        "label": "Does warranty cover accidental damage (coffee spill)?",
        "message": "If I accidentally spill coffee on it, does the warranty cover that?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Accidental damage warranty question. Distinguish standard vs accidental damage coverage; mention extended protection options without being evasive.",
    },
    {
        "id": 113, "group": "follow_up_qa",
        "label": "Can I upgrade RAM later from 16GB to 32GB?",
        "message": "If I buy a laptop now with 16GB, can I upgrade the RAM to 32GB myself later?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "RAM upgradability question. Explain soldered vs socketed reality; advise buying enough RAM upfront; mention which laptop lines tend to have upgradeable slots.",
    },
    # ── Software-Specific ─────────────────────────────────────────────────────
    {
        "id": 114, "group": "software_specific",
        "label": "SolidWorks FEA simulation, certified GPU, 32GB, $2000",
        "message": (
            "I run SolidWorks simulations and FEA analysis. Need an ISV-certified GPU, "
            "32GB RAM, and budget $2,000."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "SolidWorks requires ISV-certified GPU (Quadro/NVIDIA RTX Pro). Explain certified vs gaming GPU; show workstation-class laptops under $2000.",
    },
    {
        "id": 115, "group": "software_specific",
        "label": "MATLAB + Simulink, 32GB, no GPU needed, $1500",
        "message": (
            "I use MATLAB and Simulink for control systems modeling. Need 32GB RAM, "
            "fast CPU, SSD. No dedicated GPU needed. Budget $1,500."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "MATLAB is CPU/RAM bound. Emphasize fast CPU and 32GB RAM; note GPU not needed; show high-RAM thin-and-light or workstation laptops under $1500.",
    },
    {
        "id": 116, "group": "software_specific",
        "label": "Tableau Desktop, millions-row datasets, 16GB, $1200",
        "message": (
            "I use Tableau Desktop with datasets that have millions of rows. "
            "Needs to not choke on large extracts. 16GB RAM, budget $1,200."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Tableau is memory and SSD throughput bound. Recommend high RAM + fast NVMe under $1200; call out storage speed as important secondary spec.",
    },
    {
        "id": 117, "group": "software_specific",
        "label": "QuickBooks + dual monitors, accountant, $800",
        "message": (
            "I'm an accountant using QuickBooks and I need to run dual monitors. "
            "Light workload otherwise. Budget $800."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "QuickBooks is lightweight; dual monitor output via USB-C/HDMI is the real constraint. Recommend business laptops with good port selection under $800.",
    },
    {
        "id": 118, "group": "software_specific",
        "label": "Xcode iOS development, macOS required, 16GB, $1800",
        "message": (
            "I develop iOS apps using Xcode. Need macOS, at least 16GB RAM, "
            "fast SSD for compiling Swift projects. Budget $1,800."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Xcode requires macOS — must recommend Mac laptops only. Show MacBook options under $1800; note M-series chip advantage for compile times.",
    },
    # ── Gaming-Specific ───────────────────────────────────────────────────────
    {
        "id": 119, "group": "gaming_specific",
        "label": "1080p-only gamer, RTX 4060 is fine, $1200",
        "message": (
            "I only game at 1080p — I don't need 1440p or 4K. "
            "RTX 4060 is fine for that. Best value RTX 4060 gaming laptop under $1,200."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Informed 1080p gamer: validate RTX 4060 is solid at 1080p + $1200. Show best value RTX 4060 laptops; affirm the reasoning without unnecessary upselling.",
    },
    {
        "id": 120, "group": "gaming_specific",
        "label": "Only Minecraft + school work, 8GB OK, $500",
        "message": (
            "My use case is Minecraft (Java edition) and school work. "
            "I don't need a powerful GPU. 8GB RAM, budget $500."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Minecraft Java + school at $500: integrated graphics sufficient. Show budget laptops with decent CPU; explain why dedicated GPU isn't needed here.",
    },
    {
        "id": 121, "group": "gaming_specific",
        "label": "Hogwarts Legacy high settings, RTX 4070, $1800",
        "message": (
            "I want to play Hogwarts Legacy on high/ultra settings. "
            "I heard you need at least an RTX 4070. Budget $1,800."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Specific game title with cited GPU requirement. Validate GPU choice for Hogwarts Legacy; show best RTX 4070 options; note catalog limitations if applicable.",
    },
    {
        "id": 122, "group": "gaming_specific",
        "label": "Competitive FPS: 240Hz minimum, thin bezels, $1400",
        "message": (
            "I play competitive FPS games and specifically need a 240Hz display. "
            "Thin bezels preferred. Budget $1,400."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "240Hz is a hard requirement. Show laptops with 240Hz panels at $1400; note that fewer models hit 240Hz vs 144Hz/165Hz.",
    },
    # ── Enterprise ────────────────────────────────────────────────────────────
    {
        "id": 123, "group": "enterprise",
        "label": "Bulk: 20 identical sales laptops, $1000 each",
        "message": (
            "We need to purchase 20 identical laptops for our sales team. "
            "Business use: email, Salesforce, Teams. Budget $1,000 each. "
            "Same model for easy IT management."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Enterprise bulk order: uniform model + IT manageability + $1000 each. Recommend business-class laptops; acknowledge the volume procurement context.",
    },
    {
        "id": 124, "group": "enterprise",
        "label": "C-suite executive: premium, thin, best display, $2500",
        "message": (
            "Looking for a laptop for our CEO. Must be premium-looking, "
            "thin and light, excellent battery, top-tier display. Budget $2,500."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Executive procurement: premium aesthetics + portability + display quality at $2500. Show flagship ultrabooks — MacBook Pro or premium Windows equivalents.",
    },
    {
        "id": 125, "group": "enterprise",
        "label": "IT security: TPM 2.0, Win 11 Pro, vPro, $1200",
        "message": (
            "Our IT department requires TPM 2.0, Windows 11 Pro, and Intel vPro "
            "for remote management. Budget $1,200 per unit."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Enterprise IT requirements: TPM 2.0 + Win 11 Pro + vPro at $1200. Show business-class laptops with vPro; explain why consumer laptops won't qualify.",
    },
    {
        "id": 126, "group": "enterprise",
        "label": "Nonprofit: reliable Word/Zoom laptops, $400 each",
        "message": (
            "We're a small nonprofit with a very tight budget. Need reliable laptops "
            "for staff doing document work and Zoom calls. Budget $400 each."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Nonprofit budget-constrained: reliability + basic tasks + $400. Show best reliable options; mention refurbished as a cost-saving path.",
    },
    # ── Sustainability ────────────────────────────────────────────────────────
    {
        "id": 127, "group": "sustainability",
        "label": "EPEAT Gold certified, company policy, $1200",
        "message": (
            "Our company has a sustainability policy. I need a laptop that is "
            "EPEAT Gold certified or has strong environmental credentials. Budget $1,200."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Sustainability-driven purchase: EPEAT Gold + $1200. Engage meaningfully with eco-criteria; mention which brands have strong sustainability programs.",
    },
    {
        "id": 128, "group": "sustainability",
        "label": "Refurbished only to reduce e-waste, 16GB, $500",
        "message": (
            "I only want to buy refurbished or certified pre-owned to reduce waste. "
            "16GB RAM, under $500. Do you carry refurbished options?"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Refurbished-only preference: address whether catalog has refurb stock; recommend certified pre-owned programs or alternatives.",
    },
    {
        "id": 129, "group": "sustainability",
        "label": "Recycled materials, low carbon footprint, student, $700",
        "message": (
            "I'm a student who cares about the environment. Looking for a laptop "
            "made with recycled materials or with a documented low carbon footprint. "
            "Budget $700."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Environmental values + $700 student budget. Engage with sustainability ask; mention brands with recycled content programs; show eco-conscious affordable options.",
    },
    # ── Security / Privacy ────────────────────────────────────────────────────
    {
        "id": 130, "group": "security_privacy",
        "label": "TPM 2.0 + BitLocker required, corporate policy, $1100",
        "message": (
            "I need TPM 2.0 hardware encryption for BitLocker. "
            "It's a corporate security requirement. Budget $1,100."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Security compliance: TPM 2.0 + BitLocker + $1100. Most modern Windows laptops qualify; show business-class options and confirm TPM availability.",
    },
    {
        "id": 131, "group": "security_privacy",
        "label": "Physical webcam cover / kill switch required — journalist",
        "message": (
            "I'm a journalist and I need a laptop with a physical webcam cover "
            "or hardware kill switch for privacy. What do you have?"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Privacy-focused journalist: physical webcam privacy. Mention ThinkPad integrated cover; ask budget to narrow down recommendations.",
    },
    {
        "id": 132, "group": "security_privacy",
        "label": "Fully offline, hardware encryption, classified docs, $1000",
        "message": (
            "I work with sensitive documents and need a laptop that runs fully offline "
            "with hardware-level encryption support. Budget $1,000."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Sensitive use case: hardware encryption + air-gap capability + $1000. Recommend business-class with TPM; discuss OS telemetry settings.",
    },
    # ── Repair / Upgrade ──────────────────────────────────────────────────────
    {
        "id": 133, "group": "repair_upgrade",
        "label": "User-serviceable: replaceable SSD + battery, $1000",
        "message": (
            "I want a laptop where I can replace the SSD and battery myself "
            "without voiding the warranty. Repairability is very important. Budget $1,000."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Repairability + $1000. Mention Framework laptop concept; show ThinkPad or similar options with accessible service manuals and replaceable components.",
    },
    {
        "id": 134, "group": "repair_upgrade",
        "label": "Is RAM soldered? Want to upgrade 16GB → 32GB later",
        "message": (
            "Before I buy, I need to know: is the RAM soldered or can I upgrade it "
            "later? I want to start with 16GB and add more in a year."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "RAM upgradability pre-purchase question. Explain modern trend of soldered RAM; recommend starting with 32GB upfront; show upgradeable-RAM options.",
    },
    {
        "id": 135, "group": "repair_upgrade",
        "label": "Battery longevity 4+ years, charge-limit feature, $900",
        "message": (
            "Battery longevity matters more to me than fast charging. "
            "I want a laptop that will keep good battery health for 4+ years. "
            "Budget $900."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Battery longevity focus + $900. Explain charge-limit features (80% cap in Lenovo, ASUS); recommend brands with proven long-term battery management.",
    },
    # ── Accessibility ─────────────────────────────────────────────────────────
    {
        "id": 136, "group": "accessibility",
        "label": "Low vision: 17in+, high contrast display, $800",
        "message": (
            "I have low vision and need a large screen — at least 17 inches — "
            "with good contrast for high-contrast display mode. Budget $800."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Accessibility for low vision: 17in screen + contrast + $800. Recommend large-screen laptops; mention OS high-contrast mode and zoom accessibility features.",
    },
    {
        "id": 137, "group": "accessibility",
        "label": "Arthritis: large keys, good key travel, $700",
        "message": (
            "I have arthritis in my hands. I need a laptop with large, well-spaced "
            "keys that don't require heavy pressure. Good key travel. Budget $700."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Accessibility for arthritis: keyboard ergonomics + $700. Recommend ThinkPad-class keyboards known for key travel; mention external keyboard option.",
    },
    {
        "id": 138, "group": "accessibility",
        "label": "Colorblind: works with Windows color filter, good IPS screen, $600",
        "message": (
            "I'm red-green colorblind. I need a laptop with a good screen "
            "that works well with Windows color filter accessibility. Budget $600."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Colorblind accessibility + $600. Recommend IPS panels with good viewing angles; mention Windows color filter settings; avoid over-complicating.",
    },
    # ── Expert (additional expansions) ────────────────────────────────────────
    {
        "id": 139, "group": "expert",
        "label": "VFX student: Houdini+Nuke, RTX 4080, 32GB, OLED, $3000",
        "message": (
            "I'm a VFX student using Houdini and Nuke for compositing and fluid sims. "
            "Need RTX 4080, 32GB RAM, OLED or high color-gamut display. Budget $3,000."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "VFX: Houdini/Nuke need high VRAM + 32GB system RAM + color-accurate display. Show top-end laptops; discuss GPU VRAM importance for VFX renders.",
    },
    {
        "id": 140, "group": "expert",
        "label": "Bioinformatics researcher: BWA/GATK pipelines, 64GB RAM, Linux, $2500",
        "message": (
            "I run bioinformatics pipelines (BWA, GATK, samtools) that need "
            "lots of RAM and multi-core CPU. Need 64GB RAM, Linux-native support, budget $2,500."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "Bioinformatics: memory-intensive pipelines, 64GB RAM, multi-core CPU, Linux at $2500. Show high-RAM workstation laptops; acknowledge 64GB may be rare at this price.",
    },
    {
        "id": 141, "group": "expert",
        "label": "Indie game dev: UE5 large textures, RTX 4070+, 32GB, $2000",
        "message": (
            "I'm an indie game developer using Unreal Engine 5 with large texture assets. "
            "Need RTX 4070 minimum, 32GB RAM, fast NVMe SSD. Budget $2,000."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "UE5 game dev: GPU VRAM + 32GB system RAM + SSD throughput all critical. Show high-end gaming laptops that double as workstations; highlight VRAM capacity.",
    },
    {
        "id": 142, "group": "expert",
        "label": "SRE/DevOps: 3-4 k8s clusters, 32GB, Linux, $2500",
        "message": (
            "I'm an SRE/DevOps engineer running 3-4 Kubernetes clusters locally, "
            "Docker-in-Docker, Terraform, Ansible. Need 32GB RAM, Linux-first, "
            "fast NVMe SSD, budget $2,500."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "k8s/DevOps: memory is the bottleneck (32GB+), Linux first-class support, fast SSD. Recommend developer-focused laptops; mention ThinkPad P-series or Dell Precision.",
    },
    # ── Typos (additional expansions) ─────────────────────────────────────────
    {
        "id": 143, "group": "typos",
        "label": "Spanish/English mix: 'buen laptop escuela $600'",
        "message": "hola necesito un buen laptop para la escuela, budget like $600, gracias",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Spanish/English mix + $600 school budget. Respond helpfully in English; ask about specific school needs or show affordable student options.",
    },
    {
        "id": 144, "group": "typos",
        "label": "Heavy teen abbreviations: hw + vids, $500 skl laptop",
        "message": "need laptop 4 hw nd watching vids lol, smth good 4 skl, $500 pls",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Heavy text-speak: hw=homework, skl=school, $500. Decode correctly; ask use case specifics or show affordable reliable student laptops.",
    },
    {
        "id": 145, "group": "typos",
        "label": "ALL CAPS slow laptop, needs upgrade $400",
        "message": "MY LAPTOP IS SO SLOW IT TAKES 10 MIN TO OPEN CHROME I NEED A NEW ONE UNDER $400",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "ALL CAPS frustration + clear $400 budget. Empathize; show fastest laptops under $400; briefly explain SSD as key to snappiness.",
    },
    # ── Forum (additional expansions) ─────────────────────────────────────────
    {
        "id": 146, "group": "forum",
        "label": "LTT hyped Lenovo Legion — is it good for gaming $1400?",
        "message": (
            "I was watching Linus Tech Tips and he was hyping the Lenovo Legion line. "
            "Is that actually a good recommendation for gaming under $1,400?"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Tech influencer reference + gaming $1400. Engage with Lenovo Legion context; confirm or nuance the recommendation; show Legion and comparable alternatives.",
    },
    {
        "id": 147, "group": "forum",
        "label": "Notebookcheck 91% score — worth it for dev $1200?",
        "message": (
            "Notebookcheck gave a laptop a 91% review score. Does that mean "
            "it's actually the best choice for a developer around $1,200?"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Review site reference + developer + $1200 (no model named). Explain review scores don't always match personal needs; ask which model or show top dev laptops at $1200.",
    },
    {
        "id": 148, "group": "forum",
        "label": "Tom's Hardware A-tier RTX 4070 — which laptops have it under $1800?",
        "message": (
            "Tom's Hardware put the RTX 4070 in the A-tier for gaming. "
            "Which laptops in your store have that GPU for under $1,800?"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Tech publication GPU reference + $1800 filter. Show available RTX 4070 laptops; note catalog limitations if GPU tier isn't available.",
    },
    # ── Budget (additional expansions) ────────────────────────────────────────
    {
        "id": 149, "group": "budget",
        "label": "Student loan money, needs to last 4 years, $1200",
        "message": (
            "I'm using part of my student loan to buy a laptop. "
            "I have $1,200 and it needs to last all 4 years of college."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Student loan budget + 4-year longevity + $1200. Recommend durable, upgradeable laptops; discuss long-term value and repairability.",
    },
    {
        "id": 150, "group": "budget",
        "label": "Company says $800, good ones are $1000 — worth upgrading?",
        "message": (
            "My company said $800 for a laptop but the ones I want are around $1,000. "
            "Should I ask IT for approval, or is there something good at $800?"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Budget tension: company $800 vs desired $1000. Show strong options at both price points; empower user to make the case to IT if warranted.",
    },
    {
        "id": 151, "group": "budget",
        "label": "Parents said $600, really wants $900 gaming — help argue case",
        "message": (
            "My parents said they'll spend $600 on a laptop but I really "
            "want a gaming laptop which costs more like $900. Is $600 worth it "
            "or should I make the case for $900?"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Budget negotiation scenario. Show honest comparison of $600 vs $900 for gaming; help user articulate the value difference without being pushy.",
    },
    # ── Domain Switch (additional expansions) ─────────────────────────────────
    {
        "id": 152, "group": "domain_switch",
        "label": "Do you sell desktop PCs too?",
        "message": "Actually, do you sell desktop PCs too? Or just laptops?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Out-of-scope product category. Clarify catalog focus on laptops; offer to help find a suitable laptop for user's use case.",
    },
    {
        "id": 153, "group": "domain_switch",
        "label": "Can any laptops fold into tablet mode with stylus?",
        "message": (
            "Can any of these laptops work like a tablet if I fold the keyboard back? "
            "I want something versatile with stylus support."
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Form factor pivot to 2-in-1/tablet mode + stylus. Explain convertible options; ask budget; show relevant 2-in-1 laptops from catalog.",
    },
    {
        "id": 154, "group": "domain_switch",
        "label": "Desktop replacement: 17in, stays on desk, max power, $1800",
        "message": (
            "I want a desktop replacement — it stays on my desk, I'll never carry it. "
            "17-inch display, maximum power. I don't care about battery or weight. Budget $1,800."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Desktop replacement: 17in + raw performance + $1800, portability irrelevant. Show powerful 17in laptops; note they run hot and heavy — that's fine here.",
    },
    # ── Cross-Product (additional expansions) ─────────────────────────────────
    {
        "id": 155, "group": "cross_product",
        "label": "Does laptop come with antivirus? Is Defender enough?",
        "message": (
            "Does any of the laptops come bundled with antivirus software? "
            "Or is Windows Defender good enough on its own?"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "AV software question. Explain Windows Defender is built in and adequate for most users; mention that most laptops don't bundle third-party AV.",
    },
    {
        "id": 156, "group": "cross_product",
        "label": "AppleCare vs third-party extended warranty — worth it?",
        "message": (
            "If I buy a MacBook, is AppleCare+ worth it? Or should I get a "
            "third-party extended warranty instead?"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "Warranty comparison: AppleCare+ vs third-party. Give a balanced answer; highlight accidental damage protection as the key differentiator.",
    },
    {
        "id": 157, "group": "cross_product",
        "label": "External GPU (eGPU) over Thunderbolt — which laptops support it?",
        "message": (
            "I heard you can connect an external GPU over Thunderbolt. "
            "Can any of these laptops do that? Does it actually work well?"
        ),
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": "eGPU question: address Thunderbolt requirement and PCIe bandwidth bottleneck; show Thunderbolt-equipped laptops if user wants to explore this path.",
    },
    # ── Wrong Info (additional expansions) ────────────────────────────────────
    {
        "id": 158, "group": "wrong_info",
        "label": "Misconception: 'Macs can't get viruses', $1500",
        "message": (
            "I want to get a Mac because everyone says Macs can't get viruses. "
            "Is that still true? Budget $1,500."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "Common misconception: Macs can and do get malware. Correct gently; still recommend Mac options at $1500 since platform + budget are clear.",
    },
    {
        "id": 159, "group": "wrong_info",
        "label": "Misconception: 'more GHz = more FPS for gaming', $1200",
        "message": (
            "I want the highest GHz CPU I can get for gaming. "
            "More GHz means more FPS right? Budget $1,200."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": "CPU clock misconception. Correct gently: GPU is primary for FPS, not CPU GHz alone. Show best gaming laptops under $1200 regardless of GHz.",
    },
    {
        "id": 160, "group": "wrong_info",
        "label": "Misconception: '8GB RAM same as 8GB VRAM', $900",
        "message": (
            "I need 8GB for gaming. I have 8GB VRAM on my desktop GPU so I know "
            "that's enough. Just need a laptop with 8GB. Budget $900."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": "RAM vs VRAM confusion. Clearly explain they are different; recommend 16GB system RAM for gaming at $900 even though user said 8GB is enough.",
    },

    # ── Rigidity tests: scenarios where keyword/regex-based agents break ────
    # These test that the agent uses reasoning rather than rigid pattern matching.
    # ─────────────────────────────────────────────────────────────────────────

    # ── 161-165: Compare-from-first-message ─────────────────────────────────
    {
        "id": 161, "group": "rigidity_compare",
        "label": "Compare two laptops cold (no prior session)",
        "message": "Can you compare the Dell XPS 15 vs the MacBook Pro 16 for software development?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User asks for a direct comparison as their first message. "
            "Agent must not refuse or ask for more info — it should attempt a comparison or "
            "show both laptops side-by-side with developer-relevant commentary."
        ),
    },
    {
        "id": 162, "group": "rigidity_compare",
        "label": "Compare three laptops cold (brand + model names)",
        "message": (
            "Which is better for college: Lenovo ThinkPad X1 Carbon, "
            "HP Spectre x360, or ASUS ZenBook 14?"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Three-way comparison on first message. Agent must engage with all three "
            "models and give a recommendation or comparison, not just ask for a budget."
        ),
    },
    {
        "id": 163, "group": "rigidity_compare",
        "label": "Compare MacBook vs Windows cold with budget",
        "message": "For $1500, is the MacBook Air M3 or a Windows laptop a better buy for med school?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": (
            "Compare + budget + use case all in one message. "
            "Agent should give a direct answer with tradeoffs, recommend one clearly."
        ),
    },
    {
        "id": 164, "group": "rigidity_compare",
        "label": "Indirect comparison ('better than') phrasing",
        "message": "Is the Dell XPS 13 better than the MacBook Air M2 for everyday use?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "'Better than' phrasing is implicit comparison. "
            "Agent should give a direct comparison/recommendation, not launch the interview."
        ),
    },
    {
        "id": 165, "group": "rigidity_compare",
        "label": "Compare + exclude brand in one sentence",
        "message": "Compare gaming laptops under $1500 — exclude ASUS, just Dell or Lenovo.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["ASUS"],
        "expect_filters": ["budget", "excluded_brands"],
        "quality_note": (
            "Compare intent combined with brand exclusion and budget. "
            "No ASUS laptops must appear. Should show Dell and Lenovo gaming options."
        ),
    },

    # ── 166-170: Natural-language cart / purchase actions ────────────────────
    {
        "id": 166, "group": "rigidity_cart",
        "label": "Add to cart via 'I'll take it' phrasing",
        "message": "I'll take the second one. Can you add it to my cart?",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Post-recommendation 'add to cart' request with ordinal reference. "
            "If no prior recommendations exist in this single-turn eval, agent should ask "
            "'which laptop do you mean?' or show options first — not crash or ignore the request."
        ),
    },
    {
        "id": 167, "group": "rigidity_cart",
        "label": "Buy intent with product name, no 'add to cart' keyword",
        "message": "I want to buy the Lenovo IdeaPad 5 Pro. How do I get it?",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Purchase intent without 'add to cart'. Agent must respond helpfully — "
            "explain how to purchase or add to favorites, not treat this as a search query."
        ),
    },
    {
        "id": 168, "group": "rigidity_cart",
        "label": "'Order' phrasing instead of 'cart'",
        "message": "I'd like to order the cheapest one you showed me.",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "'Order' synonym for cart. With no prior session, agent should ask what product "
            "or show options — not error. Graceful handling of purchase intent."
        ),
    },
    {
        "id": 169, "group": "rigidity_cart",
        "label": "Implicit cart via 'favorite' phrasing",
        "message": "Save the first laptop to my favorites please",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "'Save to favorites' is a cart-like action. "
            "Without prior recommendations, agent should ask which laptop or suggest showing options."
        ),
    },
    {
        "id": 170, "group": "rigidity_cart",
        "label": "Purchase intent mid-spec message",
        "message": "I need a 16GB RAM laptop under $1000 — and once you recommend one I want to buy it right away.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["min_ram_gb", "budget"],
        "quality_note": (
            "Spec + purchase urgency combined. Agent should give 16GB / $1000 recommendations "
            "and acknowledge the purchase intent, not get confused by multi-intent."
        ),
    },

    # ── 171-175: Price-direction and budget edge cases ──────────────────────
    {
        "id": 171, "group": "rigidity_price",
        "label": "Over $X budget (minimum spend, not maximum)",
        "message": "I want to spend over $1500 on a laptop — I want something premium.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "'Over $1500' = minimum budget, not maximum. Agent must NOT recommend sub-$1500 laptops. "
            "Should show premium $1500+ options. Rigidity test: keyword 'budget' pattern must not "
            "treat this as price_max=$1500."
        ),
    },
    {
        "id": 172, "group": "rigidity_price",
        "label": "'At least $1000' spend signal",
        "message": "I'm willing to spend at least $1000 on a good laptop for work.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "'At least $1000' = price minimum. Agent should recommend $1000+ laptops, "
            "not sub-$1000 budget options. Tests budget direction parsing."
        ),
    },
    {
        "id": 173, "group": "rigidity_price",
        "label": "No budget but very high-spec ask",
        "message": "I want the absolute best laptop for machine learning — money is no object.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "'Money is no object' = no budget cap. Agent must not set a low budget default. "
            "Should show high-end ML laptops (RTX, 32GB+) without budget constraints."
        ),
    },
    {
        "id": 174, "group": "rigidity_price",
        "label": "Budget stated as 'around' with range ambiguity",
        "message": "I'm looking at around $800-$900 for a lightweight laptop for travel.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": (
            "Range budget '$800-$900'. Agent should set max around $900 (or $850-$900). "
            "Lightweight + travel context. Tests range budget extraction."
        ),
    },
    {
        "id": 175, "group": "rigidity_price",
        "label": "Price anchored to competitor product",
        "message": "I don't want to spend more than a MacBook Air — so under $1100 please. No Mac though.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["Apple"],
        "expect_filters": ["budget", "excluded_brands"],
        "quality_note": (
            "Budget anchored to MacBook Air price (~$1100) + no Apple. "
            "Agent must exclude Apple, set max ~$1100, show Windows alternatives."
        ),
    },

    # ── 176-180: Multi-intent and edge-case single messages ─────────────────
    {
        "id": 176, "group": "rigidity_multi_intent",
        "label": "Spec + brand preference + exclusion in one sentence",
        "message": (
            "I need 32GB RAM, prefer Dell or Lenovo, definitely no HP or Acer, "
            "budget $1800 for 3D modeling."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["HP", "Acer"],
        "expect_filters": ["min_ram_gb", "budget", "excluded_brands"],
        "quality_note": (
            "Four constraints in one message: RAM, brand preference, dual exclusion, budget. "
            "No HP or Acer in results. 32GB RAM and $1800 budget should both be extracted."
        ),
    },
    {
        "id": 177, "group": "rigidity_multi_intent",
        "label": "Search + follow-up question in one message",
        "message": (
            "Show me good laptops under $1200 for college. "
            "Also, what's the difference between SSD and HDD?"
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": (
            "Two requests in one: recommendation + educational question. "
            "Agent should give recs AND briefly answer the SSD vs HDD question. "
            "Multi-intent handling test."
        ),
    },
    {
        "id": 178, "group": "rigidity_multi_intent",
        "label": "Negation of spec ('no Intel CPU') as filter",
        "message": "I want a laptop with no Intel CPU — AMD Ryzen only. Budget $1000.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": (
            "'No Intel CPU' = AMD-only filter. Agent should show AMD Ryzen laptops under $1000. "
            "Tests CPU brand exclusion via negation (not a brand but a CPU line)."
        ),
    },
    {
        "id": 179, "group": "rigidity_multi_intent",
        "label": "Conditional spec ('if possible') should not block recs",
        "message": (
            "I need a laptop around $900 for college. "
            "If possible I'd like 16GB RAM, but 8GB is fine too."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": (
            "'If possible' makes 16GB RAM a soft preference, not a hard filter. "
            "Agent should recommend ~$900 laptops and prefer 16GB but not block 8GB options. "
            "Should not over-filter and return nothing."
        ),
    },
    {
        "id": 180, "group": "rigidity_multi_intent",
        "label": "Question phrased as a spec ('can it do X?') not a search",
        "message": "Can the laptops in this price range run Blender for 3D modeling? Budget $1500.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": (
            "'Can it run X?' framing with a budget. Agent should give $1500 laptops suitable for "
            "Blender/3D modeling AND address the capability question. "
            "Should not treat this as a clarifying-question-only scenario."
        ),
    },
    # ── Catalog-Impossible / Honest Scoping ──────────────────────────────────
    # These queries have constraints that no product in our catalog can satisfy.
    # A trustworthy system must DISCLOSE the gap — not silently return violating products.
    #
    # Scoring uses check_disclosure() (fires when expect_disclosure=True):
    #   1.0 — system explicitly says it couldn't find matching products (PASS, threshold 0.7)
    #   0.7 — no products returned, no disclosure language (SOFT FAIL)
    #   0.5 — products returned, all within budget (relaxation without disclosure, SOFT FAIL)
    #   0.0 — products returned, any exceeds hard_budget_usd by >5% (HARD FAIL)
    #
    # hard_budget_usd is the stated budget cap used for deterministic budget violation checks.
    {
        "id": 181, "group": "catalog_impossible",
        "label": "Gaming RTX 4090 under $150 — catalog impossible",
        "message": "I need a gaming laptop with an RTX 4090 GPU. My budget is $150.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "expect_disclosure": True,
        "hard_budget_usd": 150,
        "quality_note": (
            "Catalog impossible: no gaming laptop under $150. "
            "System must acknowledge the constraint gap honestly — not recommend products over $150 "
            "without saying so. Ideal: explains no options exist at $150, offers closest alternatives."
        ),
    },
    {
        "id": 182, "group": "catalog_impossible",
        "label": "64GB RAM under $250 — catalog impossible",
        "message": "I need a laptop with at least 64GB of RAM. My budget is $250.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget", "min_ram_gb"],
        "expect_disclosure": True,
        "hard_budget_usd": 250,
        "quality_note": (
            "Catalog impossible: no 64GB laptop under $250. "
            "Must disclose that no match exists at this budget. "
            "Showing a $600+ laptop without saying the budget was exceeded is a Trust & Safety failure."
        ),
    },
    {
        "id": 183, "group": "catalog_impossible",
        "label": "Any laptop under $50 — catalog impossible",
        "message": "What laptop can I get for $50 or less? That's my entire budget.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "expect_disclosure": True,
        "hard_budget_usd": 50,
        "quality_note": (
            "No laptop exists in our catalog for under $50. "
            "System must say clearly that no options exist at this price. "
            "Recommending any laptop without disclosing the budget mismatch is a hard failure."
        ),
    },
    {
        "id": 184, "group": "catalog_impossible",
        "label": "32GB RAM + RTX 4080 under $200 — catalog impossible",
        "message": (
            "I need a powerful laptop for machine learning. "
            "Must have 32GB RAM and NVIDIA RTX 4080 GPU. Budget is $200 maximum."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget", "min_ram_gb"],
        "expect_disclosure": True,
        "hard_budget_usd": 200,
        "quality_note": (
            "ML laptop with 32GB RAM + RTX 4080 under $200 — impossible in our catalog. "
            "Should honestly explain that no products match this combination. "
            "Silently showing a $1500 laptop is a critical Trust & Safety violation."
        ),
    },
    {
        "id": 185, "group": "catalog_impossible",
        "label": "All major brands excluded — catalog near-impossible",
        "message": (
            "Show me a laptop but please exclude HP, Dell, Lenovo, ASUS, Acer, and Apple. "
            "I only want options from other brands."
        ),
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["HP", "Dell", "Lenovo", "ASUS", "Acer", "Apple"],
        "expect_filters": [],
        "expect_disclosure": True,
        "hard_budget_usd": None,
        "quality_note": (
            "Excluding all major laptop brands from the catalog leaves very few or zero results. "
            "System should acknowledge the constraint honestly. "
            "If minor brands remain in catalog, those are acceptable — but disclosing the narrow pool is good practice."
        ),
    },
    {
        "id": 186, "group": "catalog_impossible",
        "label": "Nonexistent brand 'Futura' — catalog impossible",
        "message": "I love Futura laptops — I've heard great things about their build quality. Can you show me Futura laptop options?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "expect_disclosure": True,
        "hard_budget_usd": None,
        "quality_note": (
            "'Futura' is not a real laptop brand — no such products exist in the catalog. "
            "System must not hallucinate Futura products. "
            "Should honestly say 'Futura' doesn't appear in catalog and offer alternatives."
        ),
    },
    {
        "id": 187, "group": "catalog_impossible",
        "label": "128GB RAM under $400 — catalog impossible",
        "message": "I need a laptop with 128GB of RAM for running large AI models locally. My budget is $400.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget", "min_ram_gb"],
        "expect_disclosure": True,
        "hard_budget_usd": 400,
        "quality_note": (
            "128GB RAM under $400 is impossible — no such laptop exists in our catalog or anywhere. "
            "System should acknowledge both the spec impossibility AND the budget constraint. "
            "Recommending a $1500 laptop without disclosure is a hard failure."
        ),
    },
    {
        "id": 188, "group": "catalog_impossible",
        "label": "Gaming laptop under $200 with 16GB RAM — catalog impossible",
        "message": "I want a gaming laptop with 16GB RAM. My strict budget is $200 — I can't go over that.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget", "min_ram_gb"],
        "expect_disclosure": True,
        "hard_budget_usd": 200,
        "quality_note": (
            "No gaming laptop with 16GB RAM exists under $200 in the catalog. "
            "Budget is explicitly stated as a hard constraint ('strict', 'can't go over'). "
            "Honest disclosure required. Any product returned over $200 is a critical Trust & Safety failure."
        ),
    },

    # -------------------------------------------------------------------------
    # GROUP: orchestrator_routing (IDs 189–200)
    # Intent routing accuracy — compare vs refine vs new_search
    # -------------------------------------------------------------------------
    {
        "id": 189, "group": "orchestrator_routing",
        "label": "Compare two specific laptops",
        "message": "Can you compare the Dell XPS 15 and the MacBook Pro 14?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User is explicitly requesting a comparison, not a new search. "
            "System should route to comparison agent and provide a structured side-by-side comparison."
        ),
    },
    {
        "id": 190, "group": "orchestrator_routing",
        "label": "Refine previous results by brand",
        "message": "Only show me Dell laptops from those options",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["Apple", "HP", "Lenovo", "ASUS"],
        "expect_filters": ["brand"],
        "quality_note": (
            "User is refining previous results to Dell only. "
            "System must route as refinement (not new search) and filter to Dell exclusively."
        ),
    },
    {
        "id": 191, "group": "orchestrator_routing",
        "label": "Start over with different domain",
        "message": "Forget the laptops — show me gaming desktops instead",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User explicitly wants to start a new search in a different sub-category. "
            "System should route as new_search and acknowledge the domain pivot."
        ),
    },
    {
        "id": 192, "group": "orchestrator_routing",
        "label": "Add to cart intent",
        "message": "I'll take the second one. Add it to my cart.",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User is giving a cart action intent with ordinal reference. "
            "System should route to add-to-cart handler, not generate new recommendations."
        ),
    },
    {
        "id": 193, "group": "orchestrator_routing",
        "label": "Pros and cons of shown products",
        "message": "What are the pros and cons of these laptops?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User wants structured pros/cons analysis of previously shown products. "
            "System should route to targeted Q&A handler, not a new search."
        ),
    },
    {
        "id": 194, "group": "orchestrator_routing",
        "label": "Ask about battery life of shown products",
        "message": "Which of those has the best battery life?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User is asking a targeted attribute question about previously shown products. "
            "System should route as targeted_qa and answer from product data, not generate new recs."
        ),
    },
    {
        "id": 195, "group": "orchestrator_routing",
        "label": "Ask for similar items to one shown",
        "message": "Show me something similar to the first option but lighter",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User wants similar items with a soft preference (lighter). "
            "System should route as refinement or see-similar, not a new search from scratch."
        ),
    },
    {
        "id": 196, "group": "orchestrator_routing",
        "label": "Checkout intent",
        "message": "Let's check out now",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User is explicitly starting checkout. "
            "System must route to checkout handler, not generate recommendations."
        ),
    },
    {
        "id": 197, "group": "orchestrator_routing",
        "label": "Ambiguous follow-up — could be compare or refine",
        "message": "What about something with more storage?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Ambiguous follow-up that could be refinement (add storage constraint) or new search. "
            "System should treat as refinement — return new products with higher storage added to existing filters."
        ),
    },
    {
        "id": 198, "group": "orchestrator_routing",
        "label": "Research intent — explain a feature",
        "message": "Can you explain what Thunderbolt 4 is and why it matters?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User wants educational content about a feature, not product recommendations. "
            "System should route as research/explain, provide clear explanation."
        ),
    },
    {
        "id": 199, "group": "orchestrator_routing",
        "label": "Rate a product",
        "message": "I'd give that recommendation a 4 out of 5",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User is providing a rating/feedback, not requesting new recommendations. "
            "System should acknowledge gracefully and ask if they want to see more options."
        ),
    },
    {
        "id": 200, "group": "orchestrator_routing",
        "label": "New search after seeing results",
        "message": "Actually let me start fresh — I need a laptop for my mom who mostly uses email",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User explicitly starts a new search with different persona/use-case. "
            "System should route as new_search, clear previous session context, recommend basic/simple laptops."
        ),
    },

    # -------------------------------------------------------------------------
    # GROUP: multi_constraint (IDs 201–210)
    # ≥2 hard constraints simultaneously
    # -------------------------------------------------------------------------
    {
        "id": 201, "group": "multi_constraint",
        "label": "Brand + budget + RAM constraint",
        "message": "I need a Dell laptop under $800 with at least 16GB RAM",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["Apple", "HP", "Lenovo", "ASUS"],
        "expect_filters": ["brand", "budget", "min_ram_gb"],
        "hard_budget_usd": 800,
        "quality_note": (
            "Three simultaneous hard constraints: brand=Dell, budget<=$800, RAM>=16GB. "
            "All three must be enforced. Any non-Dell or over-budget product is a hard failure."
        ),
    },
    {
        "id": 202, "group": "multi_constraint",
        "label": "No gaming + student budget",
        "message": "I need a laptop for studying, nothing gaming-related, under $600",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "hard_budget_usd": 600,
        "quality_note": (
            "User wants a study laptop under $600, explicitly excluding gaming use-case. "
            "System should recommend business/student laptops, not gaming-spec machines."
        ),
    },
    {
        "id": 203, "group": "multi_constraint",
        "label": "Exclude multiple brands + screen size + budget",
        "message": "Looking for a 15-inch laptop under $1000. Please no Lenovo or ASUS.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["Lenovo", "ASUS"],
        "expect_filters": ["budget"],
        "hard_budget_usd": 1000,
        "quality_note": (
            "Two brand exclusions, screen size preference, and budget constraint. "
            "No Lenovo and no ASUS must both be enforced. Budget hard limit $1000."
        ),
    },
    {
        "id": 204, "group": "multi_constraint",
        "label": "OS + RAM + brand constraint",
        "message": "I need a Windows laptop from Dell or HP with at least 32GB RAM",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["Apple"],
        "expect_filters": ["min_ram_gb"],
        "quality_note": (
            "Three constraints: OS=Windows, brand=Dell or HP, RAM>=32GB. "
            "No macOS/Apple products. Results should all have 32GB+ RAM."
        ),
    },
    {
        "id": 205, "group": "multi_constraint",
        "label": "Max weight + battery + budget",
        "message": "I travel constantly. Need something under $1200, light (under 3 lbs), and good battery",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "hard_budget_usd": 1200,
        "quality_note": (
            "Travel use-case with three constraints: budget<=$1200, weight<3lbs, battery life important. "
            "System should recommend ultrabooks optimized for travel."
        ),
    },
    {
        "id": 206, "group": "multi_constraint",
        "label": "GPU + RAM + budget for ML",
        "message": "Machine learning workstation laptop: RTX 4060 or better, 32GB RAM minimum, budget $2000",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget", "min_ram_gb"],
        "hard_budget_usd": 2000,
        "quality_note": (
            "High-spec ML workstation: GPU >= RTX 4060, RAM >= 32GB, budget <= $2000. "
            "All three spec constraints should be reflected in recommendations."
        ),
    },
    {
        "id": 207, "group": "multi_constraint",
        "label": "No HP + no refurbished + under $700",
        "message": "I need a new (not refurbished) laptop under $700, nothing from HP",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["HP"],
        "expect_filters": ["budget"],
        "hard_budget_usd": 700,
        "quality_note": (
            "Two exclusions (HP brand, refurbished condition) plus budget constraint. "
            "HP must be excluded. Only new/retail products. Budget hard limit $700."
        ),
    },
    {
        "id": 208, "group": "multi_constraint",
        "label": "SSD + brand + budget combo",
        "message": "Dell or Lenovo laptop with SSD storage under $900",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["Apple", "HP", "ASUS"],
        "expect_filters": ["budget"],
        "hard_budget_usd": 900,
        "quality_note": (
            "Brand restricted to Dell or Lenovo, storage type SSD required, budget <= $900. "
            "Non-Dell/Lenovo brands should not appear."
        ),
    },
    {
        "id": 209, "group": "multi_constraint",
        "label": "Screen size + use-case + budget",
        "message": "Need a 17-inch laptop for video editing, my budget is $1500",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "hard_budget_usd": 1500,
        "quality_note": (
            "Large screen (17-inch), creative use-case (video editing), and budget constraint. "
            "System should prioritize display quality and GPU for creative work."
        ),
    },
    {
        "id": 210, "group": "multi_constraint",
        "label": "Four simultaneous constraints",
        "message": "ASUS laptop under $800 with 16GB RAM and 512GB SSD or larger",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["Apple", "Dell", "HP", "Lenovo"],
        "expect_filters": ["brand", "budget", "min_ram_gb"],
        "hard_budget_usd": 800,
        "quality_note": (
            "Four constraints: brand=ASUS, budget<=$800, RAM>=16GB, storage>=512GB. "
            "All must be enforced. Non-ASUS results are a hard failure."
        ),
    },

    # -------------------------------------------------------------------------
    # GROUP: preference_discovery (IDs 211–218)
    # Vague queries requiring elicitation
    # -------------------------------------------------------------------------
    {
        "id": 211, "group": "preference_discovery",
        "label": "Generic laptop request with no constraints",
        "message": "I need a laptop",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Completely underspecified query. System should ask a targeted clarifying question "
            "about use-case or budget rather than immediately recommending."
        ),
    },
    {
        "id": 212, "group": "preference_discovery",
        "label": "Purpose-only query without budget",
        "message": "I need something for school",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Use-case is given (school) but budget and specs are missing. "
            "System should ask about budget or key preferences before recommending."
        ),
    },
    {
        "id": 213, "group": "preference_discovery",
        "label": "Just tell me what's good",
        "message": "Just show me what's good",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Maximally vague. System should ask at least one clarifying question "
            "to understand use-case or budget before recommending."
        ),
    },
    {
        "id": 214, "group": "preference_discovery",
        "label": "Implicit use-case from context",
        "message": "My daughter is starting college and needs a computer",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Context suggests student/college use-case but no budget, brand, or specs given. "
            "System should infer student use-case and ask about budget."
        ),
    },
    {
        "id": 215, "group": "preference_discovery",
        "label": "Non-technical user with vague need",
        "message": "I just need something that doesn't freeze and can run Zoom",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Non-technical user with minimal implicit requirements: basic reliability + video calls. "
            "System should recommend budget/mid-range laptops without spec jargon."
        ),
    },
    {
        "id": 216, "group": "preference_discovery",
        "label": "Budget mentioned vaguely",
        "message": "I don't want to spend too much — maybe something affordable",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "'Affordable' is ambiguous. System should ask for a specific budget range "
            "rather than guessing what 'affordable' means for this user."
        ),
    },
    {
        "id": 217, "group": "preference_discovery",
        "label": "Gift purchase with no technical knowledge",
        "message": "I want to buy a laptop as a gift for my husband who works from home",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Gift purchase — buyer lacks technical knowledge about recipient's needs. "
            "System should ask about the recipient's work (IT? Creative? General?) and budget."
        ),
    },
    {
        "id": 218, "group": "preference_discovery",
        "label": "Replacement laptop with implicit needs",
        "message": "My old laptop broke, I need a new one for everyday stuff",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "'Everyday stuff' is vague. System should ask about budget or specific tasks "
            "before recommending. Suggesting general-use laptops is acceptable if also asking."
        ),
    },

    # -------------------------------------------------------------------------
    # GROUP: catalog_exploration (IDs 219–226)
    # Browsing / 'show me options' intent
    # -------------------------------------------------------------------------
    {
        "id": 219, "group": "catalog_exploration",
        "label": "Show me all options under $500",
        "message": "What laptops do you have under $500?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "hard_budget_usd": 500,
        "quality_note": (
            "Browsing request with single hard constraint (budget <= $500). "
            "System should show a variety of options. All returned items must be <= $500."
        ),
    },
    {
        "id": 220, "group": "catalog_exploration",
        "label": "Browse by brand",
        "message": "Show me all the MacBook options you have",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User wants to browse Apple/MacBook catalog. "
            "All results should be MacBook/Apple products."
        ),
    },
    {
        "id": 221, "group": "catalog_exploration",
        "label": "Show options for a specific use-case",
        "message": "What do you have for video editing?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Use-case browsing without budget constraint. "
            "System should show creative/video-editing laptops with relevant spec highlights."
        ),
    },
    {
        "id": 222, "group": "catalog_exploration",
        "label": "Explore gaming laptops in a price band",
        "message": "Show me gaming laptops between $800 and $1200",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "hard_budget_usd": 1200,
        "quality_note": (
            "Price range constraint (not just a ceiling): $800-$1200 gaming laptops. "
            "Results should be gaming-spec and fall within the specified price band."
        ),
    },
    {
        "id": 223, "group": "catalog_exploration",
        "label": "What's the cheapest laptop available",
        "message": "What's the cheapest laptop you have?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User wants the lowest-priced option available. "
            "System should return the entry-level laptop in catalog."
        ),
    },
    {
        "id": 224, "group": "catalog_exploration",
        "label": "Top-rated laptops in catalog",
        "message": "What are your top-rated laptops?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User wants high-rated options. System should prioritize rating as ranking signal "
            "and present results by rating quality."
        ),
    },
    {
        "id": 225, "group": "catalog_exploration",
        "label": "Browse ultrabooks category",
        "message": "Do you have any ultrabooks or thin-and-light laptops?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Category browsing for ultrabooks/thin-light segment. "
            "System should return lightweight, portable laptops."
        ),
    },
    {
        "id": 226, "group": "catalog_exploration",
        "label": "Show laptops with specific GPU",
        "message": "I want to see laptops with RTX 4070 or RTX 4080",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Specific GPU requirement browsing. "
            "System should show laptops with RTX 4070 or 4080; highlight GPU in recommendations."
        ),
    },

    # -------------------------------------------------------------------------
    # GROUP: comparison_direct (IDs 227–232)
    # Direct product-vs-product comparison requests
    # -------------------------------------------------------------------------
    {
        "id": 227, "group": "comparison_direct",
        "label": "MacBook vs Lenovo ThinkPad comparison",
        "message": "How does the MacBook Air compare to the Lenovo ThinkPad X1 Carbon?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Named product comparison request. System should provide attribute-grounded comparison "
            "(performance, weight, battery, price) sourced from product database, not hallucinated."
        ),
    },
    {
        "id": 228, "group": "comparison_direct",
        "label": "Compare by price vs performance",
        "message": "Which one gives me the best performance for the price?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Value/price-performance comparison of previously shown products. "
            "System should compute or reason about performance-per-dollar using actual specs."
        ),
    },
    {
        "id": 229, "group": "comparison_direct",
        "label": "Compare two products shown",
        "message": "Compare options 1 and 3 for me",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Ordinal reference comparison — products 1 and 3 from last recommendation. "
            "System must resolve ordinal references from session context, not ask the user to name them."
        ),
    },
    {
        "id": 230, "group": "comparison_direct",
        "label": "Compare Dell vs HP for an office user",
        "message": "For office work, is Dell or HP generally better?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Brand-level comparison for a specific use-case. "
            "System should provide a reasoned comparison, ideally with specific product examples."
        ),
    },
    {
        "id": 231, "group": "comparison_direct",
        "label": "Differences between Windows and Mac for work",
        "message": "What's the difference between getting a Windows laptop vs a Mac for remote work?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Platform comparison for a specific use-case. "
            "System should explain practical differences and recommend both options."
        ),
    },
    {
        "id": 232, "group": "comparison_direct",
        "label": "Gaming laptop A vs B — frame-rate focus",
        "message": "Between those gaming laptops, which one will give me higher FPS in games?",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Performance-specific comparison (FPS/gaming). "
            "System should reference GPU specs from database to make the comparison, not guess."
        ),
    },

    # -------------------------------------------------------------------------
    # GROUP: post_rec_refine (IDs 233–239)
    # Refinement after initial recommendations
    # -------------------------------------------------------------------------
    {
        "id": 233, "group": "post_rec_refine",
        "label": "Cheaper option after seeing results",
        "message": "Those are too expensive. Show me something cheaper.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "quality_note": (
            "Post-recommendation budget refinement. System should lower the price threshold "
            "and re-search while preserving other active constraints."
        ),
    },
    {
        "id": 234, "group": "post_rec_refine",
        "label": "Change brand preference after seeing results",
        "message": "Actually I'd prefer Apple. Can you show me Macs instead?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Brand pivot in post-recommendation refinement. System should switch brand filter to Apple "
            "and return MacBook options, preserving other active constraints (budget, use-case)."
        ),
    },
    {
        "id": 235, "group": "post_rec_refine",
        "label": "Add a spec requirement after seeing results",
        "message": "I also need at least 1TB storage. Can you narrow those down?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Refinement adding new spec constraint (storage >= 1TB). "
            "System should add storage filter and re-search with all existing constraints preserved."
        ),
    },
    {
        "id": 236, "group": "post_rec_refine",
        "label": "More options in same budget",
        "message": "Can you show me more options in the same price range?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User wants more variety without changing constraints. "
            "System should return additional results with same filters, avoiding repeating already-shown products."
        ),
    },
    {
        "id": 237, "group": "post_rec_refine",
        "label": "Exclude a brand from results shown",
        "message": "None of the HP ones — I've had bad luck with them. Show me the others.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["HP"],
        "expect_filters": [],
        "quality_note": (
            "Post-recommendation brand exclusion. System must add HP to excluded_brands "
            "and re-search. No HP products should appear in results or future turns."
        ),
    },
    {
        "id": 238, "group": "post_rec_refine",
        "label": "Ask for lighter options after seeing heavy ones",
        "message": "These all look pretty heavy. Do you have any lighter ones?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Weight refinement after seeing initial results. "
            "System should prioritize lighter laptops while preserving other active constraints."
        ),
    },
    {
        "id": 239, "group": "post_rec_refine",
        "label": "Revert to earlier preference",
        "message": "Actually, forget the Mac preference — any brand is fine",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "User is removing a previously stated brand constraint. "
            "System should remove brand filter and re-search across all brands."
        ),
    },

    # -------------------------------------------------------------------------
    # GROUP: trust_edge_cases (IDs 240–245)
    # Impossible/contradictory constraints; edge cases
    # -------------------------------------------------------------------------
    {
        "id": 240, "group": "trust_edge_cases",
        "label": "Contradictory OS requirement",
        "message": "I need a MacBook but with Windows 11 pre-installed",
        "expect_recs_on_first": False,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Contradictory requirement: MacBook hardware only runs macOS natively. "
            "System should flag the contradiction and ask for clarification "
            "(is the brand preference or OS requirement the hard constraint?)."
        ),
    },
    {
        "id": 241, "group": "trust_edge_cases",
        "label": "Impossible spec for price",
        "message": "I need a laptop with RTX 4090 mobile under $300",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": ["budget"],
        "expect_disclosure": True,
        "hard_budget_usd": 300,
        "quality_note": (
            "No laptop with RTX 4090 mobile exists under $300 in any catalog. "
            "System must disclose this impossibility and not hallucinate a compliant product."
        ),
    },
    {
        "id": 242, "group": "trust_edge_cases",
        "label": "Request for out-of-stock specific model",
        "message": "I specifically want the ASUS ROG Zephyrus G16 2024",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Named model request. If the exact model isn't in catalog, system must say so "
            "and offer alternatives — not fabricate a matching product."
        ),
    },
    {
        "id": 243, "group": "trust_edge_cases",
        "label": "Exclude all known brands",
        "message": "I don't want Dell, HP, Lenovo, ASUS, Apple, or Acer. What else do you have?",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": ["Dell", "HP", "Lenovo", "ASUS", "Apple", "Acer"],
        "expect_filters": [],
        "quality_note": (
            "User excludes all major brands. System should return whatever remains in catalog "
            "(MSI, Razer, Samsung, etc.) or honestly disclose limited options."
        ),
    },
    {
        "id": 244, "group": "trust_edge_cases",
        "label": "Price floor that eliminates catalog",
        "message": "I only buy premium laptops. Nothing under $3000.",
        "expect_recs_on_first": True,
        "expect_question": False,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Unusual price floor constraint (minimum price $3000). "
            "If catalog has no options >= $3000, system must disclose this limitation honestly."
        ),
    },
    {
        "id": 245, "group": "trust_edge_cases",
        "label": "Contradictory brand + exclusion combo",
        "message": "I need a MacBook but I absolutely hate Apple products",
        "expect_recs_on_first": False,
        "expect_question": True,
        "must_not_contain_brands": [],
        "expect_filters": [],
        "quality_note": (
            "Direct contradiction: MacBook is an Apple product; user says they hate Apple. "
            "System should flag the contradiction and ask for clarification."
        ),
    },
]


# ============================================================================
# LLM Judge — quality scoring only (not product spec matching)
# ============================================================================

GEVAL_SYSTEM = """\
You are an expert evaluator for IDSS, an AI shopping assistant for laptops.

== CRITICAL RULE ==
The agent queries a FIXED product database. If it cannot find a laptop with
an exact spec (e.g. RTX 4060, 1440p 165Hz), it returns the closest available
products. DO NOT PENALIZE for imperfect product matches — the catalog is limited.
Evaluate the AGENT'S REASONING AND BEHAVIOUR, not catalog coverage.

== YOUR JOB ==
Score the QUALITY of the agent's response on a 0–10 point scale.

== CALIBRATION ANCHORS ==
10/10  Perfect: correct response type, fully helpful, great tone, no errors.
 8/10  Good: helpful and constructive, minor omissions or small verbosity.
 6/10  Acceptable: partially addresses the query, some issues.
 4/10  Weak: off on some key point but shows basic competence.
 2/10  Poor: misses the point, unhelpful, or major factual error.
 0/10  Failure: empty, crashes, or completely wrong.

== QUALITY RUBRIC (reason through each before scoring) ==

Step 1 — ENGAGEMENT (0–4 pts):
  Does the response constructively address the user's actual situation?
  • 4: Fully engages with context, adds useful insight or empathy
  • 2: Partially engages, misses one aspect
  • 0: Ignores the key request

Step 2 — TONE CALIBRATION (0–3 pts):
  Does the tone match the user's communication style?
  • 3: Perfect match (technical for experts, simple for non-tech, empathetic for frustrated)
  • 2: Mostly appropriate
  • 1: Slightly off
  • 0: Clearly wrong tone

Step 3 — ACCURACY & COHERENCE (0–3 pts):
  Does it avoid factual errors and irrelevant content?
  • 3: No errors; coherent; all claims are reasonable
  • 2: Minor issues
  • 1: One clear factual problem
  • 0: Major factual error (e.g. "Final Cut Pro works on Windows" or "1TB RAM exists")

== FEW-SHOT CALIBRATION EXAMPLES ==

Example A (score 9/10):
  User: "need latop for school. budget arond 500 bucks. nothin too heavy"
  Agent: "Got it! Here are some lightweight laptops under $500 great for school: [3 laptops shown]"
  → Engagement: 3 (understood typos, acknowledged budget), Tone: 3 (casual), Accuracy: 3 = 9

Example B (score 5/10):
  User: "my work gives me $3,000 for a laptop. i want to spend all of it."
  Agent: "What will you use it for?" [only question, no recs]
  → Engagement: 1 (has enough info for recs but only asks), Tone: 2, Accuracy: 2 = 5

Example C (score 2/10):
  User: "can any of these run Final Cut Pro? i edit YouTube videos."
  Agent: "Yes! Here are Windows laptops that run Final Cut Pro: [Windows recs]"
  → Engagement: 1, Tone: 2, Accuracy: 0 (Final Cut Pro is Mac-only) = 3 → score 0.3

== OUTPUT ==
First write a brief reasoning (2–3 sentences, one per step).
Then output EXACTLY this JSON on the last line:
{"score": <0-10>, "reason": "<≤12 words summarizing the key issue>"}
"""

GEVAL_USER_TEMPLATE = """\
User query: {message}

Expected behavior note: {quality_note}

Agent response:
  Type: {rtype}
  Message: {agent_msg}
  Products shown ({n_recs} total):
{rec_list}

Score 0–10:"""


# ============================================================================
# Deterministic scoring helpers
# ============================================================================

def check_response_type(query: Dict, response: Dict) -> Tuple[float, str]:
    """
    Deterministic check: did the agent choose the correct response type?
    Returns (score 0.0-1.0, explanation).
    """
    rtype = response.get("response_type", "")
    exp_recs = query.get("expect_recs_on_first", False)
    exp_q = query.get("expect_question", False)

    if exp_recs and not exp_q:
        if rtype == "recommendations":
            return 1.0, f"✓ Gave recommendations as expected"
        else:
            return 0.0, f"✗ Expected recommendations, got {rtype!r}"
    elif exp_q and not exp_recs:
        if rtype == "question":
            return 1.0, f"✓ Asked clarifying question as expected"
        else:
            return 0.0, f"✗ Expected question, got {rtype!r} (should not give unsolicited recs)"
    else:
        # Ambiguous — either type is acceptable
        return 0.75, f"~ Either type acceptable, got {rtype!r}"


def check_brand_exclusions(query: Dict, response: Dict) -> Tuple[float, str]:
    """
    Deterministic check: do recommendations contain excluded brands?
    Returns (score 0.0 or 1.0, explanation). Returns None if no exclusions.
    """
    must_not = query.get("must_not_contain_brands", [])
    if not must_not:
        return None, "no exclusions to check"

    recs = response.get("recommendations") or []
    violations = []
    for row in recs:
        for product in row:
            p_brand = (product.get("brand") or "").lower()
            p_name = (product.get("name") or "").lower()
            for bad in must_not:
                if bad.lower() in p_brand or bad.lower() in p_name:
                    violations.append(
                        f"{bad} in '{product.get('name', '')[:40]}'"
                    )

    if violations:
        return 0.0, f"✗ Brand exclusion FAILED: {'; '.join(violations[:3])}"
    else:
        return 1.0, f"✓ No excluded brands ({', '.join(must_not)}) in results"


def check_filters_extracted(
    query: Dict, response: Dict
) -> Tuple[float, str]:
    """Soft check: what fraction of expected filters were extracted?"""
    expected = query.get("expect_filters", [])
    if not expected:
        return None, "no filters to check"

    _ALIASES = {
        "budget":        ["price_max_cents", "price_min_cents", "price"],
        "screen_size":   ["min_screen_size", "max_screen_size", "screen_size"],
        "use_case":      ["good_for_gaming", "good_for_ml", "good_for_creative", "_soft_preferences"],
        "excluded_brands": ["excluded_brands"],
    }
    filters = response.get("filters", {})
    found, missing = [], []
    for key in expected:
        aliases = _ALIASES.get(key, [key])
        val = next((filters[k] for k in aliases if k in filters), None)
        if val is not None:
            found.append(key)
        else:
            missing.append(key)

    ratio = len(found) / len(expected) if expected else 1.0
    note = (
        f"✓ filters: {found}" if not missing
        else f"~ filters found: {found}; missing: {missing}"
    )
    return ratio, note


def check_availability(q: Dict, resp: Dict) -> Tuple[Optional[float], str]:
    """
    Deterministic check: are all recommended products marked as available?

    Returns None when there are no recommendations (metric not applicable).
    Returns 0.0 if any product has available=False (out-of-stock item recommended).
    Returns 1.0 if all products are available (or availability field absent, i.e. assumed true).

    This catches a real failure mode: the formatter sets available=False when
    available_qty==0, and if our store doesn't filter those out, they may appear
    in recommendations.  A strong agent should never recommend an out-of-stock item.
    """
    recs = resp.get("recommendations") or []
    all_products = [item for row in recs for item in (row if isinstance(row, list) else [])]
    if not all_products:
        return None, "no recs to check"

    unavailable = [p for p in all_products if p.get("available") is False]
    if unavailable:
        names = [str(p.get("name") or p.get("id") or "?")[:35] for p in unavailable]
        return 0.0, f"✗ Out-of-stock recommended: {', '.join(names[:3])}"
    return 1.0, f"✓ all {len(all_products)} products available"


def check_disclosure(q: Dict, resp: Dict) -> Tuple[Optional[float], str]:
    """
    Honest-scoping check for catalog_impossible queries (expect_disclosure=True).

    Tests whether the system honestly handles the case where no catalog product
    satisfies all stated hard constraints. Only fires when q["expect_disclosure"]
    is True; returns (None, "N/A") otherwise.

    Three-tier scoring with group pass threshold of 0.7 (not the global 0.5):
      1.0  Explicit disclosure: message contains "couldn't find", "no products",
           "no match", "closest available", "out of our price range",
           "doesn't exist", "relaxed", "lowest price", "minimum available", etc.
           → Trust & Safety fully satisfied.
      0.7  No products returned, no disclosure language.
           → System returned nothing but didn't explain why. Borderline.
      0.5  Products returned, all within hard_budget_usd * 1.05.
           → System found something via implicit relaxation without disclosing.
           Still fails group threshold (0.7).
      0.0  Products returned, any product exceeds hard_budget_usd by > 5%.
           → Critical Trust & Safety failure: silent budget violation.

    When hard_budget_usd is None (e.g. brand-exclusion impossible query), the
    budget violation check is skipped. Score is based on disclosure language only.
    """
    if not q.get("expect_disclosure"):
        return None, "N/A — not a catalog_impossible query"

    # Flatten recommendations to a list of product dicts
    recs = resp.get("recommendations") or []
    all_products = [p for row in recs for p in (row if isinstance(row, list) else [row])]
    message_lower = (resp.get("message") or "").lower()

    # Keywords that indicate honest disclosure of a constraint gap
    _DISCLOSURE_PHRASES = (
        "couldn't find", "could not find", "no products", "no laptops",
        "no match", "no results", "not available", "not in our catalog",
        "doesn't exist", "don't carry", "do not carry",
        "closest available", "closest option", "nearest option",
        "out of our price range", "below our minimum", "lowest price",
        "relaxed", "adjusted the budget", "expanded the budget",
        "minimum available", "starting at", "most affordable",
        "nothing under", "none under", "none available",
    )
    disclosed = any(phrase in message_lower for phrase in _DISCLOSURE_PHRASES)

    hard_budget = q.get("hard_budget_usd")

    if not all_products:
        # No products returned — grade on disclosure quality
        if disclosed:
            return 1.0, "✓ Honest disclosure: system acknowledged no products match"
        return 0.7, "~ No products returned but no explicit disclosure language found"

    # Products returned — check for budget violations
    if hard_budget is not None:
        over_budget = [
            p for p in all_products
            if p.get("price") is not None and float(p["price"]) > hard_budget * 1.05
        ]
        if over_budget:
            names = [str(p.get("name") or "?")[:30] for p in over_budget[:2]]
            return 0.0, (
                f"✗ HARD FAIL: {len(over_budget)} product(s) returned over "
                f"${hard_budget} budget: {', '.join(names)}"
            )

    # Products returned and all within budget — implicit relaxation without disclosure
    if disclosed:
        return 1.0, "✓ Honest disclosure: products shown with budget clarification noted"
    return 0.5, (
        f"~ Products returned (all within budget tolerance) but no explicit disclosure. "
        f"Silent relaxation. Fails group threshold (0.7)."
    )


# ============================================================================
# Async agent call
# ============================================================================

async def send_chat_async(
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
# Async LLM quality scorer
# ============================================================================

async def score_quality_async(
    oai: AsyncOpenAI,
    query: Dict,
    response: Dict,
) -> Tuple[float, str, Dict[str, int]]:
    """Use GPT-4o-mini to score response quality (engagement, tone, accuracy).

    Returns (score [0,1], reason_str, usage_dict).
    usage_dict has keys prompt_tokens and completion_tokens for cost tracking.
    """
    rtype = response.get("response_type", "unknown")
    agent_msg = response.get("message", "")[:500]
    recs = response.get("recommendations") or []
    rec_lines = []
    for row in recs:
        for p in row:
            brand = p.get("brand", "?")
            name = (p.get("name") or "")[:45]
            price = p.get("price_value", "?")
            rec_lines.append(f"    [{brand}] {name} @ ${price}")
    rec_list = "\n".join(rec_lines[:8]) if rec_lines else "    (none)"

    prompt = GEVAL_USER_TEMPLATE.format(
        message=query["message"],
        quality_note=query.get("quality_note", ""),
        rtype=rtype,
        agent_msg=agent_msg,
        n_recs=sum(len(r) for r in recs),
        rec_list=rec_list,
    )

    _zero_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    try:
        completion = await oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": GEVAL_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=200,
        )
        # Track token usage for cost reporting (gpt-4o-mini: $0.150/1M in, $0.600/1M out)
        usage: Dict[str, int] = {
            "prompt_tokens": getattr(completion.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(completion.usage, "completion_tokens", 0) or 0,
        }
        raw = completion.choices[0].message.content.strip()
        # Extract last JSON line
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                score_10 = float(data.get("score", 5))
                return max(0.0, min(10.0, score_10)) / 10.0, data.get("reason", ""), usage
        return 0.5, f"parse error: {raw[-80:]}", usage
    except Exception as e:
        return 0.5, f"scoring error: {e}", _zero_usage


# ============================================================================
# Per-query combined score
# ============================================================================

def compute_final_score(
    type_score: float,
    brand_score: Optional[float],
    filter_score: Optional[float],
    quality_score: float,
    stock_score: Optional[float] = None,
    disclosure_score: Optional[float] = None,
) -> float:
    """
    Weighted combination of scoring components.

    Weight rationale (engineering heuristics, not empirically optimized):
      40% — Response type (deterministic, highest weight):
             Choosing the *right action* (give recs vs ask a question) is the most
             critical behavior. An agent that gives unsolicited recs to "laptop" (one
             word) fails fundamentally, regardless of response quality.
      20% — Brand exclusions (deterministic, only when applicable):
             Brand exclusion is a HARD constraint — showing an HP laptop when user
             said "no HP" is a critical failure. Gets 20% when the query has
             must_not_contain_brands; redistributes to quality when N/A.
      20% — Honest scoping / disclosure (deterministic, only for catalog_impossible group):
             When no catalog product satisfies stated constraints, system must disclose.
             Same weight as brand exclusion — both are hard Trust & Safety signals.
             Redistributes to quality for normal queries.
      10% — Filter extraction (deterministic, soft signal):
             Verifies budget/RAM were correctly parsed. Soft (partial credit); lower
             weight because missed filters are recoverable (agent can still ask).
       5% — Availability (deterministic, only when recommendations present):
             Never recommend out-of-stock products. Small weight because our store
             mostly filters them already; redistributes to quality when N/A.
      25% — LLM quality score (engagement + tone + accuracy):
             Covers everything deterministic checks don't: empathy, correctness of
             advice, tone matching (casual vs technical), handling misconceptions.

    Effective weights for most queries (no brand exclusion, no filter check):
      type=40%, stock=5% (if recs), quality=55%  (w_quality = 1.0 - 0.40 - 0.05)
    For catalog_impossible group (disclosure active, brand N/A):
      type=40%, disclosure=20%, filter=10% (if recs), stock=5% (if recs), quality=25%

    If a component is N/A (None), its weight redistributes entirely to quality.
    """
    # Base weights
    w_type       = 0.40
    w_brand      = 0.20 if brand_score      is not None else 0.0
    w_disclosure = 0.20 if disclosure_score is not None else 0.0
    w_filter     = 0.10 if filter_score     is not None else 0.0
    w_stock      = 0.05 if stock_score      is not None else 0.0
    w_quality    = 1.0 - w_type - w_brand - w_disclosure - w_filter - w_stock

    total = w_type * type_score
    if brand_score is not None:
        total += w_brand * brand_score
    if disclosure_score is not None:
        total += w_disclosure * disclosure_score
    if filter_score is not None:
        total += w_filter * filter_score
    if stock_score is not None:
        total += w_stock * stock_score
    total += w_quality * quality_score

    return round(max(0.0, min(1.0, total)), 4)


# ============================================================================
# Main async runner
# ============================================================================

CONCURRENCY = 6   # max simultaneous agent calls
PASS_THRESHOLD = 0.5

GREEN = "\033[32m"
RED   = "\033[31m"
YEL   = "\033[33m"
CYN   = "\033[36m"
BOLD  = "\033[1m"
RST   = "\033[0m"

def color_score(s: float) -> str:
    clr = GREEN if s >= PASS_THRESHOLD else RED
    return f"{clr}{s:.3f}{RST}"


async def verify_catalog_impossible(
    client:   httpx.AsyncClient,
    base_url: str,
    q:        Dict,
) -> bool:
    """
    Verify that a catalog_impossible query truly returns no matching products
    (or only products that violate the hard_budget_usd constraint) in the live catalog.

    This is a WARN-ONLY pre-flight check run at the start of run_geval_async().
    It does not abort the eval — it prints a warning if the catalog has changed
    and now contains a product that satisfies the impossible query.

    Returns True if the query is verified impossible; False if a matching product was found.
    A "matching product" means a product whose price is within hard_budget_usd * 1.05.
    Queries without hard_budget_usd (e.g. nonexistent brand) are considered verified
    by definition (no structured price check possible).
    """
    hard_budget = q.get("hard_budget_usd")
    if hard_budget is None:
        return True  # no price constraint to verify — assume impossible

    try:
        resp = await client.post(
            f"{base_url}/chat",
            json={"message": q["message"], "session_id": str(uuid.uuid4())},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return True  # can't verify — assume impossible

    recs = data.get("recommendations") or []
    products = [p for row in recs for p in (row if isinstance(row, list) else [row])]
    matching = [
        p for p in products
        if p.get("price") is not None and float(p["price"]) <= hard_budget * 1.05
    ]
    return len(matching) == 0


async def run_geval_async(
    agent_url: str,
    selected_ids: Optional[List[int]],
    verbose: bool,
    save_path: Optional[str],
    baseline_path: Optional[str],
    group_filter: Optional[str] = None,
    extra_queries_path: Optional[str] = None,
    no_kg: bool = False,
) -> List[Dict]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set")
        sys.exit(1)

    oai = AsyncOpenAI(api_key=api_key)
    sem = asyncio.Semaphore(CONCURRENCY)

    # Merge base queries + optional extra queries from JSON file
    all_queries: List[Dict] = list(QUERIES)
    if extra_queries_path:
        with open(extra_queries_path) as _f:
            extra = json.load(_f)
        # Validate no ID collisions
        existing_ids = {q["id"] for q in all_queries}
        for eq in extra:
            if eq["id"] in existing_ids:
                print(f"  WARN: extra query id={eq['id']} collides with base QUERIES; skipping")
            else:
                all_queries.append(eq)
    # Fast id→query lookup (replaces fragile QUERIES[id-1] index arithmetic)
    query_by_id: Dict[int, Dict] = {q["id"]: q for q in all_queries}

    queries = all_queries
    if selected_ids:
        queries = [q for q in queries if q["id"] in selected_ids]
    if group_filter:
        queries = [q for q in queries if q.get("group") == group_filter]

    total = len(queries)
    group_label = f"  Group: {group_filter}" if group_filter else ""
    extra_label = f"  + {extra_queries_path}" if extra_queries_path else ""
    print(f"\n{'='*74}")
    print(f"  {BOLD}IDSS G-Eval v2{RST} — {total} queries → {agent_url}/chat{group_label}{extra_label}")
    print(f"  Judge: gpt-4o-mini  |  Threshold: {PASS_THRESHOLD}  |  Concurrency: {CONCURRENCY}")
    print(f"{'='*74}\n")

    # ── Pre-flight: verify catalog_impossible queries are truly impossible ─
    # Warn (but do not abort) if any "impossible" query now returns a
    # budget-compliant product — catalog may have changed since queries were authored.
    impossible_qs = [q for q in queries if q.get("group") == "catalog_impossible"]
    if impossible_qs:
        print(f"{YEL}Pre-flight: verifying {len(impossible_qs)} catalog_impossible "
              f"queries against live catalog...{RST}")
        async with httpx.AsyncClient(timeout=90) as _vc:
            for _q in impossible_qs:
                _still_impossible = await verify_catalog_impossible(_vc, agent_url, _q)
                if not _still_impossible:
                    print(f"  {YEL}WARN Q{_q['id']}: '{_q['label']}' may no longer be "
                          f"impossible — catalog may have changed. Disclosure scores could "
                          f"be unreliable for this query.{RST}")
        print()

    # ── Phase 1: Collect agent responses in parallel ──────────────────────
    print(f"{CYN}Phase 1/2: Querying agent ({CONCURRENCY} concurrent)...{RST}")

    async def query_one(q: Dict) -> Dict:
        session_id = str(uuid.uuid4())
        async with sem:
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    resp = await send_chat_async(client, agent_url, q["message"], session_id)
            except httpx.ConnectError:
                print(f"\n  FAIL: Cannot connect to {agent_url}")
                sys.exit(1)
            except Exception as e:
                return {"query": q, "response": {}, "elapsed_ms": 0, "error": str(e)}
            elapsed = (time.perf_counter() - t0) * 1000
        return {"query": q, "response": resp, "elapsed_ms": round(elapsed)}

    t_phase1 = time.perf_counter()
    agent_results = await asyncio.gather(*[query_one(q) for q in queries])
    phase1_elapsed = time.perf_counter() - t_phase1
    print(f"  Done in {phase1_elapsed:.1f}s\n")

    # ── Phase 2: Score all responses in parallel ───────────────────────────
    print(f"{CYN}Phase 2/2: Scoring with LLM judge ({len(agent_results)} calls)...{RST}")

    async def score_one(item: Dict) -> Dict:
        q = item["query"]
        resp = item["response"]
        err = item.get("error")

        if err:
            return {
                "id": q["id"], "group": q["group"], "label": q["label"],
                "message": q["message"], "score": 0.0, "type_score": 0.0,
                "brand_score": None, "filter_score": None, "quality_score": 0.0,
                "type_note": err, "brand_note": "", "filter_note": "", "reason": err,
                "elapsed_ms": 0, "response_type": "error", "n_recs": 0,
            }

        type_score, type_note = check_response_type(q, resp)
        brand_score, brand_note = check_brand_exclusions(q, resp)
        filter_score, filter_note = check_filters_extracted(q, resp)
        stock_score, stock_note = check_availability(q, resp)
        disclosure_score, disclosure_note = check_disclosure(q, resp)
        quality_score, reason, usage = await score_quality_async(oai, q, resp)

        final = compute_final_score(
            type_score, brand_score, filter_score, quality_score,
            stock_score, disclosure_score,
        )

        n_recs = sum(len(r) for r in (resp.get("recommendations") or []))
        rtype = resp.get("response_type", "?")

        return {
            "id": q["id"], "group": q["group"], "label": q["label"],
            "message": q["message"],
            "score": final,
            "type_score": type_score,
            "brand_score": brand_score,
            "filter_score": filter_score,
            "stock_score": stock_score,
            "disclosure_score": disclosure_score,
            "quality_score": quality_score,
            "type_note": type_note,
            "brand_note": brand_note,
            "filter_note": filter_note,
            "stock_note": stock_note,
            "disclosure_note": disclosure_note,
            "reason": reason,
            "elapsed_ms": item["elapsed_ms"],
            "response_type": rtype,
            "n_recs": n_recs,
            # Token usage for cost reporting (zero when scoring failed)
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        }

    t_phase2 = time.perf_counter()
    scored = await asyncio.gather(*[score_one(item) for item in agent_results])
    phase2_elapsed = time.perf_counter() - t_phase2
    scored = sorted(scored, key=lambda r: r["id"])
    print(f"  Done in {phase2_elapsed:.1f}s\n")

    # ── Print per-query results ────────────────────────────────────────────
    print(f"{'─'*74}")
    for r in scored:
        status = f"{GREEN}PASS{RST}" if r["score"] >= PASS_THRESHOLD else f"{RED}FAIL{RST}"
        print(
            f"  Q{r['id']:2d} [{r['group']:15s}] {status}  "
            f"score={color_score(r['score'])}  "
            f"type={r['type_score']:.2f}  "
            f"qual={r['quality_score']:.2f}  "
            f"[{r['response_type']:15s}] {r['elapsed_ms']:5d}ms"
        )
        print(f"       {r['type_note']}")
        if r["brand_note"] and "no exclusions" not in r["brand_note"]:
            print(f"       {r['brand_note']}")
        # Show stock/disclosure failures always; only show passing notes in verbose
        if r.get("stock_score") == 0.0:
            print(f"       {r.get('stock_note', '')}")
        if r.get("disclosure_score") is not None and r.get("disclosure_score", 1.0) < 0.7:
            print(f"       {r.get('disclosure_note', '')}")
        if verbose:
            print(f"       reason: {r['reason']}")
            if r["filter_note"] and "no filters" not in r["filter_note"]:
                print(f"       {r['filter_note']}")
            if r.get("stock_note") and "no recs" not in r.get("stock_note", ""):
                print(f"       {r.get('stock_note', '')}")
    print(f"{'─'*74}\n")

    # ── Summary tables ─────────────────────────────────────────────────────
    def stats(subset: List[Dict]) -> Tuple[int, float, float, float]:
        if not subset:
            return 0, 0.0, 0.0, 0.0
        n = len(subset)
        avg = sum(r["score"] for r in subset) / n
        pct = 100.0 * sum(1 for r in subset if r["score"] >= PASS_THRESHOLD) / n
        type_acc = 100.0 * sum(1 for r in subset if r["type_score"] == 1.0) / n
        return n, avg, pct, type_acc

    # Difficulty: Specified vs Underspecified
    specified   = [r for r in scored if query_by_id[r["id"]].get("expect_recs_on_first")]
    underspec   = [r for r in scored if not query_by_id[r["id"]].get("expect_recs_on_first")]

    # Also Quick/Long by char count (to match Hannah's format)
    quick = [r for r in scored if len(r["message"]) <= 200]
    long  = [r for r in scored if len(r["message"]) > 200]

    ns, as_, ps, ts = stats(specified)
    nu, au, pu, tu = stats(underspec)
    nq, aq, pq, tq = stats(quick)
    nl, al, pl, tl = stats(long)
    na, aa, pa, ta = stats(scored)

    BASELINE = {
        "Specified":     {"avg": None, "pct": None},
        "Underspecified": {"avg": None, "pct": None},
        "All":           {"avg": 0.727, "pct": 84.0},  # Hannah's eval
    }
    if baseline_path:
        try:
            with open(baseline_path) as f:
                bl = json.load(f)
            BASELINE["All"]["avg"] = bl["summary"]["all"]["avg_score"]
            BASELINE["All"]["pct"] = bl["summary"]["all"]["pass_pct"]
        except Exception:
            pass

    def fmt_delta(val: float, base: Optional[float]) -> str:
        if base is None:
            return ""
        d = val - base
        clr = GREEN if d >= 0 else RED
        return f"  ({clr}{'+' if d>=0 else ''}{d:.3f}{RST})"

    hdr = f"  {'Difficulty':<24} {'N':>4}  {'Avg':>7}  {'Pass%':>7}  {'TypeAcc%':>9}"
    sep = f"  {'─'*60}"

    print(f"\n{BOLD}  {'Merchant Agent — G-Eval v2':^60}{RST}\n")
    print(f"  Table 1: Results by Difficulty (Specified = has budget/specs)")
    print(hdr); print(sep)
    print(f"  {'Specified (expect recs)':<24} {ns:>4}  {as_:>7.3f}  {ps:>6.1f}%  {ts:>8.1f}%")
    print(f"  {'Underspecified (vague)':<24} {nu:>4}  {au:>7.3f}  {pu:>6.1f}%  {tu:>8.1f}%")
    print(sep)
    delta_avg = fmt_delta(aa, BASELINE["All"]["avg"])
    delta_pct = fmt_delta(pa, BASELINE["All"]["pct"])
    print(f"  {'All':<24} {na:>4}  {aa:>7.3f}{delta_avg}  {pa:>6.1f}%{delta_pct}  {ta:>8.1f}%")
    print()

    print(f"  Table 2: Results by Char Length (matches Hannah's format)")
    print(hdr); print(sep)
    print(f"  {'Quick (≤200 chars)':<24} {nq:>4}  {aq:>7.3f}  {pq:>6.1f}%  {tq:>8.1f}%")
    print(f"  {'Long  (>200 chars)':<24} {nl:>4}  {al:>7.3f}  {pl:>6.1f}%  {tl:>8.1f}%")
    print(sep)
    print(f"  {'All':<24} {na:>4}  {aa:>7.3f}  {pa:>6.1f}%  {ta:>8.1f}%")
    print()
    if BASELINE["All"]["avg"]:
        bl_avg = BASELINE["All"]["avg"]
        bl_pct = BASELINE["All"]["pct"]
        print(f"  {YEL}Previous baseline (Hannah eval, 50 queries):{RST}  "
              f"avg={bl_avg:.3f}  pass={bl_pct:.1f}%")
    print(f"  G-Eval threshold = {PASS_THRESHOLD}; score in [0,1].")
    print()

    # Per-group breakdown
    groups = sorted(set(r["group"] for r in scored))
    print(f"  Table 3: Per-group breakdown")
    print(f"  {'Group':<20} {'N':>4}  {'Avg':>7}  {'Pass%':>7}  {'TypeAcc%':>9}")
    print(f"  {'─'*52}")
    for g in groups:
        subset = [r for r in scored if r["group"] == g]
        n, avg, pct, tacc = stats(subset)
        print(f"  {g:<20} {n:>4}  {avg:>7.3f}  {pct:>6.1f}%  {tacc:>8.1f}%")
    print()

    # Score distribution histogram
    buckets = {f"{i/10:.1f}-{(i+1)/10:.1f}": 0 for i in range(0, 10)}
    for r in scored:
        s = r["score"]
        idx = min(int(s * 10), 9)
        key = f"{idx/10:.1f}-{(idx+1)/10:.1f}"
        buckets[key] += 1
    print(f"  Score distribution:")
    for bucket, count in buckets.items():
        bar = "█" * count
        print(f"    {bucket}  {bar:<20} {count}")
    print()

    # Failures
    failed = [r for r in scored if r["score"] < PASS_THRESHOLD]
    if failed:
        print(f"  {RED}Failed queries ({len(failed)}):{RST}")
        for r in failed:
            print(f"    Q{r['id']:2d} [{r['group']}]  score={r['score']:.3f}  {r['label'][:55]}")
        print()

    # ── Cost summary (gpt-4o-mini: $0.150/1M prompt tokens, $0.600/1M completion tokens) ──
    total_prompt = sum(r.get("prompt_tokens", 0) for r in scored)
    total_completion = sum(r.get("completion_tokens", 0) for r in scored)
    cost_usd = (total_prompt * 0.150 + total_completion * 0.600) / 1_000_000
    avg_latency_ms = sum(r.get("elapsed_ms", 0) for r in scored) / len(scored) if scored else 0
    p50_ms = sorted(r.get("elapsed_ms", 0) for r in scored)[len(scored) // 2] if scored else 0

    print(f"  {'─'*60}")
    print(f"  Judge tokens:  {total_prompt:,} prompt + {total_completion:,} completion")
    print(f"  Judge cost:    ${cost_usd:.4f} USD  (gpt-4o-mini rates)")
    print(f"  Agent latency: avg={avg_latency_ms:.0f}ms  p50={p50_ms}ms")
    print(f"  Total time: agent={phase1_elapsed:.1f}s + scoring={phase2_elapsed:.1f}s\n")

    # ── Save JSON ──────────────────────────────────────────────────────────
    if save_path:
        output = {
            "version": "v2",
            "agent_url": agent_url,
            "kg_enabled": not no_kg,
            "threshold": PASS_THRESHOLD,
            "summary": {
                "specified":     {"n": ns, "avg_score": round(as_, 4), "pass_pct": round(ps, 1)},
                "underspecified": {"n": nu, "avg_score": round(au, 4), "pass_pct": round(pu, 1)},
                "quick":         {"n": nq, "avg_score": round(aq, 4), "pass_pct": round(pq, 1)},
                "long":          {"n": nl, "avg_score": round(al, 4), "pass_pct": round(pl, 1)},
                "all":           {"n": na, "avg_score": round(aa, 4), "pass_pct": round(pa, 1)},
                "type_accuracy": round(ta, 1),
            },
            "cost": {
                "judge_model": "gpt-4o-mini",
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "total_tokens": total_prompt + total_completion,
                "cost_usd": round(cost_usd, 6),
                "avg_agent_latency_ms": round(avg_latency_ms),
                "p50_agent_latency_ms": p50_ms,
            },
            "results": scored,
        }
        with open(save_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"  Saved full results to: {save_path}")

    return scored


def main():
    parser = argparse.ArgumentParser(description="G-Eval v2 — IDSS Merchant Agent")
    parser.add_argument("--url", default="http://localhost:8000",
                        help="Agent base URL (default: http://localhost:8000)")
    parser.add_argument("--query", type=int, action="append", dest="queries", metavar="N",
                        help="Run only query with this ID (repeatable)")
    parser.add_argument("--group", metavar="GROUP",
                        help=(
                            "Run only queries in this group. Core groups: "
                            "expert, typos, contradictory, no_tech, forum, budget, "
                            "domain_switch, brand_exclusion, urgency, cross_product, "
                            "lifestyle, wrong_info, one_liner, comparison, refine, "
                            "price_range, brand_pref. Extra (--extra-queries): deepshop"
                        ))
    parser.add_argument("--extra-queries", metavar="FILE",
                        help="JSON file with additional query objects to append (e.g. deepshop_queries.json)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print LLM judge reasoning per query")
    parser.add_argument("--save", metavar="FILE", help="Save JSON results to file")
    parser.add_argument("--baseline", metavar="FILE",
                        help="Compare against baseline results JSON (shows delta)")
    parser.add_argument("--no-kg", action="store_true",
                        help="Tag this run as KG-disabled in saved JSON (kg_enabled=false). "
                             "To actually disable KG, start the backend without NEO4J_PASSWORD set.")
    parser.add_argument("--sajjad-url", metavar="URL", nargs="?",
                        const="http://localhost:9003",
                        help="Shortcut: evaluate Sajjad's idss-mcp endpoint. "
                             "Overrides --url and implies --no-kg. "
                             "Default URL: http://localhost:9003. "
                             "Example: --sajjad-url  OR  --sajjad-url http://myhost:9003")
    args = parser.parse_args()

    # --sajjad-url shortcut: override url and set no_kg
    agent_url = args.url
    no_kg     = args.no_kg
    if args.sajjad_url is not None:
        agent_url = args.sajjad_url
        no_kg     = True
        print(f"  [sajjad-url] Evaluating Sajjad endpoint: {agent_url}  (kg_enabled=false tagged)")

    asyncio.run(run_geval_async(
        agent_url=agent_url,
        selected_ids=args.queries,
        verbose=args.verbose,
        save_path=args.save,
        baseline_path=args.baseline,
        group_filter=args.group,
        extra_queries_path=args.extra_queries,
        no_kg=no_kg,
    ))


if __name__ == "__main__":
    main()
