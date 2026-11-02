#!/usr/bin/env python3
"""
Compare human transcript ratings with the stored multi-turn LLM judge scores.
(no model calls)

Usage:  python3 analyze.py sheet_A.csv [sheet_B.csv]

ANALYSIS PLAN (fixed before any labels were collected, 2026-11-01)
    Unit: one transcript (40 items); the independent unit for uncertainty is the
    SCENARIO (each scenario contributes 4 items, one per condition), so CIs come
    from resampling scenarios with replacement.

    Primary:   Spearman rho between the human total (mean over annotators, /10)
               and the stored s_judge, with a scenario-clustered 95% bootstrap CI.
    Secondary: mean absolute difference (human/10 vs s_judge); per-condition mean
               human score vs mean s_judge and whether the two order the four
               conditions the same way; agreement of the pass verdict
               (s_MT >= 0.65) when s_judge is replaced by the human score;
               with two sheets, inter-annotator Spearman rho and mean |diff|.

    Interpretation, stated in advance:
      rho CI lower bound >= 0.5 and the same condition ordering
          -> the judge tracks human ratings on this sample; the ranking claim
             stands as "judge-rated, with human agreement rho = ...".
      rho point estimate < 0.3, or a different ordering of MACS-Full vs. the
          baselines -> the paper reports that the judge-based ranking is not
          supported by human ratings.
      anything in between -> reported as partial agreement, with the numbers.

OUTPUT
    analysis/judge_human_validation/human_judge_agreement.json
"""

from __future__ import annotations

import csv
import json
import random
import statistics as st
import sys
from pathlib import Path
from typing import Dict, List, Sequence

HERE = Path(__file__).resolve().parent
OUT = HERE / "human_judge_agreement.json"
BOOT_N, BOOT_SEED = 2000, 20261101
W_C, W_J, THRESHOLD = 0.55, 0.45, 0.65
FIELDS = [("constraint_satisfaction_0_4", 4), ("preference_tracking_0_3", 3),
          ("response_appropriateness_0_3", 3)]


def ranks(xs: Sequence[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    ra, rb = ranks(a), ranks(b)
    ma, mb = st.mean(ra), st.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else float("nan")


def read_sheet(path: Path, items: Dict[str, dict]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            subs = []
            for field, hi in FIELDS:
                v = float(row[field])
                assert 0 <= v <= hi, f"{path.name} {row['item']}: {field}={v} out of range"
                subs.append(v)
            total = float(row["total_0_10"]) if row["total_0_10"].strip() else sum(subs)
            assert abs(total - sum(subs)) < 1e-9, f"{path.name} {row['item']}: total != sum"
            out[row["item"]] = total / 10.0
    missing = set(items) - set(out)
    assert not missing, f"{path.name} is missing items: {sorted(missing)}"
    return out


def cluster_ci(items: Dict[str, dict], human: Dict[str, float], stat) -> List[float]:
    by_sc: Dict[int, List[str]] = {}
    for iid, meta in items.items():
        by_sc.setdefault(meta["scenario"], []).append(iid)
    scs = sorted(by_sc)
    rng = random.Random(BOOT_SEED)
    draws = []
    for _ in range(BOOT_N):
        ids = [i for s in (rng.choice(scs) for _ in scs) for i in by_sc[s]]
        v = stat(ids)
        if v == v:  # skip NaN (degenerate resample)
            draws.append(v)
    draws.sort()
    return [draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]]


def main() -> int:
    sheets = [Path(p) for p in sys.argv[1:]]
    assert 1 <= len(sheets) <= 2, "pass one or two filled sheets"
    items = json.loads((HERE / "key.json").read_text())["items"]
    per_ann = [read_sheet(p, items) for p in sheets]
    human = {i: st.mean(a[i] for a in per_ann) for i in items}
    judge = {i: items[i]["judge_score"] for i in items}

    rho_fn = lambda ids: spearman([human[i] for i in ids], [judge[i] for i in ids])
    ids = sorted(items)
    conds = sorted({m["condition"] for m in items.values()})
    cond_h = {c: st.mean(human[i] for i in ids if items[i]["condition"] == c) for c in conds}
    cond_j = {c: st.mean(judge[i] for i in ids if items[i]["condition"] == c) for c in conds}
    pass_agree = sum(
        (W_C * items[i]["constraint_score"] + W_J * human[i] >= THRESHOLD)
        == (W_C * items[i]["constraint_score"] + W_J * judge[i] >= THRESHOLD) for i in ids)

    res = {
        "n_items": len(ids), "n_annotators": len(per_ann),
        "primary_spearman_rho": round(rho_fn(ids), 3),
        "primary_rho_ci95_scenario_clustered": [round(x, 3) for x in cluster_ci(items, human, rho_fn)],
        "mean_abs_diff": round(st.mean(abs(human[i] - judge[i]) for i in ids), 3),
        "condition_mean_human": {c: round(v, 3) for c, v in cond_h.items()},
        "condition_mean_judge": {c: round(v, 3) for c, v in cond_j.items()},
        "condition_order_human": sorted(conds, key=lambda c: -cond_h[c]),
        "condition_order_judge": sorted(conds, key=lambda c: -cond_j[c]),
        "pass_verdict_agreement": f"{pass_agree}/{len(ids)}",
    }
    if len(per_ann) == 2:
        a, b = per_ann
        res["inter_annotator_spearman_rho"] = round(spearman([a[i] for i in ids], [b[i] for i in ids]), 3)
        res["inter_annotator_mean_abs_diff"] = round(st.mean(abs(a[i] - b[i]) for i in ids), 3)
    OUT.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
