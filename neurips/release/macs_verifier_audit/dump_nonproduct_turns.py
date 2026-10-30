#!/usr/bin/env python3
"""
Dump every constrained turn on which a baseline returned no structured product
(camera-ready, Rev. GhSi: "report non-product turns by behaviour type").

Uses the same turn selection as R4 in naacl_reviewer_reanalysis.py (a turn is
constrained iff analyze_memory_retention.turn_instances is non-empty), so the
counts must equal R4's (GPT 37, Gemini 59); the script asserts this.

INPUT   argv[1] = directory holding geval_multiturn_passatk5_20260421_3way_clean.json
        and run_multiturn_geval.py
OUTPUT  analysis/nonproduct_turns_dump.json  (one record per turn, with the
        harness response_type and the full assistant text, for manual labelling)
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "nonproduct_turns_dump.json"
EXPECTED = {"gpt": 37, "gemini": 59}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    data_dir = Path(sys.argv[1]).resolve()
    rr = load("rr", HERE / "naacl_reviewer_reanalysis.py")
    ret = load("ret", HERE / "analyze_memory_retention.py")
    scen = rr.load_scenarios(data_dir / "run_multiturn_geval.py")
    pts = rr.points(data_dir / "geval_multiturn_passatk5_20260421_3way_clean.json", ablation=False)

    rows = []
    for p in pts:
        if p["system"] not in EXPECTED:
            continue
        ptc = scen[p["scenario"]].get("per_turn_constraints") or []
        for ti, cons in enumerate(ptc):
            if ti >= len(p["res"]["turn_results"]) or not ret.turn_instances(cons or {}):
                continue
            t = p["res"]["turn_results"][ti]
            if t.get("products"):
                continue
            rows.append({"system": p["system"], "scenario": p["scenario"], "run": p["run"],
                         "turn": ti + 1, "active_constraints": cons,
                         "response_type": t.get("response_type"), "n_recs": t.get("n_recs"),
                         "user": t.get("user"), "assistant": t.get("assistant")})

    for k, n in EXPECTED.items():
        got = sum(r["system"] == k for r in rows)
        assert got == n, f"{k}: {got} non-product constrained turns, R4 reports {n}"
    OUT.write_text(json.dumps(rows, indent=2))
    print(f"wrote {len(rows)} turns to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
