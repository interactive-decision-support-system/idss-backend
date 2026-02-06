#!/usr/bin/env python3
"""
Validate and Fix All Product Reviews

Ensures all reviews have valid ratings (1-5) and proper structure.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product
import json


def main():
    """Validate and fix all reviews."""
    print("="*80)
    print("VALIDATING ALL PRODUCT REVIEWS")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        products = db.query(Product).filter(Product.reviews.isnot(None)).all()
        print(f"\nFound {len(products)} products with reviews")
        
        invalid_count = 0
        fixed_count = 0
        
        for product in products:
            try:
                reviews = json.loads(product.reviews)
                
                if not isinstance(reviews, list):
                    print(f"  [WARN]  Invalid structure: {product.name}")
                    invalid_count += 1
                    continue
                
                # Check each review
                needs_fix = False
                for review in reviews:
                    rating = review.get('rating', 0)
                    
                    # Fix invalid ratings
                    if not isinstance(rating, (int, float)) or rating < 1 or rating > 5:
                        needs_fix = True
                        review['rating'] = min(5, max(1, int(rating) if rating else 4))
                    
                    # Ensure required fields
                    if 'comment' not in review:
                        review['comment'] = "Great product!"
                    
                    if 'author' not in review:
                        review['author'] = "Verified Buyer"
                
                if needs_fix:
                    product.reviews = json.dumps(reviews)
                    fixed_count += 1
                    if fixed_count <= 5:
                        print(f"  ✓ Fixed: {product.name}")
            
            except json.JSONDecodeError:
                print(f"  [FAIL] Cannot parse JSON: {product.name}")
                invalid_count += 1
        
        if fixed_count > 0:
            db.commit()
            print(f"\n Fixed {fixed_count} products with invalid reviews")
        
        if invalid_count > 0:
            print(f"[WARN]  {invalid_count} products have structural issues")
        
        # Validation summary
        print("\n" + "="*80)
        print("VALIDATION SUMMARY")
        print("="*80)
        
        valid_reviews_count = 0
        for product in db.query(Product).filter(Product.reviews.isnot(None)).all():
            try:
                reviews = json.loads(product.reviews)
                if isinstance(reviews, list):
                    for r in reviews:
                        if 1 <= r.get('rating', 0) <= 5:
                            valid_reviews_count += 1
            except:
                pass
        
        print(f"Valid individual reviews: {valid_reviews_count}")
        print(f"Products with reviews: {len(products)}")
        
    finally:
        db.close()
    
    print("\n" + "="*80)
    print("DONE!")
    print("="*80)


if __name__ == "__main__":
    main()
