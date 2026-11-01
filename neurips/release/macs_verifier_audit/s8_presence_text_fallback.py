#!/usr/bin/env python3
"""
Counterfactual re-score: S8's brand-presence check without its free-text fallback.
(no model calls)

QUESTION
    fault_injection_state_updates.py showed that s_constraint's brand-presence
    check passes whenever the final reply mentions the brand, even in a negation.
    Did that fallback change any stored score? S8 is the only scenario with a
    presence check (HP, after the user re-allows and then requests it).

METHOD
    For every condition and run of S8, find final turns where no returned product
    contains "HP" (the checker's own substring rule) but s_constraint passed the
    presence check through the text fallback. Re-score those runs as if the
    presence check required an HP product (one penalty: 1.00 -> 0.67), leave every
    other run as stored, and recompute: the s_constraint ceiling count, per-system
    mean s_constraint, MPR at 0.65, and the variance decomposition of s_MT over the
    three unmodified systems (same formula as naacl_reviewer_reanalysis.py R1).
    The stored final replies of the affected runs are saved for reading; each was
    read by hand (HAND_READ below), and in every one the assistant tells the user
    it has no HP laptops.

    This is a post-hoc counterfactual. The paper's reported numbers stay as run.

OUTPUT
    analysis/s8_presence_text_fallback.json
"""

from __future__ import annotations

import importlib.util
import json
import re
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "s8_presence_text_fallback.json"
W_C, W_J, THRESHOLD = 0.55, 0.45, 0.65
HP = re.compile("hp", re.IGNORECASE)
# Hand reading of each affected final reply (2026-11-01): does it present HP options?
HAND_READ = "All affected replies state that no HP laptops are available and offer other brands."


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def decomposition(points):
    C = [W_C * p["c"] for p in points]
    J = [W_J * p["j"] for p in points]
    S = [a + b for a, b in zip(C, J)]
    mc, mj = st.mean(C), st.mean(J)
    cov = sum((a - mc) * (b - mj) for a, b in zip(C, J)) / len(C)
    vS = st.pvariance(S)
    return {"share_aC": round(st.pvariance(C) / vS, 4), "share_bJ": round(st.pvariance(J) / vS, 4),
            "share_2cov": round(2 * cov / vS, 4)}


def mpr(points, key):
    by = {}
    for p in points:
        by.setdefault(p["scenario"], []).append(W_C * p[key] + W_J * p["j"] >= THRESHOLD)
    return round(st.mean(sum(v) / len(v) for v in by.values()), 4)


def main() -> int:
    data_dir = Path(sys.argv[1]).resolve()
    rr = _load("naacl_reviewer_reanalysis")
    scen = rr.load_scenarios(data_dir / "run_multiturn_geval.py")
    assert [s for s in scen if scen[s].get("check_final_brand_presence")] == [8]
    pts = (rr.points(data_dir / "geval_multiturn_passatk5_20260421_3way_clean.json", False)
           + rr.points(data_dir / "geval_multiturn_nosession_ablation.json", True))

    affected = []
    for p in pts:
        r = p["res"]
        p["c"], p["j"] = r["constraint_score"], r["judge_score"]
        p["c_cf"] = p["c"]
        if p["scenario"] != 8:
            continue
        final = r["turn_results"][-1]
        prods = final.get("products") or []
        has_hp = any(HP.search((q.get("brand") or "") + (q.get("name") or "")) for q in prods)
        via_text = any("mentioned in final text" in n for n in r["constraint_notes"] if "HP" in n)
        if not has_hp and via_text:
            p["c_cf"] = max(0.0, p["c"] - 0.33)
            affected.append({"system": p["system"], "run": p["run"], "n_products": len(prods),
                             "s_constraint": p["c"], "s_constraint_cf": round(p["c_cf"], 2),
                             "s_judge": p["j"],
                             "pass_stored": W_C * p["c"] + W_J * p["j"] >= THRESHOLD,
                             "pass_cf": W_C * p["c_cf"] + W_J * p["j"] >= THRESHOLD,
                             "final_reply": final.get("assistant") or ""})

    systems = ["idss", "gpt", "gemini", "idss_nosession"]
    three = [p for p in pts if p["system"] in systems[:3]]
    res = {
        "hand_read": HAND_READ,
        "n_affected": len(affected),
        "affected_by_system": {s: sum(a["system"] == s for a in affected) for s in systems},
        "pass_verdicts_changed": sum(a["pass_stored"] != a["pass_cf"] for a in affected),
        "ceiling_three_systems": {"stored": sum(p["c"] == 1.0 for p in three),
                                  "counterfactual": sum(p["c_cf"] == 1.0 for p in three),
                                  "n": len(three)},
        "mean_s_constraint": {s: {"stored": round(st.mean(p["c"] for p in pts if p["system"] == s), 3),
                                  "counterfactual": round(st.mean(p["c_cf"] for p in pts if p["system"] == s), 3)}
                              for s in systems},
        "mpr": {s: {"stored": mpr([p for p in pts if p["system"] == s], "c"),
                    "counterfactual": mpr([p for p in pts if p["system"] == s], "c_cf")}
                for s in systems},
        "variance_three_systems": {"stored": decomposition(three),
                                   "counterfactual": decomposition(
                                       [{**p, "c": p["c_cf"]} for p in three])},
        "affected": affected,
    }
    OUT.write_text(json.dumps(res, indent=2))
    print(json.dumps({k: v for k, v in res.items() if k != "affected"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
