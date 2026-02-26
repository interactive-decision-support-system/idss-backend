#!/usr/bin/env python3
"""
Agent Pipeline Latency Visualization
=====================================
Generates a poster-quality PNG showing per-phase latency for the IDSS
Shopping Agent pipeline.

Outputs:
  agent_latency_chart.png   — annotated horizontal bar chart (main figure)
  agent_latency_table.png   — clean table for poster panel

Usage:
  python scripts/agent_latency_chart.py
  python scripts/agent_latency_chart.py --from-json   # load agent_latency_results.json
"""
import argparse
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import numpy as np

# ── Brand colours ──────────────────────────────────────────────────────────────
STANFORD_RED  = "#8C1515"
STANFORD_DARK = "#2D2926"

# ── Per-section colour palette (muted academic) ────────────────────────────────
SEC_COLORS = {
    "A": "#4C72B0",   # slate blue    — Session
    "B": "#7B4FAA",   # purple        — Domain detection
    "C": "#C44E52",   # crimson       — LLM calls
    "D": "#55A868",   # teal-green    — Search / DB / Redis
    "E": "#8C8C8C",   # medium grey   — Local CPU
}

# ── Benchmark data ─────────────────────────────────────────────────────────────
# Source: run_agent_latency_benchmark.py  (n=5, gpt-4o-mini, Supabase + Redis)
# Columns: (label, mean_ms, median_ms, p95_ms, section_key, note)
PHASES = [
    # Section A — Session layer
    ("Session lookup\n(existing)", 0.04, 0.03, 0.05, "A", "Redis GET"),
    ("Session creation\n(new)",    9,    9,    10,   "A", "Redis SET"),

    # Section B — Domain detection
    ("Domain detect\n(fast path)", 0.04, 0.03, 0.05, "B", "keyword dict, 0 LLM"),
    ("Domain detect\n(LLM)",       2298, 1982, 2355, "B", "gpt-4o-mini structured"),

    # Section C — LLM agent calls
    ("Criteria\nextraction",        1636, 1598, 1600, "C", "ExtractedCriteria schema"),
    ("Question\ngeneration",        1530, 1504, 1614, "C", "GeneratedQuestion schema"),
    ("Post-rec intent\ndetect",     1588, 1600, 1603, "C", "LLM classification"),
    ("Rec explanation\n(text)",     1476, 1480, 1493, "C", "~200 output tokens"),
    ("Comparison\nnarrative",       1492, 1496, 1523, "C", "~400 output tokens"),
    ("Filter\nrefinement",          1620, 1629, 1696, "C", "RefinementClassification"),

    # Section D — Search / DB
    ("DB search\n(cache hit)",      6,    6,    6,   "D", "Redis GET + deserialise"),
    ("DB search\n(cache miss)",     137,  128,  131, "D", "Supabase PostgreSQL"),
    ("KG re-rank\noverlay",         2,    2,    2,   "D", "FAISS/graph lookup"),

    # Section E — Local CPU
    ("Brand\ndiversify",            0.02, 0.02, 0.02, "E", "O(n) interleave, n=12"),
    ("Product\nformat ×6",          0.10, 0.08, 0.09, "E", "Pydantic model mapping"),
    ("Filter\nconversion",          0.01, 0.01, 0.01, "E", "Regex, no I/O"),
]

SECTION_LABELS = {
    "A": "Session Layer",
    "B": "Domain Detection",
    "C": "Agent LLM Calls\n(gpt-4o-mini)",
    "D": "Search Layer",
    "E": "Local CPU\n(no I/O)",
}


