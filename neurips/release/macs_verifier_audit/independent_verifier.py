#!/usr/bin/env python3
"""
Independent, blinded LLM verifier over the stored multi-turn runs (NAACL revision).

QUESTION
    Do labels from an independently configured verifier agree with the
    deterministic turn-level checks on the constraints those checks encode, and
    which violations does it report on constraints no checker encodes?

PREDICTIONS (stated before running; idss-backend/NAACL/cross_paper_plan.md, E4)
    - High agreement with the deterministic check on encoded budget ceilings and
      brand exclusions.
    - It flags the dropped MacBook/Apple request in S7 under MACS-NoSession
      (turns 2-3), which the deterministic check scores as compliant.
    KILL: low agreement on encoded constraints would mean these labels cannot
    serve as independent labels; reported as such.
EXPLORATORY (not pre-registered)
    Per-condition and per-scenario rates of verifier-reported violations.

VERIFIER CONFIGURATION (independent of the harness)
    Model gemini-2.5-flash-lite (the only model available to us at run time;
    it is also the backbone of the Gemini+Catalog baseline, so agreement is
    reported per system). Temperature 0, JSON output. The verifier sees the
    user messages and earlier assistant replies (truncated) up to the turn,
    and the products returned on that turn. It is NOT shown the scenario
    specification, the declared constraints, the deterministic verdict, or the
    harness judge's score. It must infer the active requirements itself.

INPUTS   stored runs in DATA_DIR (argv[1]); harness scenario specs (ast only)
OUTPUTS  analysis/independent_verifier_raw.jsonl     (one record per call; resumable)
         analysis/independent_verifier_summary.json
The API key is read at run time from the env file given by IV_ENV_FILE and is
never printed or written.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "independent_verifier_raw.jsonl"
SUMMARY = HERE / "independent_verifier_summary.json"
MODEL = "gemini-2.5-flash-lite"
ASSISTANT_TRUNC = 400

PROMPT = """You are auditing a shopping assistant for laptops and electronics.

Below is a conversation between a USER and the ASSISTANT, followed by the list of products the assistant returned on its LATEST turn.

Task:
1. List every hard requirement the USER has stated that is still in effect at the latest turn. A requirement is no longer in effect if the user later withdrew or replaced it (for example, a new budget replaces an old one; "actually X is fine" withdraws an exclusion). Requirements can come from any earlier user message, not only the latest one.
2. For each requirement, check every returned product. Use only the product name, brand and price shown; do not assume facts that are not shown.

Use these categories: "budget" (maximum price), "excluded_brand", "required_brand" (the user asked for a specific brand or product family), "spec" (RAM, storage, screen size, weight, OS, GPU, condition, etc.), "use_case", "other".
Status must be "satisfied" (every product meets it), "violated" (at least one product clearly fails it), or "cannot_determine" (the shown information is not enough).

Return JSON only, in this form:
{{"requirements": [{{"category": "...", "requirement": "short description", "value": "e.g. 1000 or HP", "status": "...", "violating_products": ["exact product names"]}}]}}

CONVERSATION:
{conversation}

PRODUCTS RETURNED ON THE LATEST TURN:
{products}
"""


def load_key():
    env = os.environ.get("IV_ENV_FILE")
    if not env:
        raise SystemExit("set IV_ENV_FILE to the env file holding GEMINI_API_KEY")
    for line in Path(env).read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("GEMINI_API_KEY not found")


def ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def call(key, prompt, ctx, retries=5):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }).encode()
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
                d = json.load(r)
            text = d["candidates"][0]["content"]["parts"][0]["text"]
            return {"ok": True, "text": text, "parsed": json.loads(text),
                    "model_version": d.get("modelVersion")}
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return {"ok": False, "error": f"HTTP {e.code}"}
        except (json.JSONDecodeError, KeyError) as e:
            if attempt < retries - 1:
                continue
            return {"ok": False, "error": f"parse: {type(e).__name__}"}
        except Exception as e:  # network
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"ok": False, "error": type(e).__name__}
    return {"ok": False, "error": "retries exhausted"}


def items(data_dir):
    files = [("geval_multiturn_passatk5_20260421_3way_clean.json", False),
             ("geval_multiturn_nosession_ablation.json", True)]
    for fname, abl in files:
        d = json.loads((data_dir / fname).read_text())
        for run_idx, run in enumerate(d["per_run_results"]):
            for sc in run:
                for key, res in sc["systems"].items():
                    system = "macs_nosession" if abl else ("macs" if key == "idss" else key)
                    turns = res["turn_results"]
                    for ti, t in enumerate(turns):
                        if not t.get("products"):
                            continue
                        conv = []
                        for j in range(ti + 1):
                            conv.append(f"USER: {turns[j]['user']}")
                            if j < ti:
                                a = (turns[j].get("assistant") or "").replace("\n", " ")
                                conv.append(f"ASSISTANT: {a[:ASSISTANT_TRUNC]}")
                        prods = "\n".join(
                            f"- {p.get('name')} | brand: {p.get('brand')} | price: ${p.get('price')}"
                            for p in t["products"])
                        prompt = PROMPT.format(conversation="\n".join(conv), products=prods)
                        yield {"system": system, "scenario": sc["id"], "run": run_idx, "turn": ti + 1,
                               "n_turns": len(turns), "products": t["products"],
                               "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                               "prompt": prompt}


def main():
    data_dir = Path(sys.argv[1]).resolve()
    work = list(items(data_dir))
    done = {}
    if RAW.exists():
        for line in RAW.read_text().splitlines():
            r = json.loads(line)
            if r.get("ok"):
                done[r["prompt_sha256"]] = r
    todo = [w for w in work if w["prompt_sha256"] not in done]
    print(f"{len(work)} turns; {len(done)} cached; {len(todo)} to call", flush=True)
    if todo:
        key, ctx = load_key(), ssl_ctx()
        with RAW.open("a") as fh, cf.ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(call, key, w["prompt"], ctx): w for w in todo}
            for i, f in enumerate(cf.as_completed(futs), 1):
                w, res = futs[f], f.result()
                rec = {k: v for k, v in w.items() if k not in ("prompt", "products")}
                rec.update(res)
                rec["model"] = MODEL
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                if res.get("ok"):
                    done[w["prompt_sha256"]] = rec
                if i % 50 == 0:
                    print(f"  {i}/{len(todo)}", flush=True)
    failed = [w for w in work if w["prompt_sha256"] not in done]
    print(f"failed calls: {len(failed)}")
    json.dump({"n_turns": len(work), "n_labeled": len(work) - len(failed), "n_failed": len(failed),
               "model": MODEL}, sys.stdout)


if __name__ == "__main__":
    main()
