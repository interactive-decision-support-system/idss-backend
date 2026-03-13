# Evaluation

This document describes the two evaluation pipelines, their metrics, baselines, and how to interpret the results and figures.

---

## Overview

The backend has **two independent evaluation pipelines**:

| Pipeline | Purpose |
|----------|--------|
| **1. Agent response evaluation** | Assess whether the chat agent’s replies are relevant and helpful (LLM-as-judge). |
| **2. Recommendations evaluation** | Assess filter extraction (query→UCP) and product recommendations (UCP→recs) against a golden dataset and baselines. |

Full end-to-end (query→recs) is **not** run for Pipeline 2: the chat agent is multiturn and often returns a clarifying question instead of recommendations, so recs-based metrics would be misleading. The pipelines evaluate **Shopping Agent** (query→UCP) and **Merchant Agent** (UCP→recs) separately.

---

## Pipeline 1: Agent Response Evaluation

### What it does

- Sends **user queries** from a test set to the chat agent (`process_chat`).
- Collects the **agent’s reply** (raw message text).
- Scores each reply with **DeepEval G-Eval** (LLM-as-judge) for relevance and helpfulness.

### Metrics

| Metric | Description |
|--------|-------------|
| **Score** | Scalar in [0, 1] from the judge. Higher = more relevant/helpful. |
| **Pass** | Binary: `score ≥ 0.5` (configurable threshold). |
| **Details** | Judge’s reason and threshold (or error if evaluation failed). |

The judge uses a **rubric**: the response is helpful if it either (1) gives laptop recommendations that match the user’s needs, or (2) asks a **relevant** clarifying question (e.g., RAM, brand, screen size). Replies should be on-topic (laptops), coherent, and appropriate for a recommendation assistant. Optional `expected_topic_or_criteria` in test cases can refine the bar per query.

### Baselines

- **Baseline (agent response):** No full agent. Uses the same test cases; for each query it:
  1. Extracts minimal filters (budget, brand) from the query via a small LLM call.
  2. Searches the **Supabase product store** with those filters.
  3. Formats a short message from the returned products (or a no-results message).

  So the baseline is “LLM + DB only”: same product store as the agent, no interview logic, no session. Same G-Eval rubric and threshold are applied to baseline replies for comparison.

### Test cases

- **Default (mixed):** `evaluation/agent_response/test_cases.json` — greeting, vehicles, laptops, books, reset.
- **Laptop-only (default in code):** Loaded from `testing/query_data_enriched.csv` (shortened_query as user_query). Use `USE_LAPTOP_QUERIES=0` to use `test_cases.json` instead.

### Results and figures

- **Output files:** `evaluation/agent_response/results/agent_response_eval_results.json` and `.csv` (and `_baseline.json` / `_baseline.csv` when run with `--baseline`).
- **Figures** (from `python -m evaluation.agent_response.plot_eval_results` [optional `--baseline`]):

| Figure | Description |
|--------|-------------|
| **agent_response_summary_table.png** | Table: N, average score, pass %. Without baseline: by difficulty (Quick ≤200 chars, Long, All). With baseline: two rows (Baseline vs Agent). |
| **agent_response_scores_by_query.png** | Score vs query rank (x = rank by agent score, y = score). With baseline, Agent and Baseline curves are overlaid. Dashed line at 0.5 = pass threshold. |
| **agent_response_agent_vs_baseline.png** | (Only with `--baseline`.) Scatter: x = baseline score, y = agent score. Points **above** the diagonal mean the agent scored higher. |

### Brief interpretation

- **Empty agent_message (score 0):** Usually environment/setup (e.g., missing Redis or API key), not parsing — the agent exception is in `agent_response_rows.json`.
- **Non-empty but failed (score &lt; 0.5):** Judge found the reply too generic, off-topic, or unhelpful; improving the agent’s wording or clarification strategy improves this.
- Comparing Agent vs Baseline in the scatter plot shows where the full agent (with interview and tools) beats the simple “extract filters → search DB → format” baseline.

---

## Pipeline 2: Recommendations Evaluation

### What it does

- Uses a **golden dataset** (`golden_dataset.json`): each item has `user_query`, `expected_ucp` (target filters), and `ground_truth_product_ids` (ideal product IDs).
- Runs four evaluation paths:
  1. **Query→UCP (Shopping Agent):** Agent produces filters from `user_query`; compare to `expected_ucp` (hard keys + soft preferences).
  2. **Query→UCP baseline:** LLM extracts filters from query (no agent); compare to `expected_ucp`.
  3. **UCP→recs (Merchant Agent):** Search using **hard keys only** from `expected_ucp`; score returned products (hard pass rate + mean similarity).
  4. **Recs baseline:** LLM extracts filters from query, search Supabase, score with same hard/soft (optional, `--skip-baseline` to omit).

Items with fewer than 3 `ground_truth_product_ids` are skipped (configurable `MIN_GROUND_TRUTH_IDS`).

### Metrics

