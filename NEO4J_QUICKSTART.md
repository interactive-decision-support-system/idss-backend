# Neo4j Knowledge Graph - Quick Start Guide

## What We Built

A **massively complex Neo4j knowledge graph** for your e-commerce platform that includes:

### For Laptops:
- ✅ CPU, GPU, RAM, Storage, Display nodes (not just properties!)
- ✅ Manufacturer → Supplier → Factory supply chain
- ✅ Software compatibility with performance ratings
- ✅ Component tier rankings (Budget, Mid-range, High-end, Ultra)
- ✅ Detailed specs: clock speeds, VRAM, ray tracing, refresh rates

### For Books:
- ✅ Author nodes with nationality, biography, awards
- ✅ Genre hierarchy (Fiction → Sci-Fi → Cyberpunk)
- ✅ Publisher relationships with founding years
- ✅ Literary connections (INSPIRED_BY, SIMILAR_THEME)
- ✅ Series tracking with position numbers
- ✅ Theme exploration (Love, Betrayal, Justice, etc.)

### Cross-Product:
- ✅ Review nodes with sentiment analysis
- ✅ User purchase/view/wishlist tracking
- ✅ Product comparison relationships
- ✅ 15+ relationship types
- ✅ 20+ node types
- ✅ Rich properties on every node

---

## Setup (3 Steps)

### 1. Start Neo4j
```bash
cd /Users/julih/Documents/LDR/idss-backend

# Start Neo4j in Docker
docker-compose -f docker-compose.neo4j.yml up -d

# Wait 10 seconds for Neo4j to start
sleep 10
```

### 2. Test Connection
```bash
cd mcp-server
python scripts/test_neo4j_connection.py
```

Expected output:
```
✅ Successfully connected to Neo4j!
✅ Query result: Hello from Neo4j!
📊 Total nodes in database: 0
```

### 3. Build the Knowledge Graph
```bash
# This will populate the graph from your PostgreSQL database
python scripts/build_knowledge_graph.py
```

This will create:
- 50 laptop nodes with full component breakdowns
- 50 book nodes with rich metadata
- 200+ component/entity nodes (CPUs, GPUs, Authors, etc.)
- 150+ review nodes with sentiment
- 500+ relationships

Takes about 1-2 minutes.

---

## Access the Graph

### Neo4j Browser
Open: http://localhost:7474

**Login:**
- Username: `neo4j`
- Password: `password123`

### Try These Queries

#### 1. See All Node Types
```cypher
MATCH (n) 
RETURN labels(n)[0] AS type, count(n) AS count 
ORDER BY count DESC
```

#### 2. Gaming Laptops with RTX GPUs
```cypher
MATCH (l:Laptop)-[:HAS_GPU]->(gpu:GPU)
WHERE gpu.ray_tracing = true 
  AND gpu.vram_gb >= 6
RETURN l.name, l.brand, gpu.model, gpu.vram_gb, l.price
ORDER BY l.price DESC
LIMIT 10
```

#### 3. Books by Genre Hierarchy
```cypher
MATCH (b:Book)-[:BELONGS_TO_GENRE]->(g:Genre)-[:SUBGENRE_OF*0..2]->(parent:Genre)
WHERE parent.name = 'Fiction'
RETURN b.title, g.name AS genre
LIMIT 20
```

#### 4. Product Reviews with Sentiment
```cypher
MATCH (p:Product)<-[:REVIEWS]-(r:Review)
RETURN p.name,
       count(r) AS review_count,
       avg(r.rating) AS avg_rating,
       avg(r.sentiment_score) AS avg_sentiment
ORDER BY avg_rating DESC
LIMIT 10
```

#### 5. Supply Chain Visualization
```cypher
MATCH path = (l:Laptop)-[:MANUFACTURED_BY]->(m:Manufacturer)-[:SOURCES_FROM]->(s:Supplier)
RETURN path
LIMIT 5
```

#### 6. Literary Connections
```cypher
MATCH (b1:Book)-[r:SIMILAR_THEME|INSPIRED_BY]->(b2:Book)
RETURN b1.title, type(r) AS connection, b2.title
LIMIT 10
```

#### 7. Find Similar Products
```cypher
MATCH (p1:Product {name: 'Dell XPS 15'})-[s:SIMILAR_TO]->(p2:Product)
RETURN p2.name, p2.price, s.score AS similarity
ORDER BY s.score DESC
LIMIT 5
```

#### 8. User Purchase Patterns
```cypher
MATCH (u:User)-[:PURCHASED]->(p:Product)
RETURN u.username,
       count(p) AS purchases,
       collect(p.category) AS categories
ORDER BY purchases DESC
LIMIT 10
```

---

## Graph Schema Visualization

In Neo4j Browser, run:
```cypher
CALL db.schema.visualization()
```

You'll see:
- 🔵 Blue nodes: Products (Laptop, Book)
- 🟢 Green nodes: Components (CPU, GPU, RAM, etc.)
- 🟡 Yellow nodes: Entities (Author, Publisher, Manufacturer)
- 🔴 Red nodes: User data (User, Review, Sentiment)
- ➡️ Arrows: 15+ relationship types

