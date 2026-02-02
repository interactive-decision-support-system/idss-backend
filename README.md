#  IDSS - Interactive Decision Support System

An Interactive Decision Support System for research on **optimal question count (k)**: *"How many questions should we ask users to get the best results?"*

## Overview

This system helps users find vehicles through:
1. **Configurable Interview** - Ask 0 to k clarifying questions before recommending
2. **Two Ranking Methods** - Embedding Similarity (Dense Vector + MMR) or Coverage-Risk Optimization
3. **Entropy-Based Diversification** - Automatically diversify results along the dimension with highest uncertainty
4. **3×N Grid Output** - Always outputs a structured grid of recommendations

## Quick Start

### Installation

```bash
# Clone the repository
cd /home/yol013/idss_new

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your API keys (OPENAI_API_KEY)

# Create symlink to data (if not exists)
ln -s /home/yol013/interactive-decision-support-system-supervisor/data data
```

### Run the Demo

```bash
# Interactive CLI demo
python scripts/demo.py

# With custom settings
python scripts/demo.py --k 0 --method coverage_risk        # Skip interview, use Coverage-Risk
python scripts/demo.py --k 5 --method embedding_similarity # Ask 5 questions, use Embedding Similarity
```

### Run the API Server

```bash
# Start FastAPI server (preloads all models at startup)
python -m idss.api.server

# Or with uvicorn (auto-reload for development)
uvicorn idss.api.server:app --reload --port 8000

# Skip preloading for faster startup (models load on first request)
IDSS_SKIP_PRELOAD=1 python -m idss.api.server

# Only preload the configured method (saves memory)
IDSS_PRELOAD_ALL=0 python -m idss.api.server

# API documentation available at:
# http://localhost:8000/docs
# http://localhost:8000/status  (shows preload timing)
```

**Note:** First startup preloads ~2GB of models and embeddings. This takes 60-120 seconds but ensures fast response times for all requests.

## Configuration

Edit `config/default.yaml`:

```yaml
idss:
  k: 3                          # Number of questions (0 = skip interview)
  n_vehicles_per_row: 3         # Vehicles per row in output
  num_rows: 3                   # Number of rows (diversification buckets)

recommendation:
  method: "embedding_similarity"  # "embedding_similarity" or "coverage_risk"

  embedding_similarity:
    lambda_param: 0.85          # MMR diversity (0=diverse, 1=relevant)

  coverage_risk:
    lambda_risk: 0.5            # Risk penalty weight
    mode: "sum"                 # "max" or "sum" aggregation
    tau: 0.5                    # Phrase similarity threshold
    alpha: 1.0                  # Coverage function steepness
```

## Architecture

```
User Input
    │
    ▼
┌─────────────────────────────────┐
│ Semantic Parser (LLM)           │
│ - Extract filters & preferences │
│ - Detect impatience             │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│ Should Recommend Now?           │
│ - k=0 mode (skip interview)     │
│ - Asked k questions             │
│ - User is impatient             │
└─────────────────────────────────┘
    │ NO                     │ YES
    ▼                        ▼
┌──────────────┐    ┌─────────────────────────────────┐
│ Ask Question │    │ SQL Filter → 500 Candidates     │
│ (LLM-based)  │    │         ▼                       │
└──────────────┘    │ Embedding Similarity or         │
                    │ Coverage-Risk → Top 100 Ranked  │
                    │         ▼                       │
                    │ Entropy Dimension Selection     │
                    │         ▼                       │
                    │ Data-Driven Bucketing           │
                    │         ▼                       │
                    │ Output: 3×N Grid                │
                    └─────────────────────────────────┘
```

## Recommendation Methods

### Embedding Similarity: Dense Vector + MMR

Uses dense embeddings for semantic similarity ranking with Maximal Marginal Relevance for diversity.

```
User Preferences → Sentence Embedding → FAISS Search → MMR Diversification
```

### Coverage-Risk Optimization

Uses phrase-level alignment with vehicle review pros/cons for coverage-risk optimization.

```
Objective: max Coverage(S) - λ·Risk(S)

Where:
- Coverage = How well selected vehicles cover user's liked features
- Risk = How much selected vehicles match user's disliked features
```

## Diversification

### Entropy-Based Dimension Selection

Automatically selects which dimension to diversify based on Shannon entropy:

```
H = -Σ p_i × log₂(p_i)
```

- **High entropy** = Many different values = Good for diversification
- **Low entropy** = Few values = User probably already decided

Example:
```
User says: "I want an SUV under $30K"

Specified: body_style, price (excluded from diversification)
Remaining entropy scores:
  - make: 3.07      ← HIGHEST (diversify by this!)
  - drivetrain: 1.98
  - year: 1.54
  - fuel_type: 1.31
```

### Data-Driven Bucketing

Bucket boundaries computed from data distribution (no hardcoded ranges):

