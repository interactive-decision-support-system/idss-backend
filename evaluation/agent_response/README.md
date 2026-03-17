# Pipeline 1: Agent response evaluation (custom LLM-as-judge)

Evaluates chat agent responses using a **custom LLM-as-judge** (no third-party eval library) for relevance and helpfulness. Supports **single-turn** and **multi-turn** conversations per test case.

## Metrics (custom judge, G-Eval style)

The pipeline uses a single **LLM call per test case** that mimics G-Eval–style evaluation:

- **Input to the judge:**  
  - **User input:** For single-turn, the user’s query. For multi-turn, the full conversation (all user turns), e.g. `User (turn 1): ...`, `User (turn 2): ...`.  
  - **Actual output:** The agent’s reply. For multi-turn, the concatenation of all agent replies (with turn labels).  
  - **Expected behavior / criteria:** A rubric string (per test case or default) describing what “helpful” means.

- **Rubric (criteria):**  
  The response is helpful if it (1) gives laptop recommendations that match the user’s stated needs (budget, use case, preferences), or (2) asks a **relevant** clarifying question (e.g. RAM, brand, screen size). It should be on-topic (laptops), coherent, and appropriate for a recommendation assistant. If the test case provides `expected_topic_or_criteria`, the judge uses that as the rubric.

- **Judge output:**  
  The LLM returns a **score in [0, 1]** and a short **reason**. The pipeline parses this (JSON or “Score: 0.7” style) and sets **passed = (score ≥ threshold)**.

- **Threshold:**  
  **0.5** — scores below 0.5 are failures; 0.5 and above are passes.

- **No parsing of agent output:**  
  The agent’s raw message is sent to the judge; we do not parse structure or intents. The score reflects the judge’s assessment of relevance and helpfulness only.

**Judge model:** OpenAI (default `gpt-4o-mini`, overridable with `OPENAI_JUDGE_MODEL`) or Ollama when `OLLAMA_MODEL_NAME` is set. No DeepEval dependency.

## Why tests fail: parsing vs agent vs environment

- **Empty `agent_message` (score 0, “empty agent response”):** **Environment/setup**, not parsing or agent logic. The agent process raised an exception before returning (e.g. `No module named 'redis'` or Redis connection failed). Check `agent_response_rows.json`: rows with empty message have an `"error"` field (e.g. missing `redis`). Fix: install dependencies and ensure Redis is available (or `UPSTASH_REDIS_URL`), then re-run the agent.
- **Non-empty but failed (score &lt; 0.5):** **Agent behavior.** The judge received the agent’s text correctly (no parsing). It scored the *content* as too generic, redundant, or not addressing the user’s stated needs. Improving these means changing how the agent replies (e.g. more specific questions, acknowledging budget/use case), not the eval pipeline.

So: **parsing is not the failure mode**—we pass the agent’s reply through as-is. Failures are either **environment** (empty due to exception) or **agent quality** (low score from the judge).

### Why the agent often underperforms (current run)

In the latest run the agent passes ~46% of cases (23/50) while the baseline passes more. See **`evaluation/agent_response/case_report.md`** for the full breakdown. Main reasons:

1. **Wrong product type** — Search relaxation sometimes returns non-laptops (e.g. mouse, number pad, motherboard); the agent presents them as the best pick and the judge fails the response.
2. **Recommendations ignore constraints** — Recs that miss stated requirements (e.g. 17" when user asked 14", Chromebooks when user needs Windows) are penalized.
3. **Generic clarifying questions** — Asking “What will you use it for?” or “What brand?” when the user already gave a detailed use case scores lower than direct recs.
4. **Catalog gaps** — Queries with no good match still get something (wrong form factor or product type); the judge fails when the reply doesn’t fit.
5. **Baseline favors direct recs** — The baseline always returns catalog-backed recommendations; for well-specified queries that often scores higher than the agent’s question or relaxed recs.

## Setup

- **Agent**: Requires `OPENAI_API_KEY` (and optionally `.env` in repo root) so the chat agent can run.
- **Redis**: The agent uses Redis for session persistence and search-result caching. If Redis is not running (and `UPSTASH_REDIS_URL` is not set), you may see connection errors during the eval. **Fix:** either start a local Redis server (`redis-server`) or set `UPSTASH_REDIS_URL` to a cloud Redis instance (e.g. Upstash). The MCP cache lives in `mcp-server/app/cache.py` and connects to `localhost:6379` by default when no Upstash URL is set.
- **Judge**: Custom LLM-as-judge. Uses OpenAI (`OPENAI_JUDGE_MODEL` or `gpt-4o-mini`) unless `OLLAMA_MODEL_NAME` is set, in which case the judge uses Ollama (e.g. `llama3.2`, `mistral`).

