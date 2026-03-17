# Recommendations Golden Dataset: Expansion to 50 and Eval Report

## (a) How the test set was expanded to 50 queries

- **Starting point:** The existing `golden_dataset.json` had 50 items (laptop_1–laptop_50) from `testing/query_data_enriched.csv` / agent-response style queries. Only **12** had ≥3 `ground_truth_product_ids`; **38** were skipped by the eval (min_ground_truth_ids = 3).
- **Expansion approach:** A script **`expand_golden_to_50.py`** was added that:
  1. **Keeps** the 11 items that already had ≥3 ground-truth IDs (laptop_1, 5, 7, 13, 17, 18, 40, 41, 43, 47, 48) with their original `user_query` and `expected_ucp`.
  2. **Replaces** the remaining 39 items with **simpler, derivative** items:
     - **Shorter `user_query`** (one line, laptop-focused), e.g. *"Gaming laptop under $1000 in the US."*
     - **Relaxed `expected_ucp`** so the catalog can supply enough matches: for all derivatives the script uses **minimal UCP** — only `category`, `product_type`, `price_max_cents`, and `_soft_preferences` (no `brand`, `min_ram_gb`, `min/max_screen_size`, or `good_for_*`). This avoids over-filtering in `populate_ground_truth.py` so each item can get ≥3 ground-truth IDs.
- **Sources:** Derivative queries were derived from the same 50 golden items (shortened/simplified) and from the same laptop use cases (budget/specs simplified). All remain laptop-focused and realistic.

**To produce the 50-item expanded golden (with empty ground truth for repopulation):**
```bash
python -m evaluation.recommendations.expand_golden_to_50
```
This overwrites `evaluation/recommendations/golden_dataset.json` with 50 items and clears `ground_truth_product_ids`. Run **`populate_ground_truth.py`** next to fill them.

---

## (b) Iterations needed for ground truth

- **First pass:** After the initial expand (with relaxed but still some hard constraints on derivatives), `populate_ground_truth.py` was run with `--golden evaluation/recommendations/golden_dataset.json --top 10 --backup`. Many items still had **0** products passing the hard filter (e.g. laptop_2, 4; laptop_3 had 1) because `good_for_gaming` / screen / brand etc. filtered out the catalog.
- **Iteration:** The expand script was updated so that **all 39 derivatives** use **minimal UCP** only (`_minimal_ucp()`: `category`, `product_type`, `price_max_cents`, `_soft_preferences`). No `good_for_*`, `brand`, `min_ram_gb`, or screen constraints. With this, when you run `populate_ground_truth.py` (with DB and network), every item should have hundreds of products passing the hard filter, and the top-10 by query–product embedding similarity will yield ≥3 ground-truth IDs per item.
- **Current repo state:** The file **`golden_dataset.json`** was **restored from `golden_dataset.json.bak`** so the eval could be run on the **12** items that already had ground truth. So the current `golden_dataset.json` has 12 evaluable items and 38 with 0 ground truth. To get **50 evaluable** items you need to:
  1. Run `expand_golden_to_50` (writes 50 items with minimal UCP for the 39 derivatives, clears ground truth).
  2. Run `populate_ground_truth --golden evaluation/recommendations/golden_dataset.json --top 10 --backup` (requires DB/network).
  3. If any item still has &lt;3 IDs, relax that item’s UCP in `expand_golden_to_50.py` (e.g. raise `price_max_cents`) and re-run expand + populate.

---

## (c) Final run summary (timestamp, metrics from summary JSON)

Eval was run on the **restored** golden (12 items with sufficient ground truth). Latest summary used for the report and figures:

- **Summary file:** `evaluation/recommendations/results/summary_20260313_141910.json`
- **Timestamp:** `20260313_141910`
- **Metrics:**
  - **num_queries:** 12  
  - **num_skipped_insufficient_ground_truth:** 38  
  - **min_ground_truth_ids:** 3  
  - **query_to_ucp (Shopping Agent) mean_ucp_match_rate:** 0.751  
  - **query_to_ucp_baseline mean_ucp_match_rate:** 0.556  
  - **ucp_to_recs (Merchant Agent) mean_hard_pass_rate:** 0.083  
  - **ucp_to_recs mean_soft_similarity:** 0.588  
  - **baseline mean_hard_pass_rate:** 0.158  
  - **baseline mean_soft_similarity:** 0.567  

After you run expand → populate and re-run the full eval on 50 items, a new `summary_YYYYMMDD_HHMMSS.json` will be written; use that with `plot_eval_results.py` to regenerate the case report and PNGs.

---

## (d) Paths to case report and PNGs

- **Case report:**  
  `evaluation/recommendations/case_report.md`

- **PNGs:**  
  - `evaluation/recommendations/results/recommendations_summary_table.png`  
  - `evaluation/recommendations/results/recommendations_scores_by_query.png`  
  - `evaluation/recommendations/results/recommendations_shopping_agent_vs_baseline.png`  
  - `evaluation/recommendations/results/recommendations_ucp_vs_baseline.png`  

To regenerate after a new eval run:
```bash
python -m evaluation.recommendations.plot_eval_results
# or with a specific summary:
python -m evaluation.recommendations.plot_eval_results --summary evaluation/recommendations/results/summary_YYYYMMDD_HHMMSS.json
```

---

## Quick reference: full workflow for 50 evaluable items

```bash
# 1. Expand to 50 items (minimal UCP for previously skipped items)
python -m evaluation.recommendations.expand_golden_to_50

# 2. Populate ground truth (requires DB + network)
python -m evaluation.recommendations.populate_ground_truth --golden evaluation/recommendations/golden_dataset.json --top 10 --backup

# 3. Run evaluation
python -m evaluation.recommendations.run_evaluation --golden evaluation/recommendations/golden_dataset.json

# 4. Regenerate case report and figures (use latest summary in results/)
python -m evaluation.recommendations.plot_eval_results
```
