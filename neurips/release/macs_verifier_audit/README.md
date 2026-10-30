# MACS verifier audit: code and stored outputs

Artifacts for "Verifying Persistent Constraint Compliance in Multi-Turn LLM Agents"
(NeurIPS 2026 Workshop: Who Verifies the Agents?).

Everything here except `independent_verifier.py` re-scores stored outputs and makes no
model calls, so it reproduces exactly. Python 3.10+, standard library only.

```
python3 analyze_memory_retention.py          # scripts with no argument read from this directory
python3 naacl_reviewer_reanalysis.py .       # scripts with an argument take the data directory
```

The product catalog is **not** included because its source license has not been
verified. Re-running the agents therefore needs a comparable catalog. Re-scoring
does not.

## Inputs

| File | Contents |
|---|---|
| `run_multiturn_geval.py` | Multi-turn harness: scenario specs, final-turn checker, drift check, judge prompt. The analyses parse it with `ast` or extract the checker from its source; they do not re-implement it. |
| `geval_multiturn_passatk5_20260421_3way_clean.json` | Stored runs: MACS, GPT+Catalog, Gemini+Catalog (10 scenarios × K=5) |
| `geval_multiturn_nosession_ablation.json` | Stored runs: MACS-NoSession (store cleared after each turn) |
| `prefix_single_turn_runs/` | Single-turn runs from before the judge fix (mean judge quality exactly 0.500, an HTTP 429 error on every query) |
| `independent_verifier_raw.jsonl` | Raw outputs of the blinded second verifier (gemini-2.5-flash-lite) |

## Paper claim → script → output

| Paper claim | Script | Output |
|---|---|---|
| Table 1: coverage, first-stated / carry-over counts, scenario-clustered CIs | `analyze_memory_retention.py` (asserts it reproduces the harness's `n_applicable` / `n_violations`) | `memory_retention_analysis.json` |
| RQ1: 149/150 at ceiling, pass ⇔ s_judge ≥ 0.222, judge ≤ 0.30 on 74/149 | `analyze_verifier_discrimination.py` | `verifier_discrimination_analysis.json` |
| RQ1: variance shares 1.5 / 96.9 / 1.6% including covariance (`R1`); exact 1.00× budget changes nothing (`R2`); threshold sweep (`R3`, App. D) | `naacl_reviewer_reanalysis.py` | `naacl_reviewer_reanalysis.json` |
| App. C: no product priced in (1.00, 1.05]× a ceiling (0 of 1,090) | `budget_band_check.py` | `budget_band_check.json` |
| Table 4, App. E: encoding audit (7/10 scenarios, 9 unread, 6 with no field) | `analyze_encoding_completeness.py` | `encoding_completeness_analysis.json` |
| App. E: S7 counterfactual 1.00 → 0.67 | `rescore_with_encoded_constraints.py` | `rescore_encoded_constraints.json` |
| App. E: S9 Chromebooks on 6/15 non-final MACS-NoSession turns | `rescore_s9_chromebook_all_turns.py` | `rescore_s9_chromebook_all_turns.json` |
| App. D: fault injection (55 mutants; s_constraint misses 15/15 non-final) | `fault_injection_carryover.py` | `fault_injection_carryover.json` |
| App. D: blinded verifier (361/362, κ = 0.95; S7 10/10 vs 0/10) | `independent_verifier.py` (model calls) → `analyze_independent_verifier.py` | `independent_verifier_summary.json` |
| RQ2, App. F: non-product turn behaviour (GPT 23/8/6; Gemini 37/9/8/1/4) | `dump_nonproduct_turns.py` → `label_nonproduct_turns.py` | `nonproduct_turns_dump.json`, `nonproduct_turn_labels.json` |
| App. F: 6 provider-error turns; Gemini MPR 52% → 52.5% excluding them | `provider_error_sensitivity.py` | `provider_error_sensitivity.json` |

The non-product behaviour labels in `label_nonproduct_turns.py` were assigned by an LLM
coding assistant reading each stored message, not by a human. The label for each
turn is listed in the script, so it can be re-checked.

`independent_verifier.py` reads an API key from the env file named by `IV_ENV_FILE`
and never writes it. It is the only script here that calls a model.
