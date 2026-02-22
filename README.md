# IDSS Backend - Multi-Domain Interactive Decision Support System

An LLM-driven Interactive Decision Support System that helps users find products through conversational interviews. The **Universal Agent** detects the user's domain, extracts preferences via structured LLM calls, and generates natural follow-up questions before delivering recommendations. Supports **vehicles**, **laptops**, **books**, and **24,000+ electronics** products.

## Architecture

```
                        Frontend (Port 3000)
                       Next.js Chat Interface
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│                  IDSS Server (Port 8000)                  │
│  POST /chat ──────► agent/chat_endpoint.py               │
│  POST /ucp/checkout-sessions ──► UCP checkout            │
│                                                          │
│            ┌──── OR ────┐                                │
│            ▼             ▼                                │
│   MCP Server (Port 8001)  (same agent/, same endpoints)  │
│                                                          │
│  Agent pipeline:                                         │
│    ├── UniversalAgent (LLM brain)                        │
│    │   ├── Domain detection                              │
│    │   ├── Criteria extraction                           │
│    │   ├── Question generation                           │
│    │   └── Post-rec refinement                           │
│    ├── chat_endpoint.py (orchestrator)                   │
│    └── interview/session_manager.py                      │
│                                                          │
│  Search:  Supabase PostgreSQL (24,150 products)          │
│  KG:      Neo4j (24,868 nodes / 40,585 relationships)   │
│  Cache:   Upstash Redis (cloud) or local Redis           │
└──────────────────────────────────────────────────────────┘
              │                         │
              ▼                         ▼
┌─────────────────────────┐   ┌─────────────────────────┐
│        Supabase         │   │      PostgreSQL          │
│   Vehicle Data          │   │    (mcp_ecommerce)       │
│   ~147k vehicles        │   │  - Electronics (~21k)    │
│   Embeddings + Phrases  │   │  - Books (~66)           │
└─────────────────────────┘   └─────────────────────────┘
```

**Key design:** Both port 8000 (IDSS server) and port 8001 (MCP server) use the same `agent/chat_endpoint.py` pipeline. The frontend connects to port 8000 by default.

## Database Schema (Supabase)

The system uses a **single `products` table** in Supabase PostgreSQL. No separate prices, inventory, carts, or orders tables.

```sql
CREATE TABLE products (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    title               TEXT,           -- Product name (mapped to 'name' in Python)
    price               NUMERIC,        -- Price in dollars (NOT cents)
    brand               TEXT,
    category            TEXT,           -- "Electronics", "Books", etc.
    product_type        TEXT,           -- "laptop", "gaming_laptop", "book", "phone"
    source              TEXT,           -- "System76", "Amazon", "BackMarket", etc.
    imageurl            TEXT,           -- Image URL (no underscore!)
    rating              NUMERIC,
    rating_count        BIGINT,
    series              TEXT,
    model               TEXT,
    link                TEXT,
    ref_id              TEXT,
    variant             TEXT,
    inventory           BIGINT,
    release_year        SMALLINT,
    delivery_promise    TEXT,
    return_policy       TEXT,
    warranty            TEXT,
    promotions_discounts TEXT,
    merchant_product_url TEXT,
    attributes          JSONB           -- Specs, description, color, GPU, etc.
);
```

### SQLAlchemy Column Mapping

The Python model maps Supabase column names to legacy attribute names so existing code works unchanged:

| Supabase Column | Python Attribute | Notes |
|-----------------|-----------------|-------|
| `id` (UUID) | `product_id` | `Column("id", PG_UUID)` |
| `title` | `name` | `Column("title", Text)` |
| `price` (dollars) | `price_value` | `Column("price", Numeric)` |
| `imageurl` | `image_url` | `Column("imageurl", Text)` |
| `attributes` (JSONB) | `attributes` | Contains: description, color, gpu_vendor, gpu_model, ram_gb, tags, kg_features |

Fields that were previously separate columns (`description`, `color`, `gpu_vendor`, `gpu_model`, `tags`, `kg_features`) are now accessed via `@property` methods that read from `attributes` JSONB.

**Removed tables:** `prices` (price now on products), `inventory` (column on products), `carts`, `cart_items`, `orders` (handled by UCP checkout). Stub classes exist in `models.py` to prevent import errors.

## Project Structure

