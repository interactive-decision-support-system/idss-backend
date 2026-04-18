"""
Build Vector Index from Database Products.

This script:
1. Loads all products from PostgreSQL for a given merchant
2. Generates embeddings for each product
3. Builds FAISS index
4. Saves index into data/merchants/<merchant_id>/<strategy>/ for fast similarity search

Usage:
    python build_vector_index.py                         # default merchant, normalizer_v1
    python build_vector_index.py --merchant acme         # acme merchant
    python build_vector_index.py --merchant acme --strategy normalizer_v2
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from app.database import SessionLocal
from app.models import make_product_model
from app.vector_search import UniversalEmbeddingStore


def build_index(merchant_id: str, strategy: str):
    """Build vector index from all products in database for a given merchant."""

    print(f"Building Vector Index for merchant={merchant_id} strategy={strategy}...")
    print("=" * 60)

    Product = make_product_model(merchant_id)
    db = SessionLocal()

    try:
        print("\nLoading products from database...")
        products = db.query(Product).all()
        print(f"   Found {len(products)} products")

        if not products:
            print("[WARN] No products found in database. Please seed products first.")
            return

        print("\nConverting products to embedding format...")
        products_dict = []
        for product in products:
            products_dict.append({
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description or "",
                "category": product.category or "",
                "brand": product.brand or "",
                "product_type": "ecommerce",
                "metadata": {}
            })

        print(f"   Converted {len(products_dict)} products")

        print("\nBuilding FAISS index...")
        vector_store = UniversalEmbeddingStore(
            merchant_id=merchant_id,
            strategy=strategy,
        )
        vector_store.build_index(products_dict, save_index=True)

        print("\n[OK] Vector index built successfully!")
        print(f"   Merchant: {merchant_id}")
        print(f"   Strategy: {strategy}")
        print(f"   Products indexed: {len(vector_store._product_ids)}")
        print(f"   Index size: {vector_store._index.ntotal if vector_store._index else 0}")
        print(f"   Index path: {vector_store.index_path}")
        print(f"   Model: {vector_store.model_name}")

        print("\nTesting vector search...")
        for test_query in ["laptop", "electronics", "vehicle", "travel"]:
            product_ids, scores = vector_store.search(test_query, k=3)
            print(f"   Query: '{test_query}'")
            print(f"   Results: {len(product_ids)} products")
            if product_ids:
                print(f"   Top match: {product_ids[0]} (score: {scores[0]:.3f})")

        print("\n" + "=" * 60)
        print("[OK] Vector index ready for use!")

    except Exception as e:
        print(f"\n[FAIL] Error building index: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build per-merchant vector index")
    parser.add_argument("--merchant", default="default", help="Merchant slug")
    parser.add_argument("--strategy", default="normalizer_v1", help="Enrichment strategy label")
    args = parser.parse_args()
    build_index(args.merchant, args.strategy)
