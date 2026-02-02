"""
Build Vector Index from Database Products.

This script:
1. Loads all products from PostgreSQL
2. Generates embeddings for each product
3. Builds FAISS index
4. Saves index for fast similarity search
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.database import SessionLocal
from app.models import Product, Price, Inventory
from app.vector_search import UniversalEmbeddingStore
from pathlib import Path


def build_index():
    """Build vector index from all products in database."""
    
    print("Building Vector Index for MCP Products...")
    print("=" * 60)
    
    # Initialize database session
    db = SessionLocal()
    
    try:
        # Get all products
        print("\nLoading products from database...")
        products = db.query(Product).join(Price).join(Inventory).all()
        print(f"   Found {len(products)} products")
        
        if not products:
            print("[WARN] No products found in database. Please seed products first.")
            return
        
        # Convert to dict format
        print("\nConverting products to embedding format...")
        products_dict = []
        for product in products:
            products_dict.append({
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description or "",
                "category": product.category or "",
                "brand": product.brand or "",
                "product_type": "ecommerce",  # Default, could be inferred from product_id prefix
                "metadata": {}
            })
        
        print(f"   Converted {len(products_dict)} products")
        
        # Build index
        print("\nBuilding FAISS index...")
        vector_store = UniversalEmbeddingStore()
        vector_store.build_index(products_dict, save_index=True)
        
        print(f"\n[OK] Vector index built successfully!")
        print(f"   Products indexed: {len(vector_store._product_ids)}")
        print(f"   Index size: {vector_store._index.ntotal if vector_store._index else 0}")
        print(f"   Model: {vector_store.model_name}")
        print(f"   Dimension: {vector_store._encoder.get_sentence_embedding_dimension() if vector_store._encoder else 'N/A'}")
        
        # Test search
        print("\nTesting vector search...")
        test_queries = [
            "laptop",
            "electronics",
            "vehicle",
            "travel"
        ]
        
        for test_query in test_queries:
            product_ids, scores = vector_store.search(test_query, k=3)
            print(f"   Query: '{test_query}'")
            print(f"   Results: {len(product_ids)} products")
            if product_ids:
                print(f"   Top match: {product_ids[0]} (score: {scores[0]:.3f})")
        
        print("\n" + "=" * 60)
        print("[OK] Vector index ready for use!")
        print("\nThe index will be automatically used in MCP search endpoints.")
        
    except Exception as e:
        print(f"\n[FAIL] Error building index: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    build_index()
