"""
Populate Neo4j Knowledge Graph with Electronics Products.

Per week4notes.txt: Knowledge graph for electronics, real product data.
Creates nodes and relationships for:
- Products (laptops, components)
- Use cases (Gaming, VideoEditing, Work, School)
- Compatibility relationships
- Brand/product line relationships
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from app.kg_service import KnowledgeGraphService
    from app.database import SessionLocal
    from app.models import Product, Price, Inventory
    import logging
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("Make sure you're in the mcp-server directory and dependencies are installed:")
    print("  pip install sqlalchemy psycopg2-binary neo4j")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def populate_kg_from_database(kg_service: KnowledgeGraphService, db):
    """
    Populate knowledge graph from PostgreSQL database.
    
    Creates:
    - Product nodes
    - Use case nodes
    - SUITABLE_FOR relationships
    - COMPATIBLE_WITH relationships (for components)
    """
    if not kg_service.is_available():
        logger.error("Neo4j not available - cannot populate KG")
        return
    
    try:
        with kg_service.driver.session() as session:
            # Clear existing data (for fresh start)
            logger.info("Clearing existing KG data...")
            session.run("MATCH (n) DETACH DELETE n")
            
            # Create Use Case nodes
            logger.info("Creating Use Case nodes...")
            use_cases = ["Gaming", "VideoEditing", "Work", "School", "Creative"]
            for uc in use_cases:
                session.run(
                    "CREATE (uc:UseCase {name: $name})",
                    name=uc
                )
            
            # Get all electronics products from database
            logger.info("Fetching electronics products from database...")
            products = db.query(Product).filter(Product.category == "Electronics").all()
            
            logger.info(f"Found {len(products)} electronics products")
            
            # Create Product nodes and relationships
            for product in products:
                # Create Product node
                session.run("""
                    MERGE (p:Product {product_id: $product_id})
                    SET p.name = $name,
                        p.category = $category,
                        p.brand = $brand,
                        p.price_cents = $price_cents,
                        p.subcategory = $subcategory
                """, {
                    "product_id": product.product_id,
                    "name": product.name,
                    "category": product.category,
                    "brand": product.brand or "Unknown",
                    "price_cents": db.query(Price).filter(Price.product_id == product.product_id).first().price_cents if db.query(Price).filter(Price.product_id == product.product_id).first() else 0,
                    "subcategory": getattr(product, 'subcategory', None) or "Electronics"
                })
                
                # Determine use cases from product name/description
                name_lower = product.name.lower()
                description_lower = (product.description or "").lower()
                text = f"{name_lower} {description_lower}"
                
                # Match use cases
                matched_use_cases = []
                
                if any(kw in text for kw in ["gaming", "gamer", "rtx", "gpu", "graphics"]):
                    matched_use_cases.append("Gaming")
                
                if any(kw in text for kw in ["video editing", "video", "editing", "creative", "adobe", "premiere"]):
                    matched_use_cases.append("VideoEditing")
                
                if any(kw in text for kw in ["work", "business", "professional", "office"]):
                    matched_use_cases.append("Work")
                
                if any(kw in text for kw in ["school", "student", "education", "chromebook"]):
                    matched_use_cases.append("School")
                
                if any(kw in text for kw in ["creative", "design", "art", "photoshop"]):
                    matched_use_cases.append("Creative")
                
                # Default to Work if no matches
                if not matched_use_cases:
                    matched_use_cases = ["Work"]
                
                # Create SUITABLE_FOR relationships
                for uc_name in matched_use_cases:
                    session.run("""
                        MATCH (p:Product {product_id: $product_id})
                        MATCH (uc:UseCase {name: $uc_name})
                        MERGE (p)-[:SUITABLE_FOR]->(uc)
                    """, {
                        "product_id": product.product_id,
                        "uc_name": uc_name
                    })
                
                # Create Brand relationship
                if product.brand:
                    session.run("""
                        MERGE (b:Brand {name: $brand_name})
                        WITH b
                        MATCH (p:Product {product_id: $product_id})
                        MERGE (p)-[:MADE_BY]->(b)
                    """, {
                        "brand_name": product.brand,
                        "product_id": product.product_id
                    })
            
            logger.info("KG population complete!")
            
            # Print statistics
            result = session.run("MATCH (p:Product) RETURN count(p) as count")
            product_count = result.single()["count"]
            
            result = session.run("MATCH (p:Product)-[:SUITABLE_FOR]->(uc:UseCase) RETURN count(*) as count")
            relationship_count = result.single()["count"]
            
            logger.info(f"KG Statistics:")
            logger.info(f"  Products: {product_count}")
            logger.info(f"  Use Case Relationships: {relationship_count}")
            
    except Exception as e:
        logger.error(f"Failed to populate KG: {e}", exc_info=True)


def main():
    """Main function to populate KG."""
    import os
    from dotenv import load_dotenv
    
    # Load environment variables
    env_path = Path(__file__).parent.parent.parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
    
    # Initialize KG service
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD")
    if not neo4j_password:
        logger.error("NEO4J_PASSWORD not set. Set it in .env (do not commit .env to git).")
        return
    kg_service = KnowledgeGraphService(uri=neo4j_uri, user=neo4j_user, password=neo4j_password)
    
    if not kg_service.is_available():
        logger.error("Cannot connect to Neo4j. Please ensure Neo4j is running.")
        return
    
    # Get database session
    db = SessionLocal()
    
    try:
        populate_kg_from_database(kg_service, db)
    finally:
        db.close()
        kg_service.close()


if __name__ == "__main__":
    main()
