#!/usr/bin/env python3
"""
Compare the blinded verifier's labels (independent_verifier_raw.jsonl) with the
deterministic turn-level checks on the constraints the benchmark encodes, and
summarize what it reports on constraints no checker encodes. No model calls.

Deterministic reference (per turn, from the harness's per_turn_constraints):
  budget ceiling  violated iff some returned product price > ceiling (exact; the
                  1.05x harness tolerance changes nothing on these runs)
  excluded brand  violated iff the brand string occurs in a product's brand or name
Matching verifier output to an encoded constraint:
  budget          a verifier requirement with category "budget" whose value parses
                  to the same number as the encoded ceiling
  excluded brand  a requirement with category "excluded_brand" whose value or text
                  contains the brand (case-insensitive)
Verifier status "violated" vs. anything else is compared with the deterministic
violated/not-violated label. Unit of uncertainty: scenario (bootstrap over the 10).
Usage: python3 analyze_independent_verifier.py <dir with run_multiturn_geval.py>
"""
from __future__ import annotations

import ast
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "independent_verifier_raw.jsonl"
OUT = HERE / "independent_verifier_summary.json"
DATA_FILES = {"macs": ("geval_multiturn_passatk5_20260421_3way_clean.json", "idss"),
              "gpt": ("geval_multiturn_passatk5_20260421_3way_clean.json", "gpt"),
              "gemini": ("geval_multiturn_passatk5_20260421_3way_clean.json", "gemini"),
              "macs_nosession": ("geval_multiturn_nosession_ablation.json", "idss")}
UNENCODED = ("required_brand", "spec", "use_case", "other")


def scenarios(harness):
    tree = ast.parse(harness.read_text())
    for n in tree.body:
        tg = getattr(n, "targets", None) or ([n.target] if isinstance(n, ast.AnnAssign) else [])
        if any(getattr(x, "id", "") == "MULTITURN_SCENARIOS" for x in tg):
            return {s["id"]: s for s in ast.literal_eval(n.value)}
    raise RuntimeError("no scenarios")


def products_for(data_dir):
    out = {}
    for system, (fname, key) in DATA_FILES.items():
        d = json.loads((data_dir / fname).read_text())
        for run_idx, run in enumerate(d["per_run_results"]):
            for sc in run:
                for ti, t in enumerate(sc["systems"][key]["turn_results"]):
                    out[(system, sc["id"], run_idx, ti + 1)] = t.get("products") or []
    return out


