#!/usr/bin/env python3
"""
Behaviour labels for the 96 constrained non-product baseline turns
(camera-ready, Rev. GhSi). Labels were assigned by reading every assistant
message in nonproduct_turns_dump.json, in one pass, by an LLM coding assistant
(Claude), not by a human; the scheme was fixed before counting:

  CLARIFY     asks the user for a missing preference; recommends nothing
  NO_MATCH    declines because the injected catalog lacks a matching product or
              the needed attribute (e.g. "none have a dedicated GPU")
  INFO        answers a non-recommendation question in prose
              (warranty advice, typical price range)
  PROSE_RECS  names catalog products in free text, but the harness extracted
              no structured product, so the turn is uncheckable
  API_ERROR   the provider returned an error string (HTTP 503) instead of a
              response; an infrastructure failure, not agent behaviour

Labels describe behaviour type only; they do not judge whether a clarifying
question was necessary.

INPUT   analysis/nonproduct_turns_dump.json   (from dump_nonproduct_turns.py)
OUTPUT  analysis/nonproduct_turn_labels.json
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
C, N, I, P, E = "CLARIFY", "NO_MATCH", "INFO", "PROSE_RECS", "API_ERROR"

# index into nonproduct_turns_dump.json -> label
LABELS = {
    0: N, 1: C, 2: C, 3: C, 4: C, 5: N, 6: C, 7: C, 8: N, 9: C,
    10: C, 11: I, 12: I, 13: C, 14: C, 15: C, 16: C, 17: N, 18: C, 19: N,
    20: I, 21: C, 22: C, 23: C, 24: C, 25: P, 26: N, 27: C, 28: C, 29: I,
    30: I, 31: C, 32: C, 33: C, 34: C, 35: N, 36: I, 37: C, 38: N, 39: I,
    40: C, 41: C, 42: C, 43: C, 44: E, 45: C, 46: C, 47: I, 48: I, 49: C,
    50: C, 51: C, 52: C, 53: E, 54: C, 55: N, 56: I, 57: C, 58: N, 59: I,
    60: C, 61: N, 62: C, 63: C, 64: C, 65: C, 66: C, 67: C, 68: I, 69: I,
    70: C, 71: C, 72: C, 73: E, 74: C, 75: N, 76: I, 77: C, 78: N, 79: I,
    80: E, 81: C, 82: C, 83: C, 84: C, 85: C, 86: C, 87: N, 88: C, 89: C,
    90: I, 91: I, 92: C, 93: C, 94: C, 95: C,
}


def main() -> None:
    rows = json.loads((HERE / "nonproduct_turns_dump.json").read_text())
    assert sorted(LABELS) == list(range(len(rows))), "every dumped turn needs exactly one label"
    for i, r in enumerate(rows):
        is_err = (r["assistant"] or "").startswith("[ERROR")
        assert is_err == (LABELS[i] == E), f"turn {i}: API_ERROR label must match error text"

    out = {"labeller": "Claude (LLM coding assistant), single pass; not human",
           "by_system": {}, "by_system_and_harness_type": {}, "turns": []}
    for sysname in ("gpt", "gemini"):
        idx = [i for i, r in enumerate(rows) if r["system"] == sysname]
        out["by_system"][sysname] = dict(Counter(LABELS[i] for i in idx).most_common())
        out["by_system_and_harness_type"][sysname] = {
            f"{rows[i]['response_type']}->{LABELS[i]}": n
            for (i_key, n) in Counter((rows[i]["response_type"], LABELS[i]) for i in idx).items()
            for i in [next(j for j in idx if (rows[j]["response_type"], LABELS[j]) == i_key)]}
    for i, r in enumerate(rows):
        out["turns"].append({k: r[k] for k in ("system", "scenario", "run", "turn", "response_type")}
                            | {"label": LABELS[i]})
    (HERE / "nonproduct_turn_labels.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: out[k] for k in ("by_system", "by_system_and_harness_type")}, indent=2))


if __name__ == "__main__":
    main()
