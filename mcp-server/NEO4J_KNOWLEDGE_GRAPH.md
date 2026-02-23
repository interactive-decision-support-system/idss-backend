## Complex Neo4j Knowledge Graph for E-Commerce

### Overview

This implementation creates a **rich, multi-dimensional knowledge graph** for laptops and books that goes far beyond simple product catalogs. The graph captures:

- **Detailed component relationships** for laptops (CPU, GPU, RAM, Storage, Display)
- **Manufacturing and supply chain** connections
- **Literary relationships** between books, authors, genres
- **User interactions** (reviews, purchases, wishlists, views)
- **Software compatibility** for laptops
- **Genre hierarchies** for books
- **Sentiment analysis** on reviews
- **Product comparisons** and recommendations
- **And much more...**

---

## Graph Schema

### Node Types

#### Products
- **Laptop** - Computing devices with detailed specs
- **Book** - Literary works with rich metadata

#### Components (Laptops)
- **CPU** - Processors with core counts, clock speeds, tiers
- **GPU** - Graphics cards with VRAM, ray tracing capabilities
- **RAM** - Memory modules with capacity, speed, type
- **Storage** - Storage devices (SSD/HDD) with interface, speeds
- **Display** - Screens with resolution, refresh rate, panel type

#### Business Entities
- **Manufacturer** - Laptop manufacturers (Dell, HP, Lenovo, etc.)
- **Supplier** - Component suppliers
- **Factory** - Manufacturing facilities
- **Publisher** - Book publishers (Penguin, HarperCollins, etc.)

#### Creative Entities
- **Author** - Book authors with nationality, biography, awards
- **Genre** - Book genres with hierarchical relationships
- **Theme** - Literary themes explored in books
- **Series** - Book series

#### Software
- **Software** - Applications (Visual Studio, Photoshop, etc.)
- **OperatingSystem** - OS versions (Windows 11, macOS, etc.)

#### User Data
- **User** - Platform users
- **Review** - Product reviews with sentiment
- **Sentiment** - Sentiment classification (positive/negative/neutral)

---

## Relationship Types

### Laptop Relationships

| Relationship | From | To | Properties | Description |
|--------------|------|-----|------------|-------------|
| `MANUFACTURED_BY` | Laptop | Manufacturer | - | Who makes the laptop |
| `HAS_CPU` | Laptop | CPU | - | Laptop's processor |
| `HAS_GPU` | Laptop | GPU | - | Laptop's graphics card |
| `HAS_RAM` | Laptop | RAM | - | Laptop's memory |
| `HAS_STORAGE` | Laptop | Storage | - | Laptop's storage device |
| `HAS_DISPLAY` | Laptop | Display | - | Laptop's screen |
| `COMPATIBLE_WITH` | Laptop | Software | `performance_rating`, `tested_date` | Software compatibility |
| `RUNS` | Laptop | OperatingSystem | - | OS compatibility |
| `SOURCES_FROM` | Manufacturer | Supplier | `component_type`, `reliability_score`, `lead_time_days` | Supply chain |
| `OPERATES` | Manufacturer | Factory | - | Manufacturing locations |

### Book Relationships

| Relationship | From | To | Properties | Description |
|--------------|------|-----|------------|-------------|
| `WRITTEN_BY` | Book | Author | - | Book authorship |
| `PUBLISHED_BY` | Book | Publisher | `year` | Publication info |
| `BELONGS_TO_GENRE` | Book | Genre | - | Primary genre |
| `SUBGENRE_OF` | Genre | Genre | - | Genre hierarchy |
| `EXPLORES_THEME` | Book | Theme | - | Literary themes |
| `PART_OF_SERIES` | Book | Series | `position` | Series membership |
| `SIMILAR_THEME` | Book | Book | `description` | Thematic connections |
| `INSPIRED_BY` | Book | Book | `description` | Literary influence |
| `RECOMMENDED_WITH` | Book | Book | - | Recommendation pairs |

