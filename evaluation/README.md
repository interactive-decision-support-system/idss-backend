# Backend Evaluation

Two **independent** evaluation pipelines:

1. **Agent response evaluation** — DeepEval LLM-as-judge (G-Eval) on chat agent replies.
2. **Recommendations evaluation** — Golden truth dataset, product embeddings, hard/soft scoring, baseline comparison.

---

## Pipeline 1: Agent response evaluation

Evaluates whether the chat agent’s replies are relevant and helpful (G-Eval).

### Run

```bash
# From repo root (ensure OPENAI_API_KEY in .env or env)
pip install -r evaluation/requirements.txt   # once
python -m evaluation.agent_response.run_eval
```

**Optional — open-source judge (Ollama):**

```bash
export OLLAMA_MODEL_NAME=llama3.2   # or mistral, etc.
python -m evaluation.agent_response.run_eval
```

### Output

- `evaluation/agent_response/results/agent_response_eval_results.json`
- `evaluation/agent_response/results/agent_response_eval_results.csv`

Columns: `test_id`, `user_query`, `agent_message`, `score`, `passed`, `details`.

### Test cases

- `evaluation/agent_response/test_cases.json` — edit to add/change (user_query, optional expected_topic_or_criteria).

---

## Pipeline 2: Recommendations evaluation

Golden dataset: **user_query**, **expected UCP** (hard/soft filters), **ground_truth_product_ids** (~10).  
Hard constraints checked directly; soft constraints = similarity (embeddings) vs golden set.  
We test **Query→UCP** (chat agent filter extraction) and **UCP→recs** (merchant agent only). **Full system (query→recs)** is not run because the chat agent is multiturn and often returns a question instead of recommendations. **Baseline** (LLM + DB, same scoring) is optional.

### Setup

1. **Create product_embeddings table** (once):

   ```bash
   psql $DATABASE_URL -f mcp-server/scripts/create_product_embeddings_table.sql
   ```

2. **Populate laptop embeddings** (idempotent):

   ```bash
   python -m evaluation.recommendations.populate_product_embeddings
   ```

3. **Populate ground-truth product IDs** (filters DB by hard constraints, then top 10 by query–product embedding similarity):

   ```bash
   python -m evaluation.recommendations.populate_ground_truth [--golden path/to/golden_dataset.json] [--top 10] [--backup]
   ```

   This overwrites `ground_truth_product_ids` in the golden file. Use `--backup` to write a `.json.bak` first.

### Run

```bash
python -m evaluation.recommendations.run_evaluation [--golden path/to/golden.json] [--skip-baseline]
```

`.env` is loaded from repo root (OPENAI_API_KEY, DATABASE_URL).

### Output (per run) in `evaluation/recommendations/results/`

| File | Description |
|------|--------------|
| `query_to_ucp_results_<ts>.csv` / `.json` | Query → UCP match rate (chat agent filter extraction) |
| `ucp_to_recs_results_<ts>.csv` / `.json` | UCP → recs: hard pass rate, mean soft similarity (merchant agent only) |
| `baseline_results_<ts>.csv` / `.json` | Baseline LLM recs (omit with `--skip-baseline`) |
| `summary_<ts>.json` | Aggregate metrics (query_to_ucp, ucp_to_recs, baseline) |

---

## Components (Pipeline 2)

- **schema.py** / **golden_dataset.json**: Golden item (user_query, expected_ucp, ground_truth_product_ids).
- **scoring.py**: Hard constraint pass rate; soft = mean similarity to ground truth (1/(1+L2 distance)).
- **baseline.py**: LLM with schema + sample products → product IDs; same scoring.
- **runners.py**: (a) Query→UCP, (b) UCP→recs, (d) baseline. (Full query→recs is not run; see above.)
- **populate_ground_truth.py**: Fills `ground_truth_product_ids` per item: filter laptops by expected_ucp hard constraints, then top 10 by user_query–product embedding similarity.
- **populate_product_embeddings.py**: Laptops only; text from name, description, category, brand, product_type, model, series, attributes; model `all-mpnet-base-v2`; upserts into `product_embeddings`.
