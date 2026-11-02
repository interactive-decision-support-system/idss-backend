#!/usr/bin/env python3
"""
EXPLORATORY follow-ups to analyze.py (written after the labels were seen; not part
of the pre-specified plan). No model calls.

Usage:  python3 exploratory.py sheet_JH.csv sheet_GPT.csv

1. Paired condition differences. Each scenario contributes one item per condition,
   so differences are paired within scenario and CIs resample scenarios. Reported
   for the author's ratings, the LLM rater's ratings, and the stored s_judge.
2. Author vs LLM-rater agreement (Spearman rho, scenario-clustered CI, mean |diff|).
   The second sheet is an LLM, not a human, and is never averaged with the author.
3. Mean sub-scores per condition for each rater (the stored judge kept only totals).

OUTPUT
    analysis/judge_human_validation/exploratory.json
"""

from __future__ import annotations

import importlib.util
import json
import random
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "exploratory.json"
BOOT_N, BOOT_SEED = 2000, 20261101
PAIRS = [("MACS-Full", "GPT+Catalog"), ("MACS-Full", "Gemini+Catalog"),
         ("MACS-NoSession", "MACS-Full")]
SUBS = ["constraint_satisfaction_0_4", "preference_tracking_0_3", "response_appropriateness_0_3"]

spec = importlib.util.spec_from_file_location("an", HERE / "analyze.py")
an = importlib.util.module_from_spec(spec)
spec.loader.exec_module(an)


def boot(scs, stat):
    rng = random.Random(BOOT_SEED)
    draws = sorted(v for v in (stat([rng.choice(scs) for _ in scs]) for _ in range(BOOT_N)) if v == v)
    return [round(draws[int(0.025 * len(draws))], 3), round(draws[int(0.975 * len(draws)) - 1], 3)]


def main() -> int:
    items = json.loads((HERE / "key.json").read_text())["items"]
    author = an.read_sheet(Path(sys.argv[1]), items)
    llm = an.read_sheet(Path(sys.argv[2]), items)
    judge = {i: m["judge_score"] for i, m in items.items()}
    cell = {(m["scenario"], m["condition"]): i for i, m in items.items()}
    scs = sorted({m["scenario"] for m in items.values()})

    diffs = {}
    for a, b in PAIRS:
        for name, src in (("author", author), ("llm_rater", llm), ("stored_judge", judge)):
            f = lambda ss, src=src: st.mean(src[cell[(s, a)]] - src[cell[(s, b)]] for s in ss)
            diffs[f"{a} minus {b} | {name}"] = {"mean": round(f(scs), 3), "ci95": boot(scs, f)}

    by_sc = {}
    for i, m in items.items():
        by_sc.setdefault(m["scenario"], []).append(i)
    rho = lambda ss: an.spearman([author[i] for s in ss for i in by_sc[s]],
                                 [llm[i] for s in ss for i in by_sc[s]])
    agree = {"spearman_rho": round(rho(scs), 3), "ci95": boot(scs, rho),
             "mean_abs_diff": round(st.mean(abs(author[i] - llm[i]) for i in items), 3)}

    import csv
    subs = {}
    for name, path in (("author", sys.argv[1]), ("llm_rater", sys.argv[2])):
        rows = {r["item"]: r for r in csv.DictReader(open(path))}
        subs[name] = {c: {k: round(st.mean(float(rows[i][k]) for i, m in items.items()
                                             if m["condition"] == c), 2) for k in SUBS}
                      for c in sorted({m["condition"] for m in items.values()})}

    res = {"note": "exploratory; not pre-specified", "paired_differences": diffs,
           "author_vs_llm_rater": agree, "subscore_means": subs}
    OUT.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
