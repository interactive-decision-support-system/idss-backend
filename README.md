# IDSS Backend - Multi-Domain Interactive Decision Support System

An LLM-driven Interactive Decision Support System that helps users find products through conversational interviews. The **Universal Agent** detects the user's domain, extracts preferences via structured LLM calls, and generates natural follow-up questions before delivering recommendations. Supports **vehicles**, **laptops**, **books**, and **24,000+ electronics** products.

## Architecture

```
                        Frontend (Port 3000)
                       Next.js Chat Interface
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│                  MCP Server (Port 8001)                   │
│                                                          │
│  POST /chat ─────► agent/                                │
│                    ├── UniversalAgent (LLM brain)         │
│                    │   ├── Domain detection               │
│                    │   ├── Criteria extraction              │
│                    │   ├── Question generation              │
│                    │   └── Post-rec refinement              │
│                    ├── chat_endpoint.py (orchestrator)     │
│                    └── interview/session_manager.py        │
│                                                          │
│  Search dispatch:                                        │
│    vehicles ──► IDSS (direct import, no HTTP)            │
│    laptops  ──► PostgreSQL                               │
│    books    ──► PostgreSQL                               │
└──────────────────────────────────────────────────────────┘
              │                         │
              ▼                         ▼
┌─────────────────────────┐   ┌─────────────────────────┐
│   SQLite + FAISS        │   │      PostgreSQL          │
│   Vehicle Data (~2GB)   │   │    (mcp_ecommerce)       │
│   ~147k vehicles        │   │  - Electronics (~21k)    │
│                         │   │  - Books (~66)           │
└─────────────────────────┘   └─────────────────────────┘
```

**Key design:** There is no separate IDSS API server. Vehicle search functions (`idss.recommendation.*`) are imported directly into the MCP server process. Only **one server** (port 8001) is needed.

## Project Structure

```
idss-backend/
├── agent/                           # Agent brain (independent of server)
│   ├── __init__.py                  # Public API re-exports
│   ├── universal_agent.py           # LLM-driven pipeline (domain → extract → question → search)
│   ├── domain_registry.py           # Domain schemas (slots, priorities, allowed values)
│   ├── prompts.py                   # All LLM prompt templates (tune without touching logic)
│   ├── chat_endpoint.py             # /chat orchestrator + search dispatchers
│   └── interview/
│       └── session_manager.py       # Session state + Redis/Neo4j persistence
│
├── mcp-server/                      # HTTP server + tools
│   ├── app/
│   │   ├── main.py                  # FastAPI app (port 8001)
│   │   ├── endpoints.py             # MCP tool-call endpoints
│   │   ├── tools/vehicle_search.py  # IDSS vehicle search wrapper (direct import)
│   │   ├── formatters.py            # Product formatting for frontend
│   │   ├── research_compare.py      # Post-rec research/compare handlers
│   │   ├── database.py              # PostgreSQL connection
│   │   ├── models.py                # SQLAlchemy models
│   │   └── ...                      # Cache, metrics, UCP, etc.
│   ├── scripts/
│   │   ├── seed_diverse.sql         # Creates tables + seed products
│   │   ├── seed_laptops_expanded.sql # Additional laptop data
│   │   ├── seed_books_expanded.sql  # Additional book data
│   │   └── merge_supabase_data.py   # Import ~24k products from Supabase
│   └── tests/
│
├── idss/                            # IDSS Vehicle Recommendation Engine
│   ├── recommendation/              # Embedding similarity, coverage-risk
│   ├── diversification/             # Entropy bucketing
│   └── core/                        # Controller
│
├── config/default.yaml              # IDSS recommendation config
├── data/                            # Vehicle data (symlink to dataset)
├── requirements.txt
└── .env                             # Environment variables
```

## Prerequisites

| Software | Version | Purpose |
|----------|---------|---------|
| Python | 3.10+ | Runtime |
| PostgreSQL | 14+ | Product database (laptops, books, electronics) |
| OpenAI API key | - | LLM calls (domain detection, extraction, question generation) |

**Optional:**

| Software | Purpose |
|----------|---------|
| Redis 6+ | Session caching (falls back to in-memory) |
| Neo4j 5+ | Knowledge graph for session memory |

## Quick Start

### 1. Clone and Install

```bash
git clone <repo-url> idss-backend
cd idss-backend

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file in the **project root** (not inside `mcp-server/`):

```bash
# Required
OPENAI_API_KEY="sk-your-openai-api-key"
DATABASE_URL="postgresql://YOUR_USERNAME@localhost:5432/mcp_ecommerce"

# LLM model configuration
OPENAI_MODEL="gpt-4o-mini"           # Model for all agent LLM calls
OPENAI_REASONING_EFFORT="low"        # Reasoning effort: low, medium, high

