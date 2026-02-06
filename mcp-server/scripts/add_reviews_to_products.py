#!/usr/bin/env python3
"""
Add realistic, detailed user reviews to all products in the database.
"""

import sys
import os
import json
import random
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_db
from app.models import Product

# Realistic reviewer names
REVIEWER_NAMES = [
    "TechEnthusiast", "BookWorm2024", "GamerPro", "StudentLife", "WorkFromHome",
    "ReadingAddict", "PCBuilder", "LaptopLover", "CarGuy", "MysteryFan",
    "SciFiReader", "CasualGamer", "PowerUser", "CreativePro", "BudgetBuyer",
    "QualitySeeker", "FirstTimeBuyer", "ReturnCustomer", "TechReviewer", "HonestBuyer",
    "VerifiedPurchaser", "DailyDriver", "WeekendReader", "DigitalNomad", "HomeOffice"
]

# Laptop reviews by use case
LAPTOP_REVIEWS = {
    "gaming": [
        {"rating": 5, "comment": "Runs all AAA games at max settings with no issues. RTX performance is incredible!", "verified": True},
        {"rating": 4, "comment": "Great gaming laptop but gets hot during intensive sessions. Highly recommend a cooling pad.", "verified": True},
        {"rating": 5, "comment": "Best gaming laptop I've owned. The display is stunning and the keyboard feels premium.", "verified": True},
        {"rating": 4, "comment": "Excellent performance for the price. Only downside is battery life while gaming.", "verified": True},
        {"rating": 5, "comment": "Handles Cyberpunk 2077 at ultra settings with ray tracing. Absolutely worth it.", "verified": True},
        {"rating": 3, "comment": "Good laptop but fan noise is quite loud. Performance is solid though.", "verified": False},
        {"rating": 5, "comment": "Frame rates are consistently high. The high refresh rate display is a game changer.", "verified": True},
    ],
    "work": [
        {"rating": 5, "comment": "Perfect for remote work. Battery lasts all day and it's very portable.", "verified": True},
        {"rating": 4, "comment": "Great for productivity. Multitasking is smooth with 16GB RAM.", "verified": True},
        {"rating": 5, "comment": "Handles Excel, PowerPoint, and video calls simultaneously without any lag.", "verified": True},
        {"rating": 4, "comment": "Solid work laptop. Keyboard is comfortable for long typing sessions.", "verified": True},
        {"rating": 5, "comment": "Been using it for 6 months for work - absolutely reliable. No issues whatsoever.", "verified": True},
        {"rating": 4, "comment": "Fast boot time and responsive. Perfect for business travel.", "verified": True},
    ],
    "creative": [
        {"rating": 5, "comment": "Photo and video editing are buttery smooth. Color accuracy is excellent.", "verified": True},
        {"rating": 4, "comment": "Handles Adobe Creative Suite without breaking a sweat. Render times are fast.", "verified": True},
        {"rating": 5, "comment": "Perfect for graphic design work. The display quality is outstanding.", "verified": True},
        {"rating": 5, "comment": "4K video editing in Premiere Pro is seamless. Highly recommend for creatives.", "verified": True},
        {"rating": 4, "comment": "Great for design work but could use more storage for project files.", "verified": True},
    ],
    "school": [
        {"rating": 5, "comment": "Perfect laptop for college. Light, portable, and battery lasts through all my classes.", "verified": True},
        {"rating": 4, "comment": "Great for online learning and assignments. Keyboard is comfortable for long essays.", "verified": True},
        {"rating": 5, "comment": "Handles coding assignments and research papers easily. Best student laptop!", "verified": True},
        {"rating": 4, "comment": "Good value for students. Runs Microsoft Office and browser tabs smoothly.", "verified": True},
        {"rating": 5, "comment": "Survived a full semester with no issues. Durable and reliable.", "verified": True},
    ],
}