- **Numerical dimensions** (price, mileage, year): Quantile-based equal-count buckets
- **Categorical dimensions** (make, fuel_type): Top-k most common values

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check + current config |
| `/chat` | POST | Conversation endpoint (interview → recommendations) |
| `/recommend` | POST | Direct recommendations (bypass interview) |
| `/recommend/compare` | POST | Compare Embedding Similarity vs Coverage-Risk results |
| `/session/{id}` | GET | Get session state |
| `/session/reset` | POST | Reset/create session |
| `/sessions` | GET | List active sessions |

### Example API Calls

**API Base URL:** `http://137.184.41.112:8000`

#### Chat Endpoint (`/chat`)

The main conversation endpoint. Supports per-request configuration overrides.

**Request Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `message` | string | Yes | User's message |
| `session_id` | string | No | Session ID for conversation continuity |
| `k` | int | No | Number of interview questions (0 = skip interview) |
| `method` | string | No | `"embedding_similarity"` or `"coverage_risk"` |
| `n_rows` | int | No | Number of result rows |
| `n_per_row` | int | No | Vehicles per row |

**Basic chat (uses default config):**
```bash
curl -X POST http://137.184.41.112:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I want an SUV under $30000"}'
```

**Skip interview (k=0) - get recommendations immediately:**
```bash
curl -X POST http://137.184.41.112:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I want an SUV under $30000",
    "k": 0
  }'
```

**Specify recommendation method:**
```bash
curl -X POST http://137.184.41.112:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I want a fuel efficient sedan",
    "k": 0,
    "method": "coverage_risk"
  }'
```

**Full configuration override:**
```bash
curl -X POST http://137.184.41.112:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I want a family car",
    "k": 2,
    "method": "embedding_similarity",
    "n_rows": 4,
    "n_per_row": 5
  }'
```

#### Session Management

To maintain conversation continuity, pass the `session_id` from previous responses:

```bash
# First message - starts new session
curl -X POST http://137.184.41.112:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I want a fuel efficient sedan",
    "k": 2
  }'

# Response: {"session_id": "abc-123", "question_count": 1, ...}

# Second message - continue same session
curl -X POST http://137.184.41.112:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I prefer Japanese brands",
    "session_id": "abc-123",
    "k": 2
  }'

# Response: {"session_id": "abc-123", "question_count": 2, ...}
```

#### Other Endpoints

```bash
# Health check
curl http://137.184.41.112:8000/

# Server status (shows preload timing)
curl http://137.184.41.112:8000/status

# Get session state
curl http://137.184.41.112:8000/session/YOUR_SESSION_ID

# Reset session
curl -X POST http://137.184.41.112:8000/session/reset \
  -H "Content-Type: application/json" \
  -d '{"session_id": "YOUR_SESSION_ID"}'

# List active sessions
curl http://137.184.41.112:8000/sessions
```

## Project Structure

```
idss_new/
├── config/
│   └── default.yaml              # Main configuration
├── data/                         # Symlink to vehicle database
├── idss/
│   ├── __init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── models.py             # Pydantic request/response models
│   │   └── server.py             # FastAPI server
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py             # Configuration management
│   │   └── controller.py         # Main IDSS controller
│   ├── data/
│   │   ├── __init__.py
│   │   └── vehicle_store.py      # SQLite vehicle database access
│   ├── diversification/
│   │   ├── __init__.py
│   │   ├── entropy.py            # Entropy-based dimension selection
│   │   ├── bucketing.py          # Data-driven quantile bucketing
│   │   └── mmr.py                # MMR diversification
│   ├── interview/
│   │   ├── __init__.py
│   │   ├── preference_slots.py   # Organizes remaining topics to ask about
│   │   └── question_generator.py # LLM-based question generation
│   ├── parsing/
│   │   ├── __init__.py
│   │   └── semantic_parser.py    # LLM-based filter extraction
│   ├── recommendation/
│   │   ├── __init__.py
│   │   ├── embedding_similarity.py  # Embedding Similarity: Dense + MMR
│   │   ├── coverage_risk.py         # Coverage-Risk Optimization
│   │   ├── dense_embedding_store.py
│   │   ├── dense_ranker.py
│   │   ├── phrase_store.py
│   │   └── preference_alignment.py
│   └── utils/
│       ├── __init__.py
│       └── logger.py
├── scripts/
│   └── demo.py                   # Interactive CLI demo
├── .env                          # API keys (not in git)
├── .gitignore
├── README.md
└── requirements.txt
```

## Data Requirements

The system requires the following data files (via symlink to original repo):

| File | Size | Purpose |
|------|------|---------|
| `uni_vehicles.db` | ~1.5 GB | Main vehicle database (167K vehicles) |
| `faiss_indices/` | ~600 MB | FAISS dense vector index (Embedding Similarity) |
| `phrase_embeddings/` | ~1.2 GB | Pre-computed phrase embeddings (Coverage-Risk) |
| `vehicle_reviews_tavily.db` | ~22 MB | Vehicle review pros/cons (Coverage-Risk) |
