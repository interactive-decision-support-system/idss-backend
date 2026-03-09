#!/usr/bin/env python3
"""
Generate IDSS architecture diagram as PNG.
Flow: User → AI Agent → Merchant Agent (Neo4j KG, Supabase, Redis) → User UI
"""
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

# Colors
CARDINAL = "#8C1515"
DARK = "#2E2D29"
ARROW = "#4A4A4A"
BOX_FILL = "#FAFAFA"


def arrow(ax, x1, y1, x2, y2, label=None):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="->", color=ARROW, lw=2))
    if label:
        mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mid_x, mid_y, label, ha="center", va="center", fontsize=8,
                color=ARROW, style="italic",
                bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.9))


def box(ax, x, y, w, h, title, subtitle=None):
    rect = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                          facecolor=BOX_FILL, edgecolor=CARDINAL, linewidth=1.5)
    ax.add_patch(rect)
    ax.text(x + w/2, y + h/2 + (0.08 if subtitle else 0), title,
            ha="center", va="center", fontsize=10, fontweight="bold", color=DARK)
    if subtitle:
        ax.text(x + w/2, y + h/2 - 0.12, subtitle, ha="center", va="center", fontsize=8, color=ARROW)


def main():
    fig, ax = plt.subplots(figsize=(9, 10))
    ax.set_xlim(0, 9)
    ax.set_ylim(0, 10)
    ax.axis("off")
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")

    # 1. USER (top)
    box(ax, 3.5, 9.0, 2, 0.5, "USER")
    # 2. AI Shopping Agent
    box(ax, 3.0, 7.2, 3, 0.7, "AI Shopping Agent", "(IDSS)")
    arrow(ax, 4.5, 9.0, 4.5, 7.9, "natural language")
    # 3. Tool call label
    ax.text(4.5, 6.6, "structured tool call (MCP or UCP)", ha="center", va="center",
            fontsize=9, color=ARROW, style="italic")
    arrow(ax, 4.5, 7.2, 4.5, 6.8)
    # 4. Merchant Agent (big container)
    mx, my, mw, mh = 1.2, 2.0, 6.6, 4.2
    rect = FancyBboxPatch((mx, my), mw, mh, boxstyle="round,pad=0.03,rounding_size=0.2",
                          facecolor="#F8F8F8", edgecolor=CARDINAL, linewidth=2)
    ax.add_patch(rect)
    ax.text(mx + mw/2, my + mh - 0.35, "Merchant Agent", ha="center", va="center",
            fontsize=12, fontweight="bold", color=CARDINAL)
    # 5. Inner: Neo4j KG
    box(ax, 2.0, 3.0, 1.6, 1.0, "Neo4j", "KG")
    # 6. Inner: Supabase Products
    box(ax, 3.9, 3.0, 1.8, 1.0, "Supabase", "Products")
    # 7. Inner: Redis cache
    box(ax, 6.0, 3.0, 1.6, 1.0, "Redis", "cache")
    # Neo4j <-> Supabase (bidirectional)
    ax.annotate("", xy=(3.8, 3.5), xytext=(3.6, 3.5),
                arrowprops=dict(arrowstyle="<->", color=ARROW, lw=1.2))
    # Supabase -> Redis
    ax.annotate("", xy=(5.9, 3.5), xytext=(5.7, 3.5),
                arrowprops=dict(arrowstyle="->", color=ARROW, lw=1.2))
    # 8. Arrow from tool call down to Merchant
    arrow(ax, 4.5, 6.4, 4.5, 6.2)
    # 9. Results label
    ax.text(4.5, 1.5, "ranked + diversified results", ha="center", va="center",
            fontsize=9, color=ARROW, style="italic")
    arrow(ax, 4.5, 2.0, 4.5, 1.7)
    # 10. USER UI (bottom)
    box(ax, 3.5, 0.4, 2, 0.5, "USER UI")
    arrow(ax, 4.5, 1.5, 4.5, 0.9)

    # Footnote
    ax.text(4.5, 0.1, "Agent cannot query KG directly; only MCP/UCP tool calls. Data sovereignty retained.",
            ha="center", va="center", fontsize=7, color=ARROW, style="italic")

    plt.tight_layout()
    out = "architecture_diagram.png"
    plt.savefig(out, bbox_inches="tight", dpi=300, facecolor="white")
    plt.close()
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
