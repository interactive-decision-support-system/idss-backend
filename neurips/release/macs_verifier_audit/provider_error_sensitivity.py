#!/usr/bin/env python3
"""
Provider-error audit of the stored multi-turn runs (found during the
camera-ready non-product-turn labelling). Some baseline turns stored an API
error string ("[ERROR: 503 UNAVAILABLE ...]") as the assistant message; the
harness scored those (scenario, run) points like any other. We locate every
such turn and report how the Gemini+Catalog aggregates move if the affected
points are excluded. Reported as a sensitivity check only; the paper's headline
numbers are left as run.

INPUT   argv[1] = directory with geval_multiturn_passatk5_20260421_3way_clean.json
        and geval_multiturn_nosession_ablation.json
OUTPUT  analysis/provider_error_sensitivity.json
"""

from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = ["geval_multiturn_passatk5_20260421_3way_clean.json", "geval_multiturn_nosession_ablation.json"]
THRESHOLD = 0.65


def macro_pass(pts):
    by = {}
    for p in pts:
        by.setdefault(p["scenario"], []).append(p["final_score"] >= THRESHOLD)
    return sum(sum(v) / len(v) for v in by.values()) / len(by)


def main() -> int:
    data_dir = Path(sys.argv[1]).resolve()
    error_turns, points = [], {}
    for f in FILES:
        d = json.loads((data_dir / f).read_text())
        for ri, run in enumerate(d["per_run_results"]):
            for sc in run:
                for key, res in sc["systems"].items():
                    name = f"{key}{'_nosession' if 'nosession' in f else ''}"
                    points.setdefault(name, []).append(
                        {"scenario": sc["id"], "run": ri, "final_score": res["final_score"],
                         "judge_score": res["judge_score"]})
                    for ti, t in enumerate(res["turn_results"]):
                        if (t.get("assistant") or "").startswith("[ERROR"):
                            error_turns.append({"system": name, "scenario": sc["id"], "run": ri,
                                                "turn": ti + 1, "judge_score": res["judge_score"],
                                                "judge_reason": res["judge_reason"]})

    out = {"n_error_turns": len(error_turns), "error_turns": error_turns, "sensitivity": {}}
    for name in sorted({e["system"] for e in error_turns}):
        bad = {(e["scenario"], e["run"]) for e in error_turns if e["system"] == name}
        allp = points[name]
        keep = [p for p in allp if (p["scenario"], p["run"]) not in bad]
        out["sensitivity"][name] = {
            "n_points_affected": len(bad), "n_points": len(allp),
            "macro_pass_all": round(macro_pass(allp), 4),
            "macro_pass_excluding_affected": round(macro_pass(keep), 4),
            "mean_s_mt_all": round(st.mean(p["final_score"] for p in allp), 4),
            "mean_s_mt_excluding_affected": round(st.mean(p["final_score"] for p in keep), 4),
            "mean_judge_all": round(st.mean(p["judge_score"] for p in allp), 4),
            "mean_judge_excluding_affected": round(st.mean(p["judge_score"] for p in keep), 4)}
    (HERE / "provider_error_sensitivity.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "error_turns"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
