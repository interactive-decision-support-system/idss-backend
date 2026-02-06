#!/usr/bin/env python3
"""
Ensure All Products Have Minimum 3 Reviews

Adds additional reviews to products that have fewer than 3 reviews.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product
import json
import random


# Generic review templates
GENERIC_REVIEWS = [
    {"rating": 5, "comment": "Excellent product! Highly recommend.", "author": "HappyCustomer"},
    {"rating": 5, "comment": "Perfect! Exactly what I needed.", "author": "SatisfiedBuyer"},
    {"rating": 4, "comment": "Great quality for the price.", "author": "ValueSeeker"},
    {"rating": 4, "comment": "Very pleased with this purchase.", "author": "RepeatCustomer"},
    {"rating": 5, "comment": "Outstanding! Will buy again.", "author": "LoyalFan"},
    {"rating": 4, "comment": "Good product, fast shipping.", "author": "QuickBuyer"},
    {"rating": 5, "comment": "Love it! Exceeded my expectations.", "author": "Impressed"},
    {"rating": 4, "comment": "Solid choice. No complaints.", "author": "PracticalUser"},
    {"rating": 5, "comment": "Absolutely perfect! Five stars!", "author": "Enthusiast"},
    {"rating": 4, "comment": "Well worth the money.", "author": "BudgetWise"},
    {"rating": 3, "comment": "It's okay. Does the job.", "author": "Neutral123"},
    {"rating": 4, "comment": "Pretty good overall.", "author": "FairReviewer"},
    {"rating": 5, "comment": "Amazing quality! So happy!", "author": "DelightedUser"},
    {"rating": 4, "comment": "Good value. Recommend it.", "author": "VerifiedPurchaser"},
    {"rating": 5, "comment": "Best purchase I've made this year!", "author": "ThrillSeeker"},
]


def main():
    """Add reviews to products that need them."""
    print("="*80)
    print("ENSURING MINIMUM 3 REVIEWS PER PRODUCT")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        # Get all products
        all_products = db.query(Product).all()
        print(f"\nTotal products: {len(all_products)}")
        
        updated_count = 0
        reviews_added = 0
        
        for product in all_products:
            current_reviews = []
            
            # Parse existing reviews
            if product.reviews:
                try:
                    current_reviews = json.loads(product.reviews)
                    if not isinstance(current_reviews, list):
                        current_reviews = []
                except:
                    current_reviews = []
            
            # Add reviews if needed
            if len(current_reviews) < 3:
                needed = 3 - len(current_reviews)
                
                # Add random reviews
                for _ in range(needed):
                    new_review = random.choice(GENERIC_REVIEWS).copy()
                    # Slight variation in ratings
                    if random.random() < 0.3:  # 30% chance to adjust
                        new_review['rating'] = random.randint(3, 5)
                    current_reviews.append(new_review)
                    reviews_added += 1
                
                # Save
                product.reviews = json.dumps(current_reviews)
                updated_count += 1
                
                if updated_count <= 10:
                    print(f"  ✓ {product.name[:50]:<50} {len(current_reviews)} reviews now")
        
        db.commit()
        
        print(f"\n Updated {updated_count} products")
        print(f" Added {reviews_added} new reviews")
        
        # Verify
        print("\nVerification:")
        sample = db.query(Product).filter(Product.reviews.isnot(None)).limit(20).all()
        
        total_sample_reviews = 0
        for p in sample:
            reviews = json.loads(p.reviews)
            total_sample_reviews += len(reviews)
        
        print(f"  Sample of 20 products: {total_sample_reviews} total reviews")
        print(f"  Average: {total_sample_reviews/20:.1f} reviews per product")
        
    finally:
        db.close()
    
    print("\n" + "="*80)
    print("DONE!")
    print("="*80)


if __name__ == "__main__":
    main()
