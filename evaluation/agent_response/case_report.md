# Agent Response Evaluation: Case Report

This report summarizes the **agent response** evaluation (G-Eval LLM-as-judge): current pass rate, failed cases, and **why the agent performs poorly**.

---

## Summary

- **Total cases:** 50
- **Passed:** 23
- **Failed:** 27
- **Pass rate:** 46%

(Threshold: 0.5. Baseline typically passes more often because it returns direct, catalog-anchored recommendations.)

---

## Why the agent performs poorly

1. **Search/retrieval returning wrong product type**  
   When the product store has no or few matches for narrow filters (e.g. 2-in-1, Linux, stylus), search relaxation can return **non-laptop items** (e.g. wireless mouse, number pad). The agent then presents them as “best value pick,” and the judge fails the response (scores ~0.1). Fixes: stricter product-type filtering, or “no results” + clarifying question instead of showing off-topic products.

2. **Recommendations that ignore stated constraints**  
   The judge penalizes recs that miss explicit user requirements: e.g. **17" when the user asked 14"**, wrong RAM/screen/weight, or recommending Chromebooks when the user needs **Windows** (streaming/capture card, vehicle diagnostic software, etc.). The agent often surfaces whatever the search returns without checking specs against the query. Improving filter→search alignment and post-filter checks would help.

3. **Generic or redundant clarifying questions**  
   When the user has already given a detailed use case (e.g. neuroscience student, CAD + gaming, streaming + video editing), asking “What will you primarily use the laptop for?” or “What brand?” is too generic. The judge marks these as unhelpful. The agent should either go straight to recommendations or ask a single, specific follow-up (e.g. “Do you need a numeric keypad?”).

4. **Catalog and filter gaps**  
   Some queries (e.g. $230 Windows 11 ThinkPad, 16" under $500, premium ultrabook with USB4) have few or no good matches. The pipeline still returns something (wrong form factor, wrong OS, or accessories), and the judge fails it. Better handling of “no good match” (e.g. explain gap and suggest relaxing a constraint) would improve scores.

5. **Baseline comparison**  
   The baseline extracts filters from the query, searches the catalog, and returns a concrete list with price/specs. For many well-specified queries, that **direct recommendation** scores higher than the agent’s single clarifying question or a rec set that ignores constraints. The agent’s tendency to ask one more thing (or to surface relaxed/wrong results) explains part of the gap.

---

## Failed cases (by test_id)

| test_id   | Score | Main reason (from judge) |
|-----------|-------|---------------------------|
| laptop_2  | 0.10  | Recommends wireless mouse, not 2-in-1 laptop (wrong product type). |
| laptop_3  | 0.26  | ThinkPad T490 doesn’t meet premium specs (32GB, USB4, dual M.2, weight). |
| laptop_4  | 0.46  | HP 17" is not backpack-friendly; misses portability and build focus. |
| laptop_5  | 0.34  | Recommends 17.3" IdeaPad; user asked ~14" matte, ThinkPad X1/Surface. |
| laptop_9  | 0.28  | Ideapad 1i has 4GB RAM, 14" HD; user wanted 16GB, 15" 1080p, ThinkPad. |
| laptop_12 | 0.41  | 15.6" Inspiron, not 16"; CPU not suited to MMOs/shooters. |
| laptop_15 | 0.29  | HP 17.3" is not 14–15" 2-in-1, no OLED. |
| laptop_17 | 0.11  | Recommends number pad, not laptop (wrong product type). |
| laptop_18 | 0.34  | HP 17" is not 2-in-1/stylus; wrongly references Apple. |
| laptop_20 | 0.42  | Rec doesn’t align with user’s stated criteria. |
| laptop_21 | 0.28  | Rec doesn’t align with requirements. |
| laptop_22 | 0.29  | Two MacBook recs don’t match user’s stated preferences. |
| laptop_23 | 0.29  | Outdated MacBook Pro model; doesn’t meet needs. |
| laptop_24 | 0.42  | Rec doesn’t match detailed use case. |
| laptop_26 | 0.34  | Device/rec doesn’t match price, review, or use case. |
| laptop_29 | 0.26  | Rec doesn’t match portability / 14" / battery. |
| laptop_32 | 0.29  | Rec fails to align with several stated requirements. |
| laptop_33 | 0.22  | HP Pavilion 17.3" doesn’t meet user’s specs. |
| laptop_35 | 0.45  | Rec doesn’t fully align with stated criteria. |
| laptop_37 | 0.33  | Rec doesn’t justify fit for stated use case. |
| laptop_38 | 0.20  | Lenovo rec doesn’t meet user’s requirements. |
| laptop_39 | 0.37  | Rec doesn’t meet 14" and other stated needs. |
| laptop_41 | 0.39  | MacBook recs don’t match user’s stated preferences. |
| laptop_42 | 0.39  | Rec doesn’t match 14" and performance. |
| laptop_44 | 0.20  | ASUS rec contradicts user’s explicit preference. |
| laptop_45 | 0.18  | Recommends desktop motherboard instead of laptop (wrong product type). |
| laptop_49 | 0.20  | Recommends desktop PC, not laptop (wrong product type). |

(Exact scores and full judge reasons are in `evaluation/agent_response/results/agent_response_eval_results.json`.)

---

## Patterns in failed cases

1. **Wrong product type** — Search returns accessories or non-laptops; agent presents them as the best pick → very low score.
2. **Screen size / form factor mismatch** — Recommending 17" when user asked 14–15", or non-2-in-1 when 2-in-1/stylus was requested.
3. **Generic clarifying questions** — “What will you primarily use it for?” or “What brand?” after the user already gave detailed needs.
4. **Chromebooks for Windows-only use cases** — Streaming, video editing, vehicle diagnostics, Foundry VTT require Windows; Chromebooks are penalized.
5. **Catalog gaps** — No good match for strict requirements; agent still returns something that doesn’t fit, instead of explaining the gap.

---

## Baseline vs agent

The baseline (extract filters → search DB → format recs) often **scores higher** than the agent on the same query because it (1) always returns direct recommendations when the catalog has hits, (2) doesn’t ask generic follow-ups, and (3) can acknowledge constraints (e.g. “no 14-inch options”) in the formatted reply. The agent’s interview flow and search relaxation help in real use but, in this eval, lead to more failed or borderline cases when the catalog or filters don’t align well with the query.

---

*Report generated from `evaluation/agent_response/results/agent_response_eval_results.json` and `agent_response_eval_results_baseline.json`.*
