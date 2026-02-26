#!/usr/bin/env python3
"""
Shopping Agent Latency Benchmark
=================================
Measures each sub-step of the agent pipeline in isolation.
Uses DIRECT function calls (no HTTP server) wherever possible.

Phases measured:
  A. SESSION LAYER (in-memory + Redis)
     A1. Session lookup — existing session (Redis GET)
     A2. Session creation — new session (Redis SET)

  B. DOMAIN DETECTION (LLM vs. fast-path)
     B1. Fast-path domain detection (keyword lookup, 0 LLM calls)
     B2. LLM domain detection (OpenAI structured output, gpt-4o-mini)

  C. AGENT LLM CALLS (gpt-4o-mini, structured outputs)
     C1. Criteria extraction (ExtractedCriteria schema)
     C2. Question generation (GeneratedQuestion schema)
     C3. Post-rec intent detection via LLM (ComparisonClassification schema)
     C4. Recommendation explanation (chat completion, ~200 tokens)
     C5. Comparison narrative (chat completion, ~400 tokens)
     C6. Filter refinement (RefinementClassification schema)

  D. SEARCH LAYER (Supabase/Redis)
     D1. E-commerce search — Redis cache hit
     D2. E-commerce search — cache miss (Supabase DB roundtrip)
     D3. KG re-ranking overlay (kg_service.search_candidates)

  E. LOCAL CPU (no I/O)
     E1. Brand diversification (interleave by brand, n=12 products)
     E2. Product formatting (format_product × 6)
     E3. Filter → search-filter conversion (get_search_filters)

Usage:
    # Full benchmark (all phases, needs OPENAI_API_KEY + DB):
    python scripts/run_agent_latency_benchmark.py

    # Skip LLM calls (faster, DB-only):
    python scripts/run_agent_latency_benchmark.py --no-llm

    # Run n repetitions per phase:
    python scripts/run_agent_latency_benchmark.py -n 5

    # Save JSON results:
    python scripts/run_agent_latency_benchmark.py --save
"""
import os
import sys
import time
import asyncio
import argparse
import statistics
import uuid

# ── Path setup ────────────────────────────────────────────────────────────────
_REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_REPO, "mcp-server"))
sys.path.insert(0, _REPO)

from dotenv import load_dotenv
load_dotenv()

N = 5   # default repetitions (LLM calls are expensive → keep low)


# ── Timing helpers ─────────────────────────────────────────────────────────────