---

## Example Use Cases

### 1. Product Recommendation Engine
```cypher
// Find products similar to what user viewed
MATCH (u:User {user_id: 'user_1234'})-[:VIEWED]->(p1:Product)-[:SIMILAR_TO]->(p2:Product)
WHERE NOT (u)-[:VIEWED|PURCHASED]->(p2)
RETURN p2.name, p2.price, count(*) AS recommendation_strength
ORDER BY recommendation_strength DESC
LIMIT 10
```

### 2. Component-Based Search
```cypher
// Find laptops with specific components
MATCH (l:Laptop)-[:HAS_CPU]->(cpu:CPU),
      (l)-[:HAS_RAM]->(ram:RAM),
      (l)-[:HAS_GPU]->(gpu:GPU)
WHERE cpu.cores >= 12
  AND ram.capacity_gb >= 32
  AND gpu.tier IN ['High-end', 'Ultra high-end']
  AND l.price < 3000
RETURN l.name, cpu.model, ram.capacity_gb, gpu.model, l.price
ORDER BY l.price
```

### 3. Author Discovery
```cypher
// Find authors similar to one you like
MATCH (a1:Author {name: 'Andy Weir'})<-[:WRITTEN_BY]-(b1:Book)-[:SIMILAR_THEME]->(b2:Book)-[:WRITTEN_BY]->(a2:Author)
WHERE a1 <> a2
RETURN a2.name, a2.nationality, count(b2) AS similar_books
ORDER BY similar_books DESC
LIMIT 5
```

### 4. Supply Chain Analysis
```cypher
// Analyze manufacturer reliability
MATCH (m:Manufacturer)-[r:SOURCES_FROM]->(s:Supplier)
RETURN m.name,
       count(s) AS supplier_count,
       avg(r.reliability_score) AS avg_reliability,
       avg(r.lead_time_days) AS avg_lead_time
ORDER BY avg_reliability DESC
```

---

## Advanced Features

### Graph Data Science (Already Installed)

#### 1. PageRank (Find Important Products)
```cypher
CALL gds.pageRank.stream('myGraph')
YIELD nodeId, score
RETURN gds.util.asNode(nodeId).name AS product, score
ORDER BY score DESC
LIMIT 10
```

#### 2. Community Detection (Product Clusters)
```cypher
CALL gds.louvain.stream('myGraph')
YIELD nodeId, communityId
RETURN communityId, collect(gds.util.asNode(nodeId).name) AS products
ORDER BY size(products) DESC
```

#### 3. Similarity Algorithms
```cypher
CALL gds.nodeSimilarity.stream('myGraph')
YIELD node1, node2, similarity
RETURN gds.util.asNode(node1).name AS product1,
       gds.util.asNode(node2).name AS product2,
       similarity
ORDER BY similarity DESC
LIMIT 20
```

---

## Environment Variables

Create `.env` file:
```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password123
```

---

## Troubleshooting

### Neo4j Not Starting
```bash
# Check if running
docker ps | grep neo4j

# View logs
docker logs idss-neo4j

# Restart
docker-compose -f docker-compose.neo4j.yml restart
```

### Connection Refused
```bash
# Wait for Neo4j to fully start (takes ~10 seconds)
sleep 10

# Test connection
python scripts/test_neo4j_connection.py
```

### Out of Memory
Edit `docker-compose.neo4j.yml`:
```yaml
environment:
  - NEO4J_dbms_memory_heap_max__size=4G  # Increase from 2G
  - NEO4J_dbms_memory_pagecache_size=2G  # Increase from 1G
```

---

## Documentation

- **Full Documentation**: `mcp-server/NEO4J_KNOWLEDGE_GRAPH.md`
- **20+ Sample Queries**: See documentation for all query examples
- **Graph Schema**: All node types and relationships documented

---

## What Makes This Complex?

### Traditional Graph (Simple):
```
Product → Category
Product → Brand
```

### Our Graph (Complex):
```
Laptop → CPU → Manufacturer → Supplier → Factory
       → GPU → Performance Tier
       → RAM → Speed/Type/Channels
       → Storage → Interface/Speeds
       → Display → Panel/Resolution/Refresh Rate
       → Software (with compatibility scores)
       → Reviews → Sentiment → User
       → Similar_To → Other Laptops (with scores)
       
Book → Author → Nationality/Awards
     → Publisher → Country/Founded
     → Genre → Subgenre_Of → Parent Genre
     → Theme (multiple)
     → Series (with position)
     → Inspired_By → Other Books
     → Reviews → Sentiment → User
```

**Result:** Instead of 2 relationships, we have **500+ relationships** of **15+ different types**!

---

## Next Steps

1. ✅ Graph is built
2. 🔍 Explore in Neo4j Browser
3. 🧪 Try sample queries
4. 🚀 Integrate with your API (use `app/neo4j_config.py` and `app/knowledge_graph.py`)
5. 📊 Add Graph Data Science algorithms
6. 🔄 Set up real-time sync from PostgreSQL

Enjoy your complex knowledge graph! 🎉
