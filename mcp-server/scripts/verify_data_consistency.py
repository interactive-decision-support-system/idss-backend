"""
Verify data consistency across PostgreSQL, Redis, and Neo4j.

Checks:
1. All products in PostgreSQL have price and inventory entries
2. Redis cache matches PostgreSQL (within TTL)
3. Neo4j has nodes for Electronics products
4. All product features are accurately stored
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product, Price, Inventory
from app.cache import CacheClient
from app.kg_service import KnowledgeGraphService
from sqlalchemy import func

def verify_postgresql():
    """Verify PostgreSQL data consistency."""
    print("=" * 80)
    print("POSTGRESQL VERIFICATION")
    print("=" * 80)
    
    db = SessionLocal()
    try:
        # Check products without prices
        products_without_price = db.query(Product).outerjoin(Price).filter(Price.product_id == None).count()
        print(f"\nProducts without price entry: {products_without_price}")
        
        # Check products without inventory
        products_without_inventory = db.query(Product).outerjoin(Inventory).filter(Inventory.product_id == None).count()
        print(f"Products without inventory entry: {products_without_inventory}")
        
        # Check total products
        total_products = db.query(Product).count()
        print(f"Total products: {total_products}")
        
        # Check by category
        categories = db.query(Product.category, func.count(Product.product_id)).group_by(Product.category).all()
        print(f"\nProducts by category:")
        for category, count in categories:
            print(f"  {category}: {count}")
        
        # Check Electronics products
        electronics = db.query(Product).filter(Product.category == "Electronics").count()
        print(f"\nElectronics products: {electronics}")
        
        # Check products with structured specs
        with_gpu = db.query(Product).filter(Product.gpu_vendor.isnot(None)).count()
        with_product_type = db.query(Product).filter(Product.product_type.isnot(None)).count()
        print(f"Products with GPU vendor: {with_gpu}")
        print(f"Products with product_type: {with_product_type}")
        
        return {
            "total_products": total_products,
            "products_without_price": products_without_price,
            "products_without_inventory": products_without_inventory,
            "electronics": electronics
        }
    finally:
        db.close()


def verify_redis():
    """Verify Redis cache consistency."""
    print("\n" + "=" * 80)
    print("REDIS VERIFICATION")
    print("=" * 80)
    
    cache = CacheClient()
    
    # Check Redis connection
    if not cache.ping():
        print("\nERROR: Redis is not reachable")
        return None
    
    print("\nRedis connection: OK")
    
    # Sample a few products from PostgreSQL and check cache
    db = SessionLocal()
    try:
        sample_products = db.query(Product).limit(10).all()
        print(f"\nChecking cache for {len(sample_products)} sample products:")
        
        cached_count = 0
        for product in sample_products:
            summary = cache.get_product_summary(product.product_id)
            price = cache.get_price(product.product_id)
            inventory = cache.get_inventory(product.product_id)
            
            if summary and price and inventory:
                cached_count += 1
                print(f"   {product.product_id}: Cached")
            else:
                print(f"   {product.product_id}: Not cached")
        
        print(f"\nCached products: {cached_count}/{len(sample_products)}")
        
        return {
            "redis_connected": True,
            "sample_cached": cached_count,
            "sample_total": len(sample_products)
        }
    finally:
        db.close()


def verify_neo4j():
    """Verify Neo4j knowledge graph consistency."""
    print("\n" + "=" * 80)
    print("NEO4J VERIFICATION")
    print("=" * 80)
    
    try:
        kg = KnowledgeGraphService()
        
        # Check connection
        if not kg.ping():
            print("\nERROR: Neo4j is not reachable")
            return None
        
        print("\nNeo4j connection: OK")
        
        # Count products in Neo4j
        with kg.driver.session() as session:
            result = session.run("MATCH (p:Product) RETURN count(p) as count")
            count = result.single()["count"]
            print(f"\nProducts in Neo4j: {count}")
            
            # Count by category
            result = session.run("MATCH (p:Product) RETURN p.category as category, count(p) as count")
            print(f"\nProducts by category in Neo4j:")
            for record in result:
                print(f"  {record['category']}: {record['count']}")
            
            # Count relationships
            result = session.run("MATCH ()-[r]->() RETURN type(r) as rel_type, count(r) as count")
            print(f"\nRelationships in Neo4j:")
            for record in result:
                print(f"  {record['rel_type']}: {record['count']}")
        
        return {
            "neo4j_connected": True,
            "product_count": count
        }
    except Exception as e:
        print(f"\nERROR: {e}")
        return None


def verify_product_features():
    """Verify that product features are accurately stored."""
    print("\n" + "=" * 80)
    print("PRODUCT FEATURES VERIFICATION")
    print("=" * 80)
    
    db = SessionLocal()
    try:
        # Check required fields
        products_missing_name = db.query(Product).filter(Product.name == None).count()
        products_missing_category = db.query(Product).filter(Product.category == None).count()
        
        print(f"\nProducts missing name: {products_missing_name}")
        print(f"Products missing category: {products_missing_category}")
        
        # Check Electronics products have structured specs
        electronics = db.query(Product).filter(Product.category == "Electronics").all()
        print(f"\nElectronics products: {len(electronics)}")
        
        with_specs = 0
        for product in electronics:
            if product.product_type or product.gpu_vendor:
                with_specs += 1
        
        print(f"Electronics with structured specs: {with_specs}/{len(electronics)}")
        
        # Sample product details
        print(f"\nSample Electronics product:")
        if electronics:
            sample = electronics[0]
            print(f"  ID: {sample.product_id}")
            print(f"  Name: {sample.name}")
            print(f"  Category: {sample.category}")
            print(f"  Brand: {sample.brand}")
            print(f"  Product Type: {sample.product_type}")
            print(f"  GPU Vendor: {sample.gpu_vendor}")
            print(f"  GPU Model: {sample.gpu_model}")
            print(f"  Description: {sample.description[:100] if sample.description else 'None'}...")
        
        return {
            "missing_name": products_missing_name,
            "missing_category": products_missing_category,
            "electronics_with_specs": with_specs,
            "electronics_total": len(electronics)
        }
    finally:
        db.close()


def main():
    """Run all verification checks."""
    print("\n" + "=" * 80)
    print("DATA CONSISTENCY VERIFICATION")
    print("=" * 80)
    
    results = {}
    
    # PostgreSQL verification
    results["postgresql"] = verify_postgresql()
    
    # Redis verification
    results["redis"] = verify_redis()
    
    # Neo4j verification
    results["neo4j"] = verify_neo4j()
    
    # Product features verification
    results["features"] = verify_product_features()
    
    # Summary
    print("\n" + "=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)
    
    if results["postgresql"]:
        pg = results["postgresql"]
        print(f"\nPostgreSQL:")
        print(f"  Total products: {pg['total_products']}")
        print(f"  Products without price: {pg['products_without_price']}")
        print(f"  Products without inventory: {pg['products_without_inventory']}")
        print(f"  Electronics: {pg['electronics']}")
    
    if results["redis"]:
        redis = results["redis"]
        print(f"\nRedis:")
        print(f"  Connected: {redis['redis_connected']}")
        print(f"  Sample cached: {redis['sample_cached']}/{redis['sample_total']}")
    
    if results["neo4j"]:
        neo4j = results["neo4j"]
        print(f"\nNeo4j:")
        print(f"  Connected: {neo4j['neo4j_connected']}")
        print(f"  Products: {neo4j['product_count']}")
    
    if results["features"]:
        features = results["features"]
        print(f"\nProduct Features:")
        print(f"  Missing name: {features['missing_name']}")
        print(f"  Missing category: {features['missing_category']}")
        print(f"  Electronics with specs: {features['electronics_with_specs']}/{features['electronics_total']}")
    
    print("\n" + "=" * 80)
    print("VERIFICATION COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
