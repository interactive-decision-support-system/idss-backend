#!/usr/bin/env python3
"""
Cross-turn memory-retention re-analysis (no new model calls).

RESEARCH QUESTION
    The multi-turn harness already evaluates every stated hard constraint on
    *every* turn (check_constraint_drift_all_turns), but the paper reports only
    the aggregate scenario-level drift rate. That aggregate cannot distinguish

        (a) "the constraint was stated this turn and was honoured"   -- no memory needed
        (b) "the constraint was stated earlier and is still honoured" -- memory needed

    and it silently ignores turns where a system returned no products at all,
    so a system that abstains often gets a small denominator and a flattering
    drift rate.

    This script recomputes the same deterministic checks from the saved run
    artifacts, but splits every (turn, constraint) check into FIRST-STATED vs
    CARRY-OVER, and reports the denominators explicitly.

INPUTS  (saved artifacts only; nothing is re-run)
    scripts/geval_multiturn_passatk5_20260421_3way_clean.json   MACS / GPT+Cat / Gemini+Cat
    scripts/geval_multiturn_nosession_ablation.json             MACS-NoSession
    scripts/run_multiturn_geval.py                              per_turn_constraints ground truth

CHECK SEMANTICS  (identical to check_constraint_drift_all_turns, verified below)
    brand exclusion : case-insensitive substring match against brand field OR product name;
                      counted once per (turn, excluded brand)
    budget ceiling  : any product with price > budget * 1.05; counted once per (turn, budget)
    applicable      : turn has >= 1 structured product AND >= 1 active constraint

SANITY CHECK
    Summing this script's per-instance counts back to the scenario level must
    reproduce the harness's own n_applicable / n_violations exactly. The script
    asserts this and refuses to emit results if it does not hold.

OUTPUT
    scripts/memory_retention_analysis.json
"""

from __future__ import annotations

import ast
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

BOOTSTRAP_N = 10000
BOOTSTRAP_SEED = 20260829

SCRIPTS = Path(__file__).resolve().parent

THREEWAY = SCRIPTS / "geval_multiturn_passatk5_20260421_3way_clean.json"
NOSESSION = SCRIPTS / "geval_multiturn_nosession_ablation.json"
HARNESS = SCRIPTS / "run_multiturn_geval.py"
OUT = SCRIPTS / "memory_retention_analysis.json"

BUDGET_TOLERANCE = 1.05  # matches the harness

SYSTEM_LABEL = {
    "idss": "MACS",
    "gpt": "GPT+Catalog",
    "gemini": "Gemini+Catalog",
    "idss_nosession": "MACS-NoSession",
}