# Optional
LOG_LEVEL=INFO
REDIS_HOST=localhost
REDIS_PORT=6379
```

**Finding your PostgreSQL username:** On Mac, it's usually your system username. Check with:
```bash
whoami
# or
psql -c "\du"
```

### 3. Setup PostgreSQL Database

```bash
# Create the database
createdb mcp_ecommerce

# Create tables + seed initial products
cd mcp-server
psql -d mcp_ecommerce -f scripts/seed_diverse.sql
psql -d mcp_ecommerce -f scripts/seed_laptops_expanded.sql
psql -d mcp_ecommerce -f scripts/seed_books_expanded.sql
```

#### Import full product catalog from Supabase (recommended)

This imports ~24,000 real products (laptops, monitors, GPUs, keyboards, etc.) from a shared Supabase database into your local PostgreSQL. It's a one-time operation that takes ~30-60 seconds:

```bash
python scripts/merge_supabase_data.py --skip-redis --skip-kg
```

The script is idempotent — running it again skips already-imported products.

#### Verify

```bash
psql -d mcp_ecommerce -c "SELECT category, COUNT(*) FROM products GROUP BY category ORDER BY count DESC;"
```

With full import you should see:
```
  category   | count
-------------+-------
 Electronics | ~21000+
 Books       |    66
```

Without Supabase import (seed data only):
```
  category   | count
-------------+-------
 Electronics |    37
 Books       |    50
```

### 4. Setup Vehicle Data (optional)

Vehicle search requires a separate dataset (~2GB). Skip this step if you only need laptops/books.

```bash
# Symlink or copy the vehicle dataset
ln -s /path/to/car_dataset_idss data/car_dataset_idss

# Required files in data/car_dataset_idss/:
# - uni_vehicles.db (~1.5 GB SQLite database)
# - vehicle_reviews_tavily.db (~22 MB)
# - bm25_index.pkl
# - phrase_embeddings/
```

### 5. Start the Server

```bash
source venv/bin/activate
uvicorn app.main:app --app-dir mcp-server --reload --port 8001
```

First startup preloads IDSS vehicle models (~60-120 seconds). To skip vehicle preloading during development:

```bash
MCP_SKIP_PRELOAD=1 uvicorn app.main:app --app-dir mcp-server --reload --port 8001
```

### 6. Verify

```bash
# Health check
curl http://localhost:8001/health

# Test laptop flow
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I want a gaming laptop"}'

# Test book flow
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "looking for a mystery novel"}'

