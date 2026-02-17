# PROJECT RECAP - IDSS Backend

## What It Does

A **multi-domain conversational product recommendation system**. Users chat with an AI interviewer that asks preference questions, then returns ranked product recommendations. Supports **7 domains**: vehicles, laptops, books, jewelry, accessories, clothing, beauty.

Two servers work together:
- **IDSS API (port 8000)** - Vehicle-specific recommendation engine with sophisticated ranking (embedding similarity, coverage-risk, entropy-based diversification). Preloads ~2GB of FAISS indices + BM25 indexes at startup.
- **MCP Server (port 8001)** - Multi-domain gateway. Handles interview flow for all domains, routes vehicle queries to IDSS, serves laptops/books/etc from PostgreSQL directly.

Frontend (separate `idss-web` repo, Next.js on port 3000) talks exclusively to the MCP server.

---

## Is the Interviewer Truly Agentic?

**Yes.** It uses LLMs at multiple decision points:

| Step | Model | File |
|------|-------|------|
| Intent/domain detection | `gpt-4o-mini` | `mcp-server/app/universal_agent.py` |
| Criteria extraction from user messages | `gpt-4o-mini` | `mcp-server/app/universal_agent.py` |
| Impatience/skip detection | `gpt-4o-mini` | `mcp-server/app/universal_agent.py` |
| Follow-up question generation | `gpt-4o` | `mcp-server/app/interview/question_generator.py` |
| Input validation (typo vs gibberish) | `Claude 3.5 Sonnet` (fallback) | `mcp-server/app/llm_validator.py` |

**However**, questions are **hybrid**: each domain slot has pre-made `example_question` + `example_replies` in `mcp-server/app/domain_registry.py` as fallback, but the primary path generates them dynamically via GPT-4o with conversation context. The LLM is instructed to end questions with an IDSS-style "feel free to also share..." invitation.

**State machine**: `INTERVIEW -> RECOMMENDATIONS -> CHECKOUT` tracked per session. Priority-based slot filling (HIGH/MEDIUM/LOW). Interview ends when: max questions reached (default 3), user is impatient (LLM-detected), or all required slots filled.

---

## How MCP Works / What MCP Capabilities Exist

MCP here is a **custom implementation** (not the Anthropic MCP SDK). It's a FastAPI server acting as a unified gateway.

### Implemented MCP Tools (listed at `GET /tools`)

| Tool | Endpoint | Description |
|------|----------|-------------|
| `search-products` | `POST /api/search-products` | Search across all domains with filters |
| `get-product` | `POST /api/get-product` | Get product detail by ID |
| `add-to-cart` | `POST /api/add-to-cart` | Add product to cart |
| `get-cart` | `POST /api/get-cart` | View cart contents |
| `checkout` | `POST /api/checkout` | Process order |

### MCP Response Envelope

All MCP responses follow: `{ status, data, trace: {request_id}, version: {catalog_version} }`

### How MCP Connects to IDSS

Via HTTP adapter (`mcp-server/app/idss_adapter.py`):
- MCP server calls `POST http://localhost:8000/chat` on IDSS
- Converts IDSS vehicle format -> MCP `ProductSummary`/`ProductDetail` format
- Adds provenance tracking (`source: "idss_sqlite"`)

For non-vehicle domains, MCP queries PostgreSQL directly (no IDSS involvement).

### Key MCP Files

- `mcp-server/app/main.py` - FastAPI app, mounts all routes
- `mcp-server/app/endpoints.py` - MCP tool endpoints (search, cart, checkout)
- `mcp-server/app/chat_endpoint.py` - Main `/chat` with domain routing (~900 lines)
- `mcp-server/app/idss_adapter.py` - IDSS <-> MCP format bridge
- `mcp-server/app/schemas.py` - MCP data models (ProductSummary, ProductDetail, etc.)
- `mcp-server/app/tool_schemas.py` - Tool definitions for MCP
- `mcp-server/app/cache.py` - Redis cache layer (product summaries, prices, sessions)

---

## Data Sources

### 1. SQLite - Vehicle Data (`data/car_dataset_idss/uni_vehicles.db`)
- ~1.5 GB vehicle database
- Queried by IDSS API at port 8000
- Contains VINs, specs, pricing, dealer info, images

### 2. SQLite - Vehicle Reviews (`data/car_dataset_idss/vehicle_reviews_tavily.db`)
- ~22 MB, pros/cons phrases per make/model/year
- Used for embedding-based similarity scoring

### 3. FAISS Indices (`data/car_dataset_idss/faiss_indices/`)
- Pre-computed dense embeddings (all-mpnet-base-v2, 768-dim)
- Used by `DenseEmbeddingStore` for fast similarity search

### 4. Phrase Embeddings (`data/car_dataset_idss/phrase_embeddings/`)
- 91,370 individual phrase embeddings across 7,240 make/model/year combos
- Used by `PhraseStore` for Method 3 scoring (sum of per-phrase cosine similarities)

### 5. BM25 Index (`data/car_dataset_idss/bm25_index.pkl`)
- Sparse text search index over vehicle descriptions

