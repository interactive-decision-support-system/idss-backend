# Future Work: Unified MCP + IDSS Architecture

## Goal

Consolidate the interview and recommendation logic so that **everything flows through MCP**, while leveraging the sophisticated algorithms currently implemented in IDSS.

## Current Architecture

```
Frontend (3000)
      │
      ▼
MCP Server (8001)
      │
      ├── UniversalAgent (interview)
      │         │
      │         ▼
      ├── vehicles → idss_adapter.search_products_idss()
      │                    │
      │                    ▼
      │              IDSS API (8000) ← Has its own interview logic (unused)
      │
      └── laptops/books → _search_ecommerce_products() → PostgreSQL
```

**Problem:** The IDSS backend (port 8000) has a complete interview + recommendation system that is being bypassed. The MCP's `UniversalAgent` handles interviews, but doesn't use IDSS's:
- Semantic preference parsing
- Coverage-risk ranking algorithm
- Embedding similarity ranking
- Entropy-based diversification bucketing

## Target Architecture

```
Frontend (3000)
      │
      ▼
MCP Server (8001) ← Single entry point for all domains
      │
      ├── Interview System (based on IDSS principles)
      │   - Semantic parsing of user messages
      │   - Dynamic question generation
      │   - Preference extraction (explicit filters + implicit preferences)
      │
      ├── Recommendation Engine (ported from IDSS)
      │   - Embedding similarity ranking
      │   - Coverage-risk ranking
      │   - Entropy-based diversification
      │
      └── Data Sources
          ├── vehicles → SQLite + FAISS (current IDSS data)
          ├── laptops → PostgreSQL
          └── books → PostgreSQL
```

## Key Components to Port from IDSS

### 1. Interview System (`idss/core/controller.py`)
- `_run_interview()` - Main interview loop
- `_generate_question()` - Dynamic question generation based on missing info
- `_parse_user_response()` - Extract preferences from natural language

### 2. Preference Parsing (`idss/core/`)
- Semantic parsing of user messages
- Extraction of explicit filters (price, brand, etc.)
- Extraction of implicit preferences (liked/disliked features)

### 3. Recommendation Algorithms (`idss/recommendation/`)
- `dense_ranker.py` - Embedding similarity ranking
- `coverage_risk_ranker.py` - Coverage-risk optimization
- Configuration via `lambda_param` and `lambda_risk`

### 4. Diversification (`idss/diversification/`)
- `bucketing.py` - Entropy-based diversification
- Automatic dimension selection (price, body type, etc.)
- Row-based result organization

## Implementation Plan

### Phase 1: Abstract IDSS Core Logic
- [x] Extract vehicle search into reusable MCP tool (`mcp-server/app/tools/vehicle_search.py`)
- [ ] Extract interview logic into reusable module
- [ ] Create abstract `RecommendationEngine` interface
- [ ] Make ranking algorithms domain-agnostic

### Phase 2: Integrate into MCP
- [x] Add IDSS recommendation engine to MCP for vehicles
- [ ] Replace `UniversalAgent` with IDSS-based interview system
- [ ] Support multiple data sources (SQLite, PostgreSQL)

### Phase 3: Unify Data Access
- [ ] Create unified product schema across domains
- [ ] Implement adapters for each data source
- [ ] Support vector search for all domains (not just vehicles)

## Recent Progress

### Vehicle Search Tool (Completed)

Created `mcp-server/app/tools/vehicle_search.py` which:
- Imports IDSS's LocalVehicleStore for SQLite vehicle database access
- Uses embedding similarity ranking (sentence-transformers + FAISS)
- Supports coverage-risk ranking as alternative
- Applies entropy-based diversification bucketing
- Normalizes agent filters to IDSS format (budget, use_case → price, body_style)

```python
from app.tools.vehicle_search import search_vehicles, VehicleSearchRequest

result = search_vehicles(VehicleSearchRequest(
    filters={"make": "Toyota", "body_style": "SUV", "budget": "$20000-$40000"},
    preferences={"liked_features": ["fuel efficiency", "spacious"]},
    method="embedding_similarity",
    n_rows=3,
    n_per_row=3
))
```

## Benefits

1. **Single codebase** - All logic in MCP, IDSS becomes optional
2. **Consistent UX** - Same interview quality across all domains
3. **Better recommendations** - Entropy bucketing for laptops/books too
4. **Easier maintenance** - One place to update algorithms

## Notes

- Keep IDSS API (port 8000) available for backward compatibility
- Consider making IDSS a library that MCP imports directly
- Vehicle data files (~2GB) will still need to be accessible to MCP
