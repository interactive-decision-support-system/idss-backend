#!/usr/bin/env python3
"""
Fault injection on stored outputs, part 2: do the checkers follow updated state?
(no new model calls)

QUESTION
    fault_injection_carryover.py planted violations of constraints that persist
    unchanged. A persistent-constraint verifier must also follow the two state
    transitions in which an earlier constraint stops applying: an OVERWRITE
    (S7: budget $1000 -> $1400 at turn 2) and a REVERSAL (S8: HP excluded at
    turn 1, re-allowed at turn 2, explicitly requested at turn 3). Here we plant
    products that are correct only under the updated state, products that are
    wrong under it, and the stale-state failure itself, and ask which signals
    respond.

DESIGN
    Base data: MACS-Full stored runs (K=5) of S7 and S8, which have no
    violations. Each mutant edits ONE turn's product list; everything else,
    including the stored judge score, is unchanged. Mutant types:

      S7 overwrite (active budget: t1 $1000, t2-t3 $1400)
        O_allowed : t2/t3 first product priced $1200 (legal only after the raise)
        O_over    : t2/t3 first product priced 1.5 x $1400 (violates new budget)
        O_pre     : t1 first product priced $1200 (violates the pre-raise budget)
      S8 reversal (HP excluded at t1 only; HP requested at t3)
        R_allowed : t2/t3 first product's brand set to HP (legal after reversal)
        R_stale   : t3, HP removed from every product's brand and name AND from
                    the assistant text (the agent still excludes HP after the
                    reversal and says nothing about HP)
        R_stale_negated : as R_stale, plus one appended sentence stating that HP
                    was left out ("HP models are excluded, as you requested
                    earlier."). Constructed text, used to probe the presence
                    check's free-text fallback.
        R_pre     : t1 first product's brand set to HP (violates the exclusion)

    Signals:
        turn-level   per-turn checks from per_turn_constraints (budget ceiling,
                     brand exclusion; analyze_memory_retention semantics)
        s_constraint the harness's own final-turn checker, extracted from source
        pass         s_MT >= 0.65 with the STORED judge score

PREDICTIONS (written before running)
    O_allowed : no signal flags (both checkers encode the $1400 overwrite).
    O_over    : turn-level flags all; s_constraint flags t3 (final) only.
    O_pre     : turn-level flags all; s_constraint flags none (t1 is not final).
    R_allowed : no signal flags. For turn-level this is vacuous: after the
                reversal it has no HP check at all.
    R_stale   : turn-level cannot flag (it has no brand-presence check);
                s_constraint flags all (no HP in products or text).
    R_stale_negated : turn-level cannot flag; s_constraint flags none, because
                its presence check passes on ANY text mention of the brand,
                including a negated one (read from the harness source).

REVISION (recorded after the first run)
    The first version of R_stale removed HP from the products but left the
    assistant text unchanged. Every stored S8 final reply names the HP products
    it recommends, so the mutant was internally inconsistent and s_constraint
    passed it through the text fallback (0/5 flagged). That run was an artifact
    of the mutation, not a finding, and is replaced by the two variants above.
    R_pre     : turn-level flags all; s_constraint flags none.

LIMITATIONS
    One scenario per transition; budget and brand constraints only. The judge
    is not re-run, so judge-side detection is not measured. A correct verdict
    on O_allowed / R_allowed shows the checkers' encoded state was updated, not
    that the agent's was.

OUTPUT
    analysis/fault_injection_state_updates.json
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "fault_injection_state_updates.json"
W_C, W_J, THRESHOLD = 0.55, 0.45, 0.65
HP_RE = re.compile("hp", re.IGNORECASE)  # the checkers match "hp" as a substring

# Expected verdict under the UPDATED state: True = a correct verifier should flag.
SHOULD_FLAG = {"O_allowed": False, "O_over": True, "O_pre": True,
               "R_allowed": False, "R_stale": True, "R_stale_negated": True,
               "R_pre": True}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def turn_level_flag(ret, cons, products) -> bool:
    return any(ret.instance_violated(i, products) for i in ret.turn_instances(cons or {}))


def mutate(kind: str, turns, ti: int):
    mt = copy.deepcopy(turns)
    prods = mt[ti]["products"]
    if kind in ("O_allowed", "O_pre"):
        prods[0]["price"] = 1200.0
    elif kind == "O_over":
        prods[0]["price"] = 1.5 * 1400
    elif kind in ("R_allowed", "R_pre"):
        prods[0]["brand"] = "HP"
    elif kind in ("R_stale", "R_stale_negated"):
        for p in prods:
            for key in ("brand", "name", "title"):
                if p.get(key):
                    p[key] = HP_RE.sub("Lenovo", p[key])
        assert not any(HP_RE.search((p.get("brand") or "") + (p.get("name") or ""))
                       for p in prods)
        text = HP_RE.sub("Lenovo", mt[ti].get("assistant") or "")
        if kind == "R_stale_negated":
            text += "\n\nHP models are excluded, as you requested earlier."
        mt[ti]["assistant"] = text
    return mt


PLAN = {7: [("O_allowed", [1, 2]), ("O_over", [1, 2]), ("O_pre", [0])],
        8: [("R_allowed", [1, 2]), ("R_stale", [2]), ("R_stale_negated", [2]), ("R_pre", [0])]}


def main() -> int:
    data_dir = Path(sys.argv[1]).resolve()
    ret = _load("analyze_memory_retention")
    rr = _load("naacl_reviewer_reanalysis")
    harness = data_dir / "run_multiturn_geval.py"
    scen = rr.load_scenarios(harness)
    checker = rr.load_checker(harness, 1.05)
    data = json.loads((data_dir / "geval_multiturn_passatk5_20260421_3way_clean.json").read_text())

    # Sanity: the scenario encodings are what this design assumes.
    assert [c.get("budget_max_usd") for c in scen[7]["per_turn_constraints"]] == [1000, 1400, 1400]
    assert scen[7]["final_constraints"]["budget_max_usd"] == 1400
    assert [c.get("excluded_brands") for c in scen[8]["per_turn_constraints"]] == [["HP"], None, None]
    assert scen[8]["check_final_brand_presence"] == ["HP"]

    mutants, skipped = [], []
    for run_idx, run in enumerate(data["per_run_results"]):
        for sc in run:
            if sc["id"] not in PLAN:
                continue
            spec = scen[sc["id"]]
            ptc = spec["per_turn_constraints"]
            res = sc["systems"]["idss"]
            turns = res["turn_results"]
            base_c, _ = checker(spec, turns)
            assert abs(base_c - res["constraint_score"]) < 1e-9, "checker does not reproduce stored score"
            assert not any(turn_level_flag(ret, ptc[i], t.get("products") or [])
                           for i, t in enumerate(turns)), "base run already violates"
            for kind, tis in PLAN[sc["id"]]:
                for ti in tis:
                    if ti >= len(turns) or not turns[ti].get("products"):
                        skipped.append({"scenario": sc["id"], "run": run_idx,
                                        "turn": ti + 1, "kind": kind, "reason": "no products"})
                        continue
                    base_hp = any(HP_RE.search((p.get("brand") or "") + (p.get("name") or ""))
                                  for p in turns[ti]["products"])
                    if kind.startswith("R_stale") and not base_hp:
                        skipped.append({"scenario": sc["id"], "run": run_idx, "turn": ti + 1,
                                        "kind": kind, "reason": "base turn has no HP product"})
                        continue
                    mt = mutate(kind, turns, ti)
                    c, notes = checker(spec, mt)
                    tl = turn_level_flag(ret, ptc[ti], mt[ti]["products"])
                    s_base = W_C * base_c + W_J * res["judge_score"]
                    s_mut = W_C * c + W_J * res["judge_score"]
                    mutants.append({
                        "scenario": sc["id"], "run": run_idx, "turn": ti + 1, "kind": kind,
                        "final_turn": ti == len(turns) - 1,
                        "should_flag": SHOULD_FLAG[kind],
                        "turn_level_flag": tl,
                        "s_constraint_flag": c < base_c - 1e-9,
                        "s_constraint_base": round(base_c, 4), "s_constraint": round(c, 4),
                        "final_text_mentions_hp": bool(HP_RE.search(mt[-1].get("assistant") or "")),
                        "pass_base": s_base >= THRESHOLD, "pass_mut": s_mut >= THRESHOLD,
                        "checker_notes": notes,
                    })

    by_kind = defaultdict(list)
    for m in mutants:
        by_kind[m["kind"]].append(m)
    summary = {}
    for kind, ms in by_kind.items():
        summary[kind] = {
            "n": len(ms), "should_flag": SHOULD_FLAG[kind],
            "turns": sorted({m["turn"] for m in ms}),
            "turn_level_flagged": sum(m["turn_level_flag"] for m in ms),
            "s_constraint_flagged": sum(m["s_constraint_flag"] for m in ms),
            "s_constraint_flagged_final_turn": sum(m["s_constraint_flag"] for m in ms if m["final_turn"]),
            "n_final_turn": sum(m["final_turn"] for m in ms),
            "pass_flipped": sum(m["pass_base"] and not m["pass_mut"] for m in ms),
            "passed_base": sum(m["pass_base"] for m in ms),
        }
        if kind.startswith("R_stale"):
            summary[kind]["final_text_mentions_hp"] = sum(m["final_text_mentions_hp"] for m in ms)
    results = {"n_mutants": len(mutants), "summary": summary,
               "skipped": skipped, "mutants": mutants}
    OUT.write_text(json.dumps(results, indent=2))
    print(json.dumps({"n_mutants": len(mutants), "summary": summary, "skipped": skipped}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
