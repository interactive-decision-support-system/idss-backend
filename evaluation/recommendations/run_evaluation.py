#!/usr/bin/env python3
"""
Run Pipeline 2 evaluation: query->UCP, UCP->recs, baseline.

Full system (query->recs) is not run because the chat agent is multiturn and often
returns a question instead of recommendations; we test the merchant agent (UCP->recs) separately.
Writes results to evaluation/recommendations/results/.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
# Load .env from repo root for OPENAI_API_KEY, DATABASE_URL, etc.
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(_REPO_ROOT, ".env")
    if os.path.isfile(_env_path):
        load_dotenv(_env_path)
except ImportError:
    pass
_MCP = os.path.join(_REPO_ROOT, "mcp-server")
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)

from evaluation.recommendations.schema import load_golden_dataset
from evaluation.recommendations.runners import (
    run_query_to_ucp,
    run_ucp_to_recs,
    run_baseline_eval,
)

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def ensure_results_dir() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def run_all(golden_path: str, skip_baseline: bool = False) -> None:
    ensure_results_dir()
    items = load_golden_dataset(golden_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # (a) Query -> UCP only
    query_to_ucp_results = [run_query_to_ucp(g) for g in items]
    out_ucp = []
    for r in query_to_ucp_results:
        out_ucp.append({
            "query_id": r["query_id"],
            "query": r["query"],
            "expected_ucp_match": r["expected_ucp_match"],
        })
    write_csv(
        RESULTS_DIR / f"query_to_ucp_results_{ts}.csv",
        out_ucp,
        ["query_id", "query", "expected_ucp_match"],
    )
    with open(RESULTS_DIR / f"query_to_ucp_results_{ts}.json", "w", encoding="utf-8") as f:
        json.dump(query_to_ucp_results, f, indent=2)
    print(f"Wrote query_to_ucp_results (CSV + JSON)")

    # (b) UCP -> recs only
    async def _ucp_recs():
        return await asyncio.gather(*[run_ucp_to_recs(g) for g in items])

    ucp_recs_results = asyncio.run(_ucp_recs())
    ucp_recs_rows = [
        {
            "query_id": r["query_id"],
            "query": r["query"],
            "hard_pass_rate": r["hard_pass_rate"],
            "mean_soft_similarity": r["mean_soft_similarity"],
            "recommended_count": r["recommended_count"],
        }
        for r in ucp_recs_results
    ]
    write_csv(
        RESULTS_DIR / f"ucp_to_recs_results_{ts}.csv",
        ucp_recs_rows,
        ["query_id", "query", "hard_pass_rate", "mean_soft_similarity", "recommended_count"],
    )
    with open(RESULTS_DIR / f"ucp_to_recs_results_{ts}.json", "w", encoding="utf-8") as f:
        json.dump(ucp_recs_results, f, indent=2)
    print(f"Wrote ucp_to_recs_results (CSV + JSON)")

    # Full system (query -> recs) is not run: the chat agent is multiturn and often
    # returns a question instead of recommendations, so recs-based metrics are not meaningful.

    # Baseline
    baseline_results = []
    if not skip_baseline:
        baseline_results = [run_baseline_eval(g) for g in items]
        baseline_rows = [
            {
                "query_id": r["query_id"],
                "query": r["query"],
                "hard_pass_rate": r["hard_pass_rate"],
                "mean_soft_similarity": r["mean_soft_similarity"],
                "recommended_count": r["recommended_count"],
            }
            for r in baseline_results
        ]
        write_csv(
            RESULTS_DIR / f"baseline_results_{ts}.csv",
            baseline_rows,
            ["query_id", "query", "hard_pass_rate", "mean_soft_similarity", "recommended_count"],
        )
        with open(RESULTS_DIR / f"baseline_results_{ts}.json", "w", encoding="utf-8") as f:
            json.dump(baseline_results, f, indent=2)
        print(f"Wrote baseline_results (CSV + JSON)")
    else:
        print("Skipped baseline (--skip-baseline)")

    # Aggregate summary
    def agg_recs(name: str, rows: list):
        if not rows:
            return None
        h = [r["hard_pass_rate"] for r in rows if "hard_pass_rate" in r]
        s = [r["mean_soft_similarity"] for r in rows if "mean_soft_similarity" in r]
        return {"run": name, "mean_hard_pass_rate": sum(h) / len(h) if h else 0, "mean_soft_similarity": sum(s) / len(s) if s else 0}

    def agg_ucp(name: str, rows: list):
        if not rows:
            return None
        m = [r["expected_ucp_match"] for r in rows if "expected_ucp_match" in r]
        return {"run": name, "mean_ucp_match_rate": sum(m) / len(m) if m else 0}

    summary = {
        "timestamp": ts,
        "golden_path": golden_path,
        "num_queries": len(items),
        "aggregates": {
            "query_to_ucp": agg_ucp("query_to_ucp", query_to_ucp_results),
            "ucp_to_recs": agg_recs("ucp_to_recs", ucp_recs_results),
            "baseline": agg_recs("baseline", baseline_results) if not skip_baseline else None,
        },
    }
    with open(RESULTS_DIR / f"summary_{ts}.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote summary_{ts}.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Pipeline 2 recommendation evaluation")
    parser.add_argument(
        "--golden",
        default=os.path.join(os.path.dirname(__file__), "golden_dataset.json"),
        help="Path to golden dataset JSON",
    )
    parser.add_argument("--skip-baseline", action="store_true", help="Skip baseline LLM run")
    args = parser.parse_args()
    run_all(args.golden, skip_baseline=args.skip_baseline)


if __name__ == "__main__":
    main()
