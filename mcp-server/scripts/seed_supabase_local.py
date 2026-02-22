#!/usr/bin/env python3
"""
Seed a local PostgreSQL database with the Supabase-compatible schema.

This script creates the 'products' table matching the live Supabase schema
and inserts sample products for local development. It does NOT modify the
live Supabase database unless DATABASE_URL points to it.

Usage:
    # 1. Seed local PostgreSQL (reads DATABASE_URL from .env)
    python scripts/seed_supabase_local.py

    # 2. Then build Neo4j knowledge graph
    python scripts/build_knowledge_graph_all.py

Environment:
    DATABASE_URL  - PostgreSQL connection string (from .env)
    NEO4J_URI     - bolt://localhost:7688 (Docker) or bolt://localhost:7687

Schema differences from old local DB:
    OLD (local SQLite/PG)             NEW (Supabase)
         
    product_id VARCHAR(50)            id UUID (auto-generated)
    name VARCHAR(500)                 title TEXT
    description TEXT                  attributes->>'description'
    price_cents (prices table)        price NUMERIC (dollars, on products)
    image_url TEXT                    imageurl TEXT (no underscore)
    subcategory VARCHAR(100)          (removed - use product_type or attributes)
    color VARCHAR(80)                 attributes->>'color'
    gpu_vendor VARCHAR(50)            attributes->>'gpu_vendor'
    gpu_model VARCHAR(100)            attributes->>'gpu_model'
    tags ARRAY                        attributes->>'tags'
    kg_features JSON                  attributes (merged into attributes)
     Removed tables 
    prices                            (price on products table)
    inventory                         (inventory column on products)
    carts / cart_items                (handled by Supabase/frontend)
    orders                            (handled by UCP checkout)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

from sqlalchemy import create_engine, text
from app.database import DATABASE_URL


def main():
    print("=" * 70)
    print("SEEDING SUPABASE-COMPATIBLE LOCAL DATABASE")
    print("=" * 70)

    if not DATABASE_URL:
        print("[ERROR] DATABASE_URL not set. Add it to .env")
        print("  Example: DATABASE_URL=postgresql://user:pass@localhost:5432/idss")
        sys.exit(1)

    # Safety check: warn if pointing at live Supabase
    if "supabase" in DATABASE_URL.lower() or "pooler" in DATABASE_URL.lower():
        print(f"\n[WARNING] DATABASE_URL points to Supabase cloud!")
        print(f"  URL: {DATABASE_URL[:50]}...")
        resp = input("  This will INSERT into your live Supabase DB. Continue? (y/N): ")
        if resp.lower() != "y":
            print("  Aborted.")
            sys.exit(0)

    engine = create_engine(DATABASE_URL)

    # Read and execute the SQL seed file
    seed_sql_path = Path(__file__).parent / "seed_supabase.sql"
    if not seed_sql_path.exists():
        print(f"[ERROR] Seed SQL not found: {seed_sql_path}")
        sys.exit(1)

    print(f"\n1. Reading seed SQL: {seed_sql_path.name}")
    sql_content = seed_sql_path.read_text()

    print("2. Executing seed SQL...")
    with engine.begin() as conn:
        # Split on semicolons and execute each statement
        statements = [s.strip() for s in sql_content.split(";") if s.strip() and not s.strip().startswith("--")]
        for i, stmt in enumerate(statements):
            try:
                conn.execute(text(stmt))
            except Exception as e:
                # Skip non-critical errors (e.g., extension already exists)
                if "already exists" in str(e).lower():
                    continue
                print(f"   [WARN] Statement {i+1}: {e}")

    # Verify
    print("\n3. Verifying...")
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT category, product_type, count(*) as cnt "
            "FROM products GROUP BY category, product_type ORDER BY 1, 2"
        ))
        print(f"\n   {'Category':<15} {'Type':<15} {'Count':>5}")
        print(f"   {''*15} {''*15} {''*5}")
        total = 0
        for row in result:
            print(f"   {row[0]:<15} {row[1]:<15} {row[2]:>5}")
            total += row[2]
        print(f"\n   Total products: {total}")

        # Show price range
        result = conn.execute(text(
            "SELECT min(price), max(price), avg(price)::numeric(10,2) FROM products"
        ))
        row = result.fetchone()
        print(f"   Price range: ${row[0]} - ${row[1]} (avg: ${row[2]})")

    print("\n" + "=" * 70)
    print("SEED COMPLETE!")
    print("=" * 70)
    print("\nNext steps:")
    print("  1. Start the MCP server:  python -m uvicorn app.main:app --port 8001")
    print("  2. Build Neo4j KG:        python scripts/build_knowledge_graph_all.py")
    print("  3. Start IDSS server:     python -m uvicorn idss.api.server:app --port 8000")
    print("  4. Start frontend:        cd ../idss-web && npm run dev")


if __name__ == "__main__":
    main()
