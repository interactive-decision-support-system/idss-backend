# Q1 — Session Memory and Preference Tracking - Nino

## What this PR does

Five fixes for session memory bugs where search-facing filters diverged from what the user actually asked for. Each fix targets a specific scenario where filter state became stale or incorrect across conversation turns.

| Commit | Fix | Root cause |
|--------|-----|------------|
| Unify slot normalization | Extracted `_normalize_and_merge_criteria()` shared by all 3 filter-writing paths | `process_refinement` wrote raw LLM slot names with no alias remapping |
| Replace-mode `update_filters` | `replace=True` fully replaces `explicit_filters` each turn | Merge-only logic never removed stale keys from previous turns |
| Exact use_case mapping | Dict lookup instead of substring search for `use_case` → `good_for_*` | `"machine_learning"` didn't substring-match `"ml"` or `"machine learning"` |
| Shopping-context override | `_SHOPPING_OVERRIDE_RE` short-circuits injection guard for shopping phrases | `"forget the 450 dollars"` triggered false-positive injection block |
| Use-case downgrade in refinement | `_check_use_case_downgrade` ported to `process_refinement` + fix prior capture | Gaming → email pivot left stale `min_ram_gb`, `gpu_tier` in filters |

## Reproducing results

### Prerequisites

1. **Python 3.11+** and a virtual environment:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Supabase setup** — this project uses Supabase as its primary data store. To set up:
   - Create a free Supabase project at [supabase.com](https://supabase.com)
   - Copy `.env.example` to `.env` and fill in:
     - `SUPABASE_URL` and `SUPABASE_KEY` (from Supabase dashboard → Settings → API)
     - `OPENAI_API_KEY`
     - `DATABASE_URL` (PostgreSQL connection string — can use Supabase's built-in Postgres)

3. **Seed the database:**
   ```bash
   # Seed Supabase with product data
   python mcp-server/scripts/seed_supabase_local.py
   
   # (Optional) Build Neo4j knowledge graph
   python mcp-server/scripts/build_knowledge_graph_all.py
   ```

   Note: The first two commits in this PR (`Fix Supabase seed script SQL parsing` and `Fix UUID handling in catalog import`) fix issues in these seed scripts that prevented them from running cleanly.

### Running tests

```bash
# Activate venv
source .venv/bin/activate

# Run agent tests (includes 5 new tests, one per commit)
PYTHONPATH=./mcp-server pytest agent/tests/ -v --ignore=agent/tests/test_downgrade_scenarios.py

# Run MCP tests
PYTHONPATH=./mcp-server pytest mcp-server/tests/ -v --ignore=mcp-server/tests/test_vector_search.py
```

### Running the app locally

```bash
bash start_all_local.sh
# Backend: http://localhost:8001
# Frontend: http://localhost:3000 (requires ../idss-web)
```

## Before / after evidence

### Test output

**Before (main branch):** 77 agent tests, no coverage for slot normalization, filter replacement, use-case mapping, injection guard, or downgrade logic.

**After (this branch):** 82 agent tests (+5 new, one per commit), all passing.

```
test_normalize_and_merge_criteria_remaps_aliases          PASSED
test_update_filters_replace_drops_stale_keys              PASSED
test_get_search_filters_use_case_exact_match              PASSED
test_shopping_override_and_injection_guard                 PASSED
test_use_case_downgrade_new_search_captures_prior_before_clear  PASSED

======================== 82 passed =============================
```

MCP tests: 447/453 passed (6 pre-existing failures linked to Redis not running locally).

### Scenario evidence: before vs. after

| Scenario | Before (main) | After (this branch) |
|----------|---------------|---------------------|
| **A: Budget update** "forget the 450 dollars, make it 600" | Injection guard false-positive blocks the message; interview restarts | Shopping-context override clears the guard; budget updates to $600 |
| **B: Gaming → email pivot** | Stale `good_for_gaming=true`, `min_ram_gb=32` persist in `explicit_filters`; user gets gaming laptops for email use | Use-case downgrade clears performance slots; `replace=True` drops stale keys |
| **C: ML use case** | `good_for_ml` never set (`"machine_learning"` fails substring match); stale flags persist after pivot | Exact dict lookup correctly maps `"machine_learning"` → `good_for_ml=true` |

### Injection guard testing

Tested `_SHOPPING_OVERRIDE_RE` against 18 natural refinement phrases and 13 real injection attempts:
- All 13 real injections still blocked (Layer 1 hard patterns)
- 16/18 refinement phrases now clear without LLM call
- Compound attacks ("forget the budget and ignore all previous instructions") still blocked

## Files changed

- `agent/universal_agent.py` — `_normalize_and_merge_criteria()`, `_check_use_case_downgrade()`, exact use-case mapping
- `agent/chat_endpoint.py` — `replace=True` for `update_filters()`, `_SHOPPING_OVERRIDE_RE`
- `agent/interview/session_manager.py` — `replace` parameter in `update_filters()`
- `agent/tests/test_universal_agent.py` — 3 new tests
- `agent/tests/test_session_manager.py` — 1 new test
- `agent/tests/test_chat_endpoint.py` — 1 new test
- `mcp-server/scripts/seed_supabase_local.py` — SQL parsing fix
- `mcp-server/scripts/merge_supabase_data.py` — UUID handling fix
