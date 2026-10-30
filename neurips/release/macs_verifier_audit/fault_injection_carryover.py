#!/usr/bin/env python3
"""
Fault injection on stored outputs: which verification signal sees a carry-over violation?
(no new model calls)

QUESTION
    In the session-store ablation, carry-over violations arose naturally in only
    one scenario (S1), on its final turn. That shows the turn-level verifier CAN
    expose a persistence failure, not which signals would see one wherever it
    occurs. Here we plant one carry-over violation at a time, in every scenario
    and turn that has a carry-over check, and ask which signals register it.

DESIGN
    Base data: MACS-Full stored runs (10 scenarios x K=5), all of which have zero
    violations. For each (run, scenario, turn, constraint) carry-over check on a
    turn with >= 1 returned product, create ONE mutant in which the first returned
    product is made to violate that constraint:
        brand exclusion : product brand set to the excluded brand
        budget ceiling  : product price set to 1.5 x the active ceiling
    Everything else (other turns, other products, the stored judge score) is
    left unchanged. Each mutant is then scored by:
        turn-level   carry-over check (analyze_memory_retention semantics)
        s_constraint the harness's own final-turn checker, extracted from source
        drift        harness per-run drift rate (n_violations / n_applicable)
        pass         s_MT >= 0.65 with the STORED judge score

    PREDICTION (stated before running)
        turn-level detects 100% by construction (this is a positive control);
        s_constraint detects only faults planted on the scenario's final turn,
        and only for constraints listed in that scenario's final-constraint block.

    LIMITATION
        The judge is not re-run, so judge-side detection is not measured; the
        pass column isolates what the deterministic channel alone would do.

OUTPUT
    analysis/fault_injection_carryover.json
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "fault_injection_carryover.json"
W_C, W_J, THRESHOLD = 0.55, 0.45, 0.65


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def drift(ret, ptc, turns):
    app = vio = 0
    for ti, cons in enumerate(ptc):
        if ti >= len(turns):
            break
        prods = turns[ti].get("products") or []
        insts = ret.turn_instances(cons or {})
        if not prods or not insts:
            continue
        for inst in insts:
            app += 1
            vio += ret.instance_violated(inst, prods)
    return vio, app


def main() -> int:
    data_dir = Path(sys.argv[1]).resolve()
    ret = _load("analyze_memory_retention")
    rr = _load("naacl_reviewer_reanalysis")
    harness = data_dir / "run_multiturn_geval.py"
    scen = rr.load_scenarios(harness)
    checker = rr.load_checker(harness, 1.05)
    data = json.loads((data_dir / "geval_multiturn_passatk5_20260421_3way_clean.json").read_text())

    mutants = []
    for run_idx, run in enumerate(data["per_run_results"]):
        for sc in run:
            spec = scen[sc["id"]]
            ptc = spec.get("per_turn_constraints") or []
            res = sc["systems"]["idss"]
            turns = res["turn_results"]
            first = ret.first_stated(ptc)
            base_c, _ = checker(spec, turns)
            assert abs(base_c - res["constraint_score"]) < 1e-9
            base_vio, base_app = drift(ret, ptc, turns)
            assert base_vio == 0, "MACS-Full base run already has a violation"
            for ti, cons in enumerate(ptc):
                if ti >= len(turns) or not turns[ti].get("products"):
                    continue
                for inst in ret.turn_instances(cons or {}):
                    if first[inst] >= ti:
                        continue  # first-stated, not carry-over
                    mt = copy.deepcopy(turns)
                    p = mt[ti]["products"][0]
                    if inst[0] == "brand":
                        p["brand"] = inst[1]
                    else:
                        p["price"] = 1.5 * float(inst[1])
                    assert ret.instance_violated(inst, mt[ti]["products"])
                    c, _ = checker(spec, mt)
                    vio, app = drift(ret, ptc, mt)
                    s_base = W_C * base_c + W_J * res["judge_score"]
                    s_mut = W_C * c + W_J * res["judge_score"]
                    mutants.append({
                        "scenario": sc["id"], "run": run_idx, "turn": ti + 1,
                        "n_turns": len(turns), "final_turn": ti == len(turns) - 1,
                        "constraint": f"{inst[0]}={inst[1]}",
                        "turn_level_detected": True,
                        "s_constraint_detected": c < base_c - 1e-9,
                        "s_constraint": round(c, 4),
                        "drift_base": base_vio / base_app, "drift_mut": vio / app,
                        "pass_base": s_base >= THRESHOLD, "pass_mut": s_mut >= THRESHOLD,
                    })

    def summ(ms):
        n = len(ms)
        return {"n": n,
                "turn_level": sum(m["turn_level_detected"] for m in ms) / n,
                "s_constraint": sum(m["s_constraint_detected"] for m in ms) / n,
                "pass_flipped": sum(m["pass_base"] and not m["pass_mut"] for m in ms) / n,
                "passed_base": sum(m["pass_base"] for m in ms),
                "mean_drift_mut": sum(m["drift_mut"] for m in ms) / n}

    by_sc = defaultdict(list)
    for m in mutants:
        by_sc[m["scenario"]].append(m)
    results = {
        "n_mutants": len(mutants),
        "overall": summ(mutants),
        "final_turn": summ([m for m in mutants if m["final_turn"]]),
        "non_final_turn": summ([m for m in mutants if not m["final_turn"]]),
        "by_scenario": {str(k): {**summ(v), "turns": sorted({m["turn"] for m in v}),
                                 "n_turns": v[0]["n_turns"],
                                 "constraints": sorted({m["constraint"] for m in v})}
                        for k, v in sorted(by_sc.items())},
        "mutants": mutants,
    }
    OUT.write_text(json.dumps(results, indent=2))
    print(json.dumps({k: v for k, v in results.items() if k != "mutants"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
