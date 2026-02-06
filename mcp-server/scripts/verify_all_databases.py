#!/usr/bin/env python3
"""
Verify All Databases Status

Quick verification script to check the status of all three databases.
Run: python scripts/verify_all_databases.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product


def verify_postgresql():
    """Verify PostgreSQL."""
    print("="*80)
    print("1. POSTGRESQL DATABASE")
    print("="*80)
    
    try:
        db = SessionLocal()
        total = db.query(Product).count()
        electronics = db.query(Product).filter(Product.category == "Electronics").count()
        books = db.query(Product).filter(Product.category == "Books").count()
        
        print(f"\n Status: RUNNING")
        print(f"   Total Products: {total:,}")
        print(f"   Electronics: {electronics}")
        print(f"   Books: {books}")
        print(f"   Other: {total - electronics - books}")
        
        db.close()
        return True
    except Exception as e:
        print(f"\n[FAIL] Status: FAILED")
        print(f"   Error: {e}")
        return False


def verify_redis():
    """Verify Redis."""
    print("\n" + "="*80)
    print("2. REDIS CACHE")
    print("="*80)
    
    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        r.ping()
        
        # Get cache stats
        cached_products = len(r.keys("mcp:product:*"))
        categories = len(r.keys("mcp:category:*"))
        brands = len(r.keys("mcp:brand:*"))
        
        # Get metadata
        import json
        metadata_raw = r.get("mcp:cache:metadata")
        if metadata_raw:
            metadata = json.loads(metadata_raw)
            total_cached = metadata.get('total_products', 0)
        else:
            total_cached = cached_products
        
        print(f"\n Status: RUNNING")
        print(f"   Cached Products: {total_cached:,}")
        print(f"   Category Indexes: {categories}")
        print(f"   Brand Indexes: {brands}")
        
        return True
    except ImportError:
        print(f"\n[WARN]  Status: NOT INSTALLED")
        print(f"   Install: pip install redis")
        return False
    except Exception as e:
        print(f"\n[FAIL] Status: NOT RUNNING")
        print(f"   Error: {e}")
        print(f"   Start Redis: redis-server")
        return False


def verify_neo4j():
    """Verify Neo4j."""
    print("\n" + "="*80)
    print("3. NEO4J KNOWLEDGE GRAPH")
    print("="*80)
    
    try:
        from app.neo4j_config import Neo4jConnection
        
        conn = Neo4jConnection()
        if conn.verify_connectivity():
            # Count nodes
            with conn.driver.session(database=conn.database) as session:
                result = session.run("MATCH (n) RETURN count(n) as count")
                total_nodes = result.single()["count"]
                
                # Count relationships
                result = session.run("MATCH ()-[r]->() RETURN count(r) as count")
                total_rels = result.single()["count"]
            
            print(f"\n Status: RUNNING")
            print(f"   Total Nodes: {total_nodes:,}")
            print(f"   Total Relationships: {total_rels:,}")
            
            conn.close()
            return True
        else:
            print(f"\n[FAIL] Status: CONNECTION FAILED")
            print(f"   Check credentials in .env file")
            return False
            
    except ImportError:
        print(f"\n[WARN]  Status: NOT INSTALLED")
        print(f"   Install: pip install neo4j")
        return False
    except Exception as e:
        print(f"\n[FAIL] Status: NOT RUNNING")
        print(f"   Error: {e}")
        print(f"\n   To set up Neo4j:")
        print(f"   1. Download from: https://neo4j.com/download/")
        print(f"   2. Or use Docker: docker run -p 7474:7474 -p 7687:7687 neo4j")
        print(f"   3. Configure .env with NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD")
        return False


def main():
    """Main verification."""
    print("="*80)
    print("DATABASE STATUS VERIFICATION")
    print("="*80)
    
    results = {
        'PostgreSQL': verify_postgresql(),
        'Redis': verify_redis(),
        'Neo4j': verify_neo4j()
    }
    
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    for db_name, status in results.items():
        status_icon = "" if status else "[FAIL]"
        print(f"{status_icon} {db_name:<15} {'OPERATIONAL' if status else 'NEEDS ATTENTION'}")
    
    operational_count = sum(results.values())
    
    print(f"\n{operational_count}/3 databases operational")
    
    if operational_count == 3:
        print("\n ALL DATABASES ARE OPERATIONAL!")
    elif operational_count >= 2:
        print("\n[WARN]  Core databases operational, some need configuration")
    else:
        print("\n[FAIL] Multiple databases need attention")
    
    print("="*80)


if __name__ == "__main__":
    main()