```
idss-backend/
├── agent/                           # Agent brain (independent of server)
│   ├── __init__.py                  # Public API re-exports
│   ├── universal_agent.py           # LLM-driven pipeline
│   ├── domain_registry.py           # Domain schemas (slots, priorities, allowed values)
│   ├── prompts.py                   # All LLM prompt templates
│   ├── chat_endpoint.py             # /chat orchestrator + search dispatchers
│   └── interview/
│       └── session_manager.py       # Session state + Redis/Neo4j persistence
│
├── mcp-server/                      # HTTP server + tools
│   ├── app/
│   │   ├── main.py                  # FastAPI app (port 8001)
│   │   ├── endpoints.py             # MCP tool-call endpoints
│   │   ├── formatters.py            # Product formatting for frontend
│   │   ├── research_compare.py      # Post-rec research/compare handlers
│   │   ├── database.py              # Supabase PostgreSQL connection
│   │   ├── models.py                # SQLAlchemy models (Supabase schema)
│   │   ├── ucp_checkout.py          # UCP checkout (Google Universal Commerce)
│   │   ├── neo4j_config.py          # Neo4j connection
│   │   ├── knowledge_graph.py       # KG builder (node/relationship creation)
│   │   └── ...                      # Cache, metrics, etc.
│   ├── scripts/
│   │   ├── seed_diverse.sql         # Creates tables + seed products
│   │   ├── seed_laptops_expanded.sql # Additional laptop data
│   │   ├── seed_books_expanded.sql  # Additional book data
│   │   └── merge_supabase_data.py   # Import ~24k products from Supabase
│   └── tests/
│
├── idss/                            # IDSS Server (port 8000)
│   └── api/
│       ├── server.py                # FastAPI app → routes /chat to agent/
│       └── models.py                # Request/response models
│
├── config/default.yaml              # Recommendation config
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
| Redis 6+ / Upstash | Session caching (falls back to in-memory) |

## Quick Start

### 1. Clone and Install

```bash
git clone <repo-url> idss-backend
cd idss-backend

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file in the **project root** (not inside `mcp-server/`):

```bash
# Required
OPENAI_API_KEY="sk-your-openai-api-key"
DATABASE_URL="postgresql://postgres:password@localhost:5432/idss"

# Supabase (vehicle search)
SUPABASE_URL="https://your-project.supabase.co"
SUPABASE_KEY="your-supabase-anon-key"

# LLM model configuration
OPENAI_MODEL="gpt-5-nano"           # Model for all agent LLM calls
OPENAI_REASONING_EFFORT="low"        # Reasoning effort: low, medium, high
# Neo4j (Docker)
NEO4J_URI=bolt://localhost:7688
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4jpassword

# Optional: Upstash Redis (cloud cache)
# UPSTASH_REDIS_URL="rediss://..."

**Finding your PostgreSQL username:** On Mac, it's usually your system username. Check with:
```bash
whoami
# or
psql -c "\du"
```

**Using Supabase (recommended):** Replace `DATABASE_URL` with your Supabase direct connection string from Project Settings > Database > Connection string > URI.

### 3. Setup Database

**Option A: Use Supabase cloud (recommended — data already loaded)**

If your Supabase already has the 24,150 products, just set `DATABASE_URL` in `.env` and skip to step 4.

**Option B: Seed a local PostgreSQL**

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

### 4. Start the Server

```bash
# Terminal 1: IDSS server (port 8000) — frontend connects here
source .venv/bin/activate
uvicorn idss.api.server:app --reload --port 8000

# Terminal 2: MCP server (port 8001) — optional, same agent
cd mcp-server
uvicorn app.main:app --reload --port 8001
```

First startup preloads vehicle embedding models (~5-10 seconds).

### 5. Start the Frontend from idss-web repo

```bash
cd ../idss-web
npm install
npm run dev
# → http://localhost:3000
```

Configure the frontend to point to the backend:

```bash
# In idss-web/.env.local
NEXT_PUBLIC_API_BASE_URL="http://localhost:8001"
```

### 6. Verify

```bash
# Health check
curl http://localhost:8001/health

# Test laptop search
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I want a gaming laptop"}'

# Test book search
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "looking for a mystery novel"}'