```bash
# From repo root
pip install -r evaluation/requirements.txt

# Optional: use local Ollama as judge
export OLLAMA_MODEL_NAME=llama3.2
# Ensure Ollama is running: ollama run llama3.2
```

## Run

```bash
# From repo root
python -m evaluation.agent_response.run_eval
```

Output:

- `evaluation/agent_response/results/agent_response_eval_results.json`
- `evaluation/agent_response/results/agent_response_eval_results.csv`

Columns: `test_id`, `user_query`, `agent_message`, `score`, `passed`, `details`.

### Baseline (for comparison table)

The baseline is **restricted to recommendations from the Supabase database** (same product store as the agent). It extracts minimal filters (budget, brand) from the query via a small LLM call, runs `search_products` against the store, and formats a short message listing only those products (or a no-results message if none). No agent interview logic, no session. Requires `DATABASE_URL` or `SUPABASE_URL`/`SUPABASE_KEY` and `OPENAI_API_KEY`.

```bash
python -m evaluation.agent_response.run_eval --baseline
```

Writes `agent_response_eval_results_baseline.json` and `.csv`. Same test cases and judge rubric. Use with `plot_eval_results --baseline` to generate comparison figures.

## Eval figures (PNG)

`plot_eval_results` generates PNG figures from the eval results:

```bash
python -m evaluation.agent_response.plot_eval_results
```

Writes to `evaluation/agent_response/results/` by default:

- **agent_response_summary_table.png** — Table of N, Avg score, Pass %. Without baseline: by difficulty (Quick ≤200 chars, Long, All). With `--baseline`: two rows (Baseline vs Agent).
- **agent_response_scores_by_query.png** — Score vs query number (x = query index, y = score). With `--baseline`, both Agent and Baseline are overlaid (two colors).
- **agent_response_agent_vs_baseline.png** — Only when `--baseline` is used: scatter of Agent score (y) vs Baseline score (x); points above the diagonal mean the agent scored higher.

**With baseline comparison** (run agent and baseline evals first):

```bash
python -m evaluation.agent_response.plot_eval_results --baseline
```

Options: `--json path/to/agent_results.json`, `--baseline [path/to/baseline.json]`, `--cutoff 200` (Quick/Long split), `--outdir path` (where to write PNGs).

## Pytest

```bash
pytest evaluation/agent_response/ -v
# or
pytest evaluation/agent_response/test_agent_response_eval.py -v
```

## Test cases

Edit `test_cases.json`: each entry has `test_id`, optional `user_query`, and optional `expected_topic_or_criteria` used by the judge.

### Single-turn (backward compatible)

Use `user_query` for one message; the pipeline runs one turn and passes that response to the judge.

```json
{ "test_id": "greeting_1", "user_query": "Hi", "expected_topic_or_criteria": "..." }
```

### Multi-turn

Use `messages`: a list of user messages in order. The pipeline runs the full conversation with one **session_id** per test case (so the agent keeps state across turns), then passes the full conversation (all user messages) and the concatenation of all agent responses to the judge.

```json
{
  "test_id": "laptop_multiturn_1",
  "messages": [
    "I need a laptop for programming under $1000",
    "16GB RAM is fine",
    "Yes, show me some options"
  ],
  "expected_topic_or_criteria": "Agent should clarify needs then provide relevant laptop recommendations."
}
```

- **Backward compatibility:** If a test case has only `user_query` (no `messages`), it is treated as a single message and runs one turn.
- **Dynamic multi-turn (response-based):** For cases with a single initial message (e.g. from CSV), set **`MULTI_TURN_DYNAMIC_TURNS=2`** (or 1, 3) so the pipeline runs extra turns. After each agent response, an LLM **generates** the next user message from the conversation (e.g. answers a clarifying question or says "show me options"). No hardcoded follow-ups—the next user message is always derived from the agent's last reply.
  ```bash
  MULTI_TURN_DYNAMIC_TURNS=2 python -m evaluation.agent_response.run_eval
  ```
  Total user turns = 1 + `MULTI_TURN_DYNAMIC_TURNS`. The judge still receives the full conversation and all agent responses.