def load_from_json(path: Path) -> None:
    """Override PHASES means from a saved agent_latency_results.json."""
    import json
    data = json.loads(path.read_text())
    phases_by_short = {p[0]: i for i, p in enumerate(PHASES)}
    mapping = {
        "A1 Session lookup — existing":     ("Session lookup\n(existing)",),
        "A2 Session creation — new":        ("Session creation\n(new)",),
        "B1 Domain detection — fast path":  ("Domain detect\n(fast path)",),
        "B2 Domain detection — LLM":        ("Domain detect\n(LLM)",),
        "C1 Criteria extraction":           ("Criteria\nextraction",),
        "C2 Question generation":           ("Question\ngeneration",),
        "C3 Post-rec intent detection":     ("Post-rec intent\ndetect",),
        "C4 Recommendation explanation":    ("Rec explanation\n(text)",),
        "C5 Comparison narrative":          ("Comparison\nnarrative",),
        "C6 Filter refinement":             ("Filter\nrefinement",),
        "D1 E-commerce search — cache hit": ("DB search\n(cache hit)",),
        "D2 E-commerce search — cache miss":("DB search\n(cache miss)",),
        "D3 KG re-ranking overlay":         ("KG re-rank\noverlay",),
    }
    for json_key, (phase_label,) in mapping.items():
        if json_key in data.get("phases", {}):
            stats = data["phases"][json_key]
            idx = next((i for i, p in enumerate(PHASES) if p[0] == phase_label), None)
            if idx is not None:
                old = list(PHASES[idx])
                old[1] = round(stats["mean"], 1)
                old[2] = round(stats["median"], 1)
                old[3] = round(stats["p95"], 1)
                PHASES[idx] = tuple(old)


# ══════════════════════════════════════════════════════════════════════════════
# Figure 1: Annotated horizontal bar chart
# ══════════════════════════════════════════════════════════════════════════════