# Test vehicle flow
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I need an SUV under 30k"}'
```

## How It Works

### The Universal Agent Pipeline

Every `/chat` message goes through this flow:

1. **Domain Detection** — Classifies into `laptops`, `books`, `vehicles`, or `unknown`
2. **Criteria Extraction** — Extracts slot values using domain-specific schemas
3. **Interview Decision** — Ask another question or show results?
4. **Question Generation** — Natural follow-up with quick replies
5. **Search Dispatch** — Supabase PostgreSQL with progressive filter relaxation
6. **Recommendation Explanation** — Conversational message highlighting standout products
7. **Post-Rec Refinement** — Filter changes, comparisons, research, checkout

### Chat Request

```json
{
  "message": "I want a laptop for gaming",
  "session_id": "optional-session-id",
  "k": 3,
  "user_actions": [
    {"type": "favorite", "product_id": "uuid-here"},
    {"type": "unfavorite", "product_id": "uuid-here"}
  ]
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `message` | string | required | User message |
| `session_id` | string | auto-generated | Session ID for multi-turn conversations |
| `k` | int | 3 | Max interview questions |
| `user_actions` | array | [] | Favorite/unfavorite actions to sync |

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Main conversation endpoint |
| `/health` | GET | Health check |
| `/ucp/checkout-sessions` | POST | Create checkout session |
| `/ucp/checkout-sessions/{id}/complete` | POST | Complete checkout |
| `/ucp/checkout-sessions/{id}/cancel` | POST | Cancel checkout |
| `/session/{id}` | GET | Get session state |
| `/session/reset` | POST | Reset/create session |

## Troubleshooting

**"role does not exist"** — Wrong PostgreSQL username in `DATABASE_URL`. Find yours with `whoami` (Mac) or `psql -c "\du"`.

**"Column expression expected, got Price"** — The old `Price` model is now a stub. If a script uses `db.query(Price)`, update it to use `product.price_value` directly.

**"Values of type UUID are not supported"** — Neo4j doesn't accept Python `uuid.UUID` objects. Convert with `str(product.product_id)` before passing to Cypher queries.

**"column does not exist" (e.g. `kg_features`, `product_type`)** — Your table schema is outdated. Drop and recreate: `psql -d mcp_ecommerce -f scripts/seed_diverse.sql` (warning: this drops all data, re-run seed scripts and Supabase import after).

**No products returned** — Check `psql -d mcp_ecommerce -c "SELECT COUNT(*) FROM products;"`. If 0, run the seed scripts (step 3c) and optionally the Supabase import (step 3d).

**Supabase import fails** — The Supabase import connects to a shared remote database. If it fails with a connection error, the remote may be unavailable. The seed data (37 laptops + 50 books) is sufficient to run the system without the Supabase import.

**Redis connection errors** — Redis is optional. The system falls back to in-memory sessions. The warning is harmless.

**UUID vs String IDs** — Supabase uses UUID for `id`. Old code that used string IDs like `"laptop-1"` needs updating to use UUIDs.

**Neo4j port confusion** — Docker maps `7475→7474` and `7688→7687`. Use `localhost:7475` for the browser and `bolt://localhost:7688` for the driver. Do NOT use the default ports (7474/7687) — those may be a separate local Neo4j install.

**Vehicle search returns no results** — Check that `SUPABASE_URL` and `SUPABASE_KEY` are set correctly in your `.env` file.
**Redis connection errors** — Redis/Upstash is optional. Falls back to in-memory sessions.

**Server crashes on startup (ImportError)** — Make sure you're running from the repo root with `--app-dir mcp-server`. The agent package must be importable from the repo root.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | - | OpenAI API key for agent LLM calls |
| `DATABASE_URL` | Yes | - | PostgreSQL connection string (e.g. `postgresql://user@localhost:5432/mcp_ecommerce`) |
| `SUPABASE_URL` | Yes | - | Supabase project URL for vehicle search |
| `SUPABASE_KEY` | Yes | - | Supabase anon key for vehicle search |
| `OPENAI_MODEL` | No | gpt-4o-mini | Model for all agent LLM calls |
| `OPENAI_REASONING_EFFORT` | No | low | Reasoning effort: low, medium, high |
| `LOG_LEVEL` | No | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `REDIS_HOST` | No | localhost | Redis host for session caching |
| `REDIS_PORT` | No | 6379 | Redis port |
| `NEO4J_URI` | No | - | Neo4j connection for knowledge graph |