def _stats(times: list) -> dict:
    s = sorted(times)
    n = len(s)
    return {
        "mean":   round(statistics.mean(s), 1),
        "median": round(s[n // 2], 1),
        "p95":    round(s[max(0, int(n * 0.95) - 1)], 1),
        "min":    round(s[0], 1),
        "max":    round(s[-1], 1),
        "n":      n,
    }


def measure_sync(fn, n=None) -> dict:
    n = n or N
    times = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    return _stats(times)


def measure_async(coro_factory, n=None) -> dict:
    n = n or N
    times = []

    async def _run():
        for _ in range(n):
            t = time.perf_counter()
            await coro_factory()
            times.append((time.perf_counter() - t) * 1000)

    asyncio.run(_run())
    return _stats(times)


def _row(label: str, stats: dict | None, note: str = "") -> tuple:
    return (label, stats, note)


# ── Section A: Session layer ───────────────────────────────────────────────────

def bench_sessions(results: list):
    print("\n[A] SESSION LAYER")
    try:
        from agent.interview.session_manager import get_session_manager
        sm = get_session_manager()

        # A1. Lookup existing session
        sid_existing = str(uuid.uuid4())
        sm.get_session(sid_existing)  # create it
        def _lookup():
            sm.get_session(sid_existing)
        r = measure_sync(_lookup)
        print(f"  A1 session lookup (existing)   mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("A1 Session lookup — existing", r, "Redis GET + deserialise"))

        # A2. New session creation
        def _new_session():
            sm.get_session(str(uuid.uuid4()))
        r = measure_sync(_new_session)
        print(f"  A2 session creation (new)      mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("A2 Session creation — new", r, "Redis SET + defaults"))

    except Exception as e:
        print(f"  [SKIP] Session layer unavailable: {e}")
        results.append(_row("A1 Session lookup — existing", None, str(e)))
        results.append(_row("A2 Session creation — new", None, str(e)))


# ── Section B: Domain detection ────────────────────────────────────────────────

def bench_domain_detection(results: list, skip_llm: bool):
    print("\n[B] DOMAIN DETECTION")
    try:
        from agent.universal_agent import UniversalAgent
        agent = UniversalAgent(session_id="bench-domain")

        # B1. Fast-path (keyword → no LLM call)
        def _fast_domain():
            agent._detect_domain_from_message("laptops")
        r = measure_sync(_fast_domain)
        print(f"  B1 fast-path (keyword match)   mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("B1 Domain detection — fast path", r, "Dict lookup, 0 LLM calls"))

        # B2. LLM domain detection
        if not skip_llm:
            queries = [
                "I need a laptop for deep learning under 2000 dollars",
                "looking for a good used sedan under 25k",
                "recommend me a science fiction novel",
                "gaming laptop with RTX 4060",
                "toyota corolla or honda civic",
            ]
            times = []
            for q in queries[:N]:
                t = time.perf_counter()
                result = agent._detect_domain_from_message(q)
                times.append((time.perf_counter() - t) * 1000)
                print(f"    {times[-1]:6.0f}ms → domain={result}  q={q[:40]}")
                time.sleep(0.1)
            r = _stats(times)
            print(f"  B2 LLM domain detection        mean={r['mean']}ms  med={r['median']}ms")
            results.append(_row("B2 Domain detection — LLM", r, "gpt-4o-mini structured output"))
        else:
            results.append(_row("B2 Domain detection — LLM", None, "skipped (--no-llm)"))

    except Exception as e:
        print(f"  [SKIP] Domain detection unavailable: {e}")
        results.append(_row("B1 Domain detection — fast path", None, str(e)))
        results.append(_row("B2 Domain detection — LLM", None, str(e)))


# ── Section C: Agent LLM calls ─────────────────────────────────────────────────

def bench_llm_calls(results: list, skip_llm: bool):
    print("\n[C] AGENT LLM CALLS")

    if skip_llm:
        for label in [
            "C1 Criteria extraction",
            "C2 Question generation",
            "C3 Post-rec intent detection",
            "C4 Recommendation explanation",
            "C5 Comparison narrative",
            "C6 Filter refinement",
        ]:
            results.append(_row(label, None, "skipped (--no-llm)"))
        return

    try:
        from agent.universal_agent import UniversalAgent
        from agent.domain_registry import get_domain_schema
        agent = UniversalAgent(session_id="bench-llm")
        agent.domain = "laptops"
        schema = get_domain_schema("laptops")

        # C1. Criteria extraction
        CRITERIA_MSGS = [
            "I need a laptop for deep learning, 32GB RAM preferred, under $2000",
            "gaming laptop with RTX 4060, at least 16GB RAM",
            "lightweight laptop under 1200 for travel",
            "developer laptop with lots of RAM for Docker",
            "budget laptop for Python under 700 dollars",
        ]
        times = []
        for msg in CRITERIA_MSGS[:N]:
            t = time.perf_counter()
            agent._extract_criteria(msg, schema)
            times.append((time.perf_counter() - t) * 1000)
            print(f"    {times[-1]:6.0f}ms  [criteria]  {msg[:50]}")
            time.sleep(0.1)
        r = _stats(times)
        print(f"  C1 criteria extraction         mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("C1 Criteria extraction", r, "gpt-4o-mini ExtractedCriteria schema"))

        # C2. Question generation
        QGEN_SLOTS = [
            ("budget", "Maximum budget for the laptop"),
            ("use_case", "Primary use case (gaming, coding, etc.)"),
            ("min_ram_gb", "Minimum RAM in GB"),
            ("brand", "Preferred brand"),
            ("screen_size", "Preferred screen size"),
        ]
        times = []
        for slot_name, _ in QGEN_SLOTS[:N]:
            slot = next((s for s in schema.slots if s.name == slot_name), schema.slots[0])
            t = time.perf_counter()
            agent._generate_question(slot, schema)
            times.append((time.perf_counter() - t) * 1000)
            print(f"    {times[-1]:6.0f}ms  [qgen]     slot={slot_name}")
            time.sleep(0.1)
        r = _stats(times)
        print(f"  C2 question generation         mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("C2 Question generation", r, "gpt-4o-mini GeneratedQuestion schema"))

        # C3. Post-rec intent detection (LLM path)
        from agent.comparison_agent import detect_post_rec_intent
        POST_REC_MSGS = [
            "I want to compare the first and third option",
            "show me something cheaper",
            "what are the pros and cons of these?",
            "which one has better battery life?",
            "I'd like to look at vehicles instead",
        ]
        times_posrec = []
        for msg in POST_REC_MSGS[:N]:
            t = time.perf_counter()
            asyncio.run(detect_post_rec_intent(msg))
            times_posrec.append((time.perf_counter() - t) * 1000)
            print(f"    {times_posrec[-1]:6.0f}ms  [intent]   {msg[:50]}")
            time.sleep(0.1)
        r = _stats(times_posrec)
        print(f"  C3 post-rec intent detection   mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("C3 Post-rec intent detection", r, "gpt-4o-mini LLM classification"))

        # C4. Recommendation explanation
        # Build a minimal fake product list
        FAKE_PRODUCTS = [
            [{"name": "Dell XPS 15", "brand": "Dell", "price": 1499.0,
              "rating": 4.7, "attributes": {"ram_gb": 16, "storage_gb": 512, "cpu": "i9-14900HK"},
              "laptop": {"specs": {"ram": "16GB", "graphics": "RTX 4060"}}}],
            [{"name": "Apple MacBook Pro 14", "brand": "Apple", "price": 1999.0,
              "rating": 4.9, "attributes": {"ram_gb": 32, "storage_gb": 1024, "cpu": "M4 Pro"},
              "laptop": {"specs": {"ram": "32GB", "graphics": "M4 Pro GPU"}}}],
        ]
        agent.domain = "laptops"
        agent.filters = {"budget": "under 2000", "use_case": "deep learning"}
        times_expl = []
        for _ in range(N):
            t = time.perf_counter()
            agent.generate_recommendation_explanation(FAKE_PRODUCTS, "laptops")
            times_expl.append((time.perf_counter() - t) * 1000)
            print(f"    {times_expl[-1]:6.0f}ms  [rec-explain]")
            time.sleep(0.1)
        r = _stats(times_expl)
        print(f"  C4 recommendation explanation  mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("C4 Recommendation explanation", r, "gpt-4o-mini text completion ~200tok"))

        # C5. Comparison narrative
        from agent.comparison_agent import generate_comparison_narrative
        FAKE_FLAT_PRODUCTS = [
            {"id": str(uuid.uuid4()), "name": "Dell XPS 15", "brand": "Dell",
             "price": 1499.0, "rating": 4.7,
             "attributes": {"ram_gb": 16, "cpu": "Core i9", "storage_gb": 512}},
            {"id": str(uuid.uuid4()), "name": "Apple MacBook Pro 14", "brand": "Apple",
             "price": 1999.0, "rating": 4.9,
             "attributes": {"ram_gb": 32, "cpu": "M4 Pro", "storage_gb": 1024}},
            {"id": str(uuid.uuid4()), "name": "Lenovo ThinkPad X1 Carbon", "brand": "Lenovo",
             "price": 1349.0, "rating": 4.5,
             "attributes": {"ram_gb": 16, "cpu": "Core Ultra 7", "storage_gb": 512}},
        ]
        times_cmp = []
        for _ in range(N):
            t = time.perf_counter()
            asyncio.run(generate_comparison_narrative(FAKE_FLAT_PRODUCTS, "compare these options", "laptops"))
            times_cmp.append((time.perf_counter() - t) * 1000)
            print(f"    {times_cmp[-1]:6.0f}ms  [compare]")
            time.sleep(0.1)
        r = _stats(times_cmp)
        print(f"  C5 comparison narrative        mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("C5 Comparison narrative", r, "gpt-4o-mini text completion ~400tok"))

        # C6. Filter refinement (process_refinement LLM path)
        REFINE_MSGS = [
            "show me something with more RAM",
            "I'd prefer something cheaper, under 1500",
            "filter to Dell only",
            "I need at least 16 inch screen",
            "switch to gaming focus",
        ]
        times_refine = []
        for msg in REFINE_MSGS[:N]:
            agent.domain = "laptops"
            agent.filters = {"budget": "under 2000", "use_case": "deep learning", "brand": "Dell"}
            t = time.perf_counter()
            agent.process_refinement(msg)
            times_refine.append((time.perf_counter() - t) * 1000)
            print(f"    {times_refine[-1]:6.0f}ms  [refine]   {msg[:50]}")
            time.sleep(0.1)
        r = _stats(times_refine)
        print(f"  C6 filter refinement           mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("C6 Filter refinement", r, "gpt-4o-mini RefinementClassification schema"))

    except Exception as e:
        import traceback
        print(f"  [ERROR] LLM benchmarks failed: {e}")
        traceback.print_exc()
        for label in ["C1 Criteria extraction", "C2 Question generation",
                      "C3 Post-rec intent detection", "C4 Recommendation explanation",
                      "C5 Comparison narrative", "C6 Filter refinement"]:
            results.append(_row(label, None, str(e)))


# ── Section D: Search layer ────────────────────────────────────────────────────

def bench_search(results: list):
    print("\n[D] SEARCH LAYER")
    try:
        from app.tools.supabase_product_store import get_product_store
        from app.cache import cache_client as cc

        store = get_product_store()
        FILTERS = {"category": "Electronics", "product_type": "laptop", "price_max_cents": 200000}
        LIMIT = 18  # same as chat_endpoint uses for 2×3 grid × 3x pool

        # Warmup: one query to establish the TCP/TLS connection to Supabase
        print("  Warming up Supabase connection...")
        store.search_products(FILTERS, limit=1)

        # D2. Cache miss — Supabase DB roundtrip (after connection is established)
        # Bust cache so we measure cold path
        import hashlib, json as _json
        _f_sorted = dict(sorted({**FILTERS, "category": "Electronics"}.items()))
        _cache_key_raw = _json.dumps(_f_sorted, sort_keys=True) + f"|Electronics|1|{LIMIT}"
        _cache_key = f"mcp:search:{hashlib.md5(_cache_key_raw.encode()).hexdigest()}"
        try:
            cc.client.delete(cc._key(_cache_key))
        except Exception:
            pass

        print("  D2 measuring cache miss (Supabase)...")
        times_miss = []
        for i in range(N):
            # Bust cache on every iteration so we always measure cold path
            try:
                cc.client.delete(cc._key(_cache_key))
            except Exception:
                pass
            t = time.perf_counter()
            store.search_products(FILTERS, limit=LIMIT)
            times_miss.append((time.perf_counter() - t) * 1000)
            print(f"    {times_miss[-1]:6.0f}ms  [D2 cache-miss iter {i+1}]")
        r_miss = _stats(times_miss)
        print(f"  D2 e-commerce search (cache miss)  mean={r_miss['mean']}ms  med={r_miss['median']}ms")
        results.append(_row("D2 E-commerce search — cache miss", r_miss, "Supabase PostgreSQL roundtrip"))

        # D1. Cache hit — prime the cache, then measure Redis GET
        store.search_products(FILTERS, limit=LIMIT)  # prime
        try:
            # The cache key used by supabase_product_store internally
            # We approximate it via the cache_client.make_search_key
            _ck = cc.make_search_key(FILTERS, "Electronics", page=1, limit=LIMIT)
        except Exception:
            _ck = None

        if _ck:
            times_hit = []
            for _ in range(N):
                t = time.perf_counter()
                cc.get_search_results(_ck)
                times_hit.append((time.perf_counter() - t) * 1000)
            r_hit = _stats(times_hit)
            print(f"  D1 e-commerce search (cache hit)   mean={r_hit['mean']}ms  med={r_hit['median']}ms")
            results.append(_row("D1 E-commerce search — cache hit", r_hit, "Redis GET + JSON deserialise"))
        else:
            results.append(_row("D1 E-commerce search — cache hit", None, "cache key unavailable"))

        # D3. KG re-ranking overlay
        try:
            from app.kg_service import get_kg_service
            kg = get_kg_service()
            if kg.is_available():
                def _kg_search():
                    kg.search_candidates(
                        query="deep learning laptop RTX 4060",
                        filters={"category": "Electronics"},
                        limit=18,
                    )
                r_kg = measure_sync(_kg_search)
                print(f"  D3 KG re-ranking overlay           mean={r_kg['mean']}ms  med={r_kg['median']}ms")
                results.append(_row("D3 KG re-ranking overlay", r_kg, "FAISS/graph candidate lookup"))
            else:
                results.append(_row("D3 KG re-ranking overlay", None, "KG not available"))
        except Exception as e:
            results.append(_row("D3 KG re-ranking overlay", None, str(e)))

    except Exception as e:
        print(f"  [SKIP] Search layer unavailable: {e}")
        for label in ["D1 E-commerce search — cache hit",
                       "D2 E-commerce search — cache miss",
                       "D3 KG re-ranking overlay"]:
            results.append(_row(label, None, str(e)))


# ── Section E: Local CPU ───────────────────────────────────────────────────────

def bench_local_cpu(results: list):
    print("\n[E] LOCAL CPU (no I/O)")
    try:
        from agent.chat_endpoint import _diversify_by_brand
        from app.formatters import format_product
        from agent.universal_agent import UniversalAgent

        # Build a fake product list
        brands = ["Dell", "Apple", "Lenovo", "HP", "Asus", "Microsoft"]
        FAKE_PRODUCTS = [
            {
                "id": str(uuid.uuid4()),
                "name": f"{b} Laptop {i}",
                "brand": b,
                "price": 999.0 + i * 100,
                "rating": 4.2 + (i % 5) * 0.1,
                "category": "Electronics",
                "product_type": "laptop",
                "attributes": {"ram_gb": 16, "storage_gb": 512},
            }
            for i, b in enumerate(brands * 2)
        ]

        # E1. Brand diversification
        def _diversify():
            _diversify_by_brand(FAKE_PRODUCTS)
        r = measure_sync(_diversify, n=100)
        print(f"  E1 brand diversification       mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("E1 Brand diversification", r, "O(n) interleave, n=12 products"))

        # E2. Product formatting (format_product × 6)
        def _format():
            for p in FAKE_PRODUCTS[:6]:
                format_product(p, "laptops")
        r = measure_sync(_format, n=100)
        print(f"  E2 product formatting ×6       mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("E2 Product formatting ×6", r, "Pydantic model + field mapping"))

        # E3. Filter → search-filter conversion
        agent = UniversalAgent(session_id="bench-cpu")
        agent.domain = "laptops"
        agent.filters = {
            "budget": "under 2000",
            "use_case": "deep learning",
            "brand": "Dell",
            "min_ram_gb": "16",
            "screen_size": "15.6",
            "storage_type": "SSD",
        }
        def _get_filters():
            agent.get_search_filters()
        r = measure_sync(_get_filters, n=100)
        print(f"  E3 filter conversion           mean={r['mean']}ms  med={r['median']}ms")
        results.append(_row("E3 Filter → search-filter conversion", r, "Regex parsing, no I/O"))

    except Exception as e:
        print(f"  [SKIP] Local CPU benchmarks failed: {e}")
        for label in ["E1 Brand diversification", "E2 Product formatting ×6",
                       "E3 Filter → search-filter conversion"]:
            results.append(_row(label, None, str(e)))


# ── Table printer ──────────────────────────────────────────────────────────────

def print_table(title: str, rows: list):
    W_LABEL = 40
    W_NOTE = 35
    print(f"\n─ {title} {'─' * (78 - len(title) - 2)}")
    hdr = f"│ {'Operation':<{W_LABEL}} │ {'Mean':>7} │ {'Median':>7} │ {'P95':>7} │ {'Notes':<{W_NOTE}} │"
    sep = f"│{'─'*(W_LABEL+2)}┼{'─'*9}┼{'─'*9}┼{'─'*9}┼{'─'*(W_NOTE+2)}│"
    print(f"┌{'─'*(W_LABEL+2)}┬{'─'*9}┬{'─'*9}┬{'─'*9}┬{'─'*(W_NOTE+2)}┐")
    print(hdr)
    print(sep)
    for label, stats, note in rows:
        if stats:
            mean = f"{stats['mean']:.0f}ms"
            med  = f"{stats['median']:.0f}ms"
            p95  = f"{stats['p95']:.0f}ms"
        else:
            mean = med = p95 = "N/A"
        note_str = (note or "")[:W_NOTE]
        print(f"│ {label:<{W_LABEL}} │ {mean:>7} │ {med:>7} │ {p95:>7} │ {note_str:<{W_NOTE}} │")
    print(f"└{'─'*(W_LABEL+2)}┴{'─'*9}┴{'─'*9}┴{'─'*9}┴{'─'*(W_NOTE+2)}┘")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agent pipeline sub-step latency benchmark")
    parser.add_argument("--no-llm", action="store_true", help="Skip all LLM API calls (faster, DB-only)")
    parser.add_argument("-n", type=int, default=5, help="Repetitions per phase (default 5)")
    parser.add_argument("--save", action="store_true", help="Save results to agent_latency_results.json")
    args = parser.parse_args()
    global N
    N = args.n

    print(f"\n{'='*80}")
    print(f"AGENT PIPELINE LATENCY BENCHMARK  (n={N} per phase)")
    print(f"{'='*80}")
    if args.no_llm:
        print("  Mode: --no-llm (LLM phases skipped)")

    results = []

    bench_sessions(results)
    bench_domain_detection(results, skip_llm=args.no_llm)
    bench_llm_calls(results, skip_llm=args.no_llm)
    bench_search(results)
    bench_local_cpu(results)

    # ── Print master table ────────────────────────────────────────────────────
    all_sections = [
        ("A. SESSION LAYER",        [r for r in results if r[0].startswith("A")]),
        ("B. DOMAIN DETECTION",     [r for r in results if r[0].startswith("B")]),
        ("C. AGENT LLM CALLS",      [r for r in results if r[0].startswith("C")]),
        ("D. SEARCH LAYER",         [r for r in results if r[0].startswith("D")]),
        ("E. LOCAL CPU (no I/O)",   [r for r in results if r[0].startswith("E")]),
    ]

    print(f"\n\n{'='*80}")
    print(f"AGENT PIPELINE — MEASURED: n={N} direct function calls each")
    print(f"{'='*80}")
    for section_title, section_rows in all_sections:
        print_table(section_title, section_rows)

    # ── Key findings ──────────────────────────────────────────────────────────
    print("\nKEY FINDINGS:")
    llm_rows = [r for r in results if r[0].startswith("C") and r[1]]
    io_rows  = [r for r in results if r[0].startswith("D") and r[1]]
    cpu_rows = [r for r in results if r[0].startswith("E") and r[1]]
    if llm_rows:
        min_llm = min(r[1]["mean"] for r in llm_rows)
        max_llm = max(r[1]["mean"] for r in llm_rows)
        print(f"  LLM calls (gpt-4o-mini):      {min_llm:.0f}–{max_llm:.0f}ms mean")
    if io_rows:
        miss_row = next((r for r in io_rows if "miss" in r[0]), None)
        hit_row  = next((r for r in io_rows if "hit" in r[0]), None)
        if miss_row:
            print(f"  Supabase search (cold):        {miss_row[1]['mean']:.0f}ms mean  "
                  f"p95={miss_row[1]['p95']:.0f}ms")
        if hit_row:
            print(f"  Redis cache hit:               {hit_row[1]['mean']:.0f}ms mean  "
                  f"(speedup ×{miss_row[1]['mean']/hit_row[1]['mean']:.0f}x vs cold)" if miss_row else "")
    if cpu_rows:
        max_cpu = max(r[1]["mean"] for r in cpu_rows)
        print(f"  Local CPU (all phases):        <{max_cpu:.1f}ms each (negligible vs I/O)")

    # ── Bottleneck summary ────────────────────────────────────────────────────
    print("\nBOTTLENECK RANKING (mean latency, slowest → fastest):")
    ranked = [(label, stats) for label, stats, _ in results if stats]
    ranked.sort(key=lambda x: -x[1]["mean"])
    for i, (label, stats) in enumerate(ranked[:8], 1):
        print(f"  {i:2d}. {label:<42}  {stats['mean']:>7.0f}ms")

    # ── Optionally save ───────────────────────────────────────────────────────
    if args.save:
        import json
        from pathlib import Path
        out = Path(_REPO) / "agent_latency_results.json"
        data = {
            "n": N,
            "phases": {
                label: stats
                for label, stats, _ in results
                if stats
            },
        }
        out.write_text(json.dumps(data, indent=2))
        print(f"\n  Results saved → {out}")


if __name__ == "__main__":
    main()