def num(v):
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(v))
    return float(m.group(0).replace(",", "")) if m else None


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def main():
    data_dir = Path(sys.argv[1]).resolve()
    scen = scenarios(data_dir / "run_multiturn_geval.py")
    prods = products_for(data_dir)
    recs = [json.loads(l) for l in RAW.read_text().splitlines()]
    recs = [r for r in recs if r.get("ok")]
    # one record per turn (latest successful)
    by_turn = {(r["system"], r["scenario"], r["run"], r["turn"]): r for r in recs}

    enc_pairs = defaultdict(list)       # family -> [(det_violated, ver_violated)]
    enc_pairs_sys = defaultdict(list)
    identified = Counter()
    encoded_total = Counter()
    disagreements = []
    off_spec = []                       # verifier budget/brand violations with no active encoded constraint
    unenc = defaultdict(Counter)        # system -> category -> n turns with a violation in that category
    any_viol = defaultdict(lambda: defaultdict(list))  # system -> scenario -> [0/1 per turn]
    s7 = []

    for (system, sid, run, turn), r in sorted(by_turn.items()):
        reqs = (r.get("parsed") or {}).get("requirements") or []
        ptc = (scen[sid].get("per_turn_constraints") or [])
        cons = ptc[turn - 1] if turn - 1 < len(ptc) else {}
        products = prods[(system, sid, run, turn)]
        # encoded budget
        b = cons.get("budget_max_usd")
        if b:
            encoded_total["budget"] += 1
            det = any(p.get("price") is not None and float(p["price"]) > b for p in products)
            match = [q for q in reqs if q.get("category") == "budget" and num(q.get("value")) == float(b)]
            if match:
                identified["budget"] += 1
                ver = any(q.get("status") == "violated" for q in match)
                enc_pairs["budget"].append((int(det), int(ver)))
                enc_pairs_sys[system].append((int(det), int(ver)))
                if det != ver:
                    disagreements.append({"system": system, "scenario": sid, "run": run, "turn": turn,
                                          "family": "budget", "ceiling": b, "det": det, "ver": ver,
                                          "max_price": max(float(p["price"]) for p in products if p.get("price") is not None)})
        for brand in cons.get("excluded_brands") or []:
            encoded_total["excluded_brand"] += 1
            det = any(brand.lower() in (p.get("brand") or "").lower() or brand.lower() in (p.get("name") or "").lower()
                      for p in products)
            match = [q for q in reqs if q.get("category") == "excluded_brand"
                     and brand.lower() in (str(q.get("value")) + " " + str(q.get("requirement"))).lower()]
            if match:
                identified["excluded_brand"] += 1
                ver = any(q.get("status") == "violated" for q in match)
                enc_pairs["excluded_brand"].append((int(det), int(ver)))
                enc_pairs_sys[system].append((int(det), int(ver)))
                if det != ver:
                    disagreements.append({"system": system, "scenario": sid, "run": run, "turn": turn,
                                          "family": "excluded_brand", "brand": brand, "det": det, "ver": ver})
        # verifier-reported budget/brand violations where the spec has no active encoded constraint
        for q in reqs:
            if q.get("status") != "violated":
                continue
            if q.get("category") == "budget" and not b:
                off_spec.append({"system": system, "scenario": sid, "turn": turn, "run": run,
                                 "category": "budget", "value": q.get("value")})
            if q.get("category") == "excluded_brand" and not (cons.get("excluded_brands")):
                off_spec.append({"system": system, "scenario": sid, "turn": turn, "run": run,
                                 "category": "excluded_brand", "value": q.get("value")})
        cats = {q.get("category") for q in reqs if q.get("status") == "violated"}
        for c in cats & set(UNENCODED):
            unenc[system][c] += 1
        any_viol[system][sid].append(int(bool(cats)))
        if sid == 7 and system in ("macs", "macs_nosession"):
            rb = [q for q in reqs if q.get("category") == "required_brand"]
            s7.append({"system": system, "run": run, "turn": turn,
                       "apple_share": sum("apple" in ((p.get("brand") or "") + (p.get("name") or "")).lower()
                                          for p in products) / max(1, len(products)),
                       "required_brand_identified": bool(rb),
                       "required_brand_violated": any(q.get("status") == "violated" for q in rb)})

    def conf(pairs):
        c = Counter(pairs)
        return {"det1_ver1": c[(1, 1)], "det1_ver0": c[(1, 0)], "det0_ver1": c[(0, 1)], "det0_ver0": c[(0, 0)],
                "agreement": sum(a == b for a, b in pairs) / len(pairs) if pairs else None,
                "kappa": kappa(pairs), "n": len(pairs)}

    def rate_ci(sys_key, seed=3):
        sc = any_viol[sys_key]
        keys = list(sc)
        pt = sum(sum(v) for v in sc.values()) / sum(len(v) for v in sc.values())
        rng = random.Random(seed)
        bs = []
        for _ in range(2000):
            ks = [keys[rng.randrange(len(keys))] for _ in keys]
            bs.append(sum(sum(sc[k]) for k in ks) / sum(len(sc[k]) for k in ks))
        bs.sort()
        return {"rate": pt, "ci95": [bs[49], bs[1949]], "n_turns": sum(len(v) for v in sc.values()),
                "per_scenario": {str(k): [sum(v), len(v)] for k, v in sorted(sc.items())}}

    s7_ns = [x for x in s7 if x["system"] == "macs_nosession" and x["turn"] >= 2]
    s7_full = [x for x in s7 if x["system"] == "macs" and x["turn"] >= 2]
    out = {
        "model": recs[0].get("model"), "model_version": Counter(r.get("model_version") for r in recs).most_common(3),
        "n_turns_labeled": len(by_turn),
        "encoded": {
            "identification_rate": {k: identified[k] / encoded_total[k] for k in encoded_total},
            "n_encoded_instances": dict(encoded_total),
            "budget": conf(enc_pairs["budget"]),
            "excluded_brand": conf(enc_pairs["excluded_brand"]),
            "pooled": conf(enc_pairs["budget"] + enc_pairs["excluded_brand"]),
            "pooled_by_system": {s: conf(v) for s, v in enc_pairs_sys.items()},
        },
        "disagreements_on_encoded": disagreements,
        "verifier_violations_without_encoded_constraint": {"n": len(off_spec),
                                                           "by_category": dict(Counter(x["category"] for x in off_spec)),
                                                           "examples": off_spec[:20]},
        "unencoded_violation_turns_by_system": {s: dict(c) for s, c in unenc.items()},
        "any_violation_rate_by_system_exploratory": {s: rate_ci(s) for s in any_viol},
        "s7_prediction": {
            "nosession_turns_2_3": {"n": len(s7_ns),
                                    "required_brand_violated": sum(x["required_brand_violated"] for x in s7_ns),
                                    "mean_apple_share": sum(x["apple_share"] for x in s7_ns) / max(1, len(s7_ns))},
            "full_turns_2_3": {"n": len(s7_full),
                               "required_brand_violated": sum(x["required_brand_violated"] for x in s7_full),
                               "mean_apple_share": sum(x["apple_share"] for x in s7_full) / max(1, len(s7_full))},
            "rows": s7,
        },
    }
    OUT.write_text(json.dumps(out, indent=2))
    e = out["encoded"]
    print("identification:", {k: round(v, 3) for k, v in e["identification_rate"].items()}, e["n_encoded_instances"])
    for k in ("budget", "excluded_brand", "pooled"):
        print(k, {a: (round(b, 3) if isinstance(b, float) else b) for a, b in e[k].items()})
    print("by system:", {s: (v["n"], round(v["agreement"], 3), v["kappa"] and round(v["kappa"], 3)) for s, v in e["pooled_by_system"].items()})
    print("off-spec verifier violations:", out["verifier_violations_without_encoded_constraint"]["by_category"])
    print("unencoded violation turns:", out["unencoded_violation_turns_by_system"])
    for s, v in out["any_violation_rate_by_system_exploratory"].items():
        print(f"any-violation {s}: {v['rate']:.3f} {[round(x,3) for x in v['ci95']]} n={v['n_turns']} per-scen={v['per_scenario']}")
    print("S7:", {k: v for k, v in out["s7_prediction"].items() if k != "rows"})


if __name__ == "__main__":
    main()
