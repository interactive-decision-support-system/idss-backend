# MCP Filtering & Parsing System Documentation

**For**: Thomas (AI Agent Integration)  
**Date**: February 4, 2026  
**Purpose**: Structured documentation of all filters, constraints, semantic parsing, and recommendation logic for AI agent integration

---

## Table of Contents
1. [System Overview](#system-overview)
2. [Filter Types & Structure](#filter-types--structure)
3. [Hard Constraints (MUST Match)](#hard-constraints-must-match)
4. [Soft Constraints (Preferences/Boost)](#soft-constraints-preferencesboost)
5. [Semantic Parsing Logic](#semantic-parsing-logic)
6. [FAISS & Vector Search](#faiss--vector-search)
7. [OR Operations](#or-operations)
8. [API Request/Response Format](#api-requestresponse-format)
9. [Integration Examples](#integration-examples)

---

## System Overview

The MCP filtering system uses a **hybrid approach**:
1. **Semantic Parsing** → Extract filters and preferences from natural language
2. **Hard Constraints** → Database-level filtering (PostgreSQL)
3. **Soft Constraints** → Ranking/boosting (IDSS algorithms)
4. **Vector Search** → Semantic similarity (FAISS embeddings)
5. **Knowledge Graph** → Compatibility & relationships (Neo4j)

### Data Flow

```
User Query → Semantic Parser → Filters + Preferences
                ↓
        [Hard Constraints] → PostgreSQL Query
                ↓
        [Candidate Products] → IDSS Ranking
                ↓
        [Soft Constraints] → Boost/Rerank
                ↓
        [Vector Search] → Semantic Similarity
                ↓
        [Final Results] → Diversification → Response
```

---

## Filter Types & Structure

### Standard Filter Object

```json
{
  "category": "Electronics",           // Hard: Product category
  "brand": "Dell",                    // Hard: Exact brand match
  "product_type": "laptop",           // Hard: Product type
  "subcategory": "Gaming",            // Hard: Use case/genre
  "price_min_cents": 100000,          // Hard: Min price ($1000)
  "price_max_cents": 200000,          // Hard: Max price ($2000)
  "gpu_vendor": "NVIDIA",             // Hard: GPU manufacturer
  "gpu_model": "RTX 4070",            // Soft: Specific GPU model
  "cpu_vendor": "Intel",              // Soft: CPU manufacturer
  "color": "Black",                   // Hard: Product color
  "use_case": "Gaming",               // Soft: Primary use case
  "_soft_preferences": {              // Soft: User preferences
    "liked_features": ["portable", "high-performance"],
    "disliked_features": ["heavy", "poor-battery"]
  },
  "_or_operation": true,              // Enables OR logic
  "_use_idss_ranking": true           // Use IDSS algorithms
}
```

---

## Hard Constraints (MUST Match)

Hard constraints are applied at the **database level** (PostgreSQL WHERE clauses). Products that don't match are **excluded entirely**.

### Electronics/Laptops Hard Constraints

| Filter | Type | Example | SQL Operation |
|--------|------|---------|---------------|
| `category` | string | "Electronics" | `Product.category == 'Electronics'` |
| `brand` | string or list | "Dell" or ["Dell", "HP"] | `Product.brand == 'Dell'` or `OR(brand IN list)` |
| `product_type` | string | "gaming_laptop" | `Product.product_type == 'gaming_laptop'` |
| `subcategory` | string | "Gaming" | `Product.subcategory == 'Gaming'` |
| `gpu_vendor` | string or list | "NVIDIA" | `Product.gpu_vendor == 'NVIDIA'` |
| `price_min_cents` | int | 100000 ($1000) | `Price.price_cents >= 100000` |
| `price_max_cents` | int | 200000 ($2000) | `Price.price_cents <= 200000` |
| `color` | string | "Black" | `Product.color == 'Black'` |

### Books Hard Constraints

| Filter | Type | Example | SQL Operation |
|--------|------|---------|---------------|
| `category` | string | "Books" | `Product.category == 'Books'` |
| `brand` | string | "Stephen King" (author) | `Product.brand == 'Stephen King'` |
| `subcategory` | string | "Sci-Fi" (genre) | `Product.subcategory == 'Sci-Fi'` |
| `product_type` | string | "book" | `Product.product_type == 'book'` |
| `price_min_cents` | int | 1000 ($10) | `Price.price_cents >= 1000` |
| `price_max_cents` | int | 3000 ($30) | `Price.price_cents <= 3000` |

### Vehicles Hard Constraints

| Filter | Type | Example | IDSS Field |
|--------|------|---------|------------|
| `make` | string | "Honda" | `make` |
| `body_style` | string | "SUV" | `body_style` |
| `year` | int | 2022 | `year` |
| `price` | int | 30000 | `price` |
| `mileage` | int | 50000 | `mileage` |
| `fuel_type` | string | "Hybrid" | `fuel_type` |

---

## Soft Constraints (Preferences/Boost)

Soft constraints are applied **after** hard filtering using IDSS ranking algorithms. They **boost** matching products but don't exclude non-matching ones.

### Soft Constraint Structure

```json
{
  "use_case": "Gaming",                    // Primary use case
  "liked_features": [                      // Positive preferences
    "portable",
    "high-performance",
    "premium build quality",
    "long battery life"
  ],
  "disliked_features": [                   // Negative preferences
    "heavy",
    "poor battery",
    "loud fans"
  ],
  "notes": "Need a laptop for game development and streaming"
}
```

### How Soft Constraints Work

1. **IDSS Ranking Algorithms**:
   - `embedding_similarity`: Dense vector embeddings + MMR diversification
   - `coverage_risk`: Coverage-risk optimization

2. **Scoring**:
   - Products matching `liked_features` get **boosted scores**
   - Products matching `disliked_features` get **penalized scores**
   - `use_case` influences feature importance weights

3. **Application**:
   ```python
   # After PostgreSQL query
   if filters.get("_use_idss_ranking"):
       ranked_products = rank_with_embedding_similarity(
           vehicles=postgres_products,
           explicit_filters=hard_filters,
           implicit_preferences=soft_preferences,
           top_k=100,
           lambda_param=0.5,          # Balance between relevance and diversity
           use_mmr=True                # Maximal Marginal Relevance for diversity
       )
   ```

---

## Semantic Parsing Logic

### Parser Location
- File: `/idss/parsing/semantic_parser.py`
- Function: `parse_user_input(message: str, domain: str) -> ParsedInput`

### What It Extracts

#### 1. Explicit Filters (Hard Constraints)

```python
class ExplicitFilters(BaseModel):
    # Electronics/Laptops
    brand: Optional[str] = None                    # Dell, HP, Apple
    product_type: Optional[str] = None             # laptop, gaming_laptop, desktop_pc
    gpu_vendor: Optional[str] = None               # NVIDIA, AMD, Intel
    gpu_model: Optional[str] = None                # RTX 4070, RTX 4080
    cpu_vendor: Optional[str] = None               # Intel, AMD, Apple
    price: Optional[str] = None                    // "0-2000", "under 1500"
    color: Optional[str] = None                    # Black, Silver, Space Gray
    
    # Books
    genre: Optional[str] = None                    # Sci-Fi, Mystery, Romance
    author: Optional[str] = None                   # Stephen King, Andy Weir
    format: Optional[str] = None                   # Hardcover, Paperback, E-book
    
    # Vehicles
    make: Optional[str] = None                     # Honda, Toyota, Ford
    body_style: Optional[str] = None               # SUV, Sedan, Truck
    year: Optional[int] = None                     # 2022, 2023
    fuel_type: Optional[str] = None                # Hybrid, Electric, Gasoline
```

#### 2. Implicit Preferences (Soft Constraints)

```python
class ImplicitPreferences(BaseModel):
    use_case: Optional[str] = None                 # Gaming, Work, School, Creative
    liked_features: List[str] = []                 # ["portable", "high-performance"]
    disliked_features: List[str] = []              # ["heavy", "loud"]
    notes: Optional[str] = None                    # Free-form user notes
```

#### 3. Impatience Signals

```python
# Detects when user wants to skip interview
is_impatient: bool = False                         # "just show me", "skip questions"
wants_recommendations: bool = False                 // "show me recommendations now"
```

### Example Parsing

**Input**: "I need a gaming laptop with NVIDIA graphics under $2000 for work and school"

**Output**:
```json
{
  "explicit_filters": {
    "product_type": "gaming_laptop",
    "gpu_vendor": "NVIDIA",
    "price": "0-2000"
  },
  "implicit_preferences": {
    "use_case": "Gaming",
    "liked_features": ["work", "school", "high-performance"],
    "notes": "gaming laptop with NVIDIA graphics for work and school"
  },
  "is_impatient": false,
  "wants_recommendations": false
}
```

### Domain Detection

```python
def detect_domain_from_message(user_message: str) -> str:
    """
    Detect product domain from message.
    
    Returns: "vehicles", "laptops", or "books"
    """
    text = user_message.lower()
    
    # Keywords for each domain
    laptop_keywords = ["laptop", "computer", "macbook", "pc", "notebook"]
    book_keywords = ["book", "novel", "author", "genre", "fiction"]
    vehicle_keywords = ["car", "truck", "suv", "vehicle", "honda", "toyota"]
    
    if any(kw in text for kw in laptop_keywords):
        return "laptops"
    elif any(kw in text for kw in book_keywords):
        return "books"
    elif any(kw in text for kw in vehicle_keywords):
        return "vehicles"
    
    return "vehicles"  # default
```

---

## FAISS & Vector Search

### Overview
- **Purpose**: Semantic similarity search using dense embeddings
- **Model**: `all-mpnet-base-v2` (768-dimensional embeddings)
- **Index**: FAISS Flat Index (exact nearest neighbor search)
- **Location**: `/idss/recommendation/dense_embedding_store.py`

### How It Works

1. **Product Embedding**:
   ```python
   # Each product has a 768-dim embedding
   product_embedding = sentence_transformer.encode(
       f"{product.name} {product.description} {product.brand}"
   )
   ```

2. **Query Embedding**:
   ```python
   query_embedding = sentence_transformer.encode(user_query)
   ```

3. **Similarity Search**:
   ```python
   # Find top-k most similar products
   distances, indices = faiss_index.search(query_embedding, k=100)
   similar_products = [products[i] for i in indices[0]]
   ```

4. **MMR Diversification** (Maximal Marginal Relevance):
   ```python
   # Balance relevance and diversity
   diversified_products = mmr_rerank(
       similar_products,
       lambda_param=0.5  # 0=max diversity, 1=max relevance
   )
   ```

### When Vector Search Is Used

- **Always enabled** for Electronics/Laptops when query contains descriptive text
- **Not used** for specific title searches (e.g., "Dune", "The Hobbit")
- **Combined with** PostgreSQL results for hybrid retrieval

### Performance

- **Latency**: 50-300ms depending on corpus size
- **Accuracy**: High for semantic queries ("laptop for video editing")
- **Fallback**: If FAISS unavailable, uses PostgreSQL keyword search

---

## OR Operations

### Overview
Enables multiple values for a single filter (e.g., "Dell OR HP laptop").

### Supported OR Operations

| Filter Type | Example | Result |
|-------------|---------|--------|
| Brand | "Dell OR HP laptop" | Products from Dell OR HP |
| GPU Vendor | "NVIDIA or AMD graphics" | NVIDIA OR AMD GPUs |
| Use Case | "gaming or work laptop" | Gaming OR Work laptops |

### Detection Logic

```python
def detect_or_operation(query: str) -> bool:
    """Check if query contains OR operation."""
    return bool(re.search(r'\b(or|OR)\b', query))
```

### Parsing Logic

```python
def parse_or_filters(query: str) -> Dict[str, Any]:
    """
    Parse OR operations from query.
    
    Input: "Dell OR HP laptop"
    Output: {"brand": ["Dell", "HP"], "_or_operation": True}
    """
    # Brand OR pattern
    match = re.search(
        r'\b(dell|hp|lenovo|asus)\s+or\s+(dell|hp|lenovo|asus)\b',
        query.lower()
    )
    
    if match:
        brands = [brand.capitalize() for brand in match.groups()]
        return {"brand": brands, "_or_operation": True}
    
    return {}
```

### Database Application

```python
# Single brand (AND operation)
query = query.filter(Product.brand == "Dell")

# Multiple brands (OR operation)
if filters.get("_or_operation") and isinstance(filters["brand"], list):
    brand_conditions = [Product.brand == b for b in filters["brand"]]
    query = query.filter(or_(*brand_conditions))
```

### Example

**Query**: "Dell OR HP laptop under $2000"

**Parsed Filters**:
```json
{
  "brand": ["Dell", "HP"],
  "category": "Electronics",
  "price_max_cents": 200000,
  "_or_operation": true
}
```

**SQL Generated**:
```sql
SELECT * FROM products
WHERE category = 'Electronics'
  AND price_cents <= 200000
  AND (brand = 'Dell' OR brand = 'HP')
```

---

## API Request/Response Format

### Request Format

```json
POST /api/search-products
{
  "query": "gaming laptop with NVIDIA under $2000",
  "filters": {
    "category": "Electronics",
    "product_type": "gaming_laptop"
  },
  "limit": 20,
  "session_id": "optional-session-id"
}
```

### Response Format

```json
{
  "status": "OK",
  "data": {
    "products": [
      {
        "product_id": "elec-asus-rog-abc123",
        "name": "ASUS ROG Strix G16",
        "price_cents": 179999,
        "brand": "ASUS",
        "category": "Electronics",
        "gpu_vendor": "NVIDIA",
        "gpu_model": "RTX 4070",
        "subcategory": "Gaming",
        "available_qty": 15
      }
    ],
    "total_count": 1,
    "next_cursor": null
  },
  "constraints": [],
  "trace": {
    "request_id": "uuid",
    "sources": ["postgres", "idss_ranking"],
    "timings_ms": {
      "parse_ms": 1.5,
      "db": 25.3,
      "idss_ranking_ms": 156.2,
      "total": 340.5
    },
    "metadata": {
      "used_or_operation": true,
      "used_idss_ranking": true,
      "applied_filters": {
        "brand": ["ASUS", "Lenovo"],
        "gpu_vendor": "NVIDIA",
        "price_max_cents": 200000
      }
    }
  }
}
```

---

## Integration Examples

### Example 1: Simple Brand Filter

**Thomas AI Agent Query**:
```json
{
  "user_intent": "Find Dell laptops",
  "extracted_entities": {
    "brand": "Dell",
    "product_category": "laptop"
  }
}
```

**MCP Request**:
```json
POST /api/search-products
{
  "query": "Dell laptops",
  "filters": {
    "category": "Electronics",
    "brand": "Dell",
    "product_type": "laptop"
  },
  "limit": 10
}
```

### Example 2: Complex Query with Preferences

**Thomas AI Agent Query**:
```json
{
  "user_intent": "Find gaming laptop for streaming",
  "extracted_entities": {
    "use_case": "gaming",
    "secondary_use": "streaming",
    "budget": {"max": 2500}
  },
  "preferences": {
    "must_have": ["high_performance", "good_webcam"],
    "nice_to_have": ["RGB_lighting", "premium_build"]
  }
}
```

**MCP Request**:
```json
POST /api/search-products
{
  "query": "gaming laptop for streaming",
  "filters": {
    "category": "Electronics",
    "product_type": "gaming_laptop",
    "price_max_cents": 250000,
    "subcategory": "Gaming",
    "_soft_preferences": {
      "use_case": "Gaming",
      "liked_features": ["high_performance", "good_webcam", "RGB_lighting", "premium_build"],
      "notes": "for gaming and streaming"
    },
    "_use_idss_ranking": true
  },
  "limit": 10
}
```

### Example 3: OR Operation

**Thomas AI Agent Query**:
```json
{
  "user_intent": "Compare Dell and HP laptops",
  "extracted_entities": {
    "brands": ["Dell", "HP"],
    "comparison_mode": true
  }
}
```

**MCP Request**:
```json
POST /api/search-products
{
  "query": "Dell OR HP laptop",
  "filters": {
    "category": "Electronics",
    "brand": ["Dell", "HP"],
    "_or_operation": true
  },
  "limit": 20
}
```

---

## Summary for AI Agent Integration

### Key Takeaways

1. **Use Hard Constraints** for required criteria (brand, price, category)
2. **Use Soft Constraints** for preferences and boosting (use_case, liked_features)
3. **Enable `_use_idss_ranking: true`** for intelligent ranking with IDSS algorithms
4. **Use `_or_operation: true`** with list values for multiple options
5. **Parse user intent** into structured filters before calling MCP
6. **Check `trace.sources`** to see which systems were used (postgres, idss_ranking, vector_search, kg)

### Recommended Workflow

```
Thomas AI Agent
    ↓
1. Parse user query → Extract intent & entities
    ↓
2. Map entities → MCP filters (hard + soft)
    ↓
3. Call MCP /api/search-products
    ↓
4. Receive ranked products
    ↓
5. Present to user with explanations
```

---

## Contact & Support

For questions or clarifications on this filtering/parsing system, contact:
- **Juli** (MCP Backend Lead)
- **Thomas** (AI Agent Integration)

**Last Updated**: February 4, 2026
