# Evaluation Findings (Sample Run)

Summary of a single run of both pipelines for presentation. Re-run the evals to refresh numbers.

---

## Pipeline 1: Agent response evaluation (DeepEval G-Eval)

**Metric:** Relevance/helpfulness (LLM-as-judge). Threshold: 0.5.

| test_id          | user_query                              | passed | score (0–1) |
|------------------|----------------------------------------|--------|-------------|
| greeting_1       | Hi                                     | ✓      | 0.77        |
| vehicles_intent_1| I'm looking for a used car under $15,000| ✓      | 0.70        |
| laptops_intent_1 | I need a laptop for programming        | ✓      | 0.67        |
| books_intent_1   | Recommend me some sci-fi books         | ✗      | 0.10        |
| reset_1          | Start over                             | ✓      | 1.00        |

**Summary:** 4/5 passed. The books case failed because the catalog returned no sci-fi books (empty result), so the judge correctly marked the response as unhelpful.

**Artifacts:** `evaluation/agent_response/results/agent_response_eval_results.csv` (and `.json`).

---

## Pipeline 2: Recommendations evaluation

### Query → UCP (chat agent filter extraction)

**Metric:** Fraction of expected UCP keys that match the agent’s produced filters.

| query_id | query (abbreviated)                    | expected_ucp_match |
|----------|----------------------------------------|--------------------|
| q1       | Gaming laptop under $2000              | 1.0                |
| q2       | MacBook coding, ~$1500                 | 1.0                |
| q3       | Cheap laptop students under $600        | 1.0                |
| q4       | Dell XPS 16GB for work                 | 0.8                |
| q5       | Lightweight 14", under $1200           | 1.0                |
| q6       | Laptop for video editing               | 1.0                |
| q7       | 32GB RAM for ML                        | 0.75               |

**Aggregate:** Mean UCP match rate ≈ **93.6%**.

**Artifacts:** `evaluation/recommendations/results/query_to_ucp_results_<ts>.csv`.

---

### UCP → recs (merchant/MCP only)

**Metrics:** Hard constraint pass rate (% of recs satisfying expected UCP); mean soft similarity to ground truth (0 when no ground truth IDs set).

| query_id | query (abbreviated) | hard_pass_rate | mean_soft_similarity | recommended_count |
|----------|---------------------|----------------|----------------------|--------------------|
| q1       | Gaming under $2000   | 0.0            | 0.0                  | 10                 |
| q2       | MacBook ~$1500       | 0.0            | 0.0                  | 5                  |
| q3       | Students under $600  | 1.0            | 0.0                  | 10                 |
| q4–q7    | …                    | 0.0            | 0.0                  | 6–10               |

**Aggregate:** Mean hard pass rate ≈ **14.3%**; mean soft similarity = **0** (ground_truth_product_ids empty in golden set).

**Artifacts:** `evaluation/recommendations/results/ucp_to_recs_results_<ts>.csv`.

**Note:** Full system (query → recs) is **not** run: the chat agent is multiturn and often returns a question instead of recommendations, so recs-based metrics would be misleading. We evaluate the merchant agent (UCP → recs) separately.

---

### Baseline (optional)

Run without `--skip-baseline` to compare an out-of-the-box LLM (schema + sample products + user query) with the same hard/soft scoring. Output: `evaluation/recommendations/results/baseline_results_<ts>.csv`.

---

## Summary JSON (Pipeline 2)

`evaluation/recommendations/results/summary_<ts>.json` contains:

- `timestamp`, `golden_path`, `num_queries`
- `aggregates.query_to_ucp.mean_ucp_match_rate`
- `aggregates.ucp_to_recs.mean_hard_pass_rate`, `mean_soft_similarity`
- `aggregates.baseline` (if run)

Use these aggregates for slides or reports.

---

## How to improve numbers

1. **Pipeline 1:** Add or fix book catalog so sci-fi queries return results; add more test cases in `test_cases.json`.
2. **Pipeline 2:**  
   - Run `python -m evaluation.recommendations.populate_ground_truth` to fill `ground_truth_product_ids` (filter by hard constraints, then top 10 by query–product embedding similarity).  
   - Run `populate_product_embeddings` so embeddings exist for scoring.  
   - Align hard-constraint checks in `scoring.py` with the actual product fields returned by the agent/MCP (e.g. price_cents, good_for_* in attributes).
