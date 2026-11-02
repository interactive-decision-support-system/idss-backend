#!/usr/bin/env python3
"""
Build a blinded human-annotation packet for validating the multi-turn LLM judge.
(no model calls)

QUESTION
    Once s_constraint saturates, s_judge decides every multi-turn ranking in the
    paper. Do human ratings of the same transcripts, on the same rubric, agree
    with the stored s_judge, and do they order the four conditions the same way?

SAMPLE
    One run per (condition, scenario) cell: 4 conditions (MACS-Full, GPT+Catalog,
    Gemini+Catalog, MACS-NoSession) x 10 scenarios = 40 transcripts. The run
    index for each cell is drawn with a fixed seed; item order is shuffled with
    the same seed. Nothing is selected by score.

WHAT ANNOTATORS SEE (matched to the judge's input)
    - the scenario goal text the judge received (quality_note);
    - the transcript rendered by the harness's own format_transcript (extracted
      from source, so assistant turns are cut at 400 characters and at most 4
      products per turn are shown, exactly as the judge saw them);
    - the judge's rubric and anchors, verbatim.
    WITHHELD: system identity, the deterministic check verdict (which the judge
    did see), the stored judge score, and s_constraint.

OUTPUTS (in this directory)
    packet.md      what annotators read
    sheet.csv      what annotators fill in (one copy per annotator)
    key.json       item -> condition, scenario, run, stored scores. Do NOT give
                   this file to annotators.
"""

from __future__ import annotations

import ast
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
SEED = 20261101
CONDITIONS = [  # (label in key, file, system key in file)
    ("MACS-Full", "geval_multiturn_passatk5_20260421_3way_clean.json", "idss"),
    ("GPT+Catalog", "geval_multiturn_passatk5_20260421_3way_clean.json", "gpt"),
    ("Gemini+Catalog", "geval_multiturn_passatk5_20260421_3way_clean.json", "gemini"),
    ("MACS-NoSession", "geval_multiturn_nosession_ablation.json", "idss"),
]


def harness_objects(harness: Path) -> Dict[str, Any]:
    src = harness.read_text()
    tree = ast.parse(src)
    out: Dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "format_transcript":
            ns: Dict[str, Any] = {}
            exec("from typing import Dict, List\n" + ast.get_source_segment(src, node), ns)
            out["format_transcript"] = ns["format_transcript"]
        targets = getattr(node, "targets", None) or (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        for t in targets:
            if isinstance(t, ast.Name) and t.id in ("MULTITURN_SCENARIOS", "MULTITURN_JUDGE_SYSTEM"):
                out[t.id] = ast.literal_eval(node.value)
    assert {"format_transcript", "MULTITURN_SCENARIOS", "MULTITURN_JUDGE_SYSTEM"} <= out.keys()
    return out


def rubric_for_humans(judge_system: str) -> str:
    """The judge's rubric verbatim, minus the machine-output instruction at the end."""
    cut = judge_system.index("Write 3–4 sentences of reasoning")
    return judge_system[:cut].rstrip()


def main() -> int:
    data_dir = Path(sys.argv[1]).resolve()
    h = harness_objects(data_dir / "run_multiturn_geval.py")
    scen = {s["id"]: s for s in h["MULTITURN_SCENARIOS"]}
    rng = random.Random(SEED)

    items: List[Dict[str, Any]] = []
    for label, fname, key in CONDITIONS:
        data = json.loads((data_dir / fname).read_text())
        by_sc: Dict[int, List[Any]] = {}
        for run_idx, run in enumerate(data["per_run_results"]):
            for sc in run:
                if key in sc["systems"]:
                    by_sc.setdefault(sc["id"], []).append((run_idx, sc["systems"][key]))
        assert sorted(by_sc) == list(range(1, 11)), (label, sorted(by_sc))
        for sid in sorted(by_sc):
            assert len(by_sc[sid]) == 5, (label, sid, len(by_sc[sid]))
            run_idx, res = rng.choice(by_sc[sid])
            items.append({"condition": label, "scenario": sid, "run": run_idx,
                          "judge_score": res["judge_score"],
                          "constraint_score": res["constraint_score"],
                          "judge_reason": res.get("judge_reason", ""),
                          "transcript": h["format_transcript"](res["turn_results"])})
    rng.shuffle(items)
    for i, it in enumerate(items, 1):
        it["item"] = f"J{i:03d}"

    rubric = rubric_for_humans(h["MULTITURN_JUDGE_SYSTEM"])
    lines = [
        "# Multi-turn transcript rating packet",
        "",
        "You will rate 40 shopping-assistant conversations. Each comes from one of several",
        "systems; which one is hidden on purpose. Rate each conversation on its own.",
        "",
        "For each item, read the scenario goal and the transcript, then enter three",
        "sub-scores in `sheet.csv`: constraint satisfaction (0–4), preference tracking",
        "(0–3), and response appropriateness (0–3). The total is their sum (0–10).",
        "Add a short note on the main failure, if any. Use only what is shown:",
        "assistant turns are cut at 400 characters and at most 4 products are listed",
        "per turn, exactly as the automated rater saw them.",
        "",
        "Please do not discuss items with the other annotator until both sheets are",
        "finished, and do not open `key.json`.",
        "",
        "## Rubric (identical to the automated rater's)",
        "",
        "```",
        rubric,
        "```",
        "",
    ]
    for it in items:
        lines += [f"## {it['item']}", "",
                  f"**Scenario goal:** {scen[it['scenario']]['quality_note']}", "",
                  "```", it["transcript"].rstrip(), "```", ""]
    (HERE / "packet.md").write_text("\n".join(lines))

    with (HERE / "sheet.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item", "constraint_satisfaction_0_4", "preference_tracking_0_3",
                    "response_appropriateness_0_3", "total_0_10", "main_failure_note",
                    "annotator_initials"])
        for it in items:
            w.writerow([it["item"], "", "", "", "", "", ""])

    key = {"seed": SEED, "n_items": len(items),
           "items": {it["item"]: {k: it[k] for k in
                                  ("condition", "scenario", "run", "judge_score",
                                   "constraint_score", "judge_reason")}
                     for it in items}}
    (HERE / "key.json").write_text(json.dumps(key, indent=2))
    print(f"wrote {len(items)} items: packet.md, sheet.csv, key.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
