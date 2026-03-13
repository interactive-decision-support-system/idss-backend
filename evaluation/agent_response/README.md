# Pipeline 1: Agent response evaluation (DeepEval LLM-as-judge)

Evaluates chat agent responses using DeepEval's G-Eval (LLM-as-judge) for relevance and helpfulness. Supports **single-turn** and **multi-turn** conversations per test case.

## What the score is (the metric)

The **score** is produced by **G-Eval**: an **LLM-as-judge** metric from DeepEval.

- **Inputs to the judge:** (1) the user query, (2) the agent’s reply (exact text), (3) a short rubric (expected behavior).
- For **multi-turn** test cases, the judge receives the full conversation (all user turns) and the concatenated agent responses, so it can evaluate coherence and helpfulness across turns.
- **Rubric:** The response counts as helpful if it either gives laptop recommendations that match the user’s needs, or asks a **relevant** clarifying question (e.g. RAM, brand, screen size). It should be on-topic (laptops), coherent, and appropriate for a recommendation assistant.
- **Output:** A single **scalar score in [0, 1]** from the judge model. We use **threshold 0.5** for pass/fail. So “score” = how relevant/helpful the judge considers the reply; higher = more on-topic and useful. **Not** parsing accuracy—we don’t parse the agent output; we send the raw message to the judge.

## Why tests fail: parsing vs agent vs environment

- **Empty `agent_message` (score 0, “empty agent response”):** **Environment/setup**, not parsing or agent logic. The agent process raised an exception before returning (e.g. `No module named 'redis'` or Redis connection failed). Check `agent_response_rows.json`: rows with empty message have an `"error"` field (e.g. missing `redis`). Fix: install dependencies and ensure Redis is available (or `UPSTASH_REDIS_URL`), then re-run the agent.
- **Non-empty but failed (score &lt; 0.5):** **Agent behavior.** The judge received the agent’s text correctly (no parsing). It scored the *content* as too generic, redundant, or not addressing the user’s stated needs. Improving these means changing how the agent replies (e.g. more specific questions, acknowledging budget/use case), not the eval pipeline.

So: **parsing is not the failure mode**—we pass the agent’s reply through as-is. Failures are either **environment** (empty due to exception) or **agent quality** (low score from the judge).

## Setup

- **Agent**: Requires `OPENAI_API_KEY` (and optionally `.env` in repo root) so the chat agent can run.
- **Redis**: The agent uses Redis for session persistence and search-result caching. If Redis is not running (and `UPSTASH_REDIS_URL` is not set), you may see connection errors during the eval. **Fix:** either start a local Redis server (`redis-server`) or set `UPSTASH_REDIS_URL` to a cloud Redis instance (e.g. Upstash). The MCP cache lives in `mcp-server/app/cache.py` and connects to `localhost:6379` by default when no Upstash URL is set.
- **Judge**: Optional open-source judge via Ollama. Set `OLLAMA_MODEL_NAME` (e.g. `llama3.2`, `mistral`, `deepseek-r1:1.5b`) and run Ollama locally. If unset, DeepEval uses its default model (GPT).

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

Writes `agent_response_eval_results_baseline.json` and `.csv`. Same test cases and G-Eval rubric. Use with `plot_eval_results --baseline` to generate comparison figures.

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
  Total user turns = 1 + `MULTI_TURN_DYNAMIC_TURNS`. The judge still receives the full conversation and all agent responses (same G-Eval as today).
- **DeepEval multi-turn:** DeepEval also provides [ConversationalTestCase](https://deepeval.com/docs/evaluation-multiturn-test-cases) with `turns=[Turn(role, content), ...]` and conversational metrics (e.g. TurnRelevancyMetric). This pipeline uses a single `LLMTestCase` with the full conversation as input and concatenated agent output; you can switch to `ConversationalTestCase` + conversational metrics if you want per-turn evaluation.
