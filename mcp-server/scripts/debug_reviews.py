#!/usr/bin/env python3
"""Debug review issues."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product
import json


def main():
    db = SessionLocal()
    
    # Get sample of 20 products with reviews
    products = db.query(Product).filter(Product.reviews.isnot(None)).limit(20).all()
    
    print(f"Checking {len(products)} products with reviews:\n")
    
    total_reviews = 0
    valid_ratings = 0
    invalid_examples = []
    
    for i, p in enumerate(products, 1):
        try:
            reviews = json.loads(p.reviews)
            total_reviews += len(reviews)
            
            print(f"{i}. {p.name[:50]}")
            print(f"   Reviews: {len(reviews)}")
            
            for j, r in enumerate(reviews, 1):
                rating = r.get('rating', 'MISSING')
                
                if isinstance(rating, (int, float)) and 1 <= rating <= 5:
                    valid_ratings += 1
                    print(f"      Review {j}: rating={rating}")
                else:
                    print(f"     [FAIL] Review {j}: rating={rating} (INVALID)")
                    if len(invalid_examples) < 5:
                        invalid_examples.append({
                            'product': p.name,
                            'review': r
                        })
            print()
        except Exception as e:
            print(f"   ERROR parsing: {e}\n")
    
    print("="*80)
    print(f"SUMMARY:")
    print(f"  Total reviews: {total_reviews}")
    print(f"  Valid ratings: {valid_ratings}")
    print(f"  Invalid ratings: {total_reviews - valid_ratings}")
    print(f"  Expected: At least 40 valid ratings")
    print("="*80)
    
    if invalid_examples:
        print("\nInvalid Examples:")
        for ex in invalid_examples:
            print(f"\nProduct: {ex['product']}")
            print(f"Review: {ex['review']}")
    
    db.close()


if __name__ == "__main__":
    main()