### User & Review Relationships

| Relationship | From | To | Properties | Description |
|--------------|------|-----|------------|-------------|
| `WROTE_REVIEW` | User | Review | - | Review authorship |
| `REVIEWS` | Review | Product | - | What is being reviewed |
| `HAS_SENTIMENT` | Review | Sentiment | - | Sentiment classification |
| `PURCHASED` | User | Product | `timestamp`, `price_at_time` | Purchase history |
| `VIEWED` | User | Product | `timestamp`, `duration_seconds` | Browsing behavior |
| `ADDED_TO_WISHLIST` | User | Product | `timestamp` | Wishlist items |

### Comparison Relationships

| Relationship | From | To | Properties | Description |
|--------------|------|-----|------------|-------------|
| `SIMILAR_TO` | Product | Product | `score` | Similarity score |
| `BETTER_THAN` | Product | Product | `score` | Quality comparison |
| `CHEAPER_THAN` | Product | Product | `price_difference` | Price comparison |
| `ALTERNATIVE_TO` | Product | Product | - | Product alternatives |

---

## Node Properties

### Laptop Node
```cypher
{
  product_id: String (unique),
  name: String,
  brand: String,
  model: String,
  price: Float,
  description: String,
  image_url: String,
  category: "Electronics",
  subcategory: String,
  available: Boolean,
  weight_kg: Float,
  portability_score: Integer (0-100),
  battery_life_hours: Integer,
  screen_size_inches: Float,
  refresh_rate_hz: Integer,
  created_at: DateTime,
  // Richer KG (§7) – Reddit-style features for complex queries (optional; backfill sets these)
  good_for_ml: Boolean,
  good_for_gaming: Boolean,
  good_for_web_dev: Boolean,
  good_for_creative: Boolean,
  repairable: Boolean,   // e.g. Framework
  refurbished: Boolean  // e.g. Back Market
}
```

### Merchant laptop sources (rich catalogs)

Laptops from **System76** (Linux; warranty/returns), **Framework** (repairable; warranty/return docs), and **Back Market** (refurbished; warranty/returns/shipping) are seeded via `scripts/scrape_merchant_laptops.py`. Descriptions include shipping, return policy, and warranty text for merchant-agent demos.

### PostgreSQL `products.kg_features` (JSONB)

Same concepts are stored in PostgreSQL for search when Neo4j is not used:

- **good_for_ml**, **good_for_gaming**, **good_for_web_dev**, **good_for_creative**: boolean (true when product matches).
- **battery_life_hours**: integer (parsed from description or default for laptops).

Backfill: `scripts/backfill_kg_features.py`. Search and `kg_service` use these in filters (see §7 in WEEK6_ACTION_PLAN.md).

### CPU Node
```cypher
{
  model: String (unique),
  manufacturer: String,
  cores: Integer,
  threads: Integer,
  base_clock_ghz: Float,
  boost_clock_ghz: Float,
  tdp_watts: Integer,
  generation: String,
  tier: String (Budget/Mid-range/High-end/Ultra)
}
```

### GPU Node
```cypher
{
  model: String (unique),
  manufacturer: String,
  vram_gb: Integer,
  memory_type: String (GDDR6/GDDR6X),
  tdp_watts: Integer,
  tier: String,
  ray_tracing: Boolean
}
```

### Book Node
```cypher
{
  product_id: String (unique),
  title: String,
  name: String,
  price: Float,
  description: String,
  image_url: String,
  category: "Books",
  isbn: String,
  pages: Integer,
  language: String,
  publication_year: Integer,
  edition: String,
  format: String (Hardcover/Paperback/eBook),
  available: Boolean,
  created_at: DateTime
}
```

### Author Node
```cypher
{
  name: String (unique),
  nationality: String,
  birth_year: Integer,
  biography: String,
  awards: [String]
}
```

