#!/usr/bin/env python3
"""
Camera-ready check (AC, Rev. 5StV #7): does any returned product fall inside the
harness's 5% budget tolerance band, i.e. ceiling < price <= 1.05 x ceiling?

Checks every product on every turn with an active per-turn budget, plus the
final turn against the scenario's final budget, in all four conditions. If the
count is zero, the 1.05x tolerance cannot have affected any reported number.

INPUT   argv[1] = directory with the two multi-turn run files and run_multiturn_geval.py
OUTPUT  analysis/budget_band_check.json
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    data_dir = Path(sys.argv[1]).resolve()
    spec = importlib.util.spec_from_file_location("rr", HERE / "naacl_reviewer_reanalysis.py")
    rr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rr)
    scen = rr.load_scenarios(data_dir / "run_multiturn_geval.py")

    n_checked, in_band = 0, []
    for fname, ablation in [("geval_multiturn_passatk5_20260421_3way_clean.json", False),
                            ("geval_multiturn_nosession_ablation.json", True)]:
        for p in rr.points(data_dir / fname, ablation):
            s = scen[p["scenario"]]
            ptc = s.get("per_turn_constraints") or []
            turns = p["res"]["turn_results"]
            for ti, t in enumerate(turns):
                ceilings = set()
                if ti < len(ptc) and ptc[ti].get("budget_max_usd"):
                    ceilings.add(ptc[ti]["budget_max_usd"])
                if ti == len(turns) - 1 and (s.get("final_constraints") or {}).get("budget_max_usd"):
                    ceilings.add(s["final_constraints"]["budget_max_usd"])
                for c in ceilings:
                    for prod in t.get("products") or []:
                        if not prod.get("price"):
                            continue
                        n_checked += 1
                        price = float(prod["price"])
                        if c < price <= 1.05 * c:
                            in_band.append({"system": p["system"], "scenario": p["scenario"],
                                            "run": p["run"], "turn": ti + 1, "ceiling": c, "price": price})

    out = {"n_product_ceiling_pairs": n_checked, "n_in_tolerance_band": len(in_band), "in_band": in_band}
    (HERE / "budget_band_check.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
