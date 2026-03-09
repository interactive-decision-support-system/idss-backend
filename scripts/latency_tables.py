#!/usr/bin/env python3
"""
Generate latency tables as PNG images for poster/paper.

Usage:
  python scripts/latency_tables.py

Outputs:
  merchant_protocol_table.png  - Merchant Protocol Layer (6 ops)
  ai_agent_table.png          - AI Shopping Agent Layer (7 ops)
  latency_table_all.png       - Combined table (13 ops)

Words fit within table cells (no overflow).
"""
import pandas as pd
import matplotlib.pyplot as plt

# Shorter labels to prevent overflow
MERCHANT_DATA = [
    ["Semantic search", 172, 124, 578],
    ["SQL search", 134, 132, 147],
    ["Get product", 105, 103, 114],
    ["Best value", "< 1", "< 1", "< 1"],
    ["Add to cart", 178, 136, 559],
    ["Zero-stock", 150, 110, 510],
]
AGENT_DATA = [
    ["Agent chat", 2102, 2118, 3685],
    ["Find similar", 2665, 1748, 11655],
    ["LLM detection", 2298, 1982, 2355],
    ["Criteria extraction", 1635, 1598, 1599],
    ["Question generation", 1530, 1503, 1614],
    ["Filter refinement", 1619, 1629, 1695],
    ["Comparison narrative", 1491, 1496, 1522],
]
ALL_DATA = MERCHANT_DATA + AGENT_DATA

COLUMNS = ["Operation", "Mean (ms)", "Median (ms)", "P95 (ms)"]


def _make_table(ax, df, title, col_widths=None):
    """Render table with proper column widths to avoid overflow."""
    ax.axis("off")
    tbl = ax.table(
        cellText=df.values,
        colLabels=df.columns,
        loc="center",
        cellLoc="center",
        colWidths=col_widths or [0.4, 0.2, 0.2, 0.2],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(11)
    tbl.scale(1.0, 2.2)
    for (i, j), cell in tbl.get_celld().items():
        cell.set_linewidth(0.5)
    ax.set_title(title, fontsize=13, fontweight="bold", color="#8C1515", pad=28)


def main():
    merchant_df = pd.DataFrame(MERCHANT_DATA, columns=COLUMNS)
    agent_df = pd.DataFrame(AGENT_DATA, columns=COLUMNS)
    all_df = pd.DataFrame(ALL_DATA, columns=COLUMNS)

    # Table 1: Merchant Protocol Layer
    fig1, ax1 = plt.subplots(figsize=(7, 3.2))
    _make_table(ax1, merchant_df, "Merchant Protocol Layer")
    plt.tight_layout(pad=2.0)
    plt.savefig("merchant_protocol_table.png", bbox_inches="tight", dpi=300, pad_inches=0.15)
    plt.close()
    print("Saved: merchant_protocol_table.png")

    # Table 2: AI Agent Layer (7 component metrics)
    fig2, ax2 = plt.subplots(figsize=(7, 4.2))
    _make_table(ax2, agent_df, "AI Shopping Agent Layer")
    plt.tight_layout(pad=2.0)
    plt.savefig("ai_agent_table.png", bbox_inches="tight", dpi=300, pad_inches=0.15)
    plt.close()
    print("Saved: ai_agent_table.png")

    # Table 3: Combined (merchant + agent, 13 ops)
    fig3, ax3 = plt.subplots(figsize=(7, 5.5))
    _make_table(ax3, all_df, "Operation Latency (All)")
    plt.tight_layout(pad=2.0)
    plt.savefig("latency_table_all.png", bbox_inches="tight", dpi=300, pad_inches=0.15)
    plt.close()
    print("Saved: latency_table_all.png")


if __name__ == "__main__":
    main()
