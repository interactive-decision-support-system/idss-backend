#!/usr/bin/env python3
"""
Constraint-encoding completeness audit (static + re-scoring; no model calls).

QUESTION
    An environment-grounded verifier can only check constraints that someone
    encoded into its ground truth. This script asks, for our own benchmark:
    which constraints does a scenario state to the user that no deterministic
    code path ever checks?

WHY IT MATTERS
    A score computed only over the encoded subset reports full compliance for a
    response that ignores an unencoded requirement. This is invisible from the
    score, and it is a partial explanation of saturation: the checker tests only
    the constraints that happen to be easy to satisfy.

METHOD
    1. STATIC: diff the keys each scenario declares (final_constraints, plus the
       top-level brand lists) against the keys the two deterministic checkers in
       run_multiturn_geval.py actually read.
    2. CLASSIFY each unchecked key as
         MECHANICAL  - expressible as a predicate over catalog fields, simply
                       not wired up (e.g. brand_preference, excluded_types)
         INEXPRESSIBLE - no catalog field encodes it (e.g. use_case, style), so
                       no environment-grounded check could cover it at all
    3. RE-SCORE the mechanically-checkable ones from the SAVED product lists, to
       quantify the size of the blind spot without re-running the agent.

OUTPUT
    scripts/encoding_completeness_analysis.json
"""

from __future__ import annotations

import importlib.util
import json
import statistics as st
from pathlib import Path
from typing import Any, Dict, List

SCRIPTS = Path(__file__).resolve().parent
THREEWAY = SCRIPTS / "geval_multiturn_passatk5_20260421_3way_clean.json"
NOSESSION = SCRIPTS / "geval_multiturn_nosession_ablation.json"
OUT = SCRIPTS / "encoding_completeness_analysis.json"

# Keys the deterministic checkers in run_multiturn_geval.py actually read.
#   check_final_turn_constraints : budget_max_usd, min_ram_gb, excluded_os,
#                                  + top-level check_final_brand_{exclusion,presence}
#   check_constraint_drift_all_turns : budget_max_usd, excluded_brands
CHECKED_KEYS = {"budget_max_usd", "min_ram_gb", "excluded_os", "excluded_brands"}
TOPLEVEL_BRAND_KEYS = ("check_final_brand_exclusion", "check_final_brand_presence")

# Can an unchecked key be expressed as a predicate over catalog fields at all?
CLASSIFY = {
    "brand_preference": "MECHANICAL",     # brand / title substring
    "excluded_types":   "MECHANICAL",     # product-type substring (e.g. Chromebook)
    "os":               "MECHANICAL",     # OS field, partially populated
    "use_case":         "INEXPRESSIBLE",  # "for video editing" has no catalog field
    "style":            "INEXPRESSIBLE",  # "professional look" has no catalog field
}


def load_scenarios() -> List[Dict[str, Any]]:
    spec = importlib.util.spec_from_file_location(
        "amr", SCRIPTS / "analyze_memory_retention.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_scenarios()


def brand_share(products: List[Dict[str, Any]], brand: str) -> float | None:
    """Fraction of returned products matching a brand/product-family string."""
    if not products:
        return None
    needle = brand.lower().split("/")[0].strip()      # "Apple/MacBook" -> "apple"
    alts = [a.strip().lower() for a in brand.lower().split("/") if a.strip()]
    hits = 0
    for p in products:
        blob = f"{p.get('brand') or ''} {p.get('name') or ''}".lower()
        if any(a in blob for a in alts) or needle in blob:
            hits += 1
    return hits / len(products)


def rescore_brand_preference(sid: int, brand: str) -> Dict[str, Any]:
    """Re-score a scenario's brand preference from SAVED products only."""
    out: Dict[str, Any] = {}
    for label, path, key in [("Full", THREEWAY, "idss"),
                             ("NoSession", NOSESSION, "idss")]:
        data = json.loads(path.read_text())
        per_turn: List[List[float]] = []
        for run in data["per_run_results"]:
            for sc in run:
                if sc["id"] != sid:
                    continue
                shares = [brand_share(t.get("products") or [], brand)
                          for t in sc["systems"][key]["turn_results"]]
                per_turn.append(shares)
        # mean share per turn index, ignoring turns with no products
        n_turns = max(len(r) for r in per_turn)
        means = []
        for i in range(n_turns):
            vals = [r[i] for r in per_turn if i < len(r) and r[i] is not None]
            means.append(round(st.mean(vals), 3) if vals else None)
        out[label] = {"n_runs": len(per_turn), "mean_share_per_turn": means}
    return out


def main() -> int:
    scenarios = load_scenarios()
    rows = []
    for s in scenarios:
        fc = s.get("final_constraints") or {}
        declared = sorted(fc.keys())
        unchecked = [k for k in declared if k not in CHECKED_KEYS]
        # a top-level brand list means brand IS checked for that scenario
        has_brand_list = any(s.get(t) for t in TOPLEVEL_BRAND_KEYS)
        if has_brand_list:
            unchecked = [k for k in unchecked if k != "brand_preference"]
        rows.append({
            "id": s["id"], "name": s["name"],
            "declared": declared,
            "unchecked": unchecked,
            "classification": {k: CLASSIFY.get(k, "UNKNOWN") for k in unchecked},
            "first_turn": s["turns"][0],
        })

    n_with_gap = sum(1 for r in rows if r["unchecked"])
    all_unchecked = [k for r in rows for k in r["unchecked"]]
    mech = [k for k in all_unchecked if CLASSIFY.get(k) == "MECHANICAL"]
    inexp = [k for k in all_unchecked if CLASSIFY.get(k) == "INEXPRESSIBLE"]

    results = {
        "source": {"harness": "run_multiturn_geval.py",
                   "three_way": THREEWAY.name, "nosession": NOSESSION.name},
        "checked_keys": sorted(CHECKED_KEYS),
        "n_scenarios": len(rows),
        "n_scenarios_with_unchecked_constraint": n_with_gap,
        "unchecked_instances_total": len(all_unchecked),
        "unchecked_mechanical": len(mech),
        "unchecked_inexpressible": len(inexp),
        "per_scenario": rows,
        "rescored": {},
    }

    # quantify the one gap that is both mechanical and load-bearing for our results
    for s in scenarios:
        fc = s.get("final_constraints") or {}
        bp = fc.get("brand_preference")
        if bp and "brand_preference" in [k for r in rows if r["id"] == s["id"]
                                         for k in r["unchecked"]]:
            results["rescored"][f"S{s['id']}"] = {
                "constraint": f"brand_preference={bp}",
                "note": "re-scored from saved product lists; no agent re-run",
                **rescore_brand_preference(s["id"], bp),
            }

    OUT.write_text(json.dumps(results, indent=2))
    print(f"wrote {OUT}\n")
    print(f"{'ID':<5}{'unchecked constraints':<34}{'class'}")
    print("-" * 62)
    for r in rows:
        if r["unchecked"]:
            cls = ",".join(sorted(set(r["classification"].values())))
            print(f"S{r['id']:<4}{', '.join(r['unchecked'])[:32]:<34}{cls}")
    print(f"\n{n_with_gap}/{len(rows)} scenarios state a constraint no checker reads")
    print(f"  mechanically checkable but unwired : {len(mech)}")
    print(f"  not expressible as a catalog predicate: {len(inexp)}")
    for k, v in results["rescored"].items():
        print(f"\n{k} re-scored ({v['constraint']}):")
        for cond in ("Full", "NoSession"):
            print(f"   {cond:<10} mean matching share per turn = {v[cond]['mean_share_per_turn']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
