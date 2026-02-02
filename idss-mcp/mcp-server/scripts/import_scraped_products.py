"""
Import scraped electronics products into PostgreSQL database.

Reads JSON files from scrape_electronics.py and inserts into MCP database.
"""

import json
import sys
import logging
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def get_db_url():
    """Get database URL from environment."""
    return os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/mcp_ecommerce")
from app.models import Product, Price, Inventory, Base

logger = logging.getLogger(__name__)


def load_scraped_products(json_file: str) -> List[Dict[str, Any]]:
    """Load scraped products from JSON file."""
    with open(json_file, 'r') as f:
        return json.load(f)


def import_products_to_db(products: List[Dict[str, Any]], db_session):
    """
    Import scraped products into PostgreSQL database.
    
    Creates Product, Price, and Inventory records.
    """
    imported = 0
    skipped = 0
    errors = 0
    
    for product_data in products:
        try:
            # Generate product_id from name and source
            product_name = product_data.get('name', 'unknown')
            source = product_data.get('source', 'unknown')
            # Create unique ID: source + hash of name
            import hashlib
            name_hash = hashlib.md5(product_name.encode()).hexdigest()[:8]
            product_id = f"prod-{source}-{name_hash}"
            
            # Check if product already exists
            existing = db_session.query(Product).filter(Product.product_id == product_id).first()
            if existing:
                logger.debug(f"Product {product_id} already exists, skipping")
                skipped += 1
                continue
            
            # Create Product record
            # Note: metadata is stored as JSONB in PostgreSQL
            import json as json_lib
            metadata_dict = {
                'subcategory': product_data.get('subcategory', ''),
                'source': source,
                'scraped_at': product_data.get('scraped_at', datetime.utcnow().isoformat()),
            }
            if product_data.get('image_url'):
                metadata_dict['image_url'] = product_data.get('image_url')
            if product_data.get('product_url'):
                metadata_dict['product_url'] = product_data.get('product_url')
            if product_data.get('rating'):
                metadata_dict['rating'] = product_data.get('rating')
            
            product = Product(
                product_id=product_id,
                name=product_data.get('name', 'Unknown Product'),
                description=product_data.get('description', ''),
                category=product_data.get('category', 'Electronics'),
                brand=product_data.get('brand', 'Unknown'),
                metadata=metadata_dict  # JSONB field
            )
            
            db_session.add(product)
            
            # Create Price record
            price_cents = product_data.get('price_cents', 0)
            if price_cents > 0:
                price = Price(
                    product_id=product_id,
                    price_cents=price_cents,
                    currency=product_data.get('currency', 'USD')
                )
                db_session.add(price)
            
            # Create Inventory record (default to 10 for scraped products)
            inventory = Inventory(
                product_id=product_id,
                available_qty=10,  # Default inventory for scraped products
                reserved_qty=0
            )
            db_session.add(inventory)
            
            db_session.commit()
            imported += 1
            
            if imported % 10 == 0:
                logger.info(f"Imported {imported} products...")
                
        except IntegrityError as e:
            db_session.rollback()
            logger.warning(f"Integrity error for product {product_data.get('name')}: {e}")
            skipped += 1
        except Exception as e:
            db_session.rollback()
            logger.error(f"Error importing product {product_data.get('name')}: {e}")
            errors += 1
    
    logger.info(f"\nImport complete:")
    logger.info(f"  Imported: {imported}")
    logger.info(f"  Skipped: {skipped}")
    logger.info(f"  Errors: {errors}")
    
    return imported, skipped, errors


def main():
    """Main import function."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    if len(sys.argv) < 2:
        print("Usage: python import_scraped_products.py <scraped_products.json>")
        print("\nExample:")
        print("  python import_scraped_products.py scraped_electronics_20260115_120000.json")
        sys.exit(1)
    
    json_file = sys.argv[1]
    
    if not Path(json_file).exists():
        logger.error(f"File not found: {json_file}")
        sys.exit(1)
    
    # Load scraped products
    logger.info(f"Loading products from {json_file}...")
    products = load_scraped_products(json_file)
    logger.info(f"Loaded {len(products)} products")
    
    # Connect to database
    db_url = get_db_url()
    engine = create_engine(db_url)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        # Import products
        logger.info("Importing products to database...")
        imported, skipped, errors = import_products_to_db(products, db)
        
        logger.info(f"\n[OK] Successfully imported {imported} products!")
        if skipped > 0:
            logger.info(f"[WARN] Skipped {skipped} products (already exist)")
        if errors > 0:
            logger.warning(f"[FAIL] {errors} products failed to import")
            
    finally:
        db.close()


if __name__ == "__main__":
    main()
