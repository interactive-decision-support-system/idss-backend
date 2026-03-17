#!/usr/bin/env python3
"""
Build a 50-item golden dataset with simpler/derivative queries so that after
populate_ground_truth.py each item gets >= min_ground_truth_ids (e.g. 3).

- Keeps items that already have >= 3 ground_truth_product_ids (query + expected_ucp).
- Replaces items with 0 or <3 with simpler derivatives: shorter query + relaxed expected_ucp
  (no brand, higher price_max if very low, no min_ram/min_screen or relaxed, drop strict good_for_*).
Output: golden_dataset.json with ground_truth_product_ids cleared (run populate_ground_truth next).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
DEFAULT_GOLDEN = _THIS_DIR / "golden_dataset.json"
MIN_KEEP = 3  # keep item as-is if it has this many ground truth IDs


def _soft_prefs(use_case: str = "general") -> dict:
    return {"use_case": use_case, "liked_features": []}


def _relax_ucp(ucp: dict, *drops: str) -> dict:
    out = {k: v for k, v in ucp.items() if k not in drops and not k.startswith("_")}
    out["_soft_preferences"] = ucp.get("_soft_preferences") or _soft_prefs()
    return out


def _relax_and_raise_price(ucp: dict, new_max_cents: int, drops: tuple = ()) -> dict:
    out = _relax_ucp(ucp, *drops)
    if "price_max_cents" in out and out["price_max_cents"] < new_max_cents:
        out["price_max_cents"] = new_max_cents
    return out


def _minimal_ucp(ucp: dict, price_max_cents: int = 200000) -> dict:
    """Keep only category, product_type, price_max_cents so catalog filter passes many products."""
    return {
        "category": ucp.get("category", "Electronics"),
        "product_type": ucp.get("product_type", "laptop"),
        "price_max_cents": min(price_max_cents, ucp.get("price_max_cents", 200000)),
        "_soft_preferences": ucp.get("_soft_preferences") or _soft_prefs(),
    }


# Simplified derivatives: (short user_query, expected_ucp builder).
# Use _minimal_ucp so hard constraints pass many catalog products and populate_ground_truth yields >= 3 per item.
DERIVATIVES = {
    "laptop_2": ("Laptop under $800 for college and light gaming, 14–15 inch.", lambda u: _minimal_ucp(u, 80000)),
    "laptop_3": ("Laptop for coding and light creative work, up to $4000, 14-inch class.", lambda u: _minimal_ucp(u, 400000)),
    "laptop_4": ("Laptop for statistics and light gaming, $1000–$1500, USA.", lambda u: _minimal_ucp(u, 150000)),
    "laptop_6": ("Laptop under $600 for video editing and streaming.", lambda u: _minimal_ucp(u, 60000)),
    "laptop_8": ("Gaming laptop under $1000 in the US.", lambda u: _minimal_ucp(u, 100000)),
    "laptop_9": ("Budget laptop under $500 for office and web, 15-inch preferred.", lambda u: _minimal_ucp(u, 50000)),
    "laptop_10": ("Laptop under $1000 for school, CAD, and light gaming.", lambda u: _minimal_ucp(u, 100000)),
    "laptop_11": ("Gaming laptop under $650, 15-inch, US.", lambda u: _minimal_ucp(u, 65000)),
    "laptop_12": ("Gaming laptop around $2000, 16-inch, US.", lambda u: _minimal_ucp(u, 200000)),
    "laptop_14": ("Laptop under $1000 for school and light gaming, US.", lambda u: _minimal_ucp(u, 100000)),
    "laptop_15": ("College laptop under $1500 for coding and light gaming, 14–15 inch.", lambda u: _minimal_ucp(u, 150000)),
    "laptop_16": ("Lightweight laptop under $1000, ~13 inch, for classes and light gaming.", lambda u: _minimal_ucp(u, 100000)),
    "laptop_19": ("Laptop $700–800 for college, Roblox and Discord, non-Apple.", lambda u: _minimal_ucp(u, 80000)),
    "laptop_20": ("15–16 inch laptop under $2000 for CAD and light gaming, US.", lambda u: _minimal_ucp(u, 200000)),
    "laptop_21": ("Windows 2-in-1 under $700 for college and light gaming, US.", lambda u: _minimal_ucp(u, 70000)),
    "laptop_22": ("MacBook under $3000 for software development, 16-inch.", lambda u: _minimal_ucp(u, 300000)),
    "laptop_23": ("MacBook for professional and creative work.", lambda u: _minimal_ucp(u, 300000)),
    "laptop_24": ("Laptop $1500–2000, strong CPU and battery, 14–16 inch, light gaming.", lambda u: _minimal_ucp(u, 200000)),
    "laptop_25": ("Gaming laptop $2000–2500, some schoolwork.", lambda u: _minimal_ucp(u, 250000)),
    "laptop_26": ("Budget 2-in-1 under $500 for photo editing and note-taking, US.", lambda u: _minimal_ucp(u, 50000)),
    "laptop_28": ("Laptop under $1200 for school and gaming (Minecraft, Valorant), 16GB RAM.", lambda u: _minimal_ucp(u, 120000)),
    "laptop_29": ("Used laptop under $1000, 14–16 inch, gaming and music.", lambda u: _minimal_ucp(u, 100000)),
    "laptop_30": ("Laptop for electrical engineering, CAD and programming, $1500.", lambda u: _minimal_ucp(u, 150000)),
    "laptop_31": ("Laptop under $1000 for work, Minecraft, Roblox, and Blender.", lambda u: _minimal_ucp(u, 100000)),
    "laptop_32": ("2-in-1 with pen support for drawing and light gaming, $2000–2400.", lambda u: _minimal_ucp(u, 240000)),
    "laptop_33": ("Portable laptop $1200–1500 for competitive FPS gaming and light editing.", lambda u: _minimal_ucp(u, 150000)),
    "laptop_34": ("Laptop with long battery for lectures and multitasking, US.", lambda u: _minimal_ucp(u, 200000)),
    "laptop_35": ("14-inch laptop under $2000 for engineering student, CAD and light gaming.", lambda u: _minimal_ucp(u, 200000)),
    "laptop_36": ("Cheapest laptop around $300 for light gaming, plugged in.", lambda u: _minimal_ucp(u, 35000)),
    "laptop_37": ("Windows laptop around $1500 for Photoshop and light Steam gaming, 15-inch.", lambda u: _minimal_ucp(u, 150000)),
    "laptop_38": ("Laptop around $1500 for CAD and 1080p gaming, portable.", lambda u: _minimal_ucp(u, 150000)),
    "laptop_39": ("Laptop for dev work (Python, Docker), 14–16 inch, up to $2500, Linux OK.", lambda u: _minimal_ucp(u, 250000)),
    "laptop_42": ("Portable 11–13 inch 2-in-1 for creative work, under $1500.", lambda u: _minimal_ucp(u, 150000)),
    "laptop_44": ("14-inch laptop under $3000, 32GB RAM, for office and video editing.", lambda u: _minimal_ucp(u, 300000)),
    "laptop_45": ("Laptop under $800 for video editing and school, 16GB RAM, 15-inch.", lambda u: _minimal_ucp(u, 80000)),
    "laptop_46": ("Small laptop under $600 for light gaming on the go.", lambda u: _minimal_ucp(u, 60000)),
    "laptop_49": ("Gaming laptop under $1700 for college, 24–32GB RAM, Helldivers 2.", lambda u: _minimal_ucp(u, 170000)),
    "laptop_50": ("15–17 inch laptop under $1000 for office and basic photo editing, US.", lambda u: _minimal_ucp(u, 100000)),
}


def main() -> int:
    golden_path = _THIS_DIR / "golden_dataset.json"
    if not golden_path.is_file():
        print(f"Error: {golden_path} not found", file=sys.stderr)
        return 1

    with open(golden_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    out = []
    for it in items:
        qid = it["query_id"]
        gt_ids = it.get("ground_truth_product_ids") or []
        keep_as_is = len(gt_ids) >= MIN_KEEP

        if keep_as_is and qid not in DERIVATIVES:
            out.append({
                "query_id": it["query_id"],
                "user_query": it["user_query"],
                "expected_ucp": it["expected_ucp"],
                "ground_truth_product_ids": [],
            })
            continue

        if qid in DERIVATIVES:
            short_query, ucp_builder = DERIVATIVES[qid]
            new_ucp = ucp_builder(it["expected_ucp"])
            out.append({
                "query_id": qid,
                "user_query": short_query,
                "expected_ucp": new_ucp,
                "ground_truth_product_ids": [],
            })
        else:
            # Keep item but clear ground truth for repopulation
            out.append({
                "query_id": it["query_id"],
                "user_query": it["user_query"],
                "expected_ucp": it["expected_ucp"],
                "ground_truth_product_ids": [],
            })

    with open(golden_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print(f"Wrote {len(out)} items to {golden_path} (ground_truth_product_ids cleared). Run populate_ground_truth.py next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
