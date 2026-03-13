"""
Load agent (and optional baseline) eval results and create PNG figures:
summary table, per-query scores, and agent-vs-baseline comparison.

Usage:
  python -m evaluation.agent_response.plot_eval_results
  python -m evaluation.agent_response.plot_eval_results --json path/to/results.json
  python -m evaluation.agent_response.plot_eval_results --baseline   # include baseline in table and plots
  python -m evaluation.agent_response.plot_eval_results --outdir results/figures
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

_THIS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = _THIS_DIR / "results"

# Query difficulty: by character length (short = quick, long = long)
QUICK_MAX_CHARS = 200

# Plot style
AGENT_COLOR = "#2e86ab"
BASELINE_COLOR = "#e94f37"
DIAGONAL_COLOR = "#6c757d"


def load_results(path: Path) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def stats(results: list) -> Tuple[int, float, float]:
    if not results:
        return 0, 0.0, 0.0
    n = len(results)
    scores = [r["score"] for r in results if isinstance(r.get("score"), (int, float))]
    avg = sum(scores) / len(scores) if scores else 0.0
    passed_pct = 100.0 * sum(1 for r in results if r.get("passed")) / n
    return n, avg, passed_pct


def _align_by_test_id(agent_results: list, baseline_results: list) -> tuple[list, list]:
    """Return (agent_scores, baseline_scores) in same order by test_id."""
    by_id_agent = {r["test_id"]: r for r in agent_results}
    by_id_baseline = {r["test_id"]: r for r in baseline_results}
    common_ids = sorted(by_id_agent.keys() & by_id_baseline.keys())
    agent_scores = [by_id_agent[tid].get("score") for tid in common_ids]
    baseline_scores = [by_id_baseline[tid].get("score") for tid in common_ids]
    # Drop pairs where either score is missing
    valid = [
        (a, b)
        for a, b in zip(agent_scores, baseline_scores)
        if isinstance(a, (int, float)) and isinstance(b, (int, float))
    ]
    if not valid:
        return [], []
    return [x[0] for x in valid], [x[1] for x in valid]


def _difficulty_score(agent_score: float, baseline_score: Optional[float]) -> float:
    """Higher = harder query. Uses 1 - max(agent, baseline) when baseline present, else 1 - agent."""
    if baseline_score is not None and isinstance(baseline_score, (int, float)):
        return 1.0 - max(agent_score, baseline_score)
    return 1.0 - agent_score


def plot_summary_table(
    agent_results: list,
    baseline_results: Optional[list],
    cutoff: int,
    out_path: Path,
) -> None:
    """Render summary statistics as a table figure."""
    fig, ax = plt.subplots(figsize=(6, 2.2 if baseline_results else 2.5))
    ax.axis("off")

    if baseline_results is not None:
        n_b, avg_b, pass_b = stats(baseline_results)
        n_a, avg_a, pass_a = stats(agent_results)
        headers = ["System", "N", "Avg score", "Pass %"]
        rows = [
            ["Baseline", str(n_b), f"{avg_b:.3f}", f"{pass_b:.1f}"],
            ["Agent", str(n_a), f"{avg_a:.3f}", f"{pass_a:.1f}"],
        ]
    else:
        def difficulty_for(q: str) -> str:
            return "Quick" if len((q or "").strip()) <= cutoff else "Long"
        quick = [r for r in agent_results if difficulty_for(r.get("user_query", "")) == "Quick"]
        long_ = [r for r in agent_results if difficulty_for(r.get("user_query", "")) == "Long"]
        n_q, avg_q, pass_q = stats(quick)
        n_l, avg_l, pass_l = stats(long_)
        n_a, avg_a, pass_a = stats(agent_results)
        headers = ["Difficulty", "N", "Avg score", "Pass %"]
        rows = [
            [f"Quick (≤{cutoff} chars)", str(n_q), f"{avg_q:.3f}", f"{pass_q:.1f}"],
            [f"Long (> {cutoff} chars)", str(n_l), f"{avg_l:.3f}", f"{pass_l:.1f}"],
            ["All", str(n_a), f"{avg_a:.3f}", f"{pass_a:.1f}"],
        ]

    table = ax.table(
        cellText=[headers] + rows,
        loc="center",
        cellLoc="center",
        colWidths=[0.35, 0.15, 0.25, 0.25],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1.2, 2.2)
    for j in range(4):
        table[(0, j)].set_facecolor("#f0f0f0")
        table[(0, j)].set_text_props(weight="bold")
    for i in range(1, len(rows) + 1):
        for j in range(4):
            table[(i, j)].set_facecolor("#fff")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_scores_by_query(
    agent_results: list,
    baseline_results: Optional[list],
    out_path: Path,
) -> None:
    """Plot score vs rank (queries sorted by difficulty: 1=easiest, N=hardest). Baseline stays aligned per query."""
    scores_a = [r.get("score") for r in agent_results if isinstance(r.get("score"), (int, float))]
    y_agent = np.array(scores_a)
    n = len(y_agent)
    x = np.arange(1, n + 1, dtype=float)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.set_ylabel("Score")
    ax.set_ylim(-0.05, 1.05)
    ax.axhline(0.5, color="gray", linestyle="--", alpha=0.7, label="Threshold 0.5")

    if baseline_results is not None:
        y_agent_aligned, y_baseline = _align_by_test_id(agent_results, baseline_results)
        if y_agent_aligned and y_baseline:
            y_agent_aligned = np.array(y_agent_aligned)
            y_baseline = np.array(y_baseline)
            # Difficulty = 1 - max(agent, baseline); higher = harder. Sort by difficulty ascending (1=easiest).
            difficulties = np.array([
                _difficulty_score(a, b) for a, b in zip(y_agent_aligned, y_baseline)
            ])
            sort_idx = np.argsort(difficulties)
            y_agent_sorted = y_agent_aligned[sort_idx]
            y_baseline_sorted = y_baseline[sort_idx]
            n = len(y_agent_sorted)
            x = np.arange(1, n + 1, dtype=float)
            ax.set_xlabel("Query rank (by difficulty, 1=easiest)")
            ax.set_title("G-Eval score by query rank (sorted by difficulty)")
            ax.plot(x, y_baseline_sorted, "o-", color=BASELINE_COLOR, markersize=4, linewidth=1, label="Baseline", alpha=0.9)
            ax.plot(x, y_agent_sorted, "s-", color=AGENT_COLOR, markersize=4, linewidth=1, label="Agent", alpha=0.9)
        else:
            # No baseline alignment: sort by difficulty = 1 - agent_score (easiest first)
            sort_idx = np.argsort(-y_agent)  # high agent score = low difficulty, so sort agent descending for easiest first
            y_agent_sorted = y_agent[sort_idx]
            x = np.arange(1, n + 1, dtype=float)
            ax.set_xlabel("Query rank (by difficulty, 1=easiest)")
            ax.set_title("G-Eval score by query rank (sorted by difficulty)")
            ax.plot(x, y_agent_sorted, "s-", color=AGENT_COLOR, markersize=4, linewidth=1, label="Agent")
    else:
        # Sort by difficulty = 1 - agent_score (easiest = high agent score first)
        sort_idx = np.argsort(-y_agent)
        y_agent_sorted = y_agent[sort_idx]
        ax.set_xlabel("Query rank (by difficulty, 1=easiest)")
        ax.set_title("G-Eval score by query rank (sorted by difficulty)")
        ax.plot(x, y_agent_sorted, "o-", color=AGENT_COLOR, markersize=4, linewidth=1, label="Agent")

    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_agent_vs_baseline(
    agent_results: list,
    baseline_results: list,
    out_path: Path,
) -> None:
    """Scatter: x=baseline score, y=agent score. Points above y=x indicate agent wins."""
    y_agent, x_baseline = _align_by_test_id(agent_results, baseline_results)
    if not y_agent or not x_baseline:
        return
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.scatter(x_baseline, y_agent, c=AGENT_COLOR, alpha=0.7, s=40, edgecolors="white", linewidths=0.5)
    lims = [0, 1]
    ax.plot(lims, lims, "--", color=DIAGONAL_COLOR, linewidth=1.5, label="Agent = Baseline")
    ax.set_xlim(lims)
    ax.set_ylim(lims)
    ax.set_xlabel("Baseline score")
    ax.set_ylabel("Agent score")
    ax.set_aspect("equal")
    ax.legend(loc="lower right")
    ax.set_title("Agent vs Baseline (above line = agent better)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate PNG figures from agent response eval results"
    )
    parser.add_argument(
        "--json",
        default=str(RESULTS_DIR / "agent_response_eval_results.json"),
        help="Path to agent results JSON",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        metavar="PATH",
        nargs="?",
        const=str(RESULTS_DIR / "agent_response_eval_results_baseline.json"),
        help="Include baseline (default: results/agent_response_eval_results_baseline.json)",
    )
    parser.add_argument(
        "--cutoff",
        type=int,
        default=QUICK_MAX_CHARS,
        help="Character cutoff for Quick vs Long (default %d)" % QUICK_MAX_CHARS,
    )
    parser.add_argument(
        "--outdir",
        default=str(RESULTS_DIR),
        help="Directory to write PNG files (default: results)",
    )
    args = parser.parse_args()

    agent_path = Path(args.json)
    if not agent_path.exists():
        print(f"Error: {agent_path} not found. Run evaluation first.", file=sys.stderr)
        return 1
    agent_results = load_results(agent_path)
    cutoff = args.cutoff
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    baseline_results = None
    if args.baseline is not None:
        baseline_path = Path(args.baseline)
        if not baseline_path.exists():
            print(f"Error: baseline file not found: {baseline_path}", file=sys.stderr)
            print("Run: python -m evaluation.agent_response.run_eval --baseline", file=sys.stderr)
            return 1
        baseline_results = load_results(baseline_path)

    # 1) Summary table as PNG
    table_path = outdir / "agent_response_summary_table.png"
    plot_summary_table(agent_results, baseline_results, cutoff, table_path)
    print(f"Saved {table_path}")

    # 2) Scores by query (run chart)
    scores_path = outdir / "agent_response_scores_by_query.png"
    plot_scores_by_query(agent_results, baseline_results, scores_path)
    print(f"Saved {scores_path}")

    # 3) Agent vs Baseline scatter (only when baseline provided)
    if baseline_results is not None:
        scatter_path = outdir / "agent_response_agent_vs_baseline.png"
        plot_agent_vs_baseline(agent_results, baseline_results, scatter_path)
        print(f"Saved {scatter_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