### Review Node
```cypher
{
  review_id: String,
  rating: Integer (1-5),
  comment: String,
  sentiment_score: Float (-1 to 1),
  sentiment_label: String (positive/negative/neutral),
  helpful_count: Integer,
  verified_purchase: Boolean,
  created_at: DateTime
}
```

---

## Sample Cypher Queries

### Laptop Queries

#### 1. Find Gaming Laptops with High-end GPUs
```cypher
MATCH (l:Laptop)-[:HAS_GPU]->(gpu:GPU)
WHERE gpu.tier IN ['High-end', 'Ultra high-end'] 
  AND gpu.ray_tracing = true
RETURN l.name, l.brand, gpu.model, gpu.vram_gb, l.price
ORDER BY l.price DESC
LIMIT 10
```

#### 2. Compare CPU Performance
```cypher
MATCH (l:Laptop)-[:HAS_CPU]->(cpu:CPU)
RETURN cpu.manufacturer, 
       cpu.tier, 
       count(l) AS laptops_count,
       avg(l.price) AS avg_price,
       avg(cpu.cores) AS avg_cores
GROUP BY cpu.manufacturer, cpu.tier
ORDER BY avg_price DESC
```

#### 3. Find Laptops by Component Specifications
```cypher
MATCH (l:Laptop)-[:HAS_CPU]->(cpu:CPU),
      (l)-[:HAS_RAM]->(ram:RAM),
      (l)-[:HAS_GPU]->(gpu:GPU)
WHERE cpu.cores >= 8
  AND ram.capacity_gb >= 16
  AND gpu.vram_gb >= 6
  AND l.price < 2000
RETURN l.name, cpu.model, ram.capacity_gb, gpu.model, l.price
ORDER BY l.price
LIMIT 20
```

#### 4. Software Compatibility Analysis
```cypher
MATCH (l:Laptop)-[c:COMPATIBLE_WITH]->(s:Software)
WHERE s.category = 'Gaming'
RETURN l.name, 
       collect({software: s.name, rating: c.performance_rating}) AS compatible_software,
       avg(c.performance_rating) AS avg_performance
ORDER BY avg_performance DESC
LIMIT 10
```

#### 5. Supply Chain Analysis
```cypher
MATCH (m:Manufacturer)-[:SOURCES_FROM]->(s:Supplier)
RETURN m.name AS manufacturer,
       count(s) AS supplier_count,
       avg(s.rating) AS avg_supplier_rating,
       collect(s.specialization) AS specializations
GROUP BY m.name
ORDER BY supplier_count DESC
```

### Book Queries

#### 6. Find Books by Genre Hierarchy
```cypher
MATCH (b:Book)-[:BELONGS_TO_GENRE]->(g:Genre)-[:SUBGENRE_OF*0..2]->(parent:Genre)
WHERE parent.name = 'Fiction'
RETURN b.title, g.name AS genre, collect(parent.name) AS parent_genres
LIMIT 20
```

#### 7. Author's Complete Works
```cypher
MATCH (a:Author)<-[:WRITTEN_BY]-(b:Book)
RETURN a.name,
       a.nationality,
       count(b) AS total_books,
       collect({title: b.title, year: b.publication_year, genre: [(b)-[:BELONGS_TO_GENRE]->(g) | g.name][0]}) AS books
ORDER BY total_books DESC
LIMIT 10
```

#### 8. Literary Connections
```cypher
MATCH (b1:Book)-[r:SIMILAR_THEME|INSPIRED_BY|RECOMMENDED_WITH]->(b2:Book)
RETURN b1.title, type(r) AS connection_type, b2.title, r.description
LIMIT 20
```

#### 9. Books in a Series
```cypher
MATCH (b:Book)-[r:PART_OF_SERIES]->(s:Series)
RETURN s.name AS series,
       s.total_books,
       collect({position: r.position, title: b.title, year: b.publication_year}) AS books
ORDER BY series
```

