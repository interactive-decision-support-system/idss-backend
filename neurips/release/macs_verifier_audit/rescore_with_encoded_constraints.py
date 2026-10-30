#!/usr/bin/env python3
"""
What would the deterministic score have been if the unchecked constraints were
encoded? Re-scoring from saved artifacts only; no agent re-runs, no model calls.

WHY THIS RATHER THAN FIXING THE BENCHMARK
    Re-encoding the ground truth after seeing a result and then re-running would
    be a post-hoc metric change. Instead we leave the shipped benchmark alone and
    compute, from the stored product lists, what the score WOULD have been. Both
    numbers are then reportable: what the harness said, and the size of its blind
    spot.

CLASSIFICATION IS VERIFIED, NOT ASSERTED
    Saved product records carry exactly {brand, name, price}. A constraint is
    only "mechanically checkable" if it can be decided from those fields. We test
    that per key rather than hand-labelling it.

SCORING
    Replicates check_final_turn_constraints: score = max(0, 1 - 0.33 * penalties),
    one penalty per violated constraint family on the FINAL turn.

OUTPUT
    scripts/rescore_encoded_constraints.json
"""

from __future__ import annotations

import importlib.util
import json
import re
import statistics as st
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCRIPTS = Path(__file__).resolve().parent
THREEWAY = SCRIPTS / "geval_multiturn_passatk5_20260421_3way_clean.json"
NOSESSION = SCRIPTS / "geval_multiturn_nosession_ablation.json"
OUT = SCRIPTS / "rescore_encoded_constraints.json"

PENALTY = 0.33
BUDGET_TOL = 1.05
W_C, W_J, THRESHOLD = 0.55, 0.45, 0.65

SAVED_FIELDS = {"brand", "name", "price"}


def load_scenarios() -> List[Dict[str, Any]]:
    spec = importlib.util.spec_from_file_location(
        "amr", SCRIPTS / "analyze_memory_retention.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_scenarios()


# --- predicates that are decidable from {brand, name, price} -----------------
def viol_brand_preference(products, value) -> bool:
    """Violated when NO returned product matches the requested family."""
    alts = [a.strip().lower() for a in str(value).split("/") if a.strip()]
    for p in products:
        blob = f"{p.get('brand') or ''} {p.get('name') or ''}".lower()
        if any(a in blob for a in alts):
            return False
    return True


def viol_excluded_types(products, value) -> bool:
    pats = [re.compile(rf"\b{re.escape(str(v).lower())}s?\b", re.I) for v in value]
    return any(pat.search(f"{p.get('name') or ''}") for p in products for pat in pats)


# key -> (predicate, decidable-from-saved-fields?)
DECIDABLE: Dict[str, Callable] = {
    "brand_preference": viol_brand_preference,
    "excluded_types":   viol_excluded_types,
}
# keys with no catalog field behind them at all
INEXPRESSIBLE = {"use_case", "style", "os"}


def runs_for(path: Path, sid: int, key: str = "idss"):
    data = json.loads(path.read_text())
    for run in data["per_run_results"]:
        for sc in run:
            if sc["id"] == sid:
                yield sc["systems"][key]


def main() -> int:
    scenarios = load_scenarios()
    CHECKED = {"budget_max_usd", "min_ram_gb", "excluded_os", "excluded_brands"}
    TOPLEVEL = ("check_final_brand_exclusion", "check_final_brand_presence")

    results: Dict[str, Any] = {
        "saved_product_fields": sorted(SAVED_FIELDS),
        "scoring": "max(0, 1 - 0.33*penalties) on the final turn",
        "note": "shipped benchmark unchanged; these are counterfactual re-scores",
        "classification": {},
        "rescored": {},
    }

    # --- verify the mechanical/inexpressible split ---------------------------
    for s in scenarios:
        fc = s.get("final_constraints") or {}
        has_brand_list = any(s.get(t) for t in TOPLEVEL)
        for k in fc:
            if k in CHECKED:
                continue
            if k == "brand_preference" and has_brand_list:
                continue
            if k in DECIDABLE:
                cls = "MECHANICAL (decidable from saved fields)"
            elif k in INEXPRESSIBLE:
                cls = "INEXPRESSIBLE (no catalog field)"
            else:
                cls = "UNKNOWN"
            results["classification"].setdefault(cls, []).append(f"S{s['id']}.{k}")

    # --- counterfactual re-score for every decidable gap ---------------------
    for s in scenarios:
        sid = s["id"]
        fc = s.get("final_constraints") or {}
        has_brand_list = any(s.get(t) for t in TOPLEVEL)
        gaps = {k: v for k, v in fc.items()
                if k in DECIDABLE and k not in CHECKED
                and not (k == "brand_preference" and has_brand_list)}
        if not gaps:
            continue

        entry: Dict[str, Any] = {"constraints_added": {k: fc[k] for k in gaps}}
        for label, path in [("Full", THREEWAY), ("NoSession", NOSESSION)]:
            orig_c, new_c, orig_pass, new_pass, judges = [], [], [], [], []
            for r in runs_for(path, sid):
                final = r["turn_results"][-1]
                prods = final.get("products") or []
                extra = sum(1 for k, v in gaps.items()
                            if prods and DECIDABLE[k](prods, v))
                oc = r["constraint_score"]
                nc = max(0.0, oc - PENALTY * extra)
                j = r["judge_score"]
                orig_c.append(oc); new_c.append(nc); judges.append(j)
                orig_pass.append(W_C * oc + W_J * j >= THRESHOLD)
                new_pass.append(W_C * nc + W_J * j >= THRESHOLD)
            entry[label] = {
                "n_runs": len(orig_c),
                "s_constraint_as_shipped": round(st.mean(orig_c), 3),
                "s_constraint_if_encoded": round(st.mean(new_c), 3),
                "mean_judge": round(st.mean(judges), 3),
                "pass_at_k_as_shipped": round(sum(orig_pass) / len(orig_pass), 2),
                "pass_at_k_if_encoded": round(sum(new_pass) / len(new_pass), 2),
            }
        results["rescored"][f"S{sid}"] = entry

    OUT.write_text(json.dumps(results, indent=2))
    print(f"wrote {OUT}\n")
    print("VERIFIED CLASSIFICATION")
    for cls, keys in results["classification"].items():
        print(f"  {cls}: {', '.join(keys)}")
    print("\nCOUNTERFACTUAL RE-SCORE (shipped -> if the constraint were encoded)")
    for sid, e in results["rescored"].items():
        print(f"\n{sid}  adding {e['constraints_added']}")
        print(f"   {'cond':<11}{'s_c shipped':>12}{'s_c encoded':>13}{'Pass@5 ship':>13}{'Pass@5 enc':>12}")
        for cond in ("Full", "NoSession"):
            d = e[cond]
            print(f"   {cond:<11}{d['s_constraint_as_shipped']:>12}{d['s_constraint_if_encoded']:>13}"
                  f"{d['pass_at_k_as_shipped']:>13}{d['pass_at_k_if_encoded']:>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
