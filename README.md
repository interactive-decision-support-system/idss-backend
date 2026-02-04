# IDSS Backend - Multi-Domain Interactive Decision Support System

A multi-domain Interactive Decision Support System that helps users find products through conversational interviews. Supports **vehicles**, **laptops**, and **books** with intelligent domain routing.

## Quick MCP Capability Examples

**Add a specific product to cart (MCP):**

Request (POST `/api/add-to-cart`)

```json
{
  "cart_id": "cart-001",
  "product_id": "laptop-001",
  "qty": 1
}
```text

Response (agent consumes `data` + `trace`):

```json
{
  "status": "OK",
  "data": {
    "cart_id": "cart-001",
    "item_count": 1,
    "total_cents": 149999
  },
  "trace": {"request_id": "..."}
}
```

Agent-facing summary: “Added Gaming Laptop RTX 4060 to your cart. Total: $1499.99.”

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend (Port 3000)                      │
│                  Next.js Chat Interface                      │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│               MCP Server (Port 8001) - Gateway               │
│                                                              │
│  POST /chat  ──────────────────────────────────────────────▶│
│       │                                                      │
│       ├── "car", "SUV", "Toyota" ──▶ IDSS Backend (8000)    │
│       ├── "laptop", "MacBook"    ──▶ PostgreSQL + Interview │
│       └── "book", "novel"        ──▶ PostgreSQL + Interview │
└─────────────────────────────────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
┌─────────────────────────┐   ┌─────────────────────────┐
│  IDSS API (Port 8000)   │   │      PostgreSQL         │
│                         │   │    (mcp_ecommerce)      │
│  - Vehicle database     │   │                         │
│  - Embedding search     │   │  - Laptops (37 items)   │
│  - Coverage-risk        │   │  - Books (50 items)     │
│  - Interview system     │   │  - Prices, Inventory    │
└─────────────────────────┘   └─────────────────────────┘
              │                         │
              ▼                         ▼
┌─────────────────────────┐   ┌─────────────────────────┐
│   SQLite + FAISS        │   │   Redis (Optional)      │
│   Vehicle Data (~2GB)   │   │   Session Cache         │
└─────────────────────────┘   └─────────────────────────┘
```

## Prerequisites

### Required

| Software | Version | Purpose |
|----------|---------|---------|
| Python | 3.10+ | Runtime |
| PostgreSQL | 14+ | E-commerce product database |
| Node.js | 18+ | Frontend (if running locally) |

### Optional (for full features)

| Software | Version | Purpose |
|----------|---------|---------|
| Redis | 6+ | Session caching (falls back to in-memory) |
| Neo4j | 5+ | Knowledge graph (future feature) |

## Quick Start

### 1. Clone and Setup Environment

```bash
# Clone the repository
git clone <repo-url> idss-backend
cd idss-backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file in the project root:

```bash
# Required
OPENAI_API_KEY="sk-your-openai-api-key"
DATABASE_URL="postgresql://YOUR_USERNAME@localhost:5432/mcp_ecommerce"

# Optional
LOG_LEVEL=INFO
REDIS_HOST=localhost
REDIS_PORT=6379
NEO4J_URI=bolt://localhost:7687
NEO4J_PASSWORD=your-password
```

**Important:** Replace `YOUR_USERNAME` with your PostgreSQL username (often your system username on Mac).

### 3. Setup PostgreSQL Database

```bash
# Create the database
createdb mcp_ecommerce

# Seed with laptop and book products
cd mcp-server
psql -d mcp_ecommerce -f scripts/seed_laptops_expanded.sql
psql -d mcp_ecommerce -f scripts/seed_books_expanded.sql

# Verify products were added
psql -d mcp_ecommerce -c "SELECT category, COUNT(*) FROM products GROUP BY category;"
# Expected output:
#   category   | count
# -------------+-------
#  Electronics |    37
#  Books       |    50
```

### 4. Setup Vehicle Data (for IDSS)

The vehicle recommendation system requires pre-built data files:

```bash
# Create symlink to vehicle data (adjust path as needed)
ln -s /path/to/car_dataset_idss data/car_dataset_idss

# Required files in data/car_dataset_idss/:
# - uni_vehicles.db (~1.5 GB) - Vehicle database
# - vehicle_reviews_tavily.db (~22 MB) - Reviews
# - bm25_index.pkl - BM25 search index
# - phrase_embeddings/ - FAISS indices
```

### 5. Start the Servers

**Terminal 1 - IDSS API (Vehicle Recommendations):**
```bash
cd /path/to/idss-backend
source venv/bin/activate
uvicorn idss.api.server:app --reload --port 8000

# First startup preloads ~2GB of models (60-120 seconds)
# For faster startup during development:
# IDSS_SKIP_PRELOAD=1 uvicorn idss.api.server:app --reload --port 8000
```

**Terminal 2 - MCP Server (Multi-Domain Gateway):**
```bash
cd /path/to/idss-backend/mcp-server
source ../venv/bin/activate
uvicorn app.main:app --reload --port 8001
```

### 6. Verify Installation

```bash
# Test MCP server health
curl http://localhost:8001/health

# Test multi-domain chat
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I want a Dell laptop"}'

# Test vehicle routing (requires IDSS on 8000)
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I want an SUV under $30000"}'
```

## Frontend Configuration

If using the IDSS Web frontend, configure it to point to the MCP server:

```bash
# In frontend/.env.local
NEXT_PUBLIC_API_BASE_URL="http://localhost:8001"
```

The frontend will call `/chat` on port 8001, which routes to the appropriate backend based on the detected domain.
For laptops and books, `/chat` always runs the MCP interview flow before returning recommendations.

