#!/usr/bin/env python3
"""
Add Comprehensive User Reviews to Products

Adds realistic, diverse reviews to products that don't have them.
Ensures every product has 3-5 reviews with varied ratings and comments.

Run: python scripts/add_more_reviews.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import Product
import json
import random
from typing import List, Dict


# Review templates by category
LAPTOP_REVIEW_TEMPLATES = {
    "gaming": [
        {"rating": 5, "comment": "Amazing gaming performance! Runs all my games smoothly.", "author": "ProGamer88"},
        {"rating": 5, "comment": "Great GPU, handles AAA titles at high settings.", "author": "GameMaster"},
        {"rating": 4, "comment": "Good for gaming but fans can get loud.", "author": "CasualGamer"},
        {"rating": 5, "comment": "Best gaming laptop in this price range!", "author": "TechReviewer"},
        {"rating": 4, "comment": "Excellent performance, could use better battery life.", "author": "MobileGamer"},
        {"rating": 5, "comment": "The RTX graphics are incredible. Worth every penny.", "author": "FPSEnthusiast"},
        {"rating": 4, "comment": "Great laptop but runs hot during intensive gaming.", "author": "HeatMonitor"},
        {"rating": 5, "comment": "Smooth 144Hz display makes gaming a joy!", "author": "DisplayNerd"},
    ],
    "work": [
        {"rating": 5, "comment": "Perfect for my remote work setup. Very reliable.", "author": "OfficeWorker"},
        {"rating": 5, "comment": "Great keyboard for typing all day!", "author": "Writer123"},
        {"rating": 4, "comment": "Solid business laptop with good battery life.", "author": "Consultant"},
        {"rating": 5, "comment": "Handles multitasking with ease. Highly recommend.", "author": "ProductivityPro"},
        {"rating": 4, "comment": "Good build quality and professional design.", "author": "CorporateIT"},
        {"rating": 5, "comment": "Perfect for video calls and Excel work.", "author": "DataAnalyst"},
        {"rating": 4, "comment": "Fast, reliable, and great for productivity apps.", "author": "FreelancerJoe"},
    ],
    "school": [
        {"rating": 4, "comment": "Great for students on a budget!", "author": "CollegeKid"},
        {"rating": 5, "comment": "Perfect for online classes and homework.", "author": "Student2024"},
        {"rating": 4, "comment": "Good value laptop for basic tasks.", "author": "ParentBuyer"},
        {"rating": 3, "comment": "Decent for the price, but a bit slow.", "author": "HighSchooler"},
        {"rating": 5, "comment": "Lightweight and easy to carry to class.", "author": "CommutingStudent"},
        {"rating": 4, "comment": "Battery lasts through full day of classes!", "author": "LibraryStudier"},
    ],
    "creative": [
        {"rating": 5, "comment": "Excellent for photo editing! Display is gorgeous.", "author": "Photographer"},
        {"rating": 5, "comment": "Handles video editing like a champ.", "author": "VideoEditor"},
        {"rating": 4, "comment": "Great color accuracy for design work.", "author": "GraphicDesigner"},
        {"rating": 5, "comment": "Perfect for my creative workflow.", "author": "ContentCreator"},
        {"rating": 4, "comment": "Powerful enough for 4K video rendering.", "author": "YouTuber"},
        {"rating": 5, "comment": "Best laptop I've used for Adobe suite.", "author": "AdobeUser"},
    ]
}

BOOK_REVIEW_TEMPLATES = {
    "Fiction": [
        {"rating": 5, "comment": "Couldn't put it down! Brilliant storytelling.", "author": "Bookworm99"},
        {"rating": 4, "comment": "Great characters and engaging plot.", "author": "FictionFan"},
        {"rating": 5, "comment": "One of the best books I've read this year!", "author": "ReadingAddict"},
        {"rating": 3, "comment": "Good but a bit slow in the middle.", "author": "CriticalReader"},
        {"rating": 5, "comment": "Beautiful prose and moving story.", "author": "LiteraryLover"},
    ],
    "Mystery": [
        {"rating": 5, "comment": "Amazing twists! Never saw the ending coming.", "author": "DetectiveFan"},
        {"rating": 5, "comment": "Kept me guessing until the very end!", "author": "MysteryAddict"},
        {"rating": 4, "comment": "Great suspense and well-paced.", "author": "ThrillerReader"},
        {"rating": 5, "comment": "Best mystery novel I've read in years.", "author": "WhodunitLover"},
    ],
    "Sci-Fi": [
        {"rating": 5, "comment": "Mind-blowing concepts! Hard sci-fi at its best.", "author": "SciFiNerd"},
        {"rating": 4, "comment": "Great world-building and technology.", "author": "FutureSeeker"},
        {"rating": 5, "comment": "Thought-provoking and imaginative.", "author": "SpaceEnthusiast"},
        {"rating": 4, "comment": "Complex but rewarding read.", "author": "PhysicsGeek"},
    ],
    "Fantasy": [
        {"rating": 5, "comment": "Epic fantasy at its finest! Love the magic system.", "author": "FantasyFan"},
        {"rating": 5, "comment": "Incredible world-building. Can't wait for the sequel!", "author": "DragonRider"},
        {"rating": 4, "comment": "Great characters and adventure.", "author": "QuestLover"},
        {"rating": 5, "comment": "Best fantasy series since Lord of the Rings.", "author": "TolkienFan"},
    ],
    "Romance": [
        {"rating": 5, "comment": "So sweet! Made me cry happy tears.", "author": "RomanceReader"},
        {"rating": 4, "comment": "Lovely chemistry between characters.", "author": "LoveStoryFan"},
        {"rating": 5, "comment": "Perfect beach read. Light and fun!", "author": "SummerReader"},
    ],
    "Business": [
        {"rating": 5, "comment": "Practical advice that actually works!", "author": "Entrepreneur"},
        {"rating": 4, "comment": "Good insights for business owners.", "author": "StartupFounder"},
        {"rating": 5, "comment": "Must-read for anyone in business.", "author": "MBA_Student"},
    ],
    "Self-Help": [
        {"rating": 5, "comment": "Life-changing book! Highly recommend.", "author": "SelfImprover"},
        {"rating": 4, "comment": "Helpful strategies and actionable tips.", "author": "MotivatedReader"},
        {"rating": 5, "comment": "This book changed my perspective on life.", "author": "Mindfulness101"},
    ],
    "Non-fiction": [
        {"rating": 5, "comment": "Fascinating and well-researched!", "author": "Historian"},
        {"rating": 4, "comment": "Informative and engaging writing.", "author": "FactSeeker"},
        {"rating": 5, "comment": "Learned so much from this book.", "author": "CuriousMind"},
    ]
}


def get_reviews_for_laptop(laptop: Product) -> List[Dict]:
    """Get appropriate reviews for a laptop."""
    subcategory = (laptop.subcategory or "").lower()
    
    # Find matching template
    template_key = "school"  # default
    for key in LAPTOP_REVIEW_TEMPLATES.keys():
        if key in subcategory:
            template_key = key
            break
    
    # Get 3-5 random reviews
    available = LAPTOP_REVIEW_TEMPLATES[template_key]
    num_reviews = random.randint(3, 5)
    return random.sample(available, min(num_reviews, len(available)))


def get_reviews_for_book(book: Product) -> List[Dict]:
    """Get appropriate reviews for a book."""
    subcategory = book.subcategory or "Fiction"
    
    # Find matching template
    template_key = subcategory if subcategory in BOOK_REVIEW_TEMPLATES else "Fiction"
    
    # Get 3-5 random reviews
    available = BOOK_REVIEW_TEMPLATES[template_key]
    num_reviews = random.randint(3, 5)
    return random.sample(available, min(num_reviews, len(available)))


def main():
    """Add reviews to products."""
    print("="*80)
    print("ADDING COMPREHENSIVE USER REVIEWS")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        # Get products without reviews
        all_products = db.query(Product).all()
        
        laptops_updated = 0
        books_updated = 0
        already_had_reviews = 0
        
        for product in all_products:
            # Check if already has reviews
            existing_reviews = None
            if product.reviews:
                try:
                    existing_reviews = json.loads(product.reviews)
                    if existing_reviews and len(existing_reviews) >= 3:
                        already_had_reviews += 1
                        continue  # Skip if already has 3+ reviews
                except:
                    pass
            
            # Generate new reviews
            new_reviews = []
            
            if product.category == "Electronics" and product.product_type in ["laptop", "gaming_laptop"]:
                new_reviews = get_reviews_for_laptop(product)
                laptops_updated += 1
            
            elif product.category == "Books":
                new_reviews = get_reviews_for_book(product)
                books_updated += 1
            
            # Merge with existing reviews
            if existing_reviews:
                new_reviews = existing_reviews + new_reviews
            
            # Update product
            if new_reviews:
                product.reviews = json.dumps(new_reviews)
        
        db.commit()
        
        # Report
        print(f"\n Results:")
        print(f"   Laptops updated: {laptops_updated}")
        print(f"   Books updated: {books_updated}")
        print(f"   Already had reviews: {already_had_reviews}")
        print(f"   Total updated: {laptops_updated + books_updated}")
        
        # Sample some reviews
        print("\n" + "="*80)
        print("SAMPLE REVIEWS")
        print("="*80)
        
        sample_laptop = db.query(Product).filter(
            Product.category == "Electronics",
            Product.reviews.isnot(None)
        ).first()
        
        if sample_laptop:
            reviews = json.loads(sample_laptop.reviews)
            print(f"\n{sample_laptop.name}:")
            for rev in reviews[:2]:
                print(f"   {rev['rating']}/5 - \"{rev['comment']}\" - {rev['author']}")
        
        sample_book = db.query(Product).filter(
            Product.category == "Books",
            Product.reviews.isnot(None)
        ).first()
        
        if sample_book:
            reviews = json.loads(sample_book.reviews)
            print(f"\n{sample_book.name}:")
            for rev in reviews[:2]:
                print(f"   {rev['rating']}/5 - \"{rev['comment']}\" - {rev['author']}")
        
        print("\n" + "="*80)
        print(" REVIEW SYSTEM COMPLETE")
        print("="*80)
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
