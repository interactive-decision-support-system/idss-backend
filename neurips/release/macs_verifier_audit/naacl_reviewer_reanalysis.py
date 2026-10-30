#!/usr/bin/env python3
"""
Re-analyses requested by the NeurIPS Verify-Agents reviewers (no new model calls).

Each block answers one reviewer question from saved artifacts only:

  R1  Variance attribution (AC, Rev. 5StV #8). The paper's 98.5% figure is
      Var(bJ) / (Var(aC) + Var(bJ)), which omits the covariance term of
      Var(aC + bJ). We report the full decomposition
          Var(S) = a^2 Var(C) + b^2 Var(J) + 2ab Cov(C, J)
      and each term's share of Var(S).

  R2  Exact budget ceiling (AC, Rev. 5StV #7). The harness counts a budget
      violation only above 1.05x the ceiling. We re-run the harness's own
      final-turn checker (extracted from source, not re-implemented) with the
      tolerance set to 1.00, after first asserting that at 1.05 it reproduces
      every stored constraint_score exactly. Same for the turn-level analysis.

  R3  Threshold sensitivity (AC, Rev. 5StV #3). Macro run pass rate for each
      system as the pass threshold sweeps 0.50..0.80, from stored s_MT.

  R4  Non-product turns by behaviour type (Rev. GhSi). Of the constrained turns
      on which a system returned no structured product, how many were a
      clarifying question vs. another response type (harness response_type).

INPUTS (saved artifacts; directory passed as argv[1])
    geval_multiturn_passatk5_20260421_3way_clean.json
    geval_multiturn_nosession_ablation.json
    run_multiturn_geval.py              (ground truth + checker source)

OUTPUT
    analysis/naacl_reviewer_reanalysis.json
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import statistics as st
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List

HERE = Path(__file__).resolve().parent
OUT = HERE / "naacl_reviewer_reanalysis.json"

W_C, W_J = 0.55, 0.45
THRESHOLD = 0.65
THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
SYSTEMS = [("idss", "MACS"), ("gpt", "GPT+Catalog"), ("gemini", "Gemini+Catalog")]


def load_checker(harness: Path, tolerance: float) -> Callable:
    """Extract check_final_turn_constraints from the harness source and bind a tolerance."""
    tree = ast.parse(harness.read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "check_final_turn_constraints")
    src = ast.get_source_segment(harness.read_text(), fn)
    assert src.count("budget * 1.05") == 1, "harness budget check changed; re-audit"
    src = src.replace("budget * 1.05", "budget * _TOL")
    ns: Dict[str, Any] = {"re": re, "_TOL": tolerance,
                          "Dict": Dict, "List": List, "Tuple": tuple}
    exec("from typing import Dict, List, Tuple\n" + src, ns)
    return ns["check_final_turn_constraints"]


def load_scenarios(harness: Path) -> Dict[int, Dict[str, Any]]:
    tree = ast.parse(harness.read_text())
    for node in tree.body:
        targets = getattr(node, "targets", None) or (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "MULTITURN_SCENARIOS":
                return {s["id"]: s for s in ast.literal_eval(node.value)}
    raise RuntimeError("MULTITURN_SCENARIOS not found")


def points(path: Path, ablation: bool) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text())
    out = []
    for run_idx, run in enumerate(data["per_run_results"]):
        for sc in run:
            for key, res in sc["systems"].items():
                out.append({"system": "idss_nosession" if ablation else key,
                            "scenario": sc["id"], "run": run_idx, "res": res})
    return out


def macro_pass(pts: List[Dict[str, Any]], score_key: str, thr: float) -> float:
    by_sc: Dict[int, List[bool]] = {}
    for p in pts:
        by_sc.setdefault(p["scenario"], []).append(p[score_key] >= thr)
    return st.mean(sum(v) / len(v) for v in by_sc.values())


def main() -> int:
    data_dir = Path(sys.argv[1]).resolve()
    harness = data_dir / "run_multiturn_geval.py"
    scen = load_scenarios(harness)
    pts = (points(data_dir / "geval_multiturn_passatk5_20260421_3way_clean.json", False)
           + points(data_dir / "geval_multiturn_nosession_ablation.json", True))

    chk105, chk100 = load_checker(harness, 1.05), load_checker(harness, 1.00)
    mism = 0
    for p in pts:
        r = p["res"]
        c105, _ = chk105(scen[p["scenario"]], r["turn_results"])
        c100, _ = chk100(scen[p["scenario"]], r["turn_results"])
        if abs(c105 - r["constraint_score"]) > 1e-9:
            mism += 1
        p["c"], p["j"] = r["constraint_score"], r["judge_score"]
        p["s"] = W_C * p["c"] + W_J * p["j"]
        p["c100"] = c100
        p["s100"] = W_C * c100 + W_J * p["j"]
    if mism:
        print(f"SANITY FAIL: extracted checker disagrees with stored score on {mism} points",
              file=sys.stderr)
        return 1
    # Stored final_score / passed must also follow from the formula.
    bad = [p for p in pts if abs(p["s"] - p["res"]["final_score"]) > 0.006
           or (p["s"] >= THRESHOLD - 1e-9) != bool(p["res"]["passed"])]
    if bad:
        print(f"SANITY FAIL: {len(bad)} points where formula != stored final/pass", file=sys.stderr)
        return 1

    three = [p for p in pts if p["system"] in ("idss", "gpt", "gemini")]

    # R1 ---------------------------------------------------------------------
    C = [W_C * p["c"] for p in three]
    J = [W_J * p["j"] for p in three]
    S = [a + b for a, b in zip(C, J)]
    mc, mj = st.mean(C), st.mean(J)
    cov = sum((a - mc) * (b - mj) for a, b in zip(C, J)) / len(C)
    vS, vC, vJ = st.pvariance(S), st.pvariance(C), st.pvariance(J)
    r1 = {"n_points": len(three), "var_S": vS, "var_aC": vC, "var_bJ": vJ,
          "two_cov": 2 * cov,
          "share_aC_of_varS": vC / vS, "share_bJ_of_varS": vJ / vS,
          "share_2cov_of_varS": 2 * cov / vS,
          "paper_formula_share_bJ": vJ / (vC + vJ),
          "check_sum": (vC + vJ + 2 * cov) / vS}

    # R2 ---------------------------------------------------------------------
    sysnames = dict(SYSTEMS + [("idss_nosession", "MACS-NoSession")])
    r2 = {}
    for key, name in sysnames.items():
        sp = [p for p in pts if p["system"] == key]
        r2[name] = {
            "points_changed": sum(1 for p in sp if abs(p["c100"] - p["c"]) > 0.006),
            "mean_c_105": st.mean(p["c"] for p in sp),
            "mean_c_100": st.mean(p["c100"] for p in sp),
            "frac_c_at_ceiling_100": sum(p["c100"] >= 1.0 for p in sp) / len(sp),
            "macro_pass_105": macro_pass(sp, "s", THRESHOLD),
            "macro_pass_100": macro_pass(sp, "s100", THRESHOLD),
            "mean_s_105": st.mean(p["s"] for p in sp),
            "mean_s_100": st.mean(p["s100"] for p in sp),
        }

    # Turn-level analysis at 1.00x, reusing the published module unchanged.
    spec = importlib.util.spec_from_file_location("ret", HERE / "analyze_memory_retention.py")
    ret = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ret)
    scen_list = list(scen.values())
    threeway = json.loads((data_dir / "geval_multiturn_passatk5_20260421_3way_clean.json").read_text())
    nosess = json.loads((data_dir / "geval_multiturn_nosession_ablation.json").read_text())
    turn = {}
    for tol in (1.05, 1.00):
        ret.BUDGET_TOLERANCE = tol
        rows = {}
        for key, data in [("idss", threeway), ("gpt", threeway), ("gemini", threeway),
                          ("idss_nosession", nosess)]:
            a = ret.analyse(data["per_run_results"], "idss" if key == "idss_nosession" else key,
                            {s["id"]: s for s in scen_list})
            if tol == 1.05:
                assert not a["sanity_check_mismatches"], f"harness mismatch for {key}"
            rows[sysnames[key]] = {
                "carryover": f'{a["carryover"]["violations"]}/{a["carryover"]["applicable"]}',
                "carryover_ci": [a["carryover"]["ci95_scenario_cluster"]["lo"],
                                 a["carryover"]["ci95_scenario_cluster"]["hi"]],
                "first_stated": f'{a["first_stated"]["violations"]}/{a["first_stated"]["applicable"]}',
                "coverage": f'{a["coverage"]["turns_with_products"]}/{a["coverage"]["constrained_turns"]}',
                "n_scenarios_with_carryover_violation": a["n_scenarios_with_carryover_violation"],
            }
        turn[f"{tol:.2f}"] = rows

    # R3 ---------------------------------------------------------------------
    r3 = {name: {f"{t:.2f}": macro_pass([p for p in pts if p["system"] == key], "s", t)
                 for t in THRESHOLDS}
          for key, name in sysnames.items()}

    # R4 ---------------------------------------------------------------------
    r4 = {}
    for key, name in sysnames.items():
        cnt: Counter = Counter()
        for p in pts:
            if p["system"] != key:
                continue
            ptc = scen[p["scenario"]].get("per_turn_constraints") or []
            for ti, cons in enumerate(ptc):
                if ti >= len(p["res"]["turn_results"]) or not ret.turn_instances(cons or {}):
                    continue
                t = p["res"]["turn_results"][ti]
                if t.get("products"):
                    cnt["returned_products"] += 1
                else:
                    cnt[f"no_products:{t.get('response_type')}"] += 1
        r4[name] = dict(sorted(cnt.items()))

    results = {"source_dir": str(data_dir), "sanity": "checker reproduces all stored scores at 1.05",
               "R1_variance": r1, "R2_exact_budget_final_turn": r2,
               "R2_exact_budget_turn_level": turn, "R3_threshold_sensitivity": r3,
               "R4_nonproduct_turn_types": r4}
    OUT.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
