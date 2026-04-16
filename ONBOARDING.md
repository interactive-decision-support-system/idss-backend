
onboarding doc for spring quarter LDR lab new members 
Made by Juli Huang

Welcome to the IDSS Onboarding Guide!


Hey, welcome to the project! This doc will get you from zero to "I understand what's going on" as fast as possible. Read it top to bottom: each section builds on the last.

You can also read our poster for a fast intro to what we’ve done so far: 
https://docs.google.com/presentation/d/1-Rb0D_-1VfE4yeUASA7KeLwjDivH5IlcnKsQhVLXG7g/edit?usp=sharing

IDSS: New Member Onboarding

Welcome! This covers both repos and enough context to start contributing in a day. Read it top-to-bottom.



What this project is

We're building a merchant agent framework for agentic e-commerce. The core idea: AI agents will increasingly do shopping on behalf of users ("buy me a gaming laptop under $1,500"). We want to understand how merchants should expose their catalogs so agents can discover and evaluate products reliably.

The system has two parts:

- A conversational shopping assistant - users describe what they want, the system asks clarifying questions and recommends products
- A multi-protocol backend- the same product catalog exposed via three competing agent-commerce protocols (MCP by Anthropic, UCP by Google, ACP by OpenAI), so we can study protocol fragmentation empirically

