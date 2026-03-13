"""
Load recommendation eval results (summary, query_to_ucp, ucp_to_recs, baseline) and create PNG figures:
summary table, per-query metrics (hard pass rate & mean similarity), and Merchant Agent vs baseline comparison.
Figure labels: Shopping Agent = query→UCP, Merchant Agent = UCP→recs.

Usage:
  python -m evaluation.recommendations.plot_eval_results
  python -m evaluation.recommendations.plot_eval_results --summary results/summary_20260302_223747.json
  python -m evaluation.recommendations.plot_eval_results --outdir results
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

_THIS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = _THIS_DIR / "results"

# Plot style (align with agent_response)
UCP_RECS_COLOR = "#2e86ab"   # agent / main system
BASELINE_COLOR = "#e94f37"
DIAGONAL_COLOR = "#6c757d"


def find_latest_summary(results_dir: Path) -> Optional[Path]:
    """Return path to most recent summary_YYYYMMDD_HHMMSS.json."""
    summaries = list(results_dir.glob("summary_*.json"))
    if not summaries:
        return None
    # Sort by name (timestamp in filename)
    summaries.sort(key=lambda p: p.name, reverse=True)
    return summaries[0]


def get_timestamp_from_summary_path(path: Path) -> str:
    """Extract YYYYMMDD_HHMMSS from summary_YYYYMMDD_HHMMSS.json."""
    m = re.match(r"summary_(\d{8}_\d{6})\.json", path.name)
    return m.group(1) if m else ""


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_results_for_summary(summary_path: Path) -> Tuple[Dict, List, List, Optional[List], Optional[List]]:
    """
    Load summary and matching query_to_ucp, ucp_to_recs, baseline, query_to_ucp_baseline by timestamp.
    Returns (summary_dict, query_to_ucp_list, ucp_to_recs_list, baseline_list or None, query_to_ucp_baseline_list or None).
    """
    results_dir = summary_path.parent
    ts = get_timestamp_from_summary_path(summary_path)
    if not ts:
        raise ValueError(f"Cannot parse timestamp from {summary_path.name}")

    summary = load_json(summary_path)
    query_to_ucp = load_json(results_dir / f"query_to_ucp_results_{ts}.json")
    ucp_to_recs = load_json(results_dir / f"ucp_to_recs_results_{ts}.json")
    baseline_path = results_dir / f"baseline_results_{ts}.json"
    baseline = load_json(baseline_path) if baseline_path.exists() else None
    q2u_baseline_path = results_dir / f"query_to_ucp_baseline_results_{ts}.json"
    query_to_ucp_baseline = load_json(q2u_baseline_path) if q2u_baseline_path.exists() else None
    return summary, query_to_ucp, ucp_to_recs, baseline, query_to_ucp_baseline


def align_by_query_id(ucp_recs: List[Dict], baseline: List[Dict]) -> Tuple[List[float], List[float], List[str]]:
    """Return (ucp_recs_values, baseline_values, query_ids) for common query_ids (metric: mean_soft_similarity)."""
    by_ucp = {r["query_id"]: r for r in ucp_recs}
    by_base = {r["query_id"]: r for r in baseline}
    common = sorted(by_ucp.keys() & by_base.keys())
    u_vals = [by_ucp[q].get("mean_soft_similarity") for q in common]
    b_vals = [by_base[q].get("mean_soft_similarity") for q in common]
    valid = [(u, b) for u, b in zip(u_vals, b_vals) if isinstance(u, (int, float)) and isinstance(b, (int, float))]
    if not valid:
        return [], [], []
    return [x[0] for x in valid], [x[1] for x in valid], common


def align_ucp_match_by_query_id(agent: List[Dict], baseline: List[Dict]) -> Tuple[List[float], List[float], List[str]]:
    """Return (agent expected_ucp_match, baseline expected_ucp_match, query_ids) for common query_ids."""
    by_agent = {r["query_id"]: r for r in agent}
    by_base = {r["query_id"]: r for r in baseline}
    common = sorted(by_agent.keys() & by_base.keys())
    a_vals = [by_agent[q].get("expected_ucp_match") for q in common]
    b_vals = [by_base[q].get("expected_ucp_match") for q in common]
    valid = [(a, b) for a, b in zip(a_vals, b_vals) if isinstance(a, (int, float)) and isinstance(b, (int, float))]
    if not valid:
        return [], [], []
    return [x[0] for x in valid], [x[1] for x in valid], common


def plot_summary_table(
    summary: Dict,
    out_path: Path,
) -> None:
    """Render summary statistics as a table figure."""
    agg = summary.get("aggregates") or {}
    q2u = agg.get("query_to_ucp") or {}
    q2u_base = agg.get("query_to_ucp_baseline") or {}
    ucp = agg.get("ucp_to_recs") or {}
    base = agg.get("baseline") or {}
    n = summary.get("num_queries", 0)

    headers = ["Stage", "N", "Metric", "Value"]
    rows = [
        ["Shopping Agent", str(n), "Mean UCP match rate", f"{(q2u.get('mean_ucp_match_rate') or 0):.3f}"],
    ]
    if q2u_base:
        rows.append(["Baseline (query→UCP)", str(n), "Mean UCP match rate", f"{(q2u_base.get('mean_ucp_match_rate') or 0):.3f}"])
    rows.extend([
        ["Merchant Agent", str(n), "Mean hard pass rate", f"{(ucp.get('mean_hard_pass_rate') or 0):.3f}"],
        ["Merchant Agent", str(n), "Mean similarity", f"{(ucp.get('mean_soft_similarity') or 0):.3f}"],
    ])
    if base:
        rows.append(["Baseline (recs)", str(n), "Mean hard pass rate", f"{(base.get('mean_hard_pass_rate') or 0):.3f}"])
        rows.append(["Baseline (recs)", str(n), "Mean similarity", f"{(base.get('mean_soft_similarity') or 0):.3f}"])

    fig, ax = plt.subplots(figsize=(7, 1.8 + 0.45 * len(rows)))
    ax.axis("off")
    table = ax.table(
        cellText=[headers] + rows,
        loc="center",
        cellLoc="center",
        colWidths=[0.22, 0.12, 0.32, 0.22],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 2.0)
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
    ucp_to_recs: List[Dict],
    baseline: Optional[List[Dict]],
    out_path: Path,
) -> None:
    """Plot mean similarity by query rank (sorted by Merchant Agent). Baseline aligned by query_id."""
    scores = [r.get("mean_soft_similarity") for r in ucp_to_recs if isinstance(r.get("mean_soft_similarity"), (int, float))]
    y_ucp = np.array(scores)
    sort_idx = np.argsort(y_ucp)
    y_ucp_sorted = y_ucp[sort_idx]
    n = len(y_ucp_sorted)
    x = np.arange(1, n + 1, dtype=float)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.set_xlabel("Query rank (by Merchant Agent mean similarity)")
    ax.set_ylabel("Mean similarity")
    ax.set_ylim(-0.05, 1.05)
    ax.axhline(0.5, color="gray", linestyle="--", alpha=0.7)

    if baseline:
        y_ucp_aligned, y_base, _ = align_by_query_id(ucp_to_recs, baseline)
        if y_ucp_aligned and y_base:
            y_ucp_aligned = np.array(y_ucp_aligned)
            y_base = np.array(y_base)
            sort_idx = np.argsort(y_ucp_aligned)
            y_ucp_sorted = y_ucp_aligned[sort_idx]
            y_base_sorted = np.array(y_base)[sort_idx]
            n = len(y_ucp_sorted)
            x = np.arange(1, n + 1, dtype=float)
            ax.plot(x, y_base_sorted, "o-", color=BASELINE_COLOR, markersize=4, linewidth=1, label="Baseline", alpha=0.9)
            ax.plot(x, y_ucp_sorted, "s-", color=UCP_RECS_COLOR, markersize=4, linewidth=1, label="Merchant Agent", alpha=0.9)
        else:
            ax.plot(x, y_ucp_sorted, "s-", color=UCP_RECS_COLOR, markersize=4, linewidth=1, label="Merchant Agent")
    else:
        ax.plot(x, y_ucp_sorted, "s-", color=UCP_RECS_COLOR, markersize=4, linewidth=1, label="Merchant Agent")
    ax.legend(loc="lower right")
    ax.set_title("Mean similarity by query rank")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _scatter_lims_with_padding(
    x_vals: List[float],
    y_vals: List[float],
    padding_frac: float = 0.12,
    min_span: float = 0.2,
    floor: float = 0.0,
    ceiling: float = 1.08,
) -> Tuple[float, float]:
    """Compute axis limits from data with padding, clamped to [floor, ceiling]. Returns (lo, hi) for both axes."""
    if not x_vals and not y_vals:
        return floor, ceiling
    all_vals = list(x_vals) + list(y_vals)
    lo, hi = float(np.min(all_vals)), float(np.max(all_vals))
    span = max(hi - lo, min_span)
    pad = span * padding_frac
    lo = max(floor, lo - pad)
    hi = min(ceiling, hi + pad)
    return lo, hi


def plot_ucp_vs_baseline(
    ucp_to_recs: List[Dict],
    baseline: List[Dict],
    out_path: Path,
    metric: str = "mean_soft_similarity",
) -> None:
    """Scatter: x=baseline metric, y=Merchant Agent metric. Points above y=x indicate Merchant Agent better."""
    y_ucp, x_base, _ = align_by_query_id(ucp_to_recs, baseline)
    if metric != "mean_soft_similarity":
        by_ucp = {r["query_id"]: r for r in ucp_to_recs}
        by_base = {r["query_id"]: r for r in baseline}
        common = sorted(by_ucp.keys() & by_base.keys())
        y_ucp = [by_ucp[q].get(metric) for q in common]
        x_base = [by_base[q].get(metric) for q in common]
        valid = [(u, b) for u, b in zip(y_ucp, x_base) if isinstance(u, (int, float)) and isinstance(b, (int, float))]
        if not valid:
            return
        y_ucp, x_base = [x[0] for x in valid], [x[1] for x in valid]
    if not y_ucp or not x_base:
        return
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.scatter(x_base, y_ucp, c=UCP_RECS_COLOR, alpha=0.7, s=40, edgecolors="white", linewidths=0.5)
    lo, hi = _scatter_lims_with_padding(x_base, y_ucp)
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    # Diagonal over visible range
    ax.plot([lo, hi], [lo, hi], "--", color=DIAGONAL_COLOR, linewidth=1.5, label="Merchant Agent = Baseline")
    xlabel = "Baseline mean similarity" if metric == "mean_soft_similarity" else f"Baseline {metric}"
    ylabel = "Merchant Agent mean similarity" if metric == "mean_soft_similarity" else f"Merchant Agent {metric}"
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_aspect("equal")
    ax.legend(loc="lower right")
    ax.set_title("Merchant Agent vs Baseline (above line = Merchant Agent better)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_shopping_agent_vs_baseline(
    query_to_ucp: List[Dict],
    query_to_ucp_baseline: List[Dict],
    out_path: Path,
) -> None:
    """Scatter: x=Baseline (query→UCP) expected_ucp_match, y=Shopping Agent expected_ucp_match. Above y=x = Shopping Agent better."""
    y_agent, x_base, _ = align_ucp_match_by_query_id(query_to_ucp, query_to_ucp_baseline)
    if not y_agent or not x_base:
        return
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.scatter(x_base, y_agent, c=UCP_RECS_COLOR, alpha=0.7, s=40, edgecolors="white", linewidths=0.5)
    lo, hi = _scatter_lims_with_padding(x_base, y_agent)
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.plot([lo, hi], [lo, hi], "--", color=DIAGONAL_COLOR, linewidth=1.5, label="Shopping Agent = Baseline")
    ax.set_xlabel("Baseline (query→UCP) UCP match rate")
    ax.set_ylabel("Shopping Agent UCP match rate")
    ax.set_aspect("equal")
    ax.legend(loc="lower right")
    ax.set_title("Shopping Agent vs Baseline (above line = Shopping Agent better)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def write_case_report(
    summary: Dict,
    query_to_ucp: List[Dict],
    ucp_to_recs: List[Dict],
    baseline: Optional[List[Dict]],
    out_path: Path,
    query_to_ucp_baseline: Optional[List[Dict]] = None,
) -> None:
    """Write case_report.md from the same data used for plots."""
    agg = summary.get("aggregates") or {}
    n = summary.get("num_queries", 0)
    skipped = summary.get("num_skipped_insufficient_ground_truth", 0)
    q2u = agg.get("query_to_ucp") or {}
    q2u_base = agg.get("query_to_ucp_baseline") or {}
    ucp = agg.get("ucp_to_recs") or {}
    base = agg.get("baseline") or {}

    lines = [
        "# Recommendations Evaluation: Case Report",
        "",
        "This report summarizes the **recommendation pipeline** evaluation: Shopping Agent (query→UCP) match rate, "
        "Merchant Agent (UCP→recs) hard pass rate and mean similarity, and baseline comparison.",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"- **Queries evaluated:** {n}",
        f"- **Skipped (insufficient ground truth):** {skipped}",
        "",
        "### Shopping Agent (query→UCP)",
        f"- **Mean UCP match rate:** {(q2u.get('mean_ucp_match_rate') or 0):.3f}",
        "",
    ]
    if q2u_base:
        lines.extend([
            "### Baseline (query→UCP)",
            f"- **Mean UCP match rate:** {(q2u_base.get('mean_ucp_match_rate') or 0):.3f}",
            "",
        ])
    lines.extend([
        "### Merchant Agent (UCP→recs)",
        f"- **Mean hard pass rate:** {(ucp.get('mean_hard_pass_rate') or 0):.3f}",
        f"- **Mean similarity:** {(ucp.get('mean_soft_similarity') or 0):.3f}",
        "",
    ])
    if base:
        lines.extend([
            "### Baseline (recs)",
            f"- **Mean hard pass rate:** {(base.get('mean_hard_pass_rate') or 0):.3f}",
            f"- **Mean similarity:** {(base.get('mean_soft_similarity') or 0):.3f}",
            "",
        ])
    lines.extend([
        "---",
        "",
        "## Queries with lowest UCP match rate (Shopping Agent)",
        "",
    ])
    # Sort by expected_ucp_match ascending, show bottom 5
    q2u_sorted = sorted(query_to_ucp, key=lambda r: r.get("expected_ucp_match") or 1.0)
    for r in q2u_sorted[:5]:
        qid = r.get("query_id", "")
        match = r.get("expected_ucp_match")
        match_str = f"{match:.3f}" if isinstance(match, (int, float)) else "—"
        query = (r.get("query") or "")[:120]
        if len((r.get("query") or "")) > 120:
            query += "..."
        lines.append(f"### {qid}")
        lines.append("")
        lines.append(f"**Query:** {query}")
        lines.append("")
        lines.append(f"**Expected UCP match:** {match_str}")
        lines.append("")
    lines.extend([
        "---",
        "",
        "## Queries with zero hard pass rate (Merchant Agent)",
        "",
        "These queries had no recommended products satisfying all hard constraints from expected_ucp.",
        "",
    ])
    zero_hard = [r for r in ucp_to_recs if (r.get("hard_pass_rate") or 0) == 0]
    for r in zero_hard[:8]:
        qid = r.get("query_id", "")
        sim = r.get("mean_soft_similarity")
        sim_str = f"{sim:.3f}" if isinstance(sim, (int, float)) else "—"
        query = (r.get("query") or "")[:100]
        if len((r.get("query") or "")) > 100:
            query += "..."
        lines.append(f"- **{qid}** — mean similarity: {sim_str}. \"{query}\"")
    lines.extend([
        "",
        "---",
        "",
        "## Patterns",
        "",
        "1. **Hard pass rate** is the fraction of recommended products that satisfy *all* hard constraints in "
        "expected_ucp (e.g. price_max_cents, brand, min_ram_gb, min/max_screen_size, good_for_*). It is often 0 "
        "because search (with relaxation) may return products that miss one or more of these constraints. "
        "Improving filter application or catalog coverage would increase hard pass rate.",
        "",
        "2. **Mean similarity** measures how close recommended products are to the ground-truth set in "
        "embedding space (1 = closest). Merchant Agent typically outperforms the baseline when the Shopping Agent "
        "produces better-aligned filters.",
        "",
        "3. **Shopping Agent (query→UCP) match rate** is key-wise agreement between produced filters and expected_ucp; mismatches "
        "often come from brand/screen/ram interpretation (e.g. agent says 14.0–14.5\", expected 13.5–14.5\").",
        "",
    ])
    if query_to_ucp_baseline:
        q2u_base_sorted = sorted(query_to_ucp_baseline, key=lambda r: r.get("expected_ucp_match") or 1.0)
        lines.extend([
            "---",
            "",
            "## Baseline (query→UCP): lowest UCP match queries",
            "",
        ])
        for r in q2u_base_sorted[:5]:
            qid = r.get("query_id", "")
            match = r.get("expected_ucp_match")
            match_str = f"{match:.3f}" if isinstance(match, (int, float)) else "—"
            query = (r.get("query") or "")[:100]
            if len((r.get("query") or "")) > 100:
                query += "..."
            lines.append(f"- **{qid}** — {match_str}. \"{query}\"")
        lines.append("")
    lines.extend([
        "---",
        "",
        "*Report generated from recommendation eval results (summary, query_to_ucp, ucp_to_recs, baseline, query_to_ucp_baseline).*",
        "",
    ])
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate PNG figures from recommendation eval results"
    )
    parser.add_argument(
        "--summary",
        default=None,
        help="Path to summary JSON (default: latest results/summary_*.json)",
    )
    parser.add_argument(
        "--outdir",
        default=str(RESULTS_DIR),
        help="Directory to write PNG files (default: results)",
    )
    args = parser.parse_args()

    summary_path = Path(args.summary) if args.summary else find_latest_summary(RESULTS_DIR)
    if not summary_path or not summary_path.exists():
        print("Error: No summary file found. Run evaluation first.", file=sys.stderr)
        print("  python -m evaluation.recommendations.run_evaluation", file=sys.stderr)
        return 1

    try:
        summary, query_to_ucp, ucp_to_recs, baseline, query_to_ucp_baseline = load_results_for_summary(summary_path)
    except FileNotFoundError as e:
        print(f"Error: Missing result file for timestamp: {e}", file=sys.stderr)
        return 1

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # 1) Summary table (includes Baseline query→UCP when present)
    table_path = outdir / "recommendations_summary_table.png"
    plot_summary_table(summary, table_path)
    print(f"Saved {table_path}")

    # 2) Scores by query (Merchant Agent / recs)
    scores_path = outdir / "recommendations_scores_by_query.png"
    plot_scores_by_query(ucp_to_recs, baseline, scores_path)
    print(f"Saved {scores_path}")

    # 3) Shopping Agent vs Baseline (query→UCP) scatter
    if query_to_ucp_baseline:
        shopping_scatter_path = outdir / "recommendations_shopping_agent_vs_baseline.png"
        plot_shopping_agent_vs_baseline(query_to_ucp, query_to_ucp_baseline, shopping_scatter_path)
        print(f"Saved {shopping_scatter_path}")

    # 4) Merchant Agent vs Baseline (recs) scatter
    if baseline:
        scatter_path = outdir / "recommendations_ucp_vs_baseline.png"
        plot_ucp_vs_baseline(ucp_to_recs, baseline, scatter_path)
        print(f"Saved {scatter_path}")

    # 5) Case report (markdown)
    report_path = _THIS_DIR / "case_report.md"
    write_case_report(summary, query_to_ucp, ucp_to_recs, baseline, report_path, query_to_ucp_baseline=query_to_ucp_baseline)
    print(f"Saved {report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
