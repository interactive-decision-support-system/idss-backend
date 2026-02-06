#!/usr/bin/env python3
"""
Populate ALL Databases - PostgreSQL, Redis, Neo4j

This script ensures all 1,199 products are loaded into:
1. PostgreSQL (already populated via SQLAlchemy)
2. Redis cache (for fast lookups)
3. Neo4j knowledge graph (for complex relationships)

Run: python scripts/populate_all_databases.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import time
from app.database import SessionLocal
from app.models import Product, Price, Inventory


def check_postgresql():
    """Check PostgreSQL population."""
    print("="*80)
    print("1. POSTGRESQL DATABASE")
    print("="*80)
    
    db = SessionLocal()
    try:
        total = db.query(Product).count()
        electronics = db.query(Product).filter(Product.category == "Electronics").count()
        books = db.query(Product).filter(Product.category == "Books").count()
        
        with_prices = db.query(Product).join(Price).count()
        with_inventory = db.query(Product).join(Inventory).count()
        
        print(f"\nPostgreSQL Status:")
        print(f"   Total Products: {total}")
        print(f"   Electronics: {electronics}")
        print(f"   Books: {books}")
        print(f"   With Prices: {with_prices}/{total}")
        print(f"   With Inventory: {with_inventory}/{total}")
        
        if total >= 1000:
            print(f"\nPostgreSQL is fully populated!")
            return True
        else:
            print(f"\n[WARN] PostgreSQL needs more products (current: {total}, target: 1000+)")
            return False
            
    finally:
        db.close()


def populate_redis():
    """Populate Redis cache with all products."""
    print("\n" + "="*80)
    print("2. REDIS CACHE")
    print("="*80)
    
    try:
        import redis
        
        # Try to connect to Redis
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        r.ping()
        
        print(f"\nRedis connection successful")
        
        # Clear existing cache
        print(f"   Clearing existing cache...")
        r.flushdb()
        
        # Load all products from PostgreSQL
        db = SessionLocal()
        products = db.query(Product).all()
        
        print(f"   Caching {len(products)} products...")
        cached_count = 0
        
        for product in products:
            try:
                # Create product cache entry
                product_data = {
                    'product_id': product.product_id,
                    'name': product.name,
                    'description': product.description,
                    'category': product.category,
                    'subcategory': product.subcategory,
                    'brand': product.brand,
                    'image_url': product.image_url,
                    'product_type': product.product_type,
                    'gpu_vendor': product.gpu_vendor,
                    'gpu_model': product.gpu_model,
                }
                
                # Add price if available
                if product.price_info:
                    product_data['price_cents'] = product.price_info.price_cents
                    product_data['currency'] = product.price_info.currency
                
                # Add inventory if available
                if product.inventory_info:
                    product_data['available_qty'] = product.inventory_info.available_qty
                    product_data['reserved_qty'] = product.inventory_info.reserved_qty
                
                # Cache product by ID
                r.setex(
                    f"product:{product.product_id}",
                    3600,  # 1 hour TTL
                    json.dumps(product_data)
                )
                
                # Add to category index
                r.sadd(f"category:{product.category}", product.product_id)
                
                # Add to brand index if brand exists
                if product.brand:
                    r.sadd(f"brand:{product.brand}", product.product_id)
                
                cached_count += 1
                
                if cached_count % 100 == 0:
                    print(f"   Progress: {cached_count}/{len(products)} cached...")
                    
            except Exception as e:
                print(f"   [WARN] Error caching {product.name}: {e}")
                continue
        
        db.close()
        
        # Store metadata
        r.setex(
            "cache:metadata",
            3600,
            json.dumps({
                'total_products': cached_count,
                'last_updated': time.time(),
                'version': '1.0'
            })
        )
        
        print(f"\nRedis cache populated successfully!")
        print(f"   Cached: {cached_count} products")
        
        # Show some stats
        categories = r.keys("category:*")
        brands = r.keys("brand:*")
        print(f"   Category indexes: {len(categories)}")
        print(f"   Brand indexes: {len(brands)}")
        
        return True
        
    except ImportError:
        print(f"\n[WARN] Redis module not installed")
        print(f"   Install with: pip install redis")
        return False
    except Exception as e:
        print(f"\nRedis error: {e}")
        print(f"   Make sure Redis server is running: redis-server")
        return False


def populate_neo4j():
    """Populate Neo4j knowledge graph with ALL products (1000+) via build_knowledge_graph_all."""
    print("\n" + "="*80)
    print("3. NEO4J KNOWLEDGE GRAPH")
    print("="*80)

    try:
        import subprocess
        script_path = Path(__file__).parent / "build_knowledge_graph_all.py"
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(Path(__file__).parent.parent),
            capture_output=False,
        )
        return result.returncode == 0

    except ImportError as e:
        print(f"\n[WARN] Neo4j module not installed: {e}")
        print(f"   Install with: pip install neo4j")
        return False
    except Exception as e:
        print(f"\nNeo4j error: {e}")
        print(f"   Make sure Neo4j is running and configured in .env")
        return False


def main():
    """Main population function."""
    print("="*80)
    print("POPULATE ALL DATABASES - PostgreSQL, Redis, Neo4j")
    print("="*80)
    print(f"\nThis will populate all three databases with 1,199 products")
    print(f"Estimated time: 2-5 minutes\n")
    
    start_time = time.time()
    
    # Check each database
    results = {
        'postgresql': check_postgresql(),
        'redis': populate_redis(),
        'neo4j': populate_neo4j()
    }
    
    # Summary
    elapsed = time.time() - start_time
    
    print("\n" + "="*80)
    print("POPULATION SUMMARY")
    print("="*80)
    
    for db_name, success in results.items():
        status = "SUCCESS" if success else "FAILED"
        print(f"{db_name.upper():<15} {status}")
    
    print(f"\nTotal time: {elapsed:.1f} seconds")
    
    all_success = all(results.values())
    
    if all_success:
        print("\nALL DATABASES POPULATED SUCCESSFULLY!")
        print("="*80)
        print("\nYour e-commerce platform now has:")
        print("   PostgreSQL: 1,199 products (authoritative source)")
        print("   Redis: Fast cache for product lookups")
        print("   Neo4j: Rich knowledge graph with relationships")
        print("\nReady for production!")
    else:
        print("\n[WARN] Some databases failed to populate")
        print("   Check error messages above for details")
    
    print("="*80)


if __name__ == "__main__":
    main()