This is a Stanford CS + MS&E research project advised by Prof. Negin Golrezaei and Prof. Amin Saberi. Live demo (I hosted on vercel and railway): [idss-web.vercel.app](https://idss-web.vercel.app)


Two repos

| Repo | What | Stack |
| `idss-backend` | API server, AI agent, DB, protocols | Python 3.13, FastAPI |
| `idss-web` | Chat UI, product cards, cart, auth | TypeScript, Next.js 15 |

Backend deploys to Railway. Frontend deploys to Vercel. They're independent, you can run them separately.

Backend setup

Prerequisites: Python 3.13, PostgreSQL, an OpenAI API key, a Supabase project.

https://github.com/interactive-decision-support-system/idss-backend
https://github.com/interactive-decision-support-system/idss-web/tree/dev
(use dev branch for idss-web)

```bash
git clone <repo> idss-backend && cd idss-backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r mcp-server/requirements.txt

cp .env.example .env
# Fill in: OPENAI_API_KEY, DATABASE_URL, SUPABASE_URL, SUPABASE_KEY
# Optionally: UPSTASH_REDIS_URL (for session persistence; falls back to in-memory)

createdb mcp_ecommerce
cd mcp-server && psql -d mcp_ecommerce -f scripts/seed_diverse.sql

uvicorn app.main:app --reload --port 8001
```
Or u can load front and backend at same time with ./start_all_local.sh
First startup takes 1–2 min (preloads vehicle embeddings). After that it's fast.

Test it works:

```bash
curl -X POST http://localhost:8001/chat \
 -H "Content-Type: application/json" \
 -d '{"message": "gaming laptop under $1500", "session_id": "test-1"}'
```

Run tests from the repo root:

```bash
pytest mcp-server/tests agent/tests
```

---

 Frontend setup

Prerequisites: Node 22+, backend running on port 8001, a Supabase project.

```bash
git clone <repo> idss-web && cd idss-web
npm install

cp .env.example .env.local
# Fill in: NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY
# NEXT_PUBLIC_API_BASE_URL=http://localhost:8001

npm run dev   # http://localhost:3000
```

For auth to work: Supabase → Authentication → Providers → enable Google OAuth, add redirect URL `http://localhost:3000/auth/callback`. Run the migrations in `supabase/migrations/` to create the favorites and cart tables.

---

 How the system works

A user sends a message. The agent pipeline runs:

1. Query rewriter - catches obvious issues (accessory disambiguation, impossible specs like "1TB RAM"), expands vague references using session context, and annotates known software/use-case signals (e.g. "Final Cut Pro" → Mac-only, "DaVinci Resolve" → needs dedicated GPU)
2. Domain detection - routes to laptops, vehicles, or books via keyword fast-path with LLM fallback
3. Criteria extraction - LLM pulls out budget, RAM, brand, use case, excluded brands, etc. from the message into typed slots defined per domain
4. Interview decision - if the user gave ≥1 substantive constraint (with a ≥4-word message guard), go straight to search; otherwise generate a clarifying question with quick-reply chips
5. Search + narrative - query the DB, apply progressive relaxation if no results, generate the recommendation text with per-product reasoning

The agent package (`agent/`) is the core research contribution. The MCP server (`mcp-server/`) is the HTTP layer that wraps it.

Key files to know:

| File | What it does |
| `agent/universal_agent.py` | Main orchestrator, all the decision logic |
| `agent/domain_registry.py` | Defines slots per product category (budget, RAM, GPU, etc.) |
| `agent/prompts.py` | All LLM prompts, separated from code so you can tune them |
| `agent/query_rewriter.py` | Pre-processes messages before the LLM sees them |
| `agent/comparison_agent.py` | Handles "compare X vs Y" queries, generates narrative |
| `mcp-server/app/chat_endpoint.py` | HTTP handler for /chat - skim the top-level function first |
| `mcp-server/app/endpoints.py` | Product search, cart, checkout, progressive relaxation |
| `mcp-server/app/knowledge_graph.py` | Neo4j knowledge graph (product relationships, session memory) |


---

 Product catalog normalization

Raw product data from Supabase goes through several normalization steps before it reaches the agent or the user. This is important to understand if you're adding new products or debugging why a search returns unexpected results.

**Column name mapping** — the Supabase schema uses different names than the Python ORM. The mapping lives in `mcp-server/app/models.py`:

| Supabase column | Python attribute | Notes |
| `id` (UUID) | `product_id` | primary key |
| `title` | `name` | display name |
| `price` (Numeric, dollars) | `price_value` | stored in dollars, agent uses cents internally |
| `imageurl` | `image_url` | — |
| `attributes` (JSONB) | `attributes` | specs blob: cpu, ram_gb, storage_gb, etc. |

**Brand derivation** — the `brand` column is often junk ("New", "Recertified", "Intel"). `_derive_brand()` in `mcp-server/app/tools/supabase_product_store.py` scans the product title and picks the first real manufacturer name. A `_NON_BRAND_TOKENS` frozenset (intel, amd, nvidia, recertified, etc.) guards against false positives.

**Spec parsing from title** — when the JSONB `attributes` blob is sparse, `_parse_specs_from_title()` extracts RAM, storage, screen size, CPU, and GPU from the title string using regex patterns (e.g. "16GB Memory" → `ram_gb=16`, "15.6\"" → `screen_size=15.6`).

**Row normalization** — `_row_to_dict()` merges the JSONB blob with title-parsed specs, formats them for display (`_fmt_gb()` → "16 GB", `_fmt_hours()` → "10 hrs"), derives the storefront source from the product URL, and maps DB keys to agent-facing keys (`cpu` → `processor`, `ram_gb` → `ram`).

**Price unit conversion** — Supabase stores prices in dollars. The agent filter layer works in cents (`price_max_cents`, `price_min_cents`). The conversion happens in `get_search_filters()` in `agent/universal_agent.py` when it builds the search request.

**LLM description normalization** — `mcp-server/app/catalog_ingestion.py` contains `CatalogNormalizer`, which uses gpt-4o-mini to rewrite raw product descriptions into 1–2 feature-focused sentences (≤30 words each). Output is UPSERTed into `merchants.products_enriched_default` under `strategy='normalizer_v1'`; raw `merchants.products_default` is never mutated. Run via `scripts/run_normalizer.py`. Readers join via `enriched_reader.hydrate_batch`. This is a one-time ingestion step, not a live inference path.

**Post-filter for brand exclusions** — SQL `NOT ILIKE '%HP%'` misses products where the DB `brand` column is "New" but the title contains "HP XYZ". A post-filter step in `_SQLAlchemyProductStore._sql_fetch()` re-checks derived brand AND product title after the SQL query returns. Don't remove it.


---

 AI agent architecture and design choices

Shopping agent design choices

The shopping agent (`agent/universal_agent.py`) is built around a slot-filling interview model, not a free-form chat model. The key design decisions:

- **Domain registry + typed slots** (`agent/domain_registry.py`): Instead of letting the LLM invent its own structure, we predefine slots per domain (e.g. `budget`, `min_ram_gb`, `use_case`, `excluded_brands`). Each slot has a priority (HIGH/MEDIUM/LOW) and an example question. This makes it easy to tune which questions the agent asks and in what order.

- **Hybrid extraction** — LLM first, regex fallback. `_extract_criteria()` calls GPT with a structured schema; if the LLM returns wrong slot names or quota is exhausted, `_regex_extract_criteria()` catches common patterns. `_SLOT_NAME_ALIASES` normalizes LLM output drift ("ram" → "min_ram_gb", "price" → "budget").

- **LLM-free fast paths** — keyword-based domain detection, the 1TB RAM impossible-spec check, and brand alias normalization all run before any LLM call. This keeps latency low for simple queries.

- **Progressive relaxation** in `mcp-server/app/endpoints.py`: if a search returns zero results, the agent drops constraints one by one (most-optional first) until it finds something. It never returns an empty list to the user.

- **Entropy-based slot selection** (`_entropy_next_slot()`): when deciding which question to ask next, the agent runs a probe search and picks the slot that would produce the most diverse result set — a research contribution aimed at minimizing the number of questions needed.

- **Prompt injection guard** in `agent/chat_endpoint.py`: three-layer hybrid filter (regex fast-path → suspicion pre-screen → LLM classifier) blocks prompt injection before the agent sees the message.

Merchant agent design choices

The backend exposes the same product catalog through three different agent-commerce protocols to study protocol fragmentation empirically. All three wrap the same internal MCP endpoints.

| Protocol | Spec origin | Design philosophy |
| MCP (Model Context Protocol) | Anthropic | Tool-based; agent calls `search_products`, `add_to_cart`, `checkout` as tools |
| UCP (Universal Commerce Protocol) | Google | REST wrapper; `mcp-server/app/ucp_client.py` relays requests to MCP internally |
| ACP (Agentic Commerce Protocol) | OpenAI | Session-lifecycle model; checkout goes through states: `incomplete` → `ready_for_payment` → `completed/canceled` |

The fragmentation is intentional — the research question is whether these protocols converge or create lock-in for merchants. Each protocol is a thin adapter over the same product store.

There is also a **Google Merchant Center feed exporter** (`mcp-server/app/merchant_feed.py`) that exports the catalog as JSON or XML for traditional ad-based discovery, as a baseline to compare against agentic discovery.


---

 Knowledge graph

`mcp-server/app/knowledge_graph.py` implements a Neo4j-backed knowledge graph that represents products and their relationships structurally, beyond what flat SQL can express.

**Node types**: Product, Laptop, Book, Jewelry, Accessory, Category, Brand, Author, Publisher, Genre, CPU, GPU, RAM, Storage, Display, Manufacturer, User, Review, Session, UserSession, SessionIntent, StepIntent.

**Relationship types**: HAS_CPU, HAS_GPU, HAS_RAM, HAS_STORAGE, HAS_DISPLAY, WRITTEN_BY, PUBLISHED_BY, MANUFACTURED_BY, BRANDED_BY, EXPLORES_THEME, SIMILAR_TO, BETTER_THAN, CHEAPER_THAN, INSPIRES, VIEWED, PURCHASED, WISHLISTED.

**Session memory** (MemOS-style): `create_session_memory()` tracks `session_intent` (Explore | Decide today | Execute purchase) and `step_intent` (Research | Compare | Negotiate | Schedule | Return) across turns. `get_session_memory()` retrieves this context before the agent processes each message.

**Entity resolution**: `run_entity_resolution()` merges duplicate Author, Manufacturer, and Brand nodes using `difflib.SequenceMatcher` with a 0.88 similarity threshold, preventing the graph from accumulating "Apple Inc." vs "Apple" as separate nodes.

The knowledge graph is currently used for session memory and product relationship queries. Future work: using the graph for recommendation diversity (SIMILAR_TO / BETTER_THAN traversals) and cross-product context (e.g. accessories compatible with a specific laptop).

Note: the knowledge graph requires a running Neo4j instance. If you're just running the chat backend, it's not a hard dependency — the agent falls back gracefully if Neo4j isn't available.


 Things to watch out for

Hot reload only watches `mcp-server/`. If you edit files in `agent/`, the server won't pick them up automatically. Do `touch mcp-server/app/main.py` to force a reload.

Stale `.pyc` files. If code changes aren't taking effect: `find . -name '*.pyc' -delete`

Column name mismatch. The Python model uses `product_id` but the Supabase column is `id`. The price is `price_value` in Python, `price` in Supabase (in dollars, not cents). The name is `name` in Python, `title` in Supabase. These are already mapped - just don't go around the ORM.

`Price`, `Inventory`, `Cart`, `CartItem`, `Order` in `models.py` are Pydantic stubs, not ORM models. Don't use `db.query()` with them - it'll fail silently. Use `Product` for all DB queries.

Carts live in memory. `_CARTS` in `endpoints.py` is a plain Python dict. It clears on server restart. In tests, clear it in your fixtures or tests will step on each other:

```python
from app.endpoints import _CARTS

@pytest.fixture(autouse=True)
def clear_carts():
   _CARTS.clear()
   yield
   _CARTS.clear()
```

Integration tests need dotenv before imports. Call `load_dotenv()` before importing anything from `app.database`, or you'll get a connection error.

LLM slot name drift. The LLM sometimes returns "ram" instead of "min_ram_gb". `_SLOT_NAME_ALIASES` in `universal_agent.py` normalizes these. If you add a new slot, add aliases there too.

Brand exclusions need post-filtering. SQL `NOT ILIKE '%HP%'` misses products where the DB brand is "New" but the title says "HP XYZ". There's a post-filter step in `_SQLAlchemyProductStore._sql_fetch()` that catches this - don't remove it.



 Where to start reading

To understand the agent: `domain_registry.py` → `prompts.py` → `universal_agent.py` → `chat_endpoint.py` (read just the main handler function first).

To understand the search: `endpoints.py` → look for `search_products` and the relaxation loop.

To understand the frontend: `src/config/domain-config.ts` → `src/app/page.tsx` (big file; read the state declarations and `handleChatMessage` first) → `src/services/api.ts`.

To add a new slot or domain: `domain_registry.py` → `prompts.py` → `universal_agent.py` (`_SLOT_NAME_ALIASES`) → `endpoints.py` → tests.



 Quick reference

| What | Command |
| Start backend | `uvicorn app.main:app --reload --port 8001` (from `mcp-server/`) |
| Start frontend | `npm run dev` (from `idss-web/`) |
| Run all tests | `pytest mcp-server/tests agent/tests` (from backend repo root) |
| Pick up agent/ changes | `touch mcp-server/app/main.py` |
| Clear stale bytecode | `find . -name '*.pyc' -delete` |
| Run eval queries | `python scripts/test_demo_queries.py` |
| Run G-Eval | `python scripts/run_geval.py` |

More details in `runbook.md` (deployment) and `AGENTS.md` in idss-web (frontend multi-domain guide).
