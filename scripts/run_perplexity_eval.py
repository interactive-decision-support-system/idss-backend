#!/usr/bin/env python3
"""
Perplexity Baseline Evaluator — IDSS G-Eval Paper Comparison
=============================================================
Sends each query in QUERIES to Perplexity's sonar model and scores the response
using the IDENTICAL judge and metrics as run_geval.py and run_gpt_baseline.py.

NOTE: All Perplexity API models use web search by design — there is no offline
Perplexity model available via their API. Per mentor feedback (Negin Golrezaei):
"the baseline shouldn't use web search." Therefore Perplexity results are labeled
⚠ web-augmented and treated as informational only, excluded from the primary
fair comparison. Primary fair comparison: IDSS vs GPT-4o-mini (catalog-bound) vs Sajjad.

Scoring (identical structure to run_gpt_baseline.py):
  type_score    — inferred from free text (same heuristic as GPT baseline)
  brand_score   — N/A (brand violations shown informational only, not in score)
  filter_score  — N/A (no structured output)
  stock_score   — N/A (no structured output)
  quality_score — same GPT-4o-mini judge (GEVAL_SYSTEM + GEVAL_USER_TEMPLATE)

Final score = compute_final_score(type_score, None, None, quality_score, None)
  → type 40% + quality 60% (same as GPT baseline for fair comparison)

Required env vars:
  PERPLEXITY_API_KEY   — Perplexity API key
  OPENAI_API_KEY       — for the GPT-4o-mini judge

Usage:
    python scripts/run_perplexity_eval.py
    python scripts/run_perplexity_eval.py --model sonar-pro
    python scripts/run_perplexity_eval.py --save scripts/geval_perplexity.json
    python scripts/run_perplexity_eval.py --baseline scripts/geval_results_v17_20260318.json
    python scripts/run_perplexity_eval.py --group expert
    python scripts/run_perplexity_eval.py --query 1 --query 26
    python scripts/run_perplexity_eval.py --verbose
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

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

# ── Import shared infrastructure ──────────────────────────────────────────────
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)

try:
    from run_geval import (
        QUERIES,
        GEVAL_SYSTEM,
        GEVAL_USER_TEMPLATE,
        score_quality_async,
        compute_final_score,
        PASS_THRESHOLD,
    )
except ImportError as e:
    print(f"ERROR: Cannot import from run_geval.py: {e}")
    sys.exit(1)

try:
    from run_gpt_baseline import (
        infer_response_type,
        check_response_type_baseline,
    )
except ImportError as e:
    print(f"ERROR: Cannot import from run_gpt_baseline.py: {e}")
    sys.exit(1)


# ── Constants ──────────────────────────────────────────────────────────────────
DEFAULT_MODEL  = "sonar"
CONCURRENCY    = 2    # keep low to avoid 429s
RETRY_DELAYS   = [5, 15, 30]  # seconds to wait after each 429 before retrying

GREEN = "\033[92m"; RED = "\033[91m"; YEL = "\033[93m"
CYN   = "\033[96m"; BOLD = "\033[1m"; RST  = "\033[0m"

def color_score(s: float) -> str:
    c = GREEN if s >= PASS_THRESHOLD else (YEL if s >= 0.5 else RED)
    return f"{c}{s:.3f}{RST}"

# Perplexity pricing (approximate early 2026, per 1M tokens)
PRICE_IN  = {"r1-1776": 0.50, "sonar": 1.00, "sonar-pro": 3.00}
PRICE_OUT = {"r1-1776": 2.18, "sonar": 1.00, "sonar-pro": 15.00}

# Perplexity system prompt — neutral shopping assistant, no web-search context
PERPLEXITY_SYSTEM = (
    "You are a knowledgeable laptop shopping assistant. "
    "When a user describes what they need, recommend specific laptop models with "
    "model names, key specs (CPU, GPU, RAM, storage), approximate retail prices, "
    "and a brief explanation of why each fits their needs. "
    "If the request is too vague, ask ONE focused clarifying question. "
    "Be concise, accurate, and match the user's communication style."
)


# ============================================================================
# Perplexity API call
# ============================================================================

async def query_perplexity_async(
    pplx: AsyncOpenAI,
    sem: asyncio.Semaphore,
    model: str,
    message: str,
) -> Tuple[str, int, int, int]:
    """Send one query to Perplexity with automatic retry on 429 rate limits.

    Returns (response_text, elapsed_ms, prompt_tokens, completion_tokens).
    """
    async with sem:
        t0 = time.perf_counter()
        pt, ct = 0, 0
        text = ""
        for attempt, delay in enumerate([0] + RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                completion = await pplx.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": PERPLEXITY_SYSTEM},
                        {"role": "user",   "content": message},
                    ],
                    max_tokens=600,
                )
                text = completion.choices[0].message.content.strip()
                # r1-1776 may emit <think>...</think> chain-of-thought; strip it
                text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
                pt   = getattr(completion.usage, "prompt_tokens",     0) or 0
                ct   = getattr(completion.usage, "completion_tokens", 0) or 0
                break  # success
            except Exception as exc:
                err_str = str(exc)
                if "429" in err_str and attempt < len(RETRY_DELAYS):
                    print(f"\n  [rate-limit] 429 on attempt {attempt+1}, retrying in {RETRY_DELAYS[attempt]}s...",
                          end=" ", flush=True)
                    continue
                text = f"[ERROR: {exc}]"
                break
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return text, elapsed_ms, pt, ct


# ============================================================================
# Informational brand text check (NOT in final score — see docstring)
# ============================================================================

def check_brand_text_info(query: Dict, response_text: str) -> str:
    """Scan response text for excluded brand names. Returns info note (not scored)."""
    must_not = query.get("must_not_contain_brands", [])
    if not must_not:
        return ""
    text_lower = response_text.lower()
    violations = [b for b in must_not if b.lower() in text_lower]
    if violations:
        return f"⚠ Mentioned excluded brand(s): {', '.join(violations)}"
    return f"✓ Did not mention excluded brand(s): {', '.join(must_not)}"


# ============================================================================
# Main evaluation loop
# ============================================================================

async def run_perplexity_eval(
    model: str,
    queries: List[Dict],
    verbose: bool,
    save_path: Optional[str],
    baseline_path: Optional[str],
) -> List[Dict]:
    pplx_key = os.environ.get("PERPLEXITY_API_KEY")
    if not pplx_key:
        print("ERROR: PERPLEXITY_API_KEY not set.")
        sys.exit(1)
    oai_key = os.environ.get("OPENAI_API_KEY")
    if not oai_key:
        print("ERROR: OPENAI_API_KEY not set (needed for judge).")
        sys.exit(1)

    pplx = AsyncOpenAI(api_key=pplx_key, base_url="https://api.perplexity.ai")
    oai  = AsyncOpenAI(api_key=oai_key)
    sem  = asyncio.Semaphore(CONCURRENCY)
    query_by_id = {q["id"]: q for q in queries}

    total = len(queries)
    print(f"\n{BOLD}Perplexity Baseline Evaluator{RST}")
    print(f"  Queries     : {total}")
    print(f"  Model       : {model} (⚠ web-augmented — all Perplexity models use web search)")
    print(f"  Judge       : gpt-4o-mini (same as IDSS eval)")
    print(f"  Threshold   : {PASS_THRESHOLD}")
    print(f"  Concurrency : {CONCURRENCY}")
    print(f"  {YEL}⚠ Perplexity has live web access — no offline API model exists.")
    print(f"  Results are INFORMATIONAL ONLY, excluded from primary fair comparison.")
    print(f"  Primary fair comparison: IDSS vs GPT-4o-mini (catalog-bound) vs Sajjad.{RST}\n")

    # ── Phase 1: Query Perplexity in parallel ──────────────────────────────
    print(f"  Phase 1 — Querying Perplexity/{model} ({total} queries) ...")
    t_phase1 = time.perf_counter()

    async def fetch_one(q: Dict) -> Dict:
        text, elapsed_ms, pt, ct = await query_perplexity_async(pplx, sem, model, q["message"])
        return {"id": q["id"], "text": text, "elapsed_ms": elapsed_ms, "pplx_pt": pt, "pplx_ct": ct}

    raw_responses = await asyncio.gather(*[fetch_one(q) for q in queries])
    phase1_elapsed = time.perf_counter() - t_phase1
    print(f"  Done in {phase1_elapsed:.1f}s\n")

    # ── Phase 2: Score all responses ──────────────────────────────────────
    print(f"  Phase 2 — Scoring {total} responses ...")
    t_phase2 = time.perf_counter()

    async def score_one(raw: Dict) -> Dict:
        q   = query_by_id[raw["id"]]
        txt = raw["text"]

        rtype      = infer_response_type(txt)
        type_score, type_note = check_response_type_baseline(q, rtype)

        # Informational brand check (not in final score)
        brand_note = check_brand_text_info(q, txt)

        # Pseudo-response for quality judge (same format as GPT baseline)
        pseudo_resp = {
            "response_type": rtype,
            "message": txt[:500],
            "recommendations": [],
        }
        quality_score, reason, usage = await score_quality_async(oai, q, pseudo_resp)

        # Same formula as GPT baseline: type 40% + quality 60% (brand/filter/stock N/A)
        final = compute_final_score(
            type_score=type_score,
            brand_score=None,
            filter_score=None,
            quality_score=quality_score,
            stock_score=None,
        )

        return {
            "id": q["id"],
            "group": q["group"],
            "label": q["label"],
            "message": q["message"],
            "score": final,
            "type_score": type_score,
            "brand_score": None,          # N/A — text-only, not in final score
            "filter_score": None,         # N/A
            "stock_score": None,          # N/A
            "quality_score": quality_score,
            "type_note": type_note,
            "brand_note": brand_note,     # informational only
            "filter_note": "N/A (no structured output)",
            "stock_note": "N/A (no structured output)",
            "reason": reason,
            "elapsed_ms": raw["elapsed_ms"],
            "response_type": rtype,
            "n_recs": 0,
            "perplexity_text": txt[:600],
            "pplx_prompt_tokens": raw["pplx_pt"],
            "pplx_completion_tokens": raw["pplx_ct"],
            "judge_prompt_tokens": usage.get("prompt_tokens", 0),
            "judge_completion_tokens": usage.get("completion_tokens", 0),
        }

    scored = await asyncio.gather(*[score_one(r) for r in raw_responses])
    phase2_elapsed = time.perf_counter() - t_phase2
    scored = sorted(scored, key=lambda r: r["id"])
    print(f"  Done in {phase2_elapsed:.1f}s\n")

    # ── Per-query display ──────────────────────────────────────────────────
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
        if r["brand_note"]:
            print(f"       {r['brand_note']}")
        if verbose:
            print(f"       reason: {r['reason']}")
            print(f"       Perplexity: {r['perplexity_text'][:120].replace(chr(10), ' ')}")
    print(f"{'─'*74}\n")

    # ── Summary ───────────────────────────────────────────────────────────
    def stats(subset: List[Dict]) -> Tuple[int, float, float, float]:
        if not subset:
            return 0, 0.0, 0.0, 0.0
        n = len(subset)
        avg = sum(r["score"] for r in subset) / n
        pct = 100.0 * sum(1 for r in subset if r["score"] >= PASS_THRESHOLD) / n
        type_acc = 100.0 * sum(1 for r in subset if r["type_score"] == 1.0) / n
        return n, avg, pct, type_acc

    all_q_by_id = {q["id"]: q for q in QUERIES}
    specified = [r for r in scored if all_q_by_id.get(r["id"], {}).get("expect_recs_on_first")]
    underspec  = [r for r in scored if not all_q_by_id.get(r["id"], {}).get("expect_recs_on_first")]
    ns, as_, ps, ts = stats(specified)
    nu, au, pu, tu  = stats(underspec)
    na, aa, pa, ta  = stats(scored)

    hdr = f"  {'Category':<28} {'N':>4}  {'Avg':>7}  {'Pass%':>7}  {'TypeAcc%':>9}"
    sep = f"  {'─'*60}"
    print(f"\n{BOLD}  {'Perplexity/sonar (⚠ web-augmented) — G-Eval':^60}{RST}\n")
    print(f"  Results by Difficulty")
    print(hdr); print(sep)
    print(f"  {'Specified (expect recs)':<28} {ns:>4}  {as_:>7.3f}  {ps:>6.1f}%  {ts:>8.1f}%")
    print(f"  {'Underspecified (vague)':<28} {nu:>4}  {au:>7.3f}  {pu:>6.1f}%  {tu:>8.1f}%")
    print(sep)
    print(f"  {'All':<28} {na:>4}  {aa:>7.3f}  {pa:>6.1f}%  {ta:>8.1f}%")

    # Per-group breakdown
    groups = sorted(set(r["group"] for r in scored))
    print(f"\n  Per-group breakdown")
    print(f"  {'Group':<20} {'N':>4}  {'Avg':>7}  {'Pass%':>7}  {'TypeAcc%':>9}")
    print(f"  {'─'*52}")
    for g in groups:
        subset = [r for r in scored if r["group"] == g]
        n, avg, pct, tacc = stats(subset)
        print(f"  {g:<20} {n:>4}  {avg:>7.3f}  {pct:>6.1f}%  {tacc:>8.1f}%")

    # Comparison vs IDSS baseline
    if baseline_path:
        try:
            with open(baseline_path) as f:
                bl = json.load(f)
            idss_avg = bl["summary"]["all"]["avg_score"]
            idss_pct = bl["summary"]["all"]["pass_pct"]
            d_avg = aa - idss_avg
            d_pct = pa - idss_pct
            da_clr = GREEN if d_avg >= 0 else RED
            dp_clr = GREEN if d_pct >= 0 else RED
            print(f"\n  {BOLD}Comparison vs IDSS baseline:{RST}")
            print(f"    IDSS:        avg={idss_avg:.3f}  pass={idss_pct:.1f}%")
            print(f"    Perplexity:  avg={aa:.3f}  pass={pa:.1f}%")
            print(f"    Delta:       avg={da_clr}{'+' if d_avg>=0 else ''}{d_avg:.3f}{RST}"
                  f"  pass={dp_clr}{'+' if d_pct>=0 else ''}{d_pct:.1f}%{RST}")
        except Exception as e:
            print(f"\n  Could not load baseline: {e}")

    # Cost
    pplx_pt_total  = sum(r["pplx_prompt_tokens"] for r in scored)
    pplx_ct_total  = sum(r["pplx_completion_tokens"] for r in scored)
    judge_pt_total = sum(r["judge_prompt_tokens"] for r in scored)
    judge_ct_total = sum(r["judge_completion_tokens"] for r in scored)
    pin   = PRICE_IN.get(model, 1.00)
    pout  = PRICE_OUT.get(model, 1.00)
    pplx_cost  = (pplx_pt_total * pin + pplx_ct_total * pout) / 1_000_000
    judge_cost = (judge_pt_total * 0.150 + judge_ct_total * 0.600) / 1_000_000
    avg_ms = sum(r["elapsed_ms"] for r in scored) / len(scored) if scored else 0

    print(f"\n  {'─'*60}")
    print(f"  Perplexity tokens : {pplx_pt_total:,} in + {pplx_ct_total:,} out")
    print(f"  Perplexity cost   : ${pplx_cost:.4f} USD  ({model} rates)")
    print(f"  Judge tokens      : {judge_pt_total:,} in + {judge_ct_total:,} out")
    print(f"  Judge cost        : ${judge_cost:.4f} USD  (gpt-4o-mini)")
    print(f"  Total cost        : ${pplx_cost + judge_cost:.4f} USD")
    print(f"  Avg Perplexity latency: {avg_ms:.0f}ms")
    print(f"  Total time: queries={phase1_elapsed:.1f}s + scoring={phase2_elapsed:.1f}s\n")
    print(f"  {YEL}⚠ INFORMATIONAL ONLY — Perplexity sonar uses web search.")
    print(f"  All Perplexity API models use live web search; no offline model exists.")
    print(f"  brand/filter/stock scores are N/A — no structured output.")
    print(f"  Primary fair comparison: IDSS vs GPT+Catalog (augmented) vs Sajjad.{RST}\n")

    # ── Save JSON ─────────────────────────────────────────────────────────
    if save_path:
        output = {
            "version": "perplexity_baseline_v2",
            "model": model,
            "threshold": PASS_THRESHOLD,
            "note": (
                "brand/filter/stock scores are N/A — Perplexity has no structured output. "
                "Model: sonar (⚠ web-augmented). All Perplexity API models use web search. "
                "INFORMATIONAL ONLY — excluded from primary fair comparison. "
                "Primary fair: IDSS vs GPT+Catalog vs Sajjad."
            ),
            "summary": {
                "specified":      {"n": ns, "avg_score": round(as_, 4), "pass_pct": round(ps, 1)},
                "underspecified": {"n": nu, "avg_score": round(au, 4), "pass_pct": round(pu, 1)},
                "all":            {"n": na, "avg_score": round(aa, 4), "pass_pct": round(pa, 1)},
                "type_accuracy":  round(ta, 1),
            },
            "cost": {
                "perplexity_model":    model,
                "perplexity_cost_usd": round(pplx_cost,  6),
                "judge_model":         "gpt-4o-mini",
                "judge_cost_usd":      round(judge_cost, 6),
                "total_cost_usd":      round(pplx_cost + judge_cost, 6),
                "avg_latency_ms":      round(avg_ms),
            },
            "results": scored,
        }
        with open(save_path, "w") as fout:
            json.dump(output, fout, indent=2)
        print(f"  Saved to: {save_path}")

    return scored


# ============================================================================
# CLI
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Perplexity shopping baseline for IDSS G-Eval paper comparison"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        choices=["sonar", "sonar-pro"],
                        help=f"Perplexity model (default: {DEFAULT_MODEL}; all models use web search)")
    parser.add_argument("--save",     help="Path to save JSON results")
    parser.add_argument("--baseline", help="IDSS results JSON to compare against (shows delta)")
    parser.add_argument("--group",    help="Only run queries from this group")
    parser.add_argument("--query",    type=int, action="append", dest="queries", metavar="N",
                        help="Run only query with this ID (repeatable)")
    parser.add_argument("--verbose",  action="store_true",
                        help="Print Perplexity response text and judge reason per query")
    args = parser.parse_args()

    queries = list(QUERIES)
    if args.group:
        queries = [q for q in queries if q["group"] == args.group]
    if args.queries:
        queries = [q for q in queries if q["id"] in args.queries]

    if not queries:
        print("No queries match the given filters.")
        sys.exit(1)

    asyncio.run(run_perplexity_eval(
        model=args.model,
        queries=queries,
        verbose=args.verbose,
        save_path=args.save,
        baseline_path=args.baseline,
    ))


if __name__ == "__main__":
    main()