#### 10. Theme Analysis
```cypher
MATCH (b:Book)-[:EXPLORES_THEME]->(t:Theme)
RETURN t.name AS theme,
       count(b) AS book_count,
       collect(b.title)[0..5] AS sample_books
ORDER BY book_count DESC
LIMIT 10
```

### Review & Sentiment Queries

#### 11. Top Rated Products by Sentiment
```cypher
MATCH (p:Product)<-[:REVIEWS]-(r:Review)
RETURN p.name,
       p.category,
       count(r) AS review_count,
       avg(r.rating) AS avg_rating,
       avg(r.sentiment_score) AS avg_sentiment,
       count(CASE WHEN r.sentiment_label = 'positive' THEN 1 END) AS positive_reviews
ORDER BY avg_rating DESC, review_count DESC
LIMIT 10
```

#### 12. User Review Patterns
```cypher
MATCH (u:User)-[:WROTE_REVIEW]->(r:Review)-[:REVIEWS]->(p:Product)
RETURN u.username,
       u.verified,
       count(r) AS total_reviews,
       avg(r.rating) AS avg_rating_given,
       collect(DISTINCT p.category) AS categories_reviewed
ORDER BY total_reviews DESC
LIMIT 10
```

#### 13. Sentiment Distribution by Product Category
```cypher
MATCH (p:Product)<-[:REVIEWS]-(r:Review)-[:HAS_SENTIMENT]->(s:Sentiment)
RETURN p.category,
       s.label AS sentiment,
       count(r) AS count,
       avg(r.rating) AS avg_rating
ORDER BY p.category, count DESC
```

### Comparison & Recommendation Queries

#### 14. Find Similar Products
```cypher
MATCH (p1:Product {product_id: $product_id})-[s:SIMILAR_TO]->(p2:Product)
RETURN p2.name, p2.price, s.score AS similarity_score
ORDER BY s.score DESC
LIMIT 5
```

#### 15. Product Alternatives
```cypher
MATCH (p1:Product)-[:SIMILAR_TO]-(p2:Product)
WHERE p1.category = 'Electronics'
  AND abs(p1.price - p2.price) < 200
RETURN p1.name, p1.price, p2.name, p2.price, abs(p1.price - p2.price) AS price_diff
ORDER BY price_diff
LIMIT 20
```

#### 16. Recommendation Engine
```cypher
// Find products similar to what user has viewed
MATCH (u:User {user_id: $user_id})-[:VIEWED]->(p1:Product)-[:SIMILAR_TO]->(p2:Product)
WHERE NOT (u)-[:VIEWED|PURCHASED]->(p2)
RETURN p2.name, 
       p2.category,
       p2.price,
       count(*) AS recommendation_strength
ORDER BY recommendation_strength DESC
LIMIT 10
```

### Complex Multi-Relationship Queries

#### 17. Laptop Ecosystem Analysis
```cypher
MATCH (l:Laptop)-[:MANUFACTURED_BY]->(m:Manufacturer),
      (l)-[:HAS_CPU]->(cpu:CPU),
      (l)-[:HAS_GPU]->(gpu:GPU),
      (l)<-[:REVIEWS]-(r:Review)
RETURN l.name,
       m.name AS manufacturer,
       cpu.model,
       gpu.model,
       count(r) AS review_count,
       avg(r.rating) AS avg_rating,
       l.price
ORDER BY avg_rating DESC, review_count DESC
LIMIT 10
```

#### 18. User Purchase Behavior Analysis
```cypher
MATCH (u:User)-[:PURCHASED]->(p:Product),
      (p)<-[:REVIEWS]-(r:Review)
WHERE (u)-[:WROTE_REVIEW]->(r)
RETURN u.username,
       count(DISTINCT p) AS products_purchased,
       count(r) AS reviews_written,
       avg(r.rating) AS avg_rating_given,
       collect(DISTINCT p.category) AS categories
ORDER BY products_purchased DESC
LIMIT 20
```