| Stage | Metric | Description |
|-------|--------|-------------|
| **Query→UCP** | **Expected UCP match rate** | Fraction of expected keys that match. **Hard keys:** all top-level `expected_ucp` keys (e.g. price_max_cents, brand, min_ram_gb, min/max_screen_size, good_for_*). **Soft preferences:** `expected_ucp._soft_preferences` (e.g. `use_case`, `liked_features`). Total match = (hard_matches + soft_matches) / (hard_total + soft_total). Per-query in [0, 1]; aggregate = mean over queries. |
| **UCP→recs** | **Hard pass rate** | Fraction of recommended products that satisfy **all** hard constraints from `expected_ucp`. Hard keys are those in `scoring.HARD_KEYS` (price_max_cents, price_min_cents, brand, product_type, category, min_ram_gb, min_screen_size, max_screen_size, good_for_gaming, good_for_ml, good_for_creative, good_for_web_dev). |
| **UCP→recs** | **Mean similarity** | For each recommended product, similarity to the ground-truth set in embedding space: `1 / (1 + L2 distance)`; then mean over recommendations. 0 if no ground truth IDs. Uses `product_embeddings` (model `all-mpnet-base-v2`) or on-the-fly encoding. |

**Note:** UCP→recs **search** uses only hard keys from `expected_ucp` (mapped to what `search_products` expects, e.g. min_screen_size→min_screen_inches, good_for_*→use_cases). Soft preferences are not sent to search; they only affect the Query→UCP match rate.

### Baselines

| Baseline | Description |
|----------|-------------|
| **Query→UCP baseline** | Same LLM-based filter extraction as the recs baseline (price_max_dollars, brand); no agent. Produced filters are compared to `expected_ucp` with the same UCP match rate (hard + _soft_preferences). |
| **Recs baseline** | LLM extracts filters from user query → `search_products` on Supabase only → same `hard_pass_rate` and `mean_soft_similarity` as Merchant Agent. |

### Golden dataset and setup

- **Schema:** Each item: `query_id`, `user_query`, `expected_ucp`, `ground_truth_product_ids`. `expected_ucp` may include top-level hard keys and an optional `_soft_preferences` object (e.g. `use_case`, `liked_features`).
- **Ground truth:** Populated by `populate_ground_truth.py` (filter by expected_ucp, then top-N by query–product embedding similarity). Run `populate_product_embeddings` first.

### Results and figures

- **Output files** (under `evaluation/recommendations/results/`, timestamped `<ts>` = `YYYYMMDD_HHMMSS`):
  - `query_to_ucp_results_<ts>.csv` / `.json` — Shopping Agent query→UCP (includes `produced_ucp`, `expected_ucp`, `expected_ucp_match`).
  - `query_to_ucp_baseline_results_<ts>.csv` / `.json` — Query→UCP baseline.
  - `ucp_to_recs_results_<ts>.csv` / `.json` — Merchant Agent UCP→recs.
  - `baseline_results_<ts>.csv` / `.json` — Recs baseline (if not `--skip-baseline`).
  - `summary_<ts>.json` — Aggregates (mean UCP match rate, mean hard pass rate, mean similarity) for all runs.
- **Figures** (from `python -m evaluation.recommendations.plot_eval_results [--summary results/summary_<ts>.json]`):

| Figure | Description |
|--------|-------------|
| **recommendations_summary_table.png** | Table of stages (Shopping Agent, Baseline query→UCP, Merchant Agent, Baseline recs) with N and mean metrics. Rows show “Mean UCP match rate”, “Mean hard pass rate”, and “Mean similarity”. |
| **recommendations_scores_by_query.png** | Mean similarity vs query rank (sorted by Merchant Agent). Y-axis “Mean similarity”; Merchant Agent and Baseline overlaid when baseline is present. |
| **recommendations_shopping_agent_vs_baseline.png** | (When query→UCP baseline exists.) Scatter: x = baseline UCP match rate, y = Shopping Agent UCP match rate. Axes use data-driven limits with padding (clamped 0–1.08). Points **above** the diagonal = Shopping Agent better. |
| **recommendations_ucp_vs_baseline.png** | (When recs baseline exists.) Scatter: x = baseline mean similarity, y = Merchant Agent mean similarity. Axes use data-driven limits with padding. Points **above** the diagonal = Merchant Agent better. |

A **case_report.md** is also written in the recommendations module, summarizing aggregates and listing lowest-UCP-match and zero-hard-pass queries.

### Brief interpretation

- **Shopping Agent (query→UCP):** UCP match rate now includes both hard filters and `_soft_preferences` (e.g. use_case, liked_features). So a lower rate can reflect mismatches on soft preferences as well as hard keys. Shopping Agent and query→UCP baseline can be close when the golden `expected_ucp` has many keys (including soft) that the simple LLM extractor does not populate.
- **Merchant Agent (UCP→recs):** Hard pass rate is often low because search uses only hard keys and may relax filters; returned products can still violate some hard constraints. Mean similarity measures how close recommendations are to the ground-truth set in embedding space.
- **Summary JSON** (`summary_<ts>.json`) is the single place for aggregate numbers (e.g. for slides or reports).

---

## How to run

- **Pipeline 1:**  
  `python -m evaluation.agent_response.run_eval`  
  Optional: `python -m evaluation.agent_response.run_eval --baseline`  
  Then: `python -m evaluation.agent_response.plot_eval_results [--baseline]`

- **Pipeline 2:**  
  `python -m evaluation.recommendations.run_evaluation [--golden path/to/golden.json] [--skip-baseline] [--ucp-recs-only]`  
  Then: `python -m evaluation.recommendations.plot_eval_results [--summary results/summary_<ts>.json] [--outdir results]`

See `evaluation/README.md` for environment setup (e.g. `OPENAI_API_KEY`, `DATABASE_URL`, Redis, product embeddings, ground truth population).
