#!/usr/bin/env python3
"""
Post-hoc re-score of S9 with its unread Chromebook exclusion checked on EVERY
turn (no model calls). Prompted by the independent verifier (RQ6); the shipped
harness reads this constraint on no turn. The user states "No Chromebooks" at
turn 3, so turns 3-5 are checked; a turn is checkable if it returned products.
A product violates the exclusion if its title matches \\bchromebook\\b, the same
title pattern the harness uses for its excluded_os check.
Usage: python3 rescore_s9_chromebook_all_turns.py <dir with stored runs>
"""
import json
import re
import sys
from pathlib import Path

PAT = re.compile(r"\bchromebook\b", re.IGNORECASE)
FILES = [("geval_multiturn_passatk5_20260421_3way_clean.json", ["idss", "gpt", "gemini"], ""),
         ("geval_multiturn_nosession_ablation.json", ["idss"], "_nosession")]


def main():
    d0 = Path(sys.argv[1])
    out = {}
    for fname, keys, suffix in FILES:
        d = json.loads((d0 / fname).read_text())
        for k in keys:
            rows = []
            for ri, run in enumerate(d["per_run_results"]):
                sc = next(s for s in run if s["id"] == 9)
                for ti, t in enumerate(sc["systems"][k]["turn_results"]):
                    if ti >= 2 and t.get("products"):
                        n = sum(bool(PAT.search(p.get("name") or "")) for p in t["products"])
                        rows.append({"run": ri, "turn": ti + 1, "n_chromebooks": n, "n_products": len(t["products"])})
            name = ("macs" if k == "idss" else k) + suffix
            out[name] = {"checkable_turns": len(rows),
                         "turns_with_chromebook": sum(r["n_chromebooks"] > 0 for r in rows),
                         "final_turn_with_chromebook": sum(r["n_chromebooks"] > 0 and r["turn"] == 5 for r in rows),
                         "rows": [r for r in rows if r["n_chromebooks"] > 0]}
    (Path(__file__).resolve().parent / "rescore_s9_chromebook_all_turns.json").write_text(json.dumps(out, indent=2))
    for k, v in out.items():
        print(f"{k:<16} {v['turns_with_chromebook']}/{v['checkable_turns']} turns with a Chromebook "
              f"(final turn: {v['final_turn_with_chromebook']})")


if __name__ == "__main__":
    main()
