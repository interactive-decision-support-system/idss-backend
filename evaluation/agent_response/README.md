# Pipeline 1: Agent response evaluation (DeepEval LLM-as-judge)

Evaluates chat agent responses using DeepEval's G-Eval (LLM-as-judge) for relevance and helpfulness.

## What the score is (the metric)

The **score** is produced by **G-Eval**: an **LLM-as-judge** metric from DeepEval.

- **Inputs to the judge:** (1) the user query, (2) the agent’s reply (exact text), (3) a short rubric (expected behavior).
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

The baseline is a **fixed clarifying question** for every query (no agent, no API/Redis):

```bash
python -m evaluation.agent_response.run_eval --baseline
```

Writes `agent_response_eval_results_baseline.json` and `.csv`. Same test cases and G-Eval rubric; only the response is the fixed string. Use with the poster summary to get a Baseline vs Agent table.

## Poster summary (numeric table by query difficulty)

```bash
python -m evaluation.agent_response.summarize_for_poster
```

Prints a compact table with **N**, **Avg score**, and **Pass %** for Quick (short queries), Long (long queries), and All. Optional: `--cutoff 200` to change the character cutoff for Quick vs Long (default 200).

**Comparison table (Baseline vs Agent):** After running the agent eval and the baseline eval (`run_eval` and `run_eval --baseline`), run:

```bash
python -m evaluation.agent_response.summarize_for_poster --baseline
```

Prints a two-row table: **Baseline** and **Agent** with N, Avg score, Pass %. Optional: `--baseline /path/to/baseline_results.json` to point to a different baseline file.

## Pytest

```bash
pytest evaluation/agent_response/ -v
# or
pytest evaluation/agent_response/test_agent_response_eval.py -v
```

## Test cases

Edit `test_cases.json`: each entry has `test_id`, `user_query`, and optional `expected_topic_or_criteria` used by the judge.
