#!/usr/bin/env python3
"""
Does the multi-turn verification signal actually discriminate? (no new model calls)

QUESTION
    The multi-turn score is s_MT = 0.55*s_constraint + 0.45*s_judge, which looks
    like a majority-deterministic, environment-grounded metric. This script asks
    whether that is true in practice, by measuring how much each component
    actually varies and how much of the composite's variance each contributes.

WHY IT MATTERS
    If s_constraint saturates at its ceiling, it cannot separate systems no
    matter what weight it carries, and the composite silently becomes an
    LLM-judge metric wearing a verifier's weights.

INPUTS (saved artifacts only)
    geval_multiturn_passatk5_20260421_3way_clean.json   MACS / GPT+Cat / Gemini+Cat
    geval_multiturn_nosession_ablation.json             MACS-NoSession

NOTE ON INDEPENDENCE
    The deterministic constraint verdict is injected into the judge prompt, so
    the judge is NOT an independent rater of constraint compliance. Agreement
    statistics below are therefore an upper bound on true agreement, and
    "judge never rates a known violation highly" is partly by construction.
    We report this rather than treating the judge as independent.

OUTPUT
    scripts/verifier_discrimination_analysis.json
"""

from __future__ import annotations

import json
import statistics as st
from pathlib import Path
from typing import Any, Dict, List

SCRIPTS = Path(__file__).resolve().parent
THREEWAY = SCRIPTS / "geval_multiturn_passatk5_20260421_3way_clean.json"
NOSESSION = SCRIPTS / "geval_multiturn_nosession_ablation.json"
OUT = SCRIPTS / "verifier_discrimination_analysis.json"

W_C, W_J = 0.55, 0.45
THRESHOLD = 0.65

LABEL = {"idss": "MACS", "gpt": "GPT+Catalog",
         "gemini": "Gemini+Catalog", "idss_nosession": "MACS-NoSession"}


def collect() -> List[Dict[str, Any]]:
    pts: List[Dict[str, Any]] = []
    for path, is_abl in [(THREEWAY, False), (NOSESSION, True)]:
        data = json.loads(path.read_text())
        for run_idx, run in enumerate(data["per_run_results"]):
            for sc in run:
                for key, res in sc["systems"].items():
                    pts.append({
                        "system": "idss_nosession" if is_abl else key,
                        "scenario": sc["id"],
                        "run": run_idx,
                        "s_constraint": res["constraint_score"],
                        "s_judge": res["judge_score"],
                        "passed": bool(res["passed"]),
                    })
    return pts


def pearson(x: List[float], y: List[float]) -> float | None:
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = (sum((a - mx) ** 2 for a in x) / n) ** 0.5
    sy = (sum((b - my) ** 2 for b in y) / n) ** 0.5
    if sx == 0 or sy == 0:
        return None  # a constant series has no correlation
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (n * sx * sy)


def summarize(pts: List[Dict[str, Any]]) -> Dict[str, Any]:
    c = [p["s_constraint"] for p in pts]
    j = [p["s_judge"] for p in pts]
    return {
        "n": len(pts),
        "s_constraint": {"mean": round(st.mean(c), 4), "sd": round(st.pstdev(c), 4),
                         "min": round(min(c), 4), "frac_at_ceiling": round(
                             sum(1 for v in c if v >= 1.0) / len(c), 4)},
        "s_judge": {"mean": round(st.mean(j), 4), "sd": round(st.pstdev(j), 4),
                    "min": round(min(j), 4), "max": round(max(j), 4)},
        "pass_rate": round(sum(1 for p in pts if p["passed"]) / len(pts), 4),
    }


def main() -> int:
    pts = collect()
    three = [p for p in pts if p["system"] in ("idss", "gpt", "gemini")]

    per_system = {LABEL[s]: summarize([p for p in pts if p["system"] == s])
                  for s in ("idss", "gpt", "gemini", "idss_nosession")}

    # --- variance attribution of the composite -----------------------------
    var_c = st.pvariance([W_C * p["s_constraint"] for p in three])
    var_j = st.pvariance([W_J * p["s_judge"] for p in three])
    share_j = var_j / (var_c + var_j) if (var_c + var_j) else None

    # --- what does passing actually reduce to? -----------------------------
    at_ceiling = [p for p in three if p["s_constraint"] >= 1.0]
    j_needed = (THRESHOLD - W_C) / W_J

    # --- agreement (upper bound; judge sees the verdict) -------------------
    compliant = [p for p in three if p["s_constraint"] >= 1.0]
    violating = [p for p in three if p["s_constraint"] < 1.0]
    low_j_compliant = [p for p in compliant if p["s_judge"] <= 0.30]

    results = {
        "source_files": {"three_way": THREEWAY.name, "nosession": NOSESSION.name},
        "weights": {"s_constraint": W_C, "s_judge": W_J},
        "threshold": THRESHOLD,
        "independence_caveat": (
            "The deterministic constraint verdict is injected into the judge "
            "prompt, so the judge is not an independent rater of constraint "
            "compliance; agreement figures are an upper bound."),
        "per_system": per_system,
        "composite_variance_attribution": {
            "n_points": len(three),
            "var_weighted_constraint": round(var_c, 6),
            "var_weighted_judge": round(var_j, 6),
            "judge_share_of_variance": round(share_j, 4) if share_j else None,
        },
        "pass_reduces_to": {
            "frac_points_at_constraint_ceiling": round(len(at_ceiling) / len(three), 4),
            "judge_score_needed_when_at_ceiling": round(j_needed, 4),
            "pass_rate_among_ceiling_points": round(
                sum(1 for p in at_ceiling if p["passed"]) / len(at_ceiling), 4),
        },
        "agreement": {
            "pearson_r_all_points": round(pearson(
                [p["s_constraint"] for p in three],
                [p["s_judge"] for p in three]) or 0.0, 4),
            "n_verifier_compliant": len(compliant),
            "mean_judge_when_compliant": round(
                st.mean([p["s_judge"] for p in compliant]), 4),
            "n_verifier_violation": len(violating),
            "mean_judge_when_violation": round(
                st.mean([p["s_judge"] for p in violating]), 4) if violating else None,
            "n_compliant_but_judge_le_0.30": len(low_j_compliant),
            "frac_compliant_but_judge_le_0.30": round(
                len(low_j_compliant) / len(compliant), 4),
        },
    }

    OUT.write_text(json.dumps(results, indent=2))
    print(f"wrote {OUT}\n")

    print(f"{'system':<17}{'n':>4}{'mean c':>9}{'sd c':>8}{'mean j':>9}{'sd j':>8}")
    print("-" * 55)
    for name, s in per_system.items():
        print(f"{name:<17}{s['n']:>4}{s['s_constraint']['mean']:>9.3f}"
              f"{s['s_constraint']['sd']:>8.3f}{s['s_judge']['mean']:>9.3f}"
              f"{s['s_judge']['sd']:>8.3f}")
    va = results["composite_variance_attribution"]
    print(f"\njudge share of composite variance : {va['judge_share_of_variance']:.1%}")
    pr = results["pass_reduces_to"]
    print(f"points at constraint ceiling      : {pr['frac_points_at_constraint_ceiling']:.0%}")
    print(f"=> passing reduces to  s_judge >= {pr['judge_score_needed_when_at_ceiling']:.3f}")
    ag = results["agreement"]
    print(f"compliant runs judged <= 0.30     : {ag['n_compliant_but_judge_le_0.30']}"
          f"/{ag['n_verifier_compliant']} ({ag['frac_compliant_but_judge_le_0.30']:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
