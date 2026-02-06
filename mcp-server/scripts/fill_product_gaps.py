#!/usr/bin/env python3
"""
Fill Product Gaps - Add products to categories with < 10 items

Ensures every category/product_type combination has 10-20 items for better
user experience and testing coverage.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import random
import json
import uuid
from datetime import datetime
from app.database import SessionLocal
from app.models import Product, Price, Inventory
from collections import defaultdict


# ============================================================================
# Product Generators by Category
# ============================================================================

def generate_accessories(product_type: str, count: int):
    """Generate jewelry and fashion accessories."""
    products = []
    
    types_data = {
        "Anklets": {
            "prefixes": ["Gold", "Silver", "Rose Gold", "Platinum", "Beaded", "Chain", "Charm"],
            "styles": ["Delicate", "Bold", "Minimalist", "Boho", "Classic", "Modern"],
            "materials": ["14K Gold", "Sterling Silver", "Gold Plated", "Stainless Steel"],
            "price_range": (15, 150)
        },
        "Bracelets": {
            "prefixes": ["Tennis", "Cuff", "Bangle", "Chain", "Charm", "Wrap", "ID"],
            "styles": ["Classic", "Statement", "Layered", "Adjustable", "Hinged"],
            "materials": ["14K Gold", "Sterling Silver", "Leather", "Titanium"],
            "price_range": (20, 300)
        },
        "Charms": {
            "prefixes": ["Heart", "Star", "Initial", "Birthstone", "Animal", "Symbol"],
            "styles": ["Dangle", "Clip-On", "Screw", "Snap", "Magnetic"],
            "materials": ["14K Gold", "Sterling Silver", "Enamel", "Crystal"],
            "price_range": (10, 80)
        },
        "Rings": {
            "prefixes": ["Engagement", "Wedding", "Statement", "Stackable", "Cocktail", "Signet"],
            "styles": ["Solitaire", "Halo", "Three-Stone", "Band", "Eternity"],
            "materials": ["14K Gold", "White Gold", "Platinum", "Sterling Silver"],
            "price_range": (30, 500)
        },
        "Carbon Offset": {
            "prefixes": ["Tree Planting", "Ocean Cleanup", "Solar Energy", "Wind Power"],
            "styles": ["Monthly", "Annual", "One-Time", "Subscription"],
            "materials": ["Digital Certificate"],
            "price_range": (5, 100)
        },
        "Scarf": {
            "prefixes": ["Silk", "Cashmere", "Wool", "Cotton", "Pashmina", "Infinity"],
            "styles": ["Classic", "Plaid", "Striped", "Solid", "Printed", "Woven"],
            "materials": ["Silk", "Cashmere", "Wool", "Cotton Blend"],
            "price_range": (25, 120)
        },
        "Belt": {
            "prefixes": ["Leather", "Canvas", "Designer", "Classic", "Western", "Reversible"],
            "styles": ["Dress", "Casual", "Statement", "Minimalist", "Braided"],
            "materials": ["Genuine Leather", "Synthetic", "Fabric"],
            "price_range": (20, 150)
        },
        "Bag": {
            "prefixes": ["Tote", "Crossbody", "Clutch", "Backpack", "Messenger", "Satchel"],
            "styles": ["Classic", "Modern", "Vintage", "Minimalist", "Structured"],
            "materials": ["Leather", "Canvas", "Synthetic"],
            "price_range": (40, 250)
        },
        "Watch": {
            "prefixes": ["Analog", "Digital", "Sport", "Dress", "Smart", "Chronograph"],
            "styles": ["Classic", "Modern", "Minimalist", "Luxury"],
            "materials": ["Stainless Steel", "Leather", "Silicone"],
            "price_range": (50, 300)
        },
        "Sunglasses": {
            "prefixes": ["Aviator", "Wayfarer", "Cat-Eye", "Round", "Sport", "Polarized"],
            "styles": ["Classic", "Trendy", "Sport", "Vintage"],
            "materials": ["Acetate", "Metal", "Mixed"],
            "price_range": (30, 180)
        },
        "Hat": {
            "prefixes": ["Fedora", "Beanie", "Baseball", "Sun", "Bucket", "Beret"],
            "styles": ["Classic", "Casual", "Structured", "Slouch"],
            "materials": ["Wool", "Cotton", "Straw", "Felt"],
            "price_range": (15, 80)
        },
        "generic": {
            "prefixes": ["Luxury", "Designer", "Handmade", "Vintage", "Modern"],
            "styles": ["Minimalist", "Boho", "Classic", "Trendy"],
            "materials": ["Mixed Materials"],
            "price_range": (15, 200)
        }
    }
    
    data = types_data.get(product_type, types_data["generic"])
    
    for i in range(count):
        prefix = random.choice(data["prefixes"])
        style = random.choice(data["styles"])
        material = random.choice(data["materials"])
        
        name = f"{prefix} {style} {product_type[:-1] if product_type.endswith('s') else product_type}"
        price = random.uniform(*data["price_range"])
        
        products.append({
            "product_id": str(uuid.uuid4()),
            "name": name,
            "description": f"{style} {product_type.lower()} made from {material}. Perfect for everyday wear or special occasions.",
            "category": "Accessories",
            "subcategory": product_type,
            "product_type": product_type,
            "brand": random.choice(["Tiffany & Co", "Pandora", "David Yurman", "Kate Spade", "Michael Kors"]),
            "price": round(price, 2),
            "image_url": f"https://example.com/accessories/{product_type.lower()}-{i+1}.jpg",
            "reviews_count": random.randint(10, 200),
            "rating": round(random.uniform(4.0, 5.0), 1),
        })
    
    return products


def generate_art_products(product_type: str, count: int):
    """Generate art and craft products."""
    products = []
    
    themes = ["Floral", "Abstract", "Geometric", "Nature", "Animals", "Quotes", "Celestial", "Vintage"]
    sizes = ["Small", "Medium", "Large", "Jumbo", "Mini"]
    
    for i in range(count):
        theme = random.choice(themes)
        size = random.choice(sizes)
        
        if product_type == "Jumbo Party Pack":
            name = f"{theme} {product_type} - {random.randint(50, 100)} Pieces"
            price = random.uniform(25, 60)
        elif product_type == "Tiny Tin":
            name = f"{theme} {product_type} Collection"
            price = random.uniform(8, 18)
        else:
            name = f"{theme} {size} Tattly Set"
            price = random.uniform(5, 25)
        
        products.append({
            "product_id": str(uuid.uuid4()),
            "name": name,
            "description": f"Beautiful {theme.lower()} themed {product_type.lower()}. Perfect for parties, gifts, or personal enjoyment.",
            "category": "Art",
            "subcategory": product_type,
            "product_type": product_type,
            "brand": "Tattly",
            "price": round(price, 2),
            "image_url": f"https://example.com/art/{product_type.replace(' ', '-').lower()}-{i+1}.jpg",
            "reviews_count": random.randint(15, 150),
            "rating": round(random.uniform(4.2, 5.0), 1),
        })
    
    return products


def generate_beauty_products(product_type: str, count: int):
    """Generate beauty and cosmetics products."""
    products = []
    
    brands = ["ColourPop", "MAC", "NARS", "Fenty Beauty", "Urban Decay", "NYX", "e.l.f.", "Maybelline"]
    colors = ["Nude", "Pink", "Red", "Berry", "Coral", "Mauve", "Plum", "Brown"]
    finishes = ["Matte", "Satin", "Glossy", "Shimmer", "Metallic", "Cream"]
    
    type_configs = {
        "Bundle": (25, 60, "Complete"),
        "Eye Trio": (18, 35, "Eyeshadow"),
        "Fragrance": (30, 80, "Perfume"),
        "Full Collection Set": (45, 100, "Complete"),
        "Jelly Much Duo": (12, 22, "Eyeshadow"),
        "Jelly Much Shadow": (8, 16, "Eyeshadow"),
        "Lip Trio": (20, 38, "Lipstick"),
        "Lippie Stix": (7, 14, "Lipstick"),
        "Lippie Stix Duo": (13, 25, "Lipstick"),
        "Mega Highlighter": (15, 28, "Highlighter"),
        "Palette Trio": (25, 50, "Eyeshadow"),
        "Powder To Creme Lipstick": (9, 18, "Lipstick"),
        "Sample": (3, 8, "Sample"),
        "Set": (20, 45, "Complete"),
        "Shadow Palette": (18, 40, "Eyeshadow"),
        "Foundation": (25, 55, "Foundation"),
        "Mascara": (8, 22, "Mascara"),
        "Blush": (12, 35, "Blush"),
        "Skincare": (15, 50, "Skincare"),
        "Lipstick": (12, 30, "Lipstick"),
        "Eyeshadow": (15, 45, "Eyeshadow"),
        "generic": (10, 30, "Beauty"),
        "shopify_product": (12, 28, "Beauty"),
    }
    
    min_price, max_price, item_type = type_configs.get(product_type, (10, 30, "Beauty"))
    
    for i in range(count):
        color = random.choice(colors)
        finish = random.choice(finishes)
        brand = random.choice(brands)
        
        name = f"{color} {finish} {product_type}"
        price = random.uniform(min_price, max_price)
        
        products.append({
            "product_id": str(uuid.uuid4()),
            "name": name,
            "description": f"High-quality {finish.lower()} {product_type.lower()} in {color.lower()} shade. Long-lasting formula.",
            "category": "Beauty",
            "subcategory": item_type,
            "product_type": product_type,
            "brand": brand,
            "price": round(price, 2),
            "image_url": f"https://example.com/beauty/{product_type.replace(' ', '-').lower()}-{i+1}.jpg",
            "reviews_count": random.randint(20, 300),
            "rating": round(random.uniform(4.0, 5.0), 1),
        })
    
    return products


def generate_clothing(product_type: str, count: int):
    """Generate clothing items."""
    products = []
    
    brands = ["Nike", "Adidas", "Lululemon", "Zara", "H&M", "Gap", "Old Navy", "Athleta"]
    colors = ["Black", "White", "Navy", "Gray", "Blue", "Red", "Green", "Pink"]
    sizes = ["XS", "S", "M", "L", "XL", "XXL"]
    materials = ["Cotton", "Polyester", "Blend", "Spandex", "Nylon", "Organic Cotton"]
    
    type_configs = {
        "Accessories": ("Scarf", "Hat", "Belt", 15, 40),
        "Dresses": ("Casual", "Formal", "Summer", 30, 80),
        "Graphic Tees": ("Logo", "Print", "Slogan", 15, 35),
        "Mens Pants": ("Chino", "Jeans", "Joggers", 35, 80),
        "Pants": ("Casual", "Dress", "Joggers", 30, 75),
        "Shirts & Blouses": ("Button-Up", "Blouse", "Tunic", 25, 60),
        "Shorts": ("Athletic", "Casual", "Denim", 20, 45),
        "Womens LS Tops": ("Long Sleeve", "Thermal", "Base Layer", 25, 55),
        "Womens Pants": ("Leggings", "Jeans", "Yoga", 30, 70),
        "Womens Shorts": ("Athletic", "Running", "Bike", 22, 48),
        "Womens Sleeveless Tops": ("Tank", "Muscle", "Crop", 18, 40),
        "Womens Sports Bras": ("Low Impact", "Medium Support", "High Impact", 25, 60),
        "Womens Tank": ("Athletic", "Casual", "Ribbed", 15, 35),
        "womens Misc.": ("Activewear", "Loungewear", 20, 50),
        "womens Socks": ("Athletic", "Casual", "Compression", 8, 20),
        "generic": ("Basic", "Essential", 18, 45),
    }
    
    style, *_, min_price, max_price = type_configs.get(product_type, ("Basic", "Essential", 18, 45))
    
    for i in range(count):
        color = random.choice(colors)
        size = random.choice(sizes)
        brand = random.choice(brands)
        material = random.choice(materials)
        
        name = f"{color} {style} {product_type}"
        price = random.uniform(min_price, max_price)
        
        products.append({
            "product_id": str(uuid.uuid4()),
            "name": name,
            "description": f"Comfortable {material.lower()} {product_type.lower()}. Size {size}. Perfect for everyday wear.",
            "category": "Clothing",
            "subcategory": product_type,
            "product_type": product_type,
            "brand": brand,
            "price": round(price, 2),
            "image_url": f"https://example.com/clothing/{product_type.replace(' ', '-').lower()}-{i+1}.jpg",
            "reviews_count": random.randint(15, 250),
            "rating": round(random.uniform(3.8, 5.0), 1),
        })
    
    return products


def generate_electronics_accessories(product_type: str, count: int):
    """Generate electronics accessories, monitors, and phones."""
    products = []
    
    if product_type == "monitor":
        brands = ["Dell", "LG", "Samsung", "ASUS", "BenQ", "Acer", "HP", "ViewSonic"]
        sizes = ["24", "27", "32", "34", "38"]
        resolutions = ["1920x1080", "2560x1440", "3840x2160", "5120x1440"]
        refresh_rates = ["60Hz", "75Hz", "144Hz", "165Hz", "240Hz"]
        
        for i in range(count):
            brand = random.choice(brands)
            size = random.choice(sizes)
            resolution = random.choice(resolutions)
            refresh = random.choice(refresh_rates)
            
            name = f"{brand} {size}\" {resolution.split('x')[1]}p {refresh} Monitor"
            price = random.uniform(150, 800)
            
            products.append({
                "product_id": str(uuid.uuid4()),
                "name": name,
                "description": f"{size}\" monitor with {resolution} resolution and {refresh} refresh rate. Perfect for gaming and productivity.",
                "category": "Electronics",
                "subcategory": "Monitor",
                "product_type": "monitor",
                "brand": brand,
                "price": round(price, 2),
                "image_url": f"https://example.com/electronics/monitor-{i+1}.jpg",
                "metadata": json.dumps({
                    "screen_size": size,
                    "resolution": resolution,
                    "refresh_rate": refresh,
                    "panel_type": random.choice(["IPS", "VA", "TN"])
                }),
                "reviews_count": random.randint(50, 400),
                "rating": round(random.uniform(4.0, 5.0), 1),
            })
    
    elif product_type == "phone":
        brands = ["Samsung", "Google", "OnePlus", "Motorola", "Xiaomi"]
        models = ["Galaxy", "Pixel", "Nord", "Edge", "Redmi"]
        storage = ["64GB", "128GB", "256GB", "512GB"]
        colors = ["Black", "White", "Blue", "Green", "Red"]
        
        for i in range(count):
            brand = random.choice(brands)
            model = random.choice(models)
            storage_size = random.choice(storage)
            color = random.choice(colors)
            
            name = f"{brand} {model} {storage_size} {color}"
            price = random.uniform(300, 900)
            
            products.append({
                "product_id": str(uuid.uuid4()),
                "name": name,
                "description": f"{brand} {model} with {storage_size} storage. 5G capable with excellent camera.",
                "category": "Electronics",
                "subcategory": "Smartphone",
                "product_type": "phone",
                "brand": brand,
                "price": round(price, 2),
                "image_url": f"https://example.com/electronics/phone-{i+1}.jpg",
                "metadata": json.dumps({
                    "storage": storage_size,
                    "color": color,
                    "connectivity": "5G",
                    "screen_size": f"{random.uniform(6.0, 6.8):.1f}\""
                }),
                "reviews_count": random.randint(80, 500),
                "rating": round(random.uniform(4.0, 5.0), 1),
            })
    
    elif product_type == "accessory":
        items = [
            ("Wireless Mouse", 15, 80, "Logitech"),
            ("Mechanical Keyboard", 60, 200, "Razer"),
            ("USB-C Hub", 30, 100, "Anker"),
            ("Laptop Stand", 25, 80, "Rain Design"),
            ("Webcam", 40, 150, "Logitech"),
            ("Headphone Stand", 15, 50, "TwelveSouth"),
        ]
        
        for i in range(count):
            item_name, min_p, max_p, brand = random.choice(items)
            color = random.choice(["Black", "Silver", "White", "Gray"])
            
            name = f"{brand} {color} {item_name}"
            price = random.uniform(min_p, max_p)
            
            products.append({
                "product_id": str(uuid.uuid4()),
                "name": name,
                "description": f"High-quality {item_name.lower()} from {brand}. {color} finish.",
                "category": "Electronics",
                "subcategory": "Accessory",
                "product_type": "accessory",
                "brand": brand,
                "price": round(price, 2),
                "image_url": f"https://example.com/electronics/accessory-{i+1}.jpg",
                "reviews_count": random.randint(30, 300),
                "rating": round(random.uniform(4.0, 5.0), 1),
            })
    
    return products


def generate_jewelry(product_type: str, count: int):
    """Generate jewelry with Pandora, Tiffany, etc. and correct subcategories."""
    products = []
    types_data = {
        "Necklace": (["Pendant", "Chain", "Choker", "Lariat", "Statement"], (40, 250)),
        "Earrings": (["Stud", "Hoop", "Drop", "Chandelier", "Huggie"], (25, 180)),
        "Bracelet": (["Tennis", "Cuff", "Bangle", "Charm", "Chain"], (30, 200)),
        "Ring": (["Engagement", "Cocktail", "Stackable", "Signet", "Band"], (50, 400)),
        "Pendant": (["Heart", "Cross", "Initial", "Gemstone", "Locket"], (35, 150)),
    }
    prefixes, price_range = types_data.get(product_type, (["Classic"], (30, 150)))
    brands = ["Pandora", "Tiffany & Co", "Swarovski", "Kay Jewelers", "Zales"]
    for i in range(count):
        style = random.choice(prefixes)
        brand = random.choice(brands)
        name = f"{brand} {style} {product_type}"
        price = random.uniform(*price_range)
        products.append({
            "product_id": str(uuid.uuid4()),
            "name": name,
            "description": f"Elegant {product_type.lower()} from {brand}. {style} style.",
            "category": "Jewelry",
            "subcategory": product_type,
            "product_type": "jewelry",
            "brand": brand,
            "price": round(price, 2),
            "image_url": f"https://placehold.co/400x400?text={product_type}",
            "reviews_count": random.randint(10, 150),
            "rating": round(random.uniform(4.0, 5.0), 1),
        })
    return products


def generate_generic_products(category: str, count: int):
    """Generate generic products for various categories."""
    products = []
    
    configs = {
        "General": (["Miscellaneous Item", "General Product", "Utility Item"], 10, 50),
        "Home & Kitchen": (["Cookware Set", "Kitchen Gadget", "Storage Container", "Utensil Set"], 15, 80),
        "Jewelry": (["Necklace", "Earrings", "Pendant", "Chain", "Brooch"], 20, 200),
        "Shoes": (["Sneakers", "Sandals", "Boots", "Loafers", "Heels"], 40, 150),
        "Sports": (["Yoga Mat", "Dumbbells", "Resistance Bands", "Water Bottle", "Gym Bag"], 15, 80),
    }
    
    item_types, min_price, max_price = configs.get(category, (["Generic Item"], 10, 50))
    
    brands = {
        "Home & Kitchen": ["OXO", "KitchenAid", "Pyrex", "Cuisinart"],
        "Jewelry": ["Pandora", "Swarovski", "Tiffany & Co", "Kay Jewelers"],
        "Shoes": ["Nike", "Adidas", "Puma", "New Balance", "Skechers"],
        "Sports": ["Nike", "Adidas", "Under Armour", "Lululemon", "Reebok"],
    }
    
    brand_list = brands.get(category, ["Generic Brand", "Quality Co", "Best Products"])
    
    for i in range(count):
        item_type = random.choice(item_types)
        brand = random.choice(brand_list)
        color = random.choice(["Black", "White", "Blue", "Red", "Gray", "Silver"])
        
        name = f"{brand} {color} {item_type}"
        price = random.uniform(min_price, max_price)
        
        subcat = item_type if category == "Jewelry" else "General"
        products.append({
            "product_id": str(uuid.uuid4()),
            "name": name,
            "description": f"High-quality {item_type.lower()} from {brand}. Durable and stylish.",
            "category": category,
            "subcategory": subcat,
            "product_type": "generic",
            "brand": brand,
            "price": round(price, 2),
            "image_url": f"https://example.com/{category.lower().replace(' & ', '-')}/item-{i+1}.jpg",
            "reviews_count": random.randint(10, 200),
            "rating": round(random.uniform(3.8, 5.0), 1),
        })
    
    return products


def save_products_to_db(products: list) -> int:
    """Save generated products to database."""
    db = SessionLocal()
    saved_count = 0
    
    for prod_data in products:
        try:
            # Create product
            product = Product(
                product_id=prod_data["product_id"],
                name=prod_data["name"],
                description=prod_data.get("description", ""),
                category=prod_data["category"],
                subcategory=prod_data.get("subcategory"),
                product_type=prod_data.get("product_type", "generic"),
                brand=prod_data.get("brand"),
                image_url=prod_data.get("image_url"),
                metadata=prod_data.get("metadata"),
                tags=json.dumps([prod_data["category"], prod_data.get("subcategory", "")]),
            )
            db.add(product)
            db.flush()
            
            # Create price
            price = Price(
                product_id=product.product_id,
                price_cents=int(prod_data["price"] * 100),
                currency="USD"
            )
            db.add(price)
            
            # Create inventory
            inventory = Inventory(
                product_id=product.product_id,
                available_qty=random.randint(10, 100),
                reserved_qty=0
            )
            db.add(inventory)
            
            # Add reviews
            reviews = []
            for _ in range(prod_data.get("reviews_count", 10)):
                reviews.append({
                    "rating": random.randint(3, 5),
                    "comment": "Great product!",
                    "author": f"User{random.randint(1, 1000)}"
                })
            product.reviews = json.dumps(reviews[:random.randint(3, 8)])
            
            db.commit()
            saved_count += 1
            
        except Exception as e:
            print(f"Error saving product {prod_data.get('name', 'unknown')}: {e}")
            db.rollback()
            continue
    
    db.close()
    return saved_count


def main():
    """Fill all product gaps."""
    print("="*80)
    print("FILLING PRODUCT GAPS")
    print("="*80)
    
    # Define gaps to fill (category, product_type, target_count)
    gaps_to_fill = [
        ("Accessories", "Anklets", 12),
        ("Accessories", "Bracelets", 14),
        ("Accessories", "Carbon Offset", 12),
        ("Accessories", "Charms", 14),
        ("Accessories", "Rings", 12),
        ("Accessories", "generic", 14),
        ("Art", "Jumbo Party Pack", 12),
        ("Art", "Tiny Tin", 12),
        ("Beauty", "Bundle", 12),
        ("Beauty", "Eye Trio", 12),
        ("Beauty", "Fragrance", 12),
        ("Beauty", "Full Collection Set", 12),
        ("Beauty", "Jelly Much Duo", 12),
        ("Beauty", "Jelly Much Shadow", 12),
        ("Beauty", "Lip Trio", 12),
        ("Beauty", "Lippie Stix", 12),
        ("Beauty", "Lippie Stix Duo", 12),
        ("Beauty", "Mega Highlighter", 12),
        ("Beauty", "Palette Trio", 12),
        ("Beauty", "Powder To Creme Lipstick", 12),
        ("Beauty", "Sample", 12),
        ("Beauty", "Set", 12),
        ("Beauty", "Shadow Palette", 12),
        ("Beauty", "Foundation", 25),
        ("Beauty", "Mascara", 25),
        ("Beauty", "Blush", 25),
        ("Beauty", "Skincare", 25),
        ("Beauty", "Lipstick", 25),
        ("Beauty", "Eyeshadow", 25),
        ("Beauty", "generic", 10),
        ("Beauty", "shopify_product", 12),
        ("Clothing", "Accessories", 12),
        ("Clothing", "Dresses", 13),
        ("Clothing", "Graphic Tees", 12),
        ("Clothing", "Mens Pants", 12),
        ("Clothing", "Pants", 12),
        ("Clothing", "Shirts & Blouses", 12),
        ("Clothing", "Shorts", 12),
        ("Clothing", "Womens LS Tops", 12),
        ("Clothing", "Womens Pants", 12),
        ("Clothing", "Womens Shorts", 12),
        ("Clothing", "Womens Sleeveless Tops", 12),
        ("Clothing", "Womens Sports Bras", 12),
        ("Clothing", "Womens Tank", 12),
        ("Clothing", "womens Misc.", 12),
        ("Clothing", "womens Socks", 12),
        ("Electronics", "accessory", 14),
        ("Electronics", "monitor", 16),
        ("Electronics", "phone", 16),
        ("General", "generic", 18),
        ("Home & Kitchen", "generic", 17),
        ("Jewelry", "generic", 15),
        ("Jewelry", "Necklace", 25),
        ("Jewelry", "Earrings", 25),
        ("Jewelry", "Bracelet", 25),
        ("Jewelry", "Ring", 25),
        ("Jewelry", "Pendant", 20),
        ("Accessories", "Scarf", 20),
        ("Accessories", "Belt", 25),
        ("Accessories", "Bag", 20),
        ("Accessories", "Watch", 20),
        ("Accessories", "Sunglasses", 20),
        ("Shoes", "generic", 15),
        ("Sports", "generic", 17),
    ]
    
    total_added = 0
    
    for category, product_type, target in gaps_to_fill:
        print(f"\nGenerating {target} {category}/{product_type} products...")
        
        products = []
        
        if category == "Accessories":
            products = generate_accessories(product_type, target)
        elif category == "Jewelry":
            products = generate_jewelry(product_type, target)
        elif category == "Art":
            products = generate_art_products(product_type, target)
        elif category == "Beauty":
            products = generate_beauty_products(product_type, target)
        elif category == "Clothing":
            products = generate_clothing(product_type, target)
        elif category == "Electronics":
            products = generate_electronics_accessories(product_type, target)
        else:
            products = generate_generic_products(category, target)
        
        saved = save_products_to_db(products)
        print(f"  Added {saved}/{target} products")
        total_added += saved
    
    print("\n" + "="*80)
    print(f"TOTAL PRODUCTS ADDED: {total_added}")
    print("="*80)
    
    # Verify new totals
    db = SessionLocal()
    final_count = db.query(Product).count()
    db.close()
    
    print(f"\nFinal Database Total: {final_count} products")
    print("="*80)


if __name__ == "__main__":
    main()
