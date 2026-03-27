# Question 2 Reproduction Guide (Cache Coherence)

## 1) Environment setup

From repo root (`idss-backend`):

```bash
source .venv/bin/activate
```

Use one of the two benchmark modes below.

## 2) Run local benchmark (local Postgres + local Redis)


```bash
DATABASE_URL="postgresql://<name>@localhost:5432/mcp_ecommerce" \
REDIS_HOST="localhost" \
REDIS_PORT="6379" \
UPSTASH_REDIS_URL="" \
python scripts/run_agent_latency_benchmark.py --no-llm -n 5 \
  | tee local_output.txt
```

## 3) Run remote benchmark (Supabase + Upstash)


```bash
DATABASE_URL="<supabase_postgres_url>" \
SUPABASE_URL="<supabase_url>" \
SUPABASE_KEY="<supabase_key>" \
UPSTASH_REDIS_URL="<upstash_rediss_url>" \
UPSTASH_REDIS_REST_URL="<upstash_rest_url>" \
UPSTASH_REDIS_REST_TOKEN="<upstash_rest_token>" \
python scripts/run_agent_latency_benchmark.py --no-llm -n 5 \
  | tee remote_output.txt
```

## 4) Run full backend+agent tests

```bash
PYTHONPATH=. pytest mcp-server/tests agent/tests -v 
```

## 6) Files generated for report evidence

- `local_output.txt`
- `remote_output.txt`
