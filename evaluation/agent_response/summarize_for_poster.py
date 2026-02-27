"""
Read agent_response_eval_results.json and print a compact poster-ready table
(numeric only). With --baseline, print a comparison table: Baseline vs Agent.

Usage:
  python -m evaluation.agent_response.summarize_for_poster
  python -m evaluation.agent_response.summarize_for_poster --json path/to/results.json
  python -m evaluation.agent_response.summarize_for_poster --baseline  # comparison table
"""

import argparse
import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = _THIS_DIR / "results"

# Query difficulty: by character length (short = quick, long = long)
QUICK_MAX_CHARS = 200


def load_results(path: Path) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def stats(results: list):
    if not results:
        return 0, 0.0, 0.0
    n = len(results)
    scores = [r["score"] for r in results if isinstance(r.get("score"), (int, float))]
    avg = sum(scores) / len(scores) if scores else 0.0
    passed_pct = 100.0 * sum(1 for r in results if r.get("passed")) / n
    return n, avg, passed_pct


def main():
    parser = argparse.ArgumentParser(description="Summarize agent response eval for poster/paper")
    parser.add_argument(
        "--json",
        default=str(RESULTS_DIR / "agent_response_eval_results.json"),
        help="Path to agent (or single) results JSON",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        metavar="PATH",
        nargs="?",
        const=str(RESULTS_DIR / "agent_response_eval_results_baseline.json"),
        help="Include baseline comparison (default: results/agent_response_eval_results_baseline.json)",
    )
    parser.add_argument(
        "--cutoff",
        type=int,
        default=QUICK_MAX_CHARS,
        help="Character cutoff for Quick vs Long (default %d)" % QUICK_MAX_CHARS,
    )
    args = parser.parse_args()
    agent_path = Path(args.json)
    if not agent_path.exists():
        print(f"Error: {agent_path} not found. Run evaluation first.", file=sys.stderr)
        sys.exit(1)
    agent_results = load_results(agent_path)
    cutoff = args.cutoff

    def difficulty_for(q: str) -> str:
        return "Quick" if len((q or "").strip()) <= cutoff else "Long"

    # Comparison table: Baseline vs Agent
    if args.baseline is not None:
        baseline_path = Path(args.baseline)
        if not baseline_path.exists():
            print(f"Error: baseline file not found: {baseline_path}", file=sys.stderr)
            print("Run: python -m evaluation.agent_response.run_eval --baseline", file=sys.stderr)
            sys.exit(1)
        baseline_results = load_results(baseline_path)
        n_b, avg_b, pass_b = stats(baseline_results)
        n_a, avg_a, pass_a = stats(agent_results)
        print("Poster table (Baseline vs Agent):")
        print()
        print("| System   |   N | Avg score | Pass % |")
        print("|----------|-----|-----------+--------|")
        print(f"| Baseline | {n_b:3d} |     {avg_b:.3f} | {pass_b:5.1f} |")
        print(f"| Agent    | {n_a:3d} |     {avg_a:.3f} | {pass_a:5.1f} |")
        print()
        print("(G-Eval threshold = 0.5; score in [0,1]. Baseline = fixed clarifying question.)")
        return 0

    # Single-results table by difficulty
    quick = [r for r in agent_results if difficulty_for(r.get("user_query", "")) == "Quick"]
    long_ = [r for r in agent_results if difficulty_for(r.get("user_query", "")) == "Long"]
    n_q, avg_q, pass_q = stats(quick)
    n_l, avg_l, pass_l = stats(long_)
    n_a, avg_a, pass_a = stats(agent_results)
    print("Poster table (numeric only, by query difficulty):")
    print()
    print("| Difficulty        |   N | Avg score | Pass % |")
    print("|-------------------|-----|-----------+--------|")
    print(f"| Quick (≤{cutoff} chars) | {n_q:3d} |     {avg_q:.3f} | {pass_q:5.1f} |")
    print(f"| Long (> {cutoff} chars)  | {n_l:3d} |     {avg_l:.3f} | {pass_l:5.1f} |")
    print(f"| All               | {n_a:3d} |     {avg_a:.3f} | {pass_a:5.1f} |")
    print()
    print("(G-Eval threshold = 0.5; score in [0,1].)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