## API Reference

### MCP Server (Port 8001) - Multi-Domain Gateway

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Main conversation endpoint (routes by domain, interview-first for laptops/books) |
| `/session/{id}` | GET | Get session state |
| `/session/reset` | POST | Reset/create session |
| `/sessions` | GET | List active sessions |
| `/health` | GET | Health check with DB/cache status |
| `/api/search-products` | POST | Direct product search |
| `/tools` | GET | List available MCP tools |

### IDSS API (Port 8000) - Vehicle Recommendations

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Vehicle conversation (interview → recommendations) |
| `/recommend` | POST | Direct recommendations (bypass interview) |
| `/recommend/compare` | POST | Compare ranking methods |
| `/status` | GET | Server status with preload timing |

### Chat Request Format

```json
{
  "message": "I want a laptop for gaming",
  "session_id": "optional-session-id",
  "k": 3,           // Number of interview questions (0 = skip)
  "n_rows": 3,      // Result rows
  "n_per_row": 3    // Items per row
}
```

### Chat Response Format

```json
{
  "response_type": "recommendations",  // or "question"
  "message": "Based on your preferences...",
  "session_id": "uuid",
  "domain": "laptops",                 // "vehicles", "laptops", "books"
  "recommendations": [[...], [...]],   // 2D grid of products
  "bucket_labels": ["Budget", "Mid-Range", "Premium"],
  "quick_replies": ["Under $1000", "Gaming", "Work"],
  "filters": {"brand": "Dell"},
  "question_count": 2
}
```

## Domain Routing

The MCP server automatically detects the domain from user messages:

| Keywords | Domain | Backend |
|----------|--------|---------|
| car, SUV, truck, Toyota, Honda... | vehicles | IDSS (port 8000) |
| laptop, MacBook, computer, Dell... | laptops | MCP interview → PostgreSQL |
| book, novel, reading, fiction... | books | MCP interview → PostgreSQL |
| hi, hello, ambiguous | none | Asks "What are you looking for?" |

## Configuration Files

### Main Configuration (`config/default.yaml`)

```yaml
idss:
  k: 3                          # Default interview questions
  n_vehicles_per_row: 3
  num_rows: 3

recommendation:
  method: "embedding_similarity"  # or "coverage_risk"
  embedding_similarity:
    lambda_param: 0.85
  coverage_risk:
    lambda_risk: 0.5
```

### MCP Configuration (`mcp-server/config.yaml`)

```yaml
default_backend: idss

backends:
  idss:
    url: http://localhost:8000
  postgres:
    url: null  # Direct DB connection

cache:
  redis_url: redis://localhost:6379
```

## Development

### Project Structure

```
idss-backend/
├── idss/                        # IDSS Vehicle Recommendation System
│   ├── api/server.py           # FastAPI server (port 8000)
│   ├── core/controller.py      # Main IDSS controller
│   ├── recommendation/         # Ranking algorithms
│   └── diversification/        # Entropy bucketing
│
├── mcp-server/                  # MCP Multi-Domain Gateway
│   ├── app/
│   │   ├── main.py            # FastAPI server (port 8001)
│   │   ├── chat_endpoint.py   # /chat with domain routing
│   │   ├── conversation_controller.py  # Domain detection
│   │   └── interview/         # Question generation
│   ├── scripts/
│   │   └── seed_*.sql         # Database seed files
│   └── config.yaml            # MCP configuration
│
├── config/default.yaml         # IDSS configuration
├── data/                       # Vehicle data (symlink)
├── requirements.txt
└── .env                        # Environment variables
```

### Running Tests

```bash
# IDSS tests
python -m pytest idss/tests/

# MCP server tests
cd mcp-server
python -m pytest tests/
```

### Adding New Products

```bash
# Add more laptops
psql -d mcp_ecommerce -c "
INSERT INTO products (product_id, name, description, category, brand)
VALUES ('prod-laptop-new-001', 'New Laptop', 'Description', 'Electronics', 'Brand');

INSERT INTO prices (product_id, price_cents, currency)
VALUES ('prod-laptop-new-001', 99999, 'USD');
"
```

## Troubleshooting

### "role does not exist" error
Your DATABASE_URL has the wrong username. Check your PostgreSQL username:
```bash
psql -c "\du"  # List users
# Update .env with correct username
```

### "database does not exist" error
Create the database:
```bash
createdb mcp_ecommerce
```

### Products not showing
1. Check products exist: `psql -d mcp_ecommerce -c "SELECT COUNT(*) FROM products;"`
2. Check brand casing matches database (e.g., "Dell" not "dell")
3. Restart the MCP server after seeding

### IDSS not loading
1. Check vehicle data exists: `ls -la data/car_dataset_idss/`
2. Check OPENAI_API_KEY is set
3. First startup takes 60-120 seconds to preload models

### Redis connection errors
Redis is optional. The system falls back to in-memory sessions if Redis is unavailable.

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | - | OpenAI API key for LLM features |
| `DATABASE_URL` | Yes | - | PostgreSQL connection string |
| `LOG_LEVEL` | No | INFO | Logging level |
| `REDIS_HOST` | No | localhost | Redis host for caching |
| `REDIS_PORT` | No | 6379 | Redis port |
| `NEO4J_URI` | No | - | Neo4j connection (future) |
| `IDSS_SKIP_PRELOAD` | No | 0 | Skip model preloading (dev) |
| `IDSS_PRELOAD_ALL` | No | 1 | Preload all methods |

## License

Stanford LDR Lab Research Project