# ---------------------------------------------------------------------------
# Ground truth: per_turn_constraints, extracted from the harness source without
# importing it (importing would pull in API clients and network config).
# ---------------------------------------------------------------------------
def load_scenarios() -> List[Dict[str, Any]]:
    tree = ast.parse(HARNESS.read_text())
    for node in tree.body:
        targets = getattr(node, "targets", None) or (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "MULTITURN_SCENARIOS":
                return ast.literal_eval(node.value)
    raise RuntimeError("MULTITURN_SCENARIOS not found in harness source")


# ---------------------------------------------------------------------------
# Constraint instances and when each was first stated
# ---------------------------------------------------------------------------
Instance = Tuple[str, str]  # ("brand", "HP") | ("budget", "1000")


def turn_instances(constraints: Dict[str, Any]) -> List[Instance]:
    """The constraint instances active on one turn, in harness check order."""
    out: List[Instance] = []
    for brand in constraints.get("excluded_brands") or []:
        out.append(("brand", str(brand)))
    budget = constraints.get("budget_max_usd")
    if budget:
        out.append(("budget", str(budget)))
    return out


def first_stated(ptc: List[Dict[str, Any]]) -> Dict[Instance, int]:
    """Earliest turn index at which each constraint instance appears."""
    first: Dict[Instance, int] = {}
    for i, constraints in enumerate(ptc):
        for inst in turn_instances(constraints or {}):
            first.setdefault(inst, i)
    return first


# ---------------------------------------------------------------------------
# The deterministic checks (copied semantics from the harness)
# ---------------------------------------------------------------------------
def brand_violated(brand: str, products: List[Dict[str, Any]]) -> bool:
    needle = brand.lower()
    for p in products:
        if needle in (p.get("brand") or "").lower() or needle in (p.get("name") or "").lower():
            return True
    return False


def budget_violated(budget: float, products: List[Dict[str, Any]]) -> bool:
    for p in products:
        price = p.get("price")
        if price is not None and float(price) > budget * BUDGET_TOLERANCE:
            return True
    return False


def instance_violated(inst: Instance, products: List[Dict[str, Any]]) -> bool:
    kind, value = inst
    if kind == "brand":
        return brand_violated(value, products)
    return budget_violated(float(value), products)


# ---------------------------------------------------------------------------
# Uncertainty. The independent unit is the SCENARIO, not the (turn, constraint)
# check: all K runs and all turns within one scenario share a script and a
# constraint set, so checks within a scenario are correlated. We resample
# scenarios with replacement and recompute the pooled ratio.
# ---------------------------------------------------------------------------
def bootstrap_ci(pairs: List[Tuple[int, int]]) -> Dict[str, Any]:
    """pairs: [(numerator, denominator)] per scenario. Returns pooled ratio + 95% CI."""
    pairs = [p for p in pairs if p[1] > 0]
    if not pairs:
        return {"point": None, "lo": None, "hi": None, "n_scenarios": 0}
    num = sum(n for n, _ in pairs)
    den = sum(d for _, d in pairs)
    rng = random.Random(BOOTSTRAP_SEED)
    draws = []
    for _ in range(BOOTSTRAP_N):
        sample = [pairs[rng.randrange(len(pairs))] for _ in range(len(pairs))]
        d = sum(x[1] for x in sample)
        if d:
            draws.append(sum(x[0] for x in sample) / d)
    draws.sort()
    lo = draws[int(0.025 * len(draws))]
    hi = draws[int(0.975 * len(draws)) - 1]
    return {"point": round(num / den, 4), "lo": round(lo, 4), "hi": round(hi, 4),
            "n_scenarios": len(pairs)}


# ---------------------------------------------------------------------------
# Core pass over one system's runs
# ---------------------------------------------------------------------------
def analyse(
    per_run_results: List[List[Dict[str, Any]]],
    system_key: str,
    scenarios_by_id: Dict[int, Dict[str, Any]],
) -> Dict[str, Any]:
    counts = {
        "carryover": {"applicable": 0, "violations": 0},
        "first_stated": {"applicable": 0, "violations": 0},
    }
    by_kind = defaultdict(lambda: {"applicable": 0, "violations": 0})
    per_scenario_recomputed = defaultdict(lambda: {"applicable": 0, "violations": 0})
    per_scenario_reported = defaultdict(lambda: {"applicable": 0, "violations": 0})

    # Recommendation coverage: of the turns that carry >=1 active constraint,
    # on how many did the system actually return structured products?
    coverage = {"constrained_turns": 0, "turns_with_products": 0}

    # Scenario is the independent unit for uncertainty: all K runs and all turns
    # within one scenario are correlated (same script, same constraints).
    by_scenario = defaultdict(lambda: {
        "carryover_applicable": 0, "carryover_violations": 0,
        "constrained_turns": 0, "turns_with_products": 0,
    })

    violation_examples: List[Dict[str, Any]] = []

    for run in per_run_results:
        for sc in run:
            spec = scenarios_by_id[sc["id"]]
            ptc = spec.get("per_turn_constraints") or []
            if not ptc:
                continue
            first = first_stated(ptc)

            sysres = sc["systems"].get(system_key)
            if sysres is None:
                continue
            turns = sysres.get("turn_results") or []

            # Harness's own numbers, for the sanity check.
            dd = sysres.get("drift_details") or {}
            per_scenario_reported[sc["id"]]["applicable"] += dd.get("n_applicable", 0)
            per_scenario_reported[sc["id"]]["violations"] += dd.get("n_violations", 0)

            for ti, constraints in enumerate(ptc):
                if ti >= len(turns):
                    break
                insts = turn_instances(constraints or {})
                if not insts:
                    continue
                coverage["constrained_turns"] += 1
                by_scenario[sc["id"]]["constrained_turns"] += 1

                products = turns[ti].get("products") or []
                if not products:
                    continue  # harness skips these; they inflate nothing
                coverage["turns_with_products"] += 1
                by_scenario[sc["id"]]["turns_with_products"] += 1

                for inst in insts:
                    bucket = "carryover" if first[inst] < ti else "first_stated"
                    violated = instance_violated(inst, products)

                    counts[bucket]["applicable"] += 1
                    by_kind[inst[0]]["applicable"] += 1
                    per_scenario_recomputed[sc["id"]]["applicable"] += 1
                    if bucket == "carryover":
                        by_scenario[sc["id"]]["carryover_applicable"] += 1
                    if violated:
                        counts[bucket]["violations"] += 1
                        by_kind[inst[0]]["violations"] += 1
                        per_scenario_recomputed[sc["id"]]["violations"] += 1
                        if bucket == "carryover":
                            by_scenario[sc["id"]]["carryover_violations"] += 1
                        if len(violation_examples) < 25:
                            violation_examples.append({
                                "scenario_id": sc["id"],
                                "scenario": sc["name"],
                                "turn": ti + 1,
                                "constraint": f"{inst[0]}={inst[1]}",
                                "class": bucket,
                                "first_stated_turn": first[inst] + 1,
                                "n_products": len(products),
                            })

    # --- sanity check against the harness's own aggregation -----------------
    mismatches = []
    for sid in sorted(set(per_scenario_recomputed) | set(per_scenario_reported)):
        a, b = per_scenario_recomputed[sid], per_scenario_reported[sid]
        if a != b:
            mismatches.append({"scenario_id": sid, "recomputed": a, "harness": b})

    def rate(d):
        return round(d["violations"] / d["applicable"], 4) if d["applicable"] else None

    sc_ids = sorted(by_scenario)
    ci_carryover = bootstrap_ci(
        [(by_scenario[s]["carryover_violations"], by_scenario[s]["carryover_applicable"])
         for s in sc_ids])
    ci_coverage = bootstrap_ci(
        [(by_scenario[s]["turns_with_products"], by_scenario[s]["constrained_turns"])
         for s in sc_ids])

    return {
        "system": SYSTEM_LABEL.get(system_key, system_key),
        "carryover": {**counts["carryover"], "violation_rate": rate(counts["carryover"]),
                      "ci95_scenario_cluster": ci_carryover},
        "first_stated": {**counts["first_stated"], "violation_rate": rate(counts["first_stated"])},
        "per_scenario": {str(s): dict(by_scenario[s]) for s in sc_ids},
        "n_scenarios_with_carryover_violation": sum(
            1 for s in sc_ids if by_scenario[s]["carryover_violations"] > 0),
        "by_constraint_kind": {
            k: {**v, "violation_rate": rate(v)} for k, v in sorted(by_kind.items())
        },
        "coverage": {
            **coverage,
            "rate": round(
                coverage["turns_with_products"] / coverage["constrained_turns"], 4
            ) if coverage["constrained_turns"] else None,
            "ci95_scenario_cluster": ci_coverage,
        },
        "violation_examples": violation_examples,
        "sanity_check_mismatches": mismatches,
    }


def main() -> int:
    scenarios = load_scenarios()
    scenarios_by_id = {s["id"]: s for s in scenarios}

    threeway = json.loads(THREEWAY.read_text())
    nosession = json.loads(NOSESSION.read_text())

    results = {
        "source_files": {
            "three_way": THREEWAY.name,
            "nosession_ablation": NOSESSION.name,
            "ground_truth": HARNESS.name,
        },
        "K": threeway["K"],
        "n_scenarios": len(scenarios),
        "budget_tolerance": BUDGET_TOLERANCE,
        "systems": {},
    }

    for key in ("idss", "gpt", "gemini"):
        results["systems"][key] = analyse(threeway["per_run_results"], key, scenarios_by_id)

    ns = analyse(nosession["per_run_results"], "idss", scenarios_by_id)
    ns["system"] = SYSTEM_LABEL["idss_nosession"]
    results["systems"]["idss_nosession"] = ns

    failed = {k: v["sanity_check_mismatches"] for k, v in results["systems"].items()
              if v["sanity_check_mismatches"]}
    if failed:
        print("SANITY CHECK FAILED — recomputed counts disagree with the harness:",
              file=sys.stderr)
        print(json.dumps(failed, indent=2)[:4000], file=sys.stderr)
        return 1

    OUT.write_text(json.dumps(results, indent=2))
    print(f"wrote {OUT}")

    print(f"\n{'System':<16}{'carry-over viol.':>20}{'95% CI':>18}"
          f"{'first-stated':>16}{'coverage':>16}{'95% CI':>18}{'#sc':>5}")
    print("-" * 109)
    for k in ("idss", "gpt", "gemini", "idss_nosession"):
        s = results["systems"][k]
        co, fs, cv = s["carryover"], s["first_stated"], s["coverage"]
        cc, vc = co["ci95_scenario_cluster"], cv["ci95_scenario_cluster"]
        print(f"{s['system']:<16}"
              + f"{co['violations']}/{co['applicable']} ({co['violation_rate']})".rjust(20)
              + f"[{cc['lo']}, {cc['hi']}]".rjust(18)
              + f"{fs['violations']}/{fs['applicable']}".rjust(16)
              + f"{cv['turns_with_products']}/{cv['constrained_turns']} ({cv['rate']})".rjust(16)
              + f"[{vc['lo']}, {vc['hi']}]".rjust(18)
              + f"{s['n_scenarios_with_carryover_violation']}".rjust(5))
    print("\n#sc = number of the 10 scenarios containing >=1 carry-over violation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