# Book reviews by genre
BOOK_REVIEWS = {
    "Sci-Fi": [
        {"rating": 5, "comment": "Mind-blowing concepts and world-building. Couldn't put it down!", "verified": True},
        {"rating": 4, "comment": "Great sci-fi with interesting characters. The ending was a bit rushed.", "verified": True},
        {"rating": 5, "comment": "One of the best sci-fi books I've read. The author's imagination is incredible.", "verified": True},
        {"rating": 4, "comment": "Fascinating exploration of future technology. Highly recommended for sci-fi fans.", "verified": True},
        {"rating": 5, "comment": "Gripping from start to finish. The plot twists kept me guessing.", "verified": True},
    ],
    "Mystery": [
        {"rating": 5, "comment": "Kept me guessing until the very end. Brilliant mystery with great pacing.", "verified": True},
        {"rating": 4, "comment": "Well-crafted whodunit. The detective character is compelling.", "verified": True},
        {"rating": 5, "comment": "Page-turner! I read it in one sitting. The plot twists are unexpected.", "verified": True},
        {"rating": 4, "comment": "Engaging mystery with clever clues. Recommended for mystery lovers.", "verified": True},
        {"rating": 5, "comment": "Masterfully plotted. The atmosphere and tension are perfect.", "verified": True},
    ],
    "Fiction": [
        {"rating": 5, "comment": "Beautiful writing and unforgettable characters. Moved me to tears.", "verified": True},
        {"rating": 4, "comment": "Thought-provoking and emotionally resonant. A must-read.", "verified": True},
        {"rating": 5, "comment": "One of those rare books that stays with you. Highly recommended.", "verified": True},
        {"rating": 4, "comment": "Compelling story with rich character development. Very well written.", "verified": True},
        {"rating": 5, "comment": "Captivating from beginning to end. The author's style is engaging.", "verified": True},
    ],
    "Non-Fiction": [
        {"rating": 5, "comment": "Incredibly informative and well-researched. Changed my perspective.", "verified": True},
        {"rating": 4, "comment": "Learned so much from this book. The author explains complex topics clearly.", "verified": True},
        {"rating": 5, "comment": "Eye-opening and thought-provoking. Everyone should read this.", "verified": True},
        {"rating": 4, "comment": "Fascinating insights backed by solid research. Highly educational.", "verified": True},
        {"rating": 5, "comment": "Accessible yet comprehensive. Perfect balance of depth and readability.", "verified": True},
    ],
    "Self-Help": [
        {"rating": 5, "comment": "Life-changing advice presented in an actionable way. Already seeing results!", "verified": True},
        {"rating": 4, "comment": "Practical tips that actually work. Motivating and insightful.", "verified": True},
        {"rating": 5, "comment": "This book helped me develop better habits. Highly recommend!", "verified": True},
        {"rating": 4, "comment": "Solid self-improvement guide. The exercises are particularly useful.", "verified": True},
        {"rating": 5, "comment": "Transformative read. The strategies are simple but powerful.", "verified": True},
    ],
}

# Generic positive/neutral reviews
GENERIC_REVIEWS = [
    {"rating": 5, "comment": "Excellent product! Exceeded my expectations.", "verified": True},
    {"rating": 4, "comment": "Good quality and great value for money.", "verified": True},
    {"rating": 5, "comment": "Very satisfied with this purchase. Would buy again.", "verified": True},
    {"rating": 4, "comment": "Solid product. Does exactly what it promises.", "verified": True},
    {"rating": 3, "comment": "It's okay. Nothing special but gets the job done.", "verified": False},
    {"rating": 4, "comment": "Happy with my purchase. Good quality for the price.", "verified": True},
    {"rating": 5, "comment": "Highly recommend! Great product.", "verified": True},
]


def generate_reviews_for_product(product: Product, num_reviews: int = None) -> str:
    """Generate realistic reviews for a product based on its type and category."""
    
    if num_reviews is None:
        # Randomize number of reviews (1-7 reviews per product)
        num_reviews = random.randint(1, 7)
    
    reviews = []
    
    # Select review pool based on product type
    if product.category == "Electronics" and product.subcategory:
        use_case = product.subcategory.lower()
        review_pool = LAPTOP_REVIEWS.get(use_case.lower(), GENERIC_REVIEWS)
    elif product.category == "Books" and product.subcategory:
        genre = product.subcategory
        review_pool = BOOK_REVIEWS.get(genre, GENERIC_REVIEWS)
    else:
        review_pool = GENERIC_REVIEWS
    
    # Generate reviews
    for i in range(num_reviews):
        review_template = random.choice(review_pool)
        
        review = {
            "rating": review_template["rating"],
            "comment": review_template["comment"],
            "author": random.choice(REVIEWER_NAMES),
            "verified_purchase": review_template.get("verified", True),
            "helpful_count": random.randint(0, 50),
            "date": (datetime.now() - timedelta(days=random.randint(1, 365))).strftime("%Y-%m-%d")
        }
        
        reviews.append(review)
    
    return json.dumps(reviews, ensure_ascii=False)


def main():
    """Add reviews to all products."""
    db = next(get_db())
    
    # Get all products
    products = db.query(Product).all()
    total = len(products)
    
    print(f"Found {total} products")
    print("Adding realistic user reviews...")
    
    updated = 0
    for i, product in enumerate(products, 1):
        # Generate and add reviews
        reviews_json = generate_reviews_for_product(product)
        product.reviews = reviews_json
        
        updated += 1
        
        if i % 100 == 0:
            print(f"  Processed {i}/{total} products...")
            db.commit()
    
    # Final commit
    db.commit()
    print(f"\n Successfully added reviews to {updated} products!")
    
    # Show sample
    sample = db.query(Product).filter(Product.reviews != None).first()
    if sample:
        reviews = json.loads(sample.reviews)
        print(f"\nSample product: {sample.name}")
        print(f"Number of reviews: {len(reviews)}")
        print(f"Sample review: {reviews[0]}")


if __name__ == "__main__":
    main()
