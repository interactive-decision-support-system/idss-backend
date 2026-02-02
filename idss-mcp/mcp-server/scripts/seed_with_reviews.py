"""
Generate realistic products with fake user reviews in descriptions.
Populates PostgreSQL database with diverse products across all categories.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.database import SessionLocal, engine, Base
from app.models import Product, Price, Inventory
from datetime import datetime
import random

# Fake user review templates
REVIEW_TEMPLATES = {
    "positive": [
        "Absolutely love this product! Exceeded my expectations. {detail}",
        "Best purchase I've made this year. {detail} Highly recommend!",
        "Outstanding quality and performance. {detail} Worth every penny.",
        "Five stars! {detail} This product is fantastic.",
        "Great value for money. {detail} Very satisfied with my purchase.",
        "Excellent product! {detail} Would definitely buy again.",
        "Top-notch quality. {detail} Exceeded all my expectations.",
        "Perfect for my needs. {detail} Couldn't be happier!",
        "Amazing product! {detail} Better than I expected.",
        "Highly recommend! {detail} Great quality and fast shipping."
    ],
    "mixed": [
        "Good product overall, but {minor_issue}. {positive_aspect}",
        "Solid purchase. {positive_aspect} However, {minor_issue}",
        "Works well for the most part. {positive_aspect} Only complaint: {minor_issue}",
        "Decent product. {positive_aspect} {minor_issue} but still worth it.",
        "Pretty good! {positive_aspect} {minor_issue} but overall satisfied."
    ],
    "negative": [
        "Not what I expected. {issue} Disappointed with this purchase.",
        "Had some issues with {issue}. Not worth the price in my opinion.",
        "Returned this item. {issue} Quality wasn't as advertised.",
        "Overpriced for what you get. {issue} Would not recommend.",
        "Disappointing. {issue} Expected better quality for this price."
    ]
}

# Product-specific review details
PRODUCT_DETAILS = {
    "laptop": {
        "positive": ["Battery life is incredible", "Screen is crystal clear", "Runs everything smoothly", "Lightweight and portable", "Fast boot times"],
        "negative": ["Battery drains quickly", "Screen has some glare", "Gets hot under load", "Heavier than expected", "Slow startup"]
    },
    "phone": {
        "positive": ["Camera is amazing", "Battery lasts all day", "Super fast performance", "Great display quality", "Love the design"],
        "negative": ["Battery life is poor", "Camera quality is mediocre", "Lags sometimes", "Screen scratches easily", "Overheats"]
    },
    "headphones": {
        "positive": ["Sound quality is incredible", "Noise canceling works great", "Comfortable for long wear", "Battery lasts forever", "Perfect for travel"],
        "negative": ["Sound quality is okay", "Noise canceling is weak", "Uncomfortable after an hour", "Battery dies quickly", "Build quality feels cheap"]
    },
    "book": {
        "positive": ["Life-changing read", "Well-written and engaging", "Full of practical advice", "Couldn't put it down", "Highly insightful"],
        "negative": ["Too basic", "Repetitive content", "Not what I expected", "Overhyped", "Waste of time"]
    },
    "kitchen": {
        "positive": ["Works perfectly", "Easy to use", "Great quality", "Makes cooking easier", "Durable and well-built"],
        "negative": ["Hard to clean", "Broke after a few uses", "Not as powerful as advertised", "Takes up too much space", "Instructions unclear"]
    },
    "sports": {
        "positive": ["Comfortable fit", "Great quality", "Perfect for workouts", "Durable construction", "Worth the price"],
        "negative": ["Uncomfortable", "Falls apart quickly", "Wrong size", "Poor quality materials", "Not worth it"]
    },
    "clothing": {
        "positive": ["Perfect fit", "Great quality fabric", "Comfortable to wear", "Looks exactly as pictured", "Fast shipping"],
        "negative": ["Runs small", "Fabric feels cheap", "Color is different from photo", "Poor stitching", "Not true to size"]
    }
}

def generate_reviews(product_type: str, num_reviews: int = 5) -> str:
    """Generate fake user reviews for a product."""
    reviews = []
    
    # Mix of positive, mixed, and negative reviews
    review_distribution = {
        "positive": int(num_reviews * 0.6),  # 60% positive
        "mixed": int(num_reviews * 0.25),    # 25% mixed
        "negative": int(num_reviews * 0.15)   # 15% negative
    }
    
    details = PRODUCT_DETAILS.get(product_type, {
        "positive": ["Great product", "Works well", "Good quality"],
        "negative": ["Could be better", "Some issues", "Not perfect"]
    })
    
    # Generate positive reviews
    for _ in range(review_distribution["positive"]):
        template = random.choice(REVIEW_TEMPLATES["positive"])
        detail = random.choice(details["positive"])
        review = template.format(detail=detail)
        reviews.append(f"***** {review}")
    
    # Generate mixed reviews
    for _ in range(review_distribution["mixed"]):
        template = random.choice(REVIEW_TEMPLATES["mixed"])
        positive = random.choice(details["positive"])
        negative = random.choice(details["negative"])
        review = template.format(positive_aspect=positive, minor_issue=negative)
        reviews.append(f"*** {review}")
    
    # Generate negative reviews
    for _ in range(review_distribution["negative"]):
        template = random.choice(REVIEW_TEMPLATES["negative"])
        issue = random.choice(details["negative"])
        review = template.format(issue=issue)
        reviews.append(f"** {review}")
    
    return "\n\n".join(reviews)


# Comprehensive product catalog with reviews
PRODUCTS_DATA = [
    # ============================================================================
    # ELECTRONICS - Laptops (10 products)
    # ============================================================================
    {
        "product_id": "ELEC-001",
        "name": "MacBook Pro 16\" M3 Max",
        "description": generate_reviews("laptop", 8),
        "category": "Electronics",
        "brand": "Apple",
        "price_cents": 349900,
        "stock": 12
    },
    {
        "product_id": "ELEC-002",
        "name": "Dell XPS 15 OLED",
        "description": generate_reviews("laptop", 7),
        "category": "Electronics",
        "brand": "Dell",
        "price_cents": 229900,
        "stock": 18
    },
    {
        "product_id": "ELEC-003",
        "name": "Lenovo ThinkPad X1 Carbon Gen 11",
        "description": generate_reviews("laptop", 6),
        "category": "Electronics",
        "brand": "Lenovo",
        "price_cents": 149900,
        "stock": 25
    },
    {
        "product_id": "ELEC-004",
        "name": "HP Spectre x360 14\"",
        "description": generate_reviews("laptop", 5),
        "category": "Electronics",
        "brand": "HP",
        "price_cents": 129900,
        "stock": 15
    },
    {
        "product_id": "ELEC-005",
        "name": "ASUS ROG Zephyrus G14",
        "description": generate_reviews("laptop", 6),
        "category": "Electronics",
        "brand": "ASUS",
        "price_cents": 159900,
        "stock": 10
    },
    {
        "product_id": "ELEC-006",
        "name": "Microsoft Surface Laptop Studio 2",
        "description": generate_reviews("laptop", 5),
        "category": "Electronics",
        "brand": "Microsoft",
        "price_cents": 199900,
        "stock": 8
    },
    {
        "product_id": "ELEC-007",
        "name": "Razer Blade 15 Advanced",
        "description": generate_reviews("laptop", 4),
        "category": "Electronics",
        "brand": "Razer",
        "price_cents": 249900,
        "stock": 6
    },
    {
        "product_id": "ELEC-008",
        "name": "Acer Predator Helios 16",
        "description": generate_reviews("laptop", 5),
        "category": "Electronics",
        "brand": "Acer",
        "price_cents": 179900,
        "stock": 9
    },
    {
        "product_id": "ELEC-009",
        "name": "LG Gram 17\" Ultra-Lightweight",
        "description": generate_reviews("laptop", 6),
        "category": "Electronics",
        "brand": "LG",
        "price_cents": 139900,
        "stock": 14
    },
    {
        "product_id": "ELEC-010",
        "name": "Framework Laptop 16",
        "description": generate_reviews("laptop", 4),
        "category": "Electronics",
        "brand": "Framework",
        "price_cents": 169900,
        "stock": 7
    },
    
    # ============================================================================
    # ELECTRONICS - Smartphones (10 products)
    # ============================================================================
    {
        "product_id": "ELEC-011",
        "name": "iPhone 15 Pro Max 256GB",
        "description": generate_reviews("phone", 9),
        "category": "Electronics",
        "brand": "Apple",
        "price_cents": 119900,
        "stock": 8
    },
    {
        "product_id": "ELEC-012",
        "name": "Samsung Galaxy S24 Ultra 512GB",
        "description": generate_reviews("phone", 8),
        "category": "Electronics",
        "brand": "Samsung",
        "price_cents": 129900,
        "stock": 15
    },
    {
        "product_id": "ELEC-013",
        "name": "Google Pixel 8 Pro 256GB",
        "description": generate_reviews("phone", 7),
        "category": "Electronics",
        "brand": "Google",
        "price_cents": 99900,
        "stock": 22
    },
    {
        "product_id": "ELEC-014",
        "name": "OnePlus 12 256GB",
        "description": generate_reviews("phone", 6),
        "category": "Electronics",
        "brand": "OnePlus",
        "price_cents": 79900,
        "stock": 18
    },
    {
        "product_id": "ELEC-015",
        "name": "Xiaomi 14 Ultra 512GB",
        "description": generate_reviews("phone", 5),
        "category": "Electronics",
        "brand": "Xiaomi",
        "price_cents": 89900,
        "stock": 12
    },
    {
        "product_id": "ELEC-016",
        "name": "Nothing Phone (2) 256GB",
        "description": generate_reviews("phone", 4),
        "category": "Electronics",
        "brand": "Nothing",
        "price_cents": 59900,
        "stock": 20
    },
    {
        "product_id": "ELEC-017",
        "name": "Motorola Edge 40 Pro",
        "description": generate_reviews("phone", 5),
        "category": "Electronics",
        "brand": "Motorola",
        "price_cents": 69900,
        "stock": 16
    },
    {
        "product_id": "ELEC-018",
        "name": "Sony Xperia 1 V",
        "description": generate_reviews("phone", 4),
        "category": "Electronics",
        "brand": "Sony",
        "price_cents": 129900,
        "stock": 5
    },
    {
        "product_id": "ELEC-019",
        "name": "ASUS ROG Phone 7",
        "description": generate_reviews("phone", 3),
        "category": "Electronics",
        "brand": "ASUS",
        "price_cents": 99900,
        "stock": 7
    },
    {
        "product_id": "ELEC-020",
        "name": "Fairphone 5 256GB",
        "description": generate_reviews("phone", 4),
        "category": "Electronics",
        "brand": "Fairphone",
        "price_cents": 69900,
        "stock": 9
    },
    
    # ============================================================================
    # ELECTRONICS - Headphones & Audio (10 products)
    # ============================================================================
    {
        "product_id": "ELEC-021",
        "name": "Sony WH-1000XM5 Noise Canceling Headphones",
        "description": generate_reviews("headphones", 8),
        "category": "Electronics",
        "brand": "Sony",
        "price_cents": 39999,
        "stock": 45
    },
    {
        "product_id": "ELEC-022",
        "name": "Bose QuietComfort Ultra Headphones",
        "description": generate_reviews("headphones", 7),
        "category": "Electronics",
        "brand": "Bose",
        "price_cents": 42900,
        "stock": 32
    },
    {
        "product_id": "ELEC-023",
        "name": "Apple AirPods Pro (2nd Gen) with USB-C",
        "description": generate_reviews("headphones", 9),
        "category": "Electronics",
        "brand": "Apple",
        "price_cents": 24900,
        "stock": 67
    },
    {
        "product_id": "ELEC-024",
        "name": "Sennheiser Momentum 4 Wireless",
        "description": generate_reviews("headphones", 6),
        "category": "Electronics",
        "brand": "Sennheiser",
        "price_cents": 34999,
        "stock": 28
    },
    {
        "product_id": "ELEC-025",
        "name": "Bowers & Wilkins Px8",
        "description": generate_reviews("headphones", 5),
        "category": "Electronics",
        "brand": "Bowers & Wilkins",
        "price_cents": 54999,
        "stock": 12
    },
    {
        "product_id": "ELEC-026",
        "name": "JBL Tour One M2",
        "description": generate_reviews("headphones", 4),
        "category": "Electronics",
        "brand": "JBL",
        "price_cents": 29999,
        "stock": 35
    },
    {
        "product_id": "ELEC-027",
        "name": "Audio-Technica ATH-M50xBT2",
        "description": generate_reviews("headphones", 5),
        "category": "Electronics",
        "brand": "Audio-Technica",
        "price_cents": 19999,
        "stock": 42
    },
    {
        "product_id": "ELEC-028",
        "name": "Beats Studio Pro",
        "description": generate_reviews("headphones", 6),
        "category": "Electronics",
        "brand": "Beats",
        "price_cents": 34999,
        "stock": 38
    },
    {
        "product_id": "ELEC-029",
        "name": "Sony WF-1000XM5 Earbuds",
        "description": generate_reviews("headphones", 7),
        "category": "Electronics",
        "brand": "Sony",
        "price_cents": 29999,
        "stock": 55
    },
    {
        "product_id": "ELEC-030",
        "name": "Samsung Galaxy Buds2 Pro",
        "description": generate_reviews("headphones", 6),
        "category": "Electronics",
        "brand": "Samsung",
        "price_cents": 22999,
        "stock": 48
    },
    
    # ============================================================================
    # BOOKS (15 products)
    # ============================================================================
    {
        "product_id": "BOOK-001",
        "name": "Atomic Habits by James Clear",
        "description": generate_reviews("book", 12),
        "category": "Books",
        "brand": "Penguin Random House",
        "price_cents": 1799,
        "stock": 250
    },
    {
        "product_id": "BOOK-002",
        "name": "The Psychology of Money by Morgan Housel",
        "description": generate_reviews("book", 10),
        "category": "Books",
        "brand": "Harriman House",
        "price_cents": 1599,
        "stock": 180
    },
    {
        "product_id": "BOOK-003",
        "name": "System Design Interview Vol 1 by Alex Xu",
        "description": generate_reviews("book", 8),
        "category": "Books",
        "brand": "ByteByteGo",
        "price_cents": 4999,
        "stock": 95
    },
    {
        "product_id": "BOOK-004",
        "name": "Deep Work by Cal Newport",
        "description": generate_reviews("book", 9),
        "category": "Books",
        "brand": "Grand Central Publishing",
        "price_cents": 1699,
        "stock": 145
    },
    {
        "product_id": "BOOK-005",
        "name": "Designing Data-Intensive Applications by Martin Kleppmann",
        "description": generate_reviews("book", 7),
        "category": "Books",
        "brand": "O'Reilly Media",
        "price_cents": 5999,
        "stock": 72
    },
    {
        "product_id": "BOOK-006",
        "name": "Thinking, Fast and Slow by Daniel Kahneman",
        "description": generate_reviews("book", 11),
        "category": "Books",
        "brand": "Farrar, Straus and Giroux",
        "price_cents": 1899,
        "stock": 220
    },
    {
        "product_id": "BOOK-007",
        "name": "The Almanack of Naval Ravikant by Eric Jorgenson",
        "description": generate_reviews("book", 8),
        "category": "Books",
        "brand": "Magrathea Publishing",
        "price_cents": 2499,
        "stock": 130
    },
    {
        "product_id": "BOOK-008",
        "name": "Clean Code by Robert C. Martin",
        "description": generate_reviews("book", 9),
        "category": "Books",
        "brand": "Prentice Hall",
        "price_cents": 5499,
        "stock": 88
    },
    {
        "product_id": "BOOK-009",
        "name": "The Lean Startup by Eric Ries",
        "description": generate_reviews("book", 7),
        "category": "Books",
        "brand": "Crown Business",
        "price_cents": 1799,
        "stock": 165
    },
    {
        "product_id": "BOOK-010",
        "name": "Zero to One by Peter Thiel",
        "description": generate_reviews("book", 8),
        "category": "Books",
        "brand": "Crown Business",
        "price_cents": 1899,
        "stock": 190
    },
    {
        "product_id": "BOOK-011",
        "name": "The Pragmatic Programmer by Andy Hunt & Dave Thomas",
        "description": generate_reviews("book", 8),
        "category": "Books",
        "brand": "Addison-Wesley",
        "price_cents": 4999,
        "stock": 105
    },
    {
        "product_id": "BOOK-012",
        "name": "Sapiens by Yuval Noah Harari",
        "description": generate_reviews("book", 10),
        "category": "Books",
        "brand": "Harper",
        "price_cents": 1899,
        "stock": 175
    },
    {
        "product_id": "BOOK-013",
        "name": "The 7 Habits of Highly Effective People by Stephen Covey",
        "description": generate_reviews("book", 9),
        "category": "Books",
        "brand": "Free Press",
        "price_cents": 1699,
        "stock": 200
    },
    {
        "product_id": "BOOK-014",
        "name": "Influence: The Psychology of Persuasion by Robert Cialdini",
        "description": generate_reviews("book", 7),
        "category": "Books",
        "brand": "Harper Business",
        "price_cents": 1799,
        "stock": 140
    },
    {
        "product_id": "BOOK-015",
        "name": "The Art of Computer Programming Vol 1 by Donald Knuth",
        "description": generate_reviews("book", 6),
        "category": "Books",
        "brand": "Addison-Wesley",
        "price_cents": 7999,
        "stock": 45
    },
    
    # ============================================================================
    # HOME & KITCHEN (15 products)
    # ============================================================================
    {
        "product_id": "HOME-001",
        "name": "Breville Barista Express Espresso Machine",
        "description": generate_reviews("kitchen", 7),
        "category": "Home & Kitchen",
        "brand": "Breville",
        "price_cents": 69999,
        "stock": 14
    },
    {
        "product_id": "HOME-002",
        "name": "Vitamix 5200 Blender",
        "description": generate_reviews("kitchen", 8),
        "category": "Home & Kitchen",
        "brand": "Vitamix",
        "price_cents": 44999,
        "stock": 23
    },
    {
        "product_id": "HOME-003",
        "name": "KitchenAid Artisan Stand Mixer 5-Qt",
        "description": generate_reviews("kitchen", 9),
        "category": "Home & Kitchen",
        "brand": "KitchenAid",
        "price_cents": 42999,
        "stock": 31
    },
    {
        "product_id": "HOME-004",
        "name": "Ninja Foodi 14-in-1 Pressure Cooker",
        "description": generate_reviews("kitchen", 6),
        "category": "Home & Kitchen",
        "brand": "Ninja",
        "price_cents": 24999,
        "stock": 42
    },
    {
        "product_id": "HOME-005",
        "name": "Dyson V15 Detect Cordless Vacuum",
        "description": generate_reviews("kitchen", 7),
        "category": "Home & Kitchen",
        "brand": "Dyson",
        "price_cents": 74999,
        "stock": 17
    },
    {
        "product_id": "HOME-006",
        "name": "iRobot Roomba j7+ Self-Emptying Robot Vacuum",
        "description": generate_reviews("kitchen", 8),
        "category": "Home & Kitchen",
        "brand": "iRobot",
        "price_cents": 79999,
        "stock": 12
    },
    {
        "product_id": "HOME-007",
        "name": "All-Clad D3 Stainless 10-Piece Cookware Set",
        "description": generate_reviews("kitchen", 5),
        "category": "Home & Kitchen",
        "brand": "All-Clad",
        "price_cents": 79900,
        "stock": 8
    },
    {
        "product_id": "HOME-008",
        "name": "Le Creuset Dutch Oven 5.5 Qt",
        "description": generate_reviews("kitchen", 6),
        "category": "Home & Kitchen",
        "brand": "Le Creuset",
        "price_cents": 37999,
        "stock": 19
    },
    {
        "product_id": "HOME-009",
        "name": "Nespresso Vertuo Next with Aeroccino",
        "description": generate_reviews("kitchen", 7),
        "category": "Home & Kitchen",
        "brand": "Nespresso",
        "price_cents": 19999,
        "stock": 38
    },
    {
        "product_id": "HOME-010",
        "name": "Instant Pot Pro 10-in-1 Pressure Cooker",
        "description": generate_reviews("kitchen", 8),
        "category": "Home & Kitchen",
        "brand": "Instant Pot",
        "price_cents": 14999,
        "stock": 56
    },
    {
        "product_id": "HOME-011",
        "name": "Cuisinart Food Processor 14-Cup",
        "description": generate_reviews("kitchen", 6),
        "category": "Home & Kitchen",
        "brand": "Cuisinart",
        "price_cents": 29999,
        "stock": 27
    },
    {
        "product_id": "HOME-012",
        "name": "OXO Good Grips 9-Piece Kitchen Tool Set",
        "description": generate_reviews("kitchen", 5),
        "category": "Home & Kitchen",
        "brand": "OXO",
        "price_cents": 8999,
        "stock": 68
    },
    {
        "product_id": "HOME-013",
        "name": "Shark Navigator Lift-Away Vacuum",
        "description": generate_reviews("kitchen", 7),
        "category": "Home & Kitchen",
        "brand": "Shark",
        "price_cents": 19999,
        "stock": 35
    },
    {
        "product_id": "HOME-014",
        "name": "Lodge Cast Iron Skillet 12-Inch",
        "description": generate_reviews("kitchen", 6),
        "category": "Home & Kitchen",
        "brand": "Lodge",
        "price_cents": 2999,
        "stock": 89
    },
    {
        "product_id": "HOME-015",
        "name": "Zwilling J.A. Henckels Professional S 8-Piece Knife Set",
        "description": generate_reviews("kitchen", 5),
        "category": "Home & Kitchen",
        "brand": "Zwilling",
        "price_cents": 39999,
        "stock": 16
    },
    
    # ============================================================================
    # SPORTS & FITNESS (15 products)
    # ============================================================================
    {
        "product_id": "SPORT-001",
        "name": "Nike Air Zoom Pegasus 40 Running Shoes",
        "description": generate_reviews("sports", 8),
        "category": "Sports",
        "brand": "Nike",
        "price_cents": 12999,
        "stock": 125
    },
    {
        "product_id": "SPORT-002",
        "name": "Manduka PRO Yoga Mat 6mm",
        "description": generate_reviews("sports", 7),
        "category": "Sports",
        "brand": "Manduka",
        "price_cents": 12800,
        "stock": 67
    },
    {
        "product_id": "SPORT-003",
        "name": "Bowflex SelectTech 552 Adjustable Dumbbells",
        "description": generate_reviews("sports", 6),
        "category": "Sports",
        "brand": "Bowflex",
        "price_cents": 39900,
        "stock": 15
    },
    {
        "product_id": "SPORT-004",
        "name": "Peloton Bike+ with Rotating Screen",
        "description": generate_reviews("sports", 5),
        "category": "Sports",
        "brand": "Peloton",
        "price_cents": 249500,
        "stock": 4
    },
    {
        "product_id": "SPORT-005",
        "name": "TRX HOME2 Suspension Training System",
        "description": generate_reviews("sports", 5),
        "category": "Sports",
        "brand": "TRX",
        "price_cents": 16999,
        "stock": 43
    },
    {
        "product_id": "SPORT-006",
        "name": "Adidas Ultraboost Light Running Shoes",
        "description": generate_reviews("sports", 7),
        "category": "Sports",
        "brand": "Adidas",
        "price_cents": 18000,
        "stock": 98
    },
    {
        "product_id": "SPORT-007",
        "name": "Yeti Rambler 26oz Insulated Water Bottle",
        "description": generate_reviews("sports", 6),
        "category": "Sports",
        "brand": "Yeti",
        "price_cents": 3800,
        "stock": 210
    },
    {
        "product_id": "SPORT-008",
        "name": "Fitbit Charge 6 Fitness Tracker",
        "description": generate_reviews("sports", 8),
        "category": "Sports",
        "brand": "Fitbit",
        "price_cents": 15999,
        "stock": 78
    },
    {
        "product_id": "SPORT-009",
        "name": "Theragun Prime Percussive Massage Gun",
        "description": generate_reviews("sports", 6),
        "category": "Sports",
        "brand": "Therabody",
        "price_cents": 29999,
        "stock": 29
    },
    {
        "product_id": "SPORT-010",
        "name": "REI Co-op Flash 55 Backpack",
        "description": generate_reviews("sports", 5),
        "category": "Sports",
        "brand": "REI",
        "price_cents": 19900,
        "stock": 24
    },
    {
        "product_id": "SPORT-011",
        "name": "Garmin Forerunner 955 GPS Watch",
        "description": generate_reviews("sports", 7),
        "category": "Sports",
        "brand": "Garmin",
        "price_cents": 59999,
        "stock": 18
    },
    {
        "product_id": "SPORT-012",
        "name": "Rogue Fitness Ohio Power Bar",
        "description": generate_reviews("sports", 4),
        "category": "Sports",
        "brand": "Rogue",
        "price_cents": 29900,
        "stock": 12
    },
    {
        "product_id": "SPORT-013",
        "name": "Lululemon Align High-Rise Leggings",
        "description": generate_reviews("clothing", 9),
        "category": "Sports",
        "brand": "Lululemon",
        "price_cents": 9800,
        "stock": 145
    },
    {
        "product_id": "SPORT-014",
        "name": "Under Armour HeatGear Compression Shirt",
        "description": generate_reviews("clothing", 6),
        "category": "Sports",
        "brand": "Under Armour",
        "price_cents": 3499,
        "stock": 178
    },
    {
        "product_id": "SPORT-015",
        "name": "Brooks Ghost 15 Running Shoes",
        "description": generate_reviews("sports", 7),
        "category": "Sports",
        "brand": "Brooks",
        "price_cents": 13999,
        "stock": 92
    },
    
    # ============================================================================
    # CLOTHING (15 products)
    # ============================================================================
    {
        "product_id": "CLOTH-001",
        "name": "Nike Air Max 270 Men's Running Shoes",
        "description": generate_reviews("clothing", 8),
        "category": "Clothing",
        "brand": "Nike",
        "price_cents": 15999,
        "stock": 25
    },
    {
        "product_id": "CLOTH-002",
        "name": "Levi's 501 Original Fit Jeans",
        "description": generate_reviews("clothing", 9),
        "category": "Clothing",
        "brand": "Levi's",
        "price_cents": 6999,
        "stock": 40
    },
    {
        "product_id": "CLOTH-003",
        "name": "Patagonia Down Sweater Jacket",
        "description": generate_reviews("clothing", 7),
        "category": "Clothing",
        "brand": "Patagonia",
        "price_cents": 29999,
        "stock": 15
    },
    {
        "product_id": "CLOTH-004",
        "name": "Carhartt WIP Classic Work Jacket",
        "description": generate_reviews("clothing", 6),
        "category": "Clothing",
        "brand": "Carhartt",
        "price_cents": 14999,
        "stock": 28
    },
    {
        "product_id": "CLOTH-005",
        "name": "Adidas Originals Superstar Sneakers",
        "description": generate_reviews("clothing", 8),
        "category": "Clothing",
        "brand": "Adidas",
        "price_cents": 8999,
        "stock": 52
    },
    {
        "product_id": "CLOTH-006",
        "name": "The North Face Nuptse Jacket",
        "description": generate_reviews("clothing", 7),
        "category": "Clothing",
        "brand": "The North Face",
        "price_cents": 24999,
        "stock": 19
    },
    {
        "product_id": "CLOTH-007",
        "name": "Vans Old Skool Classic Sneakers",
        "description": generate_reviews("clothing", 9),
        "category": "Clothing",
        "brand": "Vans",
        "price_cents": 6999,
        "stock": 68
    },
    {
        "product_id": "CLOTH-008",
        "name": "Ralph Lauren Classic Fit Polo Shirt",
        "description": generate_reviews("clothing", 6),
        "category": "Clothing",
        "brand": "Ralph Lauren",
        "price_cents": 8999,
        "stock": 45
    },
    {
        "product_id": "CLOTH-009",
        "name": "Champion Reverse Weave Hoodie",
        "description": generate_reviews("clothing", 7),
        "category": "Clothing",
        "brand": "Champion",
        "price_cents": 5999,
        "stock": 58
    },
    {
        "product_id": "CLOTH-010",
        "name": "New Balance 990v5 Running Shoes",
        "description": generate_reviews("clothing", 8),
        "category": "Clothing",
        "brand": "New Balance",
        "price_cents": 18499,
        "stock": 33
    },
    {
        "product_id": "CLOTH-011",
        "name": "Tommy Hilfiger Classic Chinos",
        "description": generate_reviews("clothing", 5),
        "category": "Clothing",
        "brand": "Tommy Hilfiger",
        "price_cents": 6999,
        "stock": 47
    },
    {
        "product_id": "CLOTH-012",
        "name": "Converse Chuck Taylor All Star High Tops",
        "description": generate_reviews("clothing", 10),
        "category": "Clothing",
        "brand": "Converse",
        "price_cents": 5999,
        "stock": 89
    },
    {
        "product_id": "CLOTH-013",
        "name": "Columbia Bugaboo Fleece Jacket",
        "description": generate_reviews("clothing", 6),
        "category": "Clothing",
        "brand": "Columbia",
        "price_cents": 7999,
        "stock": 36
    },
    {
        "product_id": "CLOTH-014",
        "name": "Puma Suede Classic Sneakers",
        "description": generate_reviews("clothing", 7),
        "category": "Clothing",
        "brand": "Puma",
        "price_cents": 6999,
        "stock": 54
    },
    {
        "product_id": "CLOTH-015",
        "name": "Uniqlo Ultra Light Down Jacket",
        "description": generate_reviews("clothing", 8),
        "category": "Clothing",
        "brand": "Uniqlo",
        "price_cents": 4999,
        "stock": 72
    },
]

def seed_products_with_reviews():
    """Seed database with products that have user reviews in descriptions."""
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    # Check if already seeded
    existing = db.query(Product).first()
    if existing:
        print("[WARN] Database already has products. Clearing and reseeding...")
        db.query(Inventory).delete()
        db.query(Price).delete()
        db.query(Product).delete()
        db.commit()
    
    print(f"\nSeeding {len(PRODUCTS_DATA)} products with user reviews...\n")
    
    for data in PRODUCTS_DATA:
        # Create product
        product = Product(
            product_id=data["product_id"],
            name=data["name"],
            description=data["description"],
            category=data["category"],
            brand=data.get("brand", "Unknown"),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(product)
        db.flush()
        
        # Add price
        price = Price(
            product_id=data["product_id"],
            price_cents=data["price_cents"],
            currency="USD"
        )
        db.add(price)
        
        # Add inventory
        inventory = Inventory(
            product_id=data["product_id"],
            available_qty=data["stock"],
            reserved_qty=0
        )
        db.add(inventory)
        
        print(f"  [OK] {data['category']:20s} | {data['product_id']:12s} | {data['name'][:50]}")
    
    db.commit()
    
    # Verify
    total = db.query(Product).count()
    print(f"\n[OK] Successfully seeded {total} products with user reviews!")
    
    # Show category breakdown
    print("\nCategory Breakdown:")
    from sqlalchemy import func
    categories = db.query(
        Product.category,
        func.count(Product.product_id).label('count')
    ).group_by(Product.category).all()
    
    for category, count in categories:
        print(f"  • {category:20s}: {count} products")
    
    # Show sample review
    sample = db.query(Product).filter(Product.description.isnot(None)).first()
    if sample:
        print(f"\nSample Review (from {sample.name}):")
        print(f"   {sample.description[:200]}...")
    
    db.close()


if __name__ == "__main__":
    seed_products_with_reviews()
