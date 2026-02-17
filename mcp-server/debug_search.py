import asyncio
import sys
import os
import logging
from dotenv import load_dotenv

# Setup environment
load_dotenv()
sys.path.append(os.path.join(os.path.dirname(__file__), "../"))
# Add repo root for agent package
sys.path.append(os.path.join(os.path.dirname(__file__), "../../"))

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from agent.chat_endpoint import _search_ecommerce_products
from app.database import SessionLocal
from app.models import Product

async def debug_search(category: str):
    print(f"\n\n=== DEBUG SEARCH: {category} ===")
    
    # Check DB directly
    db = SessionLocal()
    count = db.query(Product).filter(Product.category == category).count()
    print(f"DB Count for '{category}': {count}")
    db.close()
    
    filters = {}
    
    # Run search
    # This logic mirrors what's in chat_endpoint.py
    # Note: _search_ecommerce_products now applies format_product internally
    try:
        buckets, labels = await _search_ecommerce_products(filters, category, n_rows=5, n_per_row=1)
        
        if not buckets:
            print("No results found!")
            return

        print(f"Found {len(buckets)} buckets.")
        
        for i, bucket in enumerate(buckets):
            for item in bucket:
                print(f"\nItem: {item.get('name')}")
                print(f"  ID: {item.get('id')}")
                print(f"  Brand: {item.get('brand')}")
                print(f"  Image: {item.get('image', {}).get('primary')}")
                
        first_item = buckets[0][0]
        
        import json
        print("--- First Recommendation JSON ---")
        print(f"Description: {first_item.get('description')}")
        print(json.dumps(first_item, indent=2))
        
        # Specific Checks
        p_type = first_item.get("productType")
        print(f"\nProduct Type: {p_type}")
        
        if p_type == "laptop":
            print(f"Laptop Specs: {first_item.get('laptop', {}).get('specs')}")
        elif p_type == "book":
            print(f"Book Author: {first_item.get('book', {}).get('author')}")
            print(f"Book Image: {first_item.get('image')}")

    except Exception as e:
        print(f"Search failed: {e}")
        import traceback
        traceback.print_exc()

async def main():
    # await debug_search("Electronics") # Laptops
    await debug_search("Books")       # Books

if __name__ == "__main__":
    asyncio.run(main())