#### 19. Cross-Product Recommendations
```cypher
// Users who bought this also bought...
MATCH (p1:Product {product_id: $product_id})<-[:PURCHASED]-(u:User)-[:PURCHASED]->(p2:Product)
WHERE p1 <> p2
RETURN p2.name,
       p2.category,
       p2.price,
       count(DISTINCT u) AS co_purchase_count
ORDER BY co_purchase_count DESC
LIMIT 10
```

#### 20. Full Product Context
```cypher
MATCH (p:Product {product_id: $product_id})
OPTIONAL MATCH (p)<-[:REVIEWS]-(r:Review)-[:WROTE_REVIEW]-(u:User)
OPTIONAL MATCH (p)-[rel]->(connected)
RETURN p,
       collect(DISTINCT {type: type(rel), node: connected}) AS connections,
       count(r) AS total_reviews,
       avg(r.rating) AS avg_rating,
       collect(DISTINCT u.username)[0..5] AS reviewers
```

---

## Graph Analytics Use Cases

### 1. **Supply Chain Optimization**
Analyze manufacturer-supplier-factory relationships to identify:
- Bottlenecks in component sourcing
- Supplier reliability scores
- Optimal factory locations

### 2. **Product Recommendation Engine**
Use multi-hop paths to recommend products based on:
- Similar component specifications
- User purchase history
- Review sentiment
- Price ranges

### 3. **Market Segmentation**
Identify product clusters based on:
- Component specifications
- Price tiers
- User demographics
- Review sentiment

### 4. **Sentiment Trend Analysis**
Track sentiment over time for:
- Product lines
- Manufacturers
- Authors/publishers
- Software compatibility

### 5. **Literary Network Analysis**
Map relationships between:
- Authors and their influences
- Genre evolution
- Theme clustering
- Series connections

---

## Setup Instructions

### Prerequisites
```bash
# Install Neo4j (Docker recommended)
docker pull neo4j:latest

# Run Neo4j
docker run \
    --name neo4j \
    -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/your_password \
    -v $HOME/neo4j/data:/data \
    neo4j:latest
```

### Environment Variables
```bash
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USERNAME="neo4j"
export NEO4J_PASSWORD="your_password"
```

### Build the Graph
```bash
cd mcp-server
python scripts/build_knowledge_graph.py
```

### Access Neo4j Browser
Open http://localhost:7474 in your browser

---

## Performance Considerations

### Indexing Strategy
- **Unique constraints** on IDs prevent duplicates
- **Property indexes** on frequently queried fields (name, price, brand)
- **Composite indexes** for multi-field queries

### Query Optimization
- Use `MERGE` for idempotent operations
- Add `LIMIT` clauses to prevent large result sets
- Use `PROFILE` to analyze query performance
- Consider query parameterization for caching

### Scaling
- Enable **Bloom** for visual exploration
- Use **Graph Data Science** library for advanced analytics
- Implement **caching** for frequently accessed paths
- Consider **sharding** for very large datasets

---

## Future Enhancements

1. **Real-time Updates**: Sync with PostgreSQL using change data capture
2. **ML Integration**: Use GDS algorithms for link prediction, community detection
3. **Temporal Graphs**: Track price changes, spec updates over time
4. **Multi-tenancy**: Separate graphs per store/brand
5. **Graph Embeddings**: Generate vector representations for semantic search
6. **Knowledge Completion**: Auto-infer missing relationships using AI

---

## Conclusion

This knowledge graph implementation transforms flat relational data into a rich, interconnected web of relationships that enables:

 **Complex querying** - Multi-hop path queries across products, users, reviews
 **Deep insights** - Supply chain, sentiment, compatibility analysis
 **Smart recommendations** - Context-aware product suggestions
 **Scalability** - Handles millions of nodes and relationships
 **Flexibility** - Easy to extend with new node types and relationships

The graph grows organically as new products, reviews, and interactions are added, automatically discovering new patterns and connections.