def make_bar_chart(out_path: Path):
    # ── Layout ─────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(13, 8))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#FAFAFA")

    n = len(PHASES)
    y_positions = list(range(n - 1, -1, -1))   # top → bottom

    bars_mean = []
    section_spans = {}   # section → (y_min, y_max) in data coords

    FLOOR_MS = 0.05   # render sub-0.05ms bars as this so they're visible

    for i, (label, mean, median, p95, sec, note) in enumerate(PHASES):
        y = y_positions[i]
        color = SEC_COLORS[sec]
        display_val = max(mean, FLOOR_MS)

        # ── Bar ──────────────────────────────────────────────────────────────
        ax.barh(y, display_val, color=color, alpha=0.85,
                edgecolor="white", linewidth=0.8, height=0.72)

        # ── P95 whisker (if meaningfully different from mean) ──────────────
        if p95 > mean * 1.05 and p95 > FLOOR_MS * 2:
            ax.plot([display_val, max(p95, FLOOR_MS)], [y, y],
                    color=color, linewidth=2, alpha=0.5)
            ax.plot([max(p95, FLOOR_MS)], [y], marker="|",
                    color=color, markersize=8, alpha=0.7)

        # ── Value label ───────────────────────────────────────────────────
        if mean < FLOOR_MS * 1.5:
            value_str = "< 0.1 ms"
        elif mean >= 1000:
            value_str = f"{mean/1000:.2f} s"
        elif mean >= 1:
            value_str = f"{mean:.0f} ms"
        else:
            value_str = f"{mean:.2f} ms"

        ax.text(max(display_val, FLOOR_MS) * 1.15, y, value_str,
                va="center", ha="left", fontsize=9, color=STANFORD_DARK,
                fontweight="bold")

        bars_mean.append(display_val)

        # track section extents
        if sec not in section_spans:
            section_spans[sec] = [y, y]
        section_spans[sec][0] = min(section_spans[sec][0], y)
        section_spans[sec][1] = max(section_spans[sec][1], y)

    # ── Y-axis labels (phase names) ─────────────────────────────────────────
    ax.set_yticks(y_positions)
    ax.set_yticklabels([p[0] for p in PHASES], fontsize=9.5, color=STANFORD_DARK)
    ax.tick_params(axis="y", length=0)

    # ── X-axis (log scale) ──────────────────────────────────────────────────
    ax.set_xscale("log")
    ax.set_xlim(0.03, 8000)
    ax.set_xlabel("Latency (ms, log scale)", fontsize=11, color=STANFORD_DARK, labelpad=8)
    ax.xaxis.set_label_position("bottom")

    # Custom x-ticks at power-of-10 + a few helpers
    xticks = [0.05, 1, 10, 100, 1000, 3000]
    xlabels = ["< 0.1", "1", "10", "100", "1 000", "3 000"]
    ax.set_xticks(xticks)
    ax.set_xticklabels(xlabels, fontsize=9, color=STANFORD_DARK)
    ax.tick_params(axis="x", length=3, color="#CCCCCC")

    # ── Grid ────────────────────────────────────────────────────────────────
    ax.xaxis.grid(True, which="both", color="#E0E0E0", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)

    # ── Section bands (coloured stripe on left margin) ──────────────────────
    band_x_start = -2.8   # in log-data coords, use transform
    for sec, (y_lo, y_hi) in section_spans.items():
        color = SEC_COLORS[sec]
        # Shaded background band
        ax.axhspan(y_lo - 0.45, y_hi + 0.45, xmin=0, xmax=1,
                   color=color, alpha=0.06, zorder=0)
        # Left colour stripe
        stripe = FancyBboxPatch(
            (0.002, (y_lo - 0.45) / n),
            0.007,
            (y_hi - y_lo + 0.9) / n,
            transform=fig.transFigure,
            boxstyle="square,pad=0",
            color=color, alpha=0.9, clip_on=False,
        )
        fig.add_artist(stripe)

    # ── Section labels (right of colour stripe, left margin) ───────────────
    for sec, (y_lo, y_hi) in section_spans.items():
        y_center = (y_lo + y_hi) / 2
        color = SEC_COLORS[sec]
        ax.annotate(
            SECTION_LABELS[sec],
            xy=(0.012, (y_center + 0.5) / n),
            xycoords="figure fraction",
            fontsize=7.5, color=color, fontweight="bold",
            va="center", ha="left",
            rotation=90,
            annotation_clip=False,
        )

    # ── Vertical reference lines ─────────────────────────────────────────────
    for ref_ms, label, ls in [
        (137,  "Supabase cold\n137 ms",  "--"),
        (1500, "gpt-4o-mini\n~1 500 ms", ":"),
    ]:
        ax.axvline(ref_ms, color="#888888", linewidth=1.2, linestyle=ls, zorder=1)
        ax.text(ref_ms * 1.05, n - 0.3, label,
                fontsize=8, color="#666666", va="top", ha="left",
                style="italic")

    # ── Annotation: cache speedup ────────────────────────────────────────────
    # Find y positions for cache hit and miss rows
    miss_y = next(y_positions[i] for i, p in enumerate(PHASES) if "cache miss" in p[0])
    hit_y  = next(y_positions[i] for i, p in enumerate(PHASES) if "cache hit"  in p[0])
    ax.annotate(
        "×22 speedup\n(Redis vs. Supabase)",
        xy=(9, (miss_y + hit_y) / 2),
        xytext=(9, (miss_y + hit_y) / 2),
        fontsize=8, color=SEC_COLORS["D"],
        va="center", ha="left",
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=SEC_COLORS["D"],
                  alpha=0.9, linewidth=1),
    )

    # ── Annotation: LLM bottleneck callout ───────────────────────────────────
    llm_mid_y = next(y_positions[i] for i, p in enumerate(PHASES) if "Criteria" in p[0])
    ax.annotate(
        "OpenAI API\nbottleneck",
        xy=(1600, llm_mid_y),
        xytext=(4200, llm_mid_y + 2),
        arrowprops=dict(arrowstyle="->", color=STANFORD_RED, lw=1.2),
        fontsize=8.5, color=STANFORD_RED, fontweight="bold",
        va="center",
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=STANFORD_RED,
                  alpha=0.9, linewidth=1.2),
    )

    # ── Summary stat boxes (top-right) ────────────────────────────────────────
    stats_x = 0.66
    stats_y = 0.97
    summary_items = [
        (STANFORD_RED,   "LLM calls",   "1 476 – 2 298 ms"),
        (SEC_COLORS["D"],"Supabase cold","137 ms  (p95 131 ms)"),
        (SEC_COLORS["D"],"Redis hit",    "6 ms  (×22 faster)"),
        (SEC_COLORS["A"],"Session ops",  "< 10 ms"),
        (SEC_COLORS["E"],"CPU phases",   "< 0.1 ms each"),
    ]
    for k, (color, stat_label, stat_val) in enumerate(summary_items):
        box_y = stats_y - k * 0.062
        fig.text(stats_x, box_y, "●", color=color, fontsize=14,
                 transform=fig.transFigure, va="top")
        fig.text(stats_x + 0.022, box_y, f"{stat_label}:",
                 color=STANFORD_DARK, fontsize=8.5, fontweight="bold",
                 transform=fig.transFigure, va="top")
        fig.text(stats_x + 0.022, box_y - 0.030, stat_val,
                 color="#444444", fontsize=8,
                 transform=fig.transFigure, va="top")

    # ── Title ────────────────────────────────────────────────────────────────
    fig.suptitle(
        "IDSS Shopping Agent — Per-Phase Latency Breakdown",
        fontsize=14, fontweight="bold", color=STANFORD_RED,
        y=1.01,
    )
    ax.set_title(
        "Direct function-call measurements  ·  n = 5 per phase  ·  gpt-4o-mini  ·  Supabase + Upstash Redis",
        fontsize=8.5, color="#666666", pad=6,
    )

    # ── Footer ───────────────────────────────────────────────────────────────
    fig.text(0.5, -0.02,
             "Log-scale x-axis  ·  Whiskers show P95  ·  Sub-0.05 ms bars floored for visibility",
             ha="center", fontsize=7.5, color="#999999")

    # ── Spine styling ────────────────────────────────────────────────────────
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color("#CCCCCC")
        ax.spines[spine].set_linewidth(0.8)

    plt.tight_layout(rect=[0.03, 0, 0.99, 1])
    fig.savefig(out_path, dpi=300, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close()
    print(f"Saved: {out_path}")


# ══════════════════════════════════════════════════════════════════════════════
# Figure 2: Clean table PNG (for poster panel insert)
# ══════════════════════════════════════════════════════════════════════════════

def make_table_png(out_path: Path):
    # ── Data — compact rows, one per logical group ──────────────────────────
    TABLE_ROWS = [
        # (section_label, operation, mean_ms_str, median_ms_str, p95_ms_str, sec_key)
        ("A. Session",     "Session lookup (existing)",      "< 0.1",  "< 0.1", "< 0.1", "A"),
        ("",               "Session creation (new)",         "9",      "9",     "10",    "A"),
        ("B. Domain",      "Fast-path (keyword dict)",       "< 0.1",  "< 0.1", "< 0.1", "B"),
        ("",               "LLM detection (gpt-4o-mini)",    "2 298",  "1 982", "2 355", "B"),
        ("C. LLM Calls",   "Criteria extraction",            "1 636",  "1 598", "1 600", "C"),
        ("",               "Question generation",            "1 530",  "1 504", "1 614", "C"),
        ("",               "Post-rec intent detect",         "1 588",  "1 600", "1 603", "C"),
        ("",               "Rec explanation (~200 tok)",     "1 476",  "1 480", "1 493", "C"),
        ("",               "Comparison narrative (~400 tok)","1 492",  "1 496", "1 523", "C"),
        ("",               "Filter refinement",              "1 620",  "1 629", "1 696", "C"),
        ("D. Search",      "Cache hit (Redis GET)",           "6",      "6",     "6",    "D"),
        ("",               "Cache miss (Supabase DB)",        "137",    "128",   "131",  "D"),
        ("",               "KG re-rank overlay",              "2",      "2",     "2",    "D"),
        ("E. CPU",         "Brand diversify / format / parse","< 0.1", "< 0.1", "< 0.1","E"),
    ]

    n_rows = len(TABLE_ROWS)
    n_cols = 5
    col_labels = ["Section", "Operation", "Mean (ms)", "Median (ms)", "P95 (ms)"]

    fig_h = 0.42 * n_rows + 1.2
    fig, ax = plt.subplots(figsize=(11, fig_h))
    fig.patch.set_facecolor("white")
    ax.axis("off")

    # ── Build cell arrays ────────────────────────────────────────────────────
    cell_text  = [[r[0], r[1], r[2], r[3], r[4]] for r in TABLE_ROWS]
    cell_colors = []
    for r in TABLE_ROWS:
        sec = r[5]
        base = matplotlib.colors.to_rgba(SEC_COLORS[sec], alpha=0.12)
        # Slightly darker for header-like "Section" cell
        sec_cell = matplotlib.colors.to_rgba(SEC_COLORS[sec], alpha=0.22 if r[0] else 0.08)
        cell_colors.append([
            sec_cell,
            base,
            base,
            base,
            base,
        ])

    tbl = ax.table(
        cellText=cell_text,
        colLabels=col_labels,
        loc="center",
        cellLoc="center",
        colWidths=[0.12, 0.36, 0.14, 0.14, 0.14],
        cellColours=cell_colors,
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9.5)
    tbl.scale(1.0, 1.9)

    # ── Header row styling ───────────────────────────────────────────────────
    for j in range(n_cols):
        cell = tbl[0, j]
        cell.set_facecolor(STANFORD_RED)
        cell.set_text_props(color="white", fontweight="bold", fontsize=10)
        cell.set_edgecolor("white")
        cell.set_linewidth(0.8)

    # ── Body cell styling ────────────────────────────────────────────────────
    for i, row in enumerate(TABLE_ROWS, start=1):
        sec = row[5]
        color = SEC_COLORS[sec]
        for j in range(n_cols):
            cell = tbl[i, j]
            cell.set_edgecolor("#DDDDDD")
            cell.set_linewidth(0.5)
            # Numeric columns: right-align
            if j >= 2:
                cell.set_text_props(ha="right", fontfamily="monospace")
            # Section label: bold + colour
            if j == 0 and row[0]:
                cell.set_text_props(color=color, fontweight="bold", fontsize=9)
            # Operation column: left-align
            if j == 1:
                cell.set_text_props(ha="left")

    ax.set_title(
        "IDSS Shopping Agent — Per-Phase Latency  (n=5 · gpt-4o-mini · Supabase + Redis)",
        fontsize=11, fontweight="bold", color=STANFORD_RED, pad=14,
    )

    # ── Legend dots ──────────────────────────────────────────────────────────
    legend_items = [
        mpatches.Patch(color=SEC_COLORS[s], label=lbl, alpha=0.7)
        for s, lbl in [
            ("A", "Session"), ("B", "Domain detection"),
            ("C", "LLM calls"), ("D", "Search / DB"), ("E", "Local CPU"),
        ]
    ]
    ax.legend(handles=legend_items, loc="lower center",
              bbox_to_anchor=(0.5, -0.04), ncol=5,
              fontsize=8.5, frameon=False)

    plt.tight_layout(pad=0.6)
    fig.savefig(out_path, dpi=300, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close()
    print(f"Saved: {out_path}")


# ══════════════════════════════════════════════════════════════════════════════
# Figure 3: Compact 2-column summary for poster (MCP-style)
# ══════════════════════════════════════════════════════════════════════════════

def make_summary_table(out_path: Path):
    """
    A compact summary table that mirrors the existing merchant_protocol_table.png style,
    but with colour-coded section separators.
    """
    SUMMARY_DATA = [
        # (section_label, operation_short, mean, p95)
        ("Session",    "Session lookup",        "< 0.1 ms", "< 0.1 ms", "A"),
        ("",           "Session creation",      "9 ms",     "10 ms",    "A"),
        ("Domain",     "Fast-path detection",   "< 0.1 ms", "< 0.1 ms", "B"),
        ("",           "LLM detection",         "2 298 ms", "2 355 ms", "B"),
        ("LLM Calls",  "Criteria extraction",   "1 636 ms", "1 600 ms", "C"),
        ("",           "Question generation",   "1 530 ms", "1 614 ms", "C"),
        ("",           "Post-rec intent",       "1 588 ms", "1 603 ms", "C"),
        ("",           "Rec explanation",       "1 476 ms", "1 493 ms", "C"),
        ("",           "Comparison narrative",  "1 492 ms", "1 523 ms", "C"),
        ("",           "Filter refinement",     "1 620 ms", "1 696 ms", "C"),
        ("Search",     "Cache hit (Redis)",     "6 ms",     "6 ms",     "D"),
        ("",           "Cache miss (Supabase)", "137 ms",   "131 ms",   "D"),
        ("",           "KG re-rank overlay",    "2 ms",     "2 ms",     "D"),
        ("CPU",        "All CPU phases",        "< 0.1 ms", "< 0.1 ms", "E"),
    ]

    n_rows = len(SUMMARY_DATA)
    col_labels = ["Section", "Operation", "Mean", "P95"]
    n_cols = len(col_labels)

    fig_h = 0.38 * n_rows + 1.2
    fig, ax = plt.subplots(figsize=(8, fig_h))
    fig.patch.set_facecolor("white")
    ax.axis("off")

    cell_text = [[r[0], r[1], r[2], r[3]] for r in SUMMARY_DATA]
    cell_colors = []
    for r in SUMMARY_DATA:
        sec = r[4]
        base  = matplotlib.colors.to_rgba(SEC_COLORS[sec], alpha=0.11)
        label_bg = matplotlib.colors.to_rgba(SEC_COLORS[sec], alpha=0.22 if r[0] else 0.06)
        cell_colors.append([label_bg, base, base, base])

    tbl = ax.table(
        cellText=cell_text,
        colLabels=col_labels,
        loc="center",
        cellLoc="center",
        colWidths=[0.13, 0.42, 0.20, 0.20],
        cellColours=cell_colors,
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9.5)
    tbl.scale(1.0, 2.0)

    # Header
    for j in range(n_cols):
        cell = tbl[0, j]
        cell.set_facecolor(STANFORD_RED)
        cell.set_text_props(color="white", fontweight="bold")
        cell.set_edgecolor("white")

    # Body
    for i, row in enumerate(SUMMARY_DATA, start=1):
        sec = row[4]
        for j in range(n_cols):
            cell = tbl[i, j]
            cell.set_edgecolor("#DDDDDD")
            cell.set_linewidth(0.5)
            if j in (2, 3):
                cell.set_text_props(ha="right", fontfamily="monospace")
            if j == 0 and row[0]:
                cell.set_text_props(color=SEC_COLORS[sec], fontweight="bold")
            if j == 1:
                cell.set_text_props(ha="left")

    ax.set_title(
        "AI Shopping Agent — Sub-Phase Latency",
        fontsize=12, fontweight="bold", color=STANFORD_RED, pad=14,
    )

    plt.tight_layout(pad=0.6)
    fig.savefig(out_path, dpi=300, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close()
    print(f"Saved: {out_path}")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-json", action="store_true",
                        help="Load means from agent_latency_results.json")
    parser.add_argument("--out-dir", default=".",
                        help="Output directory for PNGs (default: current dir)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.from_json:
        json_path = Path(__file__).parent.parent / "agent_latency_results.json"
        if json_path.exists():
            load_from_json(json_path)
            print(f"Loaded data from {json_path}")
        else:
            print(f"Warning: {json_path} not found, using hardcoded data", file=sys.stderr)

    make_bar_chart(out_dir / "agent_latency_chart.png")
    make_table_png(out_dir / "agent_latency_table.png")
    make_summary_table(out_dir / "agent_latency_summary.png")

    print("\nDone. Three PNGs generated:")
    print("  agent_latency_chart.png   — annotated bar chart (main poster figure)")
    print("  agent_latency_table.png   — full detailed table")
    print("  agent_latency_summary.png — compact 4-column table (MCP style)")


if __name__ == "__main__":
    main()