# Test vehicle flow (requires step 4)
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I need an SUV under 30k"}'
```

## How It Works

### The Universal Agent Pipeline

Every `/chat` message goes through this flow:

1. **Domain Detection** — Classifies into `vehicles`, `laptops`, `books`, or `unknown`
2. **Criteria Extraction** — Extracts slot values from the message using domain-specific schemas with allowed values
3. **Interview Decision** — Should we ask another question or show results? Based on: question count vs limit, impatience detection, explicit recommendation requests
4. **Question Generation** — Generates a natural follow-up question with quick replies and an invitation to share other preferences
5. **Search Dispatch** — When ready, dispatches to the appropriate search backend:
   - Vehicles: direct IDSS import (`idss.recommendation.embedding_similarity`)
   - Laptops/Books: PostgreSQL with progressive filter relaxation
6. **Recommendation Explanation** — Generates a conversational message highlighting one standout product
7. **Post-Rec Refinement** — After recommendations, the agent classifies follow-up messages as filter changes ("show me something cheaper"), domain switches ("actually show me books"), new searches, or actions (research, compare). Natural language refinements update filters and re-run search automatically.

### Domain Schemas

Defined in `agent/domain_registry.py`. Each domain has priority-ranked slots:

| Domain | HIGH slots | MEDIUM slots | LOW slots |
|--------|-----------|-------------|-----------|
| Vehicles | Budget, Use Case, Body Style | Features, Brand | Fuel Type, Condition |
| Laptops | Use Case, Budget | Brand, OS | Screen Size |
| Books | Genre | Format | Budget |

Vehicle slots include `allowed_values` for categorical filters (body style, fuel type, brand) so the LLM outputs exact database-compatible values.

### Prompt Tuning

All LLM prompts are in `agent/prompts.py`. You can adjust:
- `DOMAIN_DETECTION_PROMPT` — routing behavior
- `CRITERIA_EXTRACTION_PROMPT` — what/how the agent extracts
- `PRICE_CONTEXT` — per-domain price interpretation
- `QUESTION_GENERATION_PROMPT` — question style and invitation pattern
- `RECOMMENDATION_EXPLANATION_PROMPT` — how recommendations are presented
- `POST_REC_REFINEMENT_PROMPT` — how post-recommendation follow-ups are classified

### Model Configuration

All LLM calls use a single configurable model. Set via environment variables:

```bash
OPENAI_MODEL="gpt-4o-mini"       # Any OpenAI-compatible model
OPENAI_REASONING_EFFORT="low"    # low | medium | high
```

All 6 LLM call sites (domain detection, criteria extraction, question generation, recommendation explanation, refinement classification, legacy question generator) use the same model and reasoning settings.

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Main conversation endpoint |
| `/session/{id}` | GET | Get session state |
| `/session/reset` | POST | Reset/create session |
| `/sessions` | GET | List active sessions |
| `/health` | GET | Health check |
| `/api/search-products` | POST | Direct product search (MCP tool) |
| `/api/get-product` | POST | Get product details (MCP tool) |
| `/api/add-to-cart` | POST | Add to cart (MCP tool) |
| `/api/checkout` | POST | Checkout (MCP tool) |
| `/tools` | GET | List available MCP tools |
| `/tools/openai` | GET | Tools in OpenAI function calling format |
| `/tools/claude` | GET | Tools in Claude tool use format |

### Chat Request

```json
{
  "message": "I want a laptop for gaming",
  "session_id": "optional-session-id",
  "k": 3,
  "n_rows": 3,
  "n_per_row": 3
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `message` | string | required | User message |
| `session_id` | string | auto-generated | Session ID for multi-turn conversations |
| `k` | int | 3 | Max interview questions (0 = skip to recommendations) |
| `n_rows` | int | 3 | Number of result rows |
| `n_per_row` | int | 3 | Items per row |

### Chat Response

```json
{
  "response_type": "recommendations",
  "message": "The Dell XPS 15 stands out as a great match...",
  "session_id": "uuid",
  "domain": "laptops",
  "recommendations": [[...], [...]],
  "bucket_labels": ["Budget-Friendly ($600-$800)", "Premium ($1200-$1500)"],
  "quick_replies": ["See similar items", "Compare items", "Research"],
  "filters": {"brand": "Dell", "price_max_cents": 150000},
  "question_count": 2
}
```

## Frontend Configuration

Point the frontend to the MCP server:

```bash
# In frontend/.env.local
NEXT_PUBLIC_API_BASE_URL="http://localhost:8001"
```

## Running Tests

```bash
cd mcp-server
python -m pytest tests/
```

## Troubleshooting

**"role does not exist"** — Wrong PostgreSQL username in `DATABASE_URL`. Find yours with `whoami` (Mac) or `psql -c "\du"`.

**"database does not exist"** — Run `createdb mcp_ecommerce`.

**"column does not exist" (e.g. `kg_features`, `product_type`)** — Your table schema is outdated. Drop and recreate: `psql -d mcp_ecommerce -f scripts/seed_diverse.sql` (warning: this drops all data, re-run seed scripts and Supabase import after).

**No products returned** — Check `psql -d mcp_ecommerce -c "SELECT COUNT(*) FROM products;"`. If 0, run the seed scripts (step 3c) and optionally the Supabase import (step 3d).

**Supabase import fails** — The Supabase import connects to a shared remote database. If it fails with a connection error, the remote may be unavailable. The seed data (37 laptops + 50 books) is sufficient to run the system without the Supabase import.

**Redis connection errors** — Redis is optional. The system falls back to in-memory sessions. The warning is harmless.

**Neo4j connection refused** — Neo4j is optional. The warning `Connection refused on port 7687` is harmless.

**IDSS models slow to load** — First startup preloads ~2GB of vehicle data. Use `MCP_SKIP_PRELOAD=1` for faster dev restarts (vehicles won't work until first request triggers lazy load).

**Server crashes on startup (ImportError)** — Make sure you're running from the repo root with `--app-dir mcp-server`. The agent package must be importable from the repo root.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | - | OpenAI API key for agent LLM calls |
| `DATABASE_URL` | Yes | - | PostgreSQL connection string (e.g. `postgresql://user@localhost:5432/mcp_ecommerce`) |
| `OPENAI_MODEL` | No | gpt-4o-mini | Model for all agent LLM calls |
| `OPENAI_REASONING_EFFORT` | No | low | Reasoning effort: low, medium, high |
| `LOG_LEVEL` | No | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `REDIS_HOST` | No | localhost | Redis host for session caching |
| `REDIS_PORT` | No | 6379 | Redis port |
| `NEO4J_URI` | No | - | Neo4j connection for knowledge graph |
| `MCP_SKIP_PRELOAD` | No | 0 | Skip IDSS vehicle model preloading (dev only) |
