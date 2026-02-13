# 🗄️ Neo4j Setup Guide

## Quick Start with Docker (Recommended)

### 1. Start Neo4j with Docker Compose
```bash
cd /Users/julih/Documents/LDR/idss-backend
docker-compose -f docker-compose-neo4j.yml up -d
```

### 2. Wait for Neo4j to Start (30 seconds)
```bash
sleep 30
```

### 3. Verify Connection
```bash
# Check Neo4j is running
docker ps | grep neo4j

# Or open browser: http://localhost:7474
# Login: neo4j / neo4jpassword
```

### 4. Populate with All 1,199 Products
```bash
python mcp-server/scripts/populate_all_databases.py
```

---

## Alternative: Use Existing Neo4j (if you fix permissions)

If you want to use the existing local Neo4j install:

### Option A: Fix Permissions
```bash
sudo chown -R $(whoami) /opt/homebrew/var/log/neo4j/
neo4j stop
neo4j-admin dbms set-initial-password yourpassword
neo4j start
```

### Option B: Use Neo4j Desktop
1. Download from: https://neo4j.com/download/
2. Install Neo4j Desktop
3. Create a new database
4. Get connection details (usually bolt://localhost:7687)
5. Update `.env` with your credentials

---

## Connection Settings (Already Updated in .env)

```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4jpassword
```

---

## What Will Be Created

Once populated, your Neo4j knowledge graph will contain:

### Nodes
- **260 Laptop nodes** with:
  - CPU, GPU, RAM, Storage specs
  - Screen size, brand, price
  - Performance tiers
  
- **500 Book nodes** with:
  - Author, genre, publisher
  - ISBN, format, pages
  - Rating information

- **~200 Other Product nodes** (phones, tablets, etc.)

### Relationships
- `:HAS_CPU`, `:HAS_GPU`, `:HAS_RAM`
- `:MANUFACTURED_BY`
- `:WRITTEN_BY`, `:PUBLISHED_BY`
- `:BELONGS_TO_GENRE`
- `:SIMILAR_TO` (product recommendations)
- `:COMPETES_WITH` (competing products)

### Indexes & Constraints
- Unique product IDs
- Indexed names, brands, prices
- Optimized for fast queries

---

## Useful Neo4j Commands

### Check Database Status
```cypher
// Count all nodes
MATCH (n) RETURN count(n)

// Count by type
MATCH (n:Laptop) RETURN count(n)
MATCH (n:Book) RETURN count(n)

// Count relationships
MATCH ()-[r]->() RETURN count(r)
```

### Query Examples
```cypher
// Find gaming laptops with NVIDIA GPUs
MATCH (l:Laptop)-[:HAS_GPU]->(g:GPU)
WHERE g.vendor = 'NVIDIA' AND l.subcategory = 'Gaming'
RETURN l.name, l.price, g.model

// Find books by genre
MATCH (b:Book)-[:BELONGS_TO_GENRE]->(g:Genre)
WHERE g.name = 'Sci-Fi'
RETURN b.title, b.author, b.price

// Product recommendations (similar products)
MATCH (p:Product {name: 'Some Product'})-[:SIMILAR_TO]->(similar)
RETURN similar.name, similar.price
```

---

## Troubleshooting

### Docker not installed?
```bash
# Install Docker Desktop from: https://www.docker.com/products/docker-desktop
```

### Port 7687 already in use?
```bash
# Stop existing Neo4j
neo4j stop

# Or kill the process
lsof -ti:7687 | xargs kill -9
```

### Connection refused?
```bash
# Check if Neo4j container is running
docker logs idss-neo4j

# Restart container
docker-compose -f docker-compose-neo4j.yml restart
```

---

## 🚀 Ready to Populate!

Once Neo4j is running, populate it:
```bash
python mcp-server/scripts/populate_all_databases.py
```

This will:
1. ✅ Verify PostgreSQL (already populated)
2. ✅ Verify Redis (already populated)  
3. ✅ Populate Neo4j with all 1,199 products
4. ✅ Create relationships and indexes
5. ✅ Build knowledge graph structure

**Estimated time:** 2-3 minutes

---

## Browser Access

- **Neo4j Browser:** http://localhost:7474
- **Username:** neo4j
- **Password:** neo4jpassword

Visualize your knowledge graph and run Cypher queries!