### 6. PostgreSQL (`mcp_ecommerce`)
- Tables: `products`, `prices`, `inventory`, `carts`, `cart_items`, `orders`
- Seeded via SQL scripts: `seed_laptops_expanded.sql` (37 items), `seed_books_expanded.sql` (50 items)
- Also has jewelry, clothing, beauty, accessories products (added via various seed scripts)

### 7. Redis (optional, falls back to in-memory)
- DB 0: MCP cache (product summaries, prices, inventory) with TTLs
- DB 1: Agent cache (sessions, conversations)

### 8. Neo4j (future/optional)
- Knowledge graph - referenced in code but not actively used

---

## Project Structure

```
idss-backend/
├── idss/                              # IDSS Vehicle Engine (port 8000)
│   ├── api/
│   │   ├── server.py                  # FastAPI entry point
│   │   ├── models.py                  # Request/response Pydantic models
│   │   └── endpoints.py              # /chat, /recommend, /status
│   ├── core/
│   │   ├── controller.py             # Main orchestrator (interview + recommend)
│   │   └── config.py                 # YAML config loader
│   ├── recommendation/
│   │   ├── dense_embedding_store.py  # FAISS similarity search
│   │   ├── phrase_store.py           # Per-phrase embedding scoring
│   │   ├── progressive_relaxation.py # Filter relaxation when too few results
│   │   └── ...
│   ├── diversification/              # Entropy-based bucketing
│   └── data/vehicle_store.py         # SQLite vehicle queries
│
├── mcp-server/                        # MCP Gateway (port 8001)
│   ├── app/
│   │   ├── main.py                   # FastAPI entry point
│   │   ├── chat_endpoint.py          # /chat - domain routing, interview, recs
│   │   ├── universal_agent.py        # LLM-powered slot-filling agent
│   │   ├── conversation_controller.py # Domain detection logic
│   │   ├── domain_registry.py        # Slot definitions per domain
│   │   ├── query_specificity.py      # Determines if query needs interview
│   │   ├── llm_validator.py          # Input validation via Claude
│   │   ├── idss_adapter.py           # IDSS <-> MCP format bridge
│   │   ├── endpoints.py             # MCP tool endpoints
│   │   ├── schemas.py               # MCP data models
│   │   ├── cache.py                 # Redis cache
│   │   ├── database.py              # SQLAlchemy/Postgres connection
│   │   ├── models.py                # ORM models
│   │   ├── vector_search.py         # Embedding search for products
│   │   ├── laptop_recommender.py    # Laptop-specific ranking
│   │   └── interview/
│   │       ├── session_manager.py    # Session state tracking
│   │       └── question_generator.py # LLM question generation
│   ├── scripts/
│   │   ├── seed_laptops_expanded.sql
│   │   ├── seed_books_expanded.sql
│   │   └── ... (various seed/scrape scripts)
│   └── config.yaml
│
├── config/default.yaml                # IDSS config (k, methods, lambdas)
├── data/car_dataset_idss/             # Vehicle data (symlink, ~2GB)
├── requirements.txt                   # Python dependencies
├── .env                               # API keys, DB URLs
└── kg.txt                             # Knowledge graph / intent taxonomy doc
```

---

## Dependencies (from requirements.txt)

**Core**: `fastapi`, `uvicorn`, `pydantic`
**LLM**: `openai`, `anthropic`
**Databases**: `sqlalchemy`, `psycopg2-binary` (Postgres), `redis`
**ML/Search**: `sentence-transformers`, `faiss-cpu`, `numpy`, `scikit-learn`
**NLP**: `rank-bm25`, `nltk`
**Config**: `pyyaml`, `python-dotenv`
**HTTP**: `httpx`, `aiohttp`
**Graph** (optional): `neo4j`

---

## Is the README Accurate for Setup?

**Mostly yes, with caveats:**

1. **Accurate**: Python 3.10+, PostgreSQL, venv setup, pip install, DB creation/seeding, env vars, two-terminal startup, test commands.

2. **Missing/Incomplete**:
   - README only lists 3 domains (vehicles, laptops, books) - actual codebase supports 7 (also jewelry, accessories, clothing, beauty)
   - `ANTHROPIC_API_KEY` not mentioned in env vars but is used by `llm_validator.py`
   - Vehicle data (`data/car_dataset_idss/`) requires a ~2GB dataset that is not included in the repo and no download link is provided - you need to get it separately
   - Additional SQL seed scripts exist for jewelry/clothing/beauty but aren't mentioned
   - `sentence-transformers` requires downloading the `all-mpnet-base-v2` model (~400MB) on first run - not mentioned
   - Neo4j listed as optional but has zero actual functionality
   - The `config.yaml` and `config/default.yaml` paths referenced may not match actual file locations

3. **Potentially confusing**: The term "MCP" is used loosely. This is NOT the Anthropic Model Context Protocol SDK - it's a custom FastAPI gateway that borrows MCP naming conventions for its tool/endpoint patterns.
