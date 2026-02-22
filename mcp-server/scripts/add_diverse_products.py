"""
Add diverse products to PostgreSQL database for testing.

Adds:
- 40+ Books (various genres, authors, formats)
- 40+ Electronics (laptops, tablets, phones, accessories)

Run from mcp-server directory:
    python scripts/add_diverse_products.py
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.database import SessionLocal, engine, Base
from app.models import Product, Price, Inventory
import uuid

def generate_product_id(category, name):
    """Generate unique product ID."""
    prefix = "book" if category == "Books" else "elec"
    short_name = name.lower().replace(" ", "-")[:20]
    unique = uuid.uuid4().hex[:6]
    return f"{prefix}-{short_name}-{unique}"

def add_books():
    """Add diverse book collection."""
    books = [
        # Fiction - Bestsellers
        {"name": "The Midnight Library", "author": "Matt Haig", "genre": "Fiction", "format": "Hardcover", "price": 2699, "description": "A dazzling novel about the choices that go into a life well lived.", "publisher": "Viking"},
        {"name": "Where the Crawdads Sing", "author": "Delia Owens", "genre": "Fiction", "format": "Paperback", "price": 1799, "description": "A murder mystery and coming-of-age story set in the marshes of North Carolina.", "publisher": "Putnam"},
        {"name": "The Seven Husbands of Evelyn Hugo", "author": "Taylor Jenkins Reid", "genre": "Fiction", "format": "Paperback", "price": 1699, "description": "A captivating story of Hollywood's golden age.", "publisher": "Atria"},
        {"name": "Circe", "author": "Madeline Miller", "genre": "Fiction", "format": "Hardcover", "price": 2799, "description": "The enchanting story of the goddess Circe from Greek mythology.", "publisher": "Little Brown"},
        {"name": "The Song of Achilles", "author": "Madeline Miller", "genre": "Fiction", "format": "Paperback", "price": 1599, "description": "A retelling of the Trojan War from Patroclus's perspective.", "publisher": "Ecco"},
        
        # Mystery & Thriller
        {"name": "The Silent Patient", "author": "Alex Michaelides", "genre": "Mystery", "format": "Paperback", "price": 1699, "description": "A woman's act of violence against her husband—and of the therapist obsessed with uncovering her motive.", "publisher": "Celadon"},
        {"name": "Gone Girl", "author": "Gillian Flynn", "genre": "Mystery", "format": "Paperback", "price": 1599, "description": "A psychological thriller about a marriage gone terribly wrong.", "publisher": "Crown"},
        {"name": "The Guest List", "author": "Lucy Foley", "genre": "Mystery", "format": "Hardcover", "price": 2799, "description": "A murder mystery set at a glamorous wedding on a remote Irish island.", "publisher": "Morrow"},
        {"name": "The Thursday Murder Club", "author": "Richard Osman", "genre": "Mystery", "format": "Paperback", "price": 1799, "description": "Four unlikely friends investigate murders in a British retirement village.", "publisher": "Pamela Dorman"},
        {"name": "The Woman in the Window", "author": "A.J. Finn", "genre": "Mystery", "format": "Paperback", "price": 1699, "description": "An agoraphobic woman believes she witnessed a crime.", "publisher": "Morrow"},
        
        # Science Fiction
        {"name": "Project Hail Mary", "author": "Andy Weir", "genre": "Sci-Fi", "format": "Hardcover", "price": 2899, "description": "A lone astronaut must save humanity from extinction.", "publisher": "Ballantine"},
        {"name": "The Three-Body Problem", "author": "Cixin Liu", "genre": "Sci-Fi", "format": "Paperback", "price": 1699, "description": "China's secret military project sends signals into space.", "publisher": "Tor"},
        {"name": "Dune", "author": "Frank Herbert", "genre": "Sci-Fi", "format": "Paperback", "price": 1899, "description": "The epic story of Paul Atreides on the desert planet Arrakis.", "publisher": "Ace"},
        {"name": "The Martian", "author": "Andy Weir", "genre": "Sci-Fi", "format": "Paperback", "price": 1599, "description": "An astronaut is stranded on Mars and must survive.", "publisher": "Crown"},
        {"name": "Foundation", "author": "Isaac Asimov", "genre": "Sci-Fi", "format": "Paperback", "price": 1699, "description": "The collapse and rebirth of galactic civilization.", "publisher": "Spectra"},
        
        # Romance
        {"name": "Beach Read", "author": "Emily Henry", "genre": "Romance", "format": "Paperback", "price": 1699, "description": "Two writers challenge each other to write in the other's genre.", "publisher": "Berkley"},
        {"name": "People We Meet on Vacation", "author": "Emily Henry", "genre": "Romance", "format": "Paperback", "price": 1699, "description": "Two best friends take one last trip to see if they can be more.", "publisher": "Berkley"},
        {"name": "The Love Hypothesis", "author": "Ali Hazelwood", "genre": "Romance", "format": "Paperback", "price": 1599, "description": "A fake-dating experiment between two Stanford scientists.", "publisher": "Berkley"},
        {"name": "Red, White & Royal Blue", "author": "Casey McQuiston", "genre": "Romance", "format": "Paperback", "price": 1699, "description": "The First Son falls for the Prince of Wales.", "publisher": "St. Martin's Griffin"},
        
        # Non-Fiction
        {"name": "Educated", "author": "Tara Westover", "genre": "Non-fiction", "format": "Paperback", "price": 1799, "description": "A memoir about a woman who leaves her survivalist family to pursue education.", "publisher": "Random House"},
        {"name": "Atomic Habits", "author": "James Clear", "genre": "Non-fiction", "format": "Hardcover", "price": 2799, "description": "Tiny changes, remarkable results. An easy & proven way to build good habits.", "publisher": "Avery"},
        {"name": "Sapiens", "author": "Yuval Noah Harari", "genre": "Non-fiction", "format": "Paperback", "price": 1899, "description": "A brief history of humankind.", "publisher": "Harper"},
        {"name": "Thinking, Fast and Slow", "author": "Daniel Kahneman", "genre": "Non-fiction", "format": "Paperback", "price": 1799, "description": "The two systems that drive the way we think.", "publisher": "Farrar Straus"},
        {"name": "The Power of Now", "author": "Eckhart Tolle", "genre": "Non-fiction", "format": "Paperback", "price": 1699, "description": "A guide to spiritual enlightenment.", "publisher": "New World Library"},
        
        # Fantasy
        {"name": "The Name of the Wind", "author": "Patrick Rothfuss", "genre": "Fantasy", "format": "Paperback", "price": 1899, "description": "The tale of Kvothe, a magically gifted young man.", "publisher": "DAW"},
        {"name": "The Hobbit", "author": "J.R.R. Tolkien", "genre": "Fantasy", "format": "Paperback", "price": 1599, "description": "Bilbo Baggins's unexpected adventure.", "publisher": "Mariner"},
        {"name": "A Court of Thorns and Roses", "author": "Sarah J. Maas", "genre": "Fantasy", "format": "Paperback", "price": 1799, "description": "A retelling of Beauty and the Beast in a faerie world.", "publisher": "Bloomsbury"},
        {"name": "The Way of Kings", "author": "Brandon Sanderson", "genre": "Fantasy", "format": "Hardcover", "price": 2999, "description": "Epic fantasy in a world of storms and magic.", "publisher": "Tor"},
        {"name": "The Fifth Season", "author": "N.K. Jemisin", "genre": "Fantasy", "format": "Paperback", "price": 1699, "description": "A world ending in earthquakes, and those who can stop it.", "publisher": "Orbit"},
        
        # Young Adult
        {"name": "The Hunger Games", "author": "Suzanne Collins", "genre": "Young Adult", "format": "Paperback", "price": 1299, "description": "Survival of the fittest in a dystopian televised battle.", "publisher": "Scholastic"},
        {"name": "Six of Crows", "author": "Leigh Bardugo", "genre": "Young Adult", "format": "Paperback", "price": 1599, "description": "A heist story in a fantasy world.", "publisher": "Holt"},
        {"name": "The Fault in Our Stars", "author": "John Green", "genre": "Young Adult", "format": "Paperback", "price": 1399, "description": "A poignant love story between two cancer patients.", "publisher": "Dutton"},
        
        # Business & Tech
        {"name": "Zero to One", "author": "Peter Thiel", "genre": "Business", "format": "Hardcover", "price": 2799, "description": "Notes on startups and building the future.", "publisher": "Crown Business"},
        {"name": "The Lean Startup", "author": "Eric Ries", "genre": "Business", "format": "Paperback", "price": 1899, "description": "How today's entrepreneurs use continuous innovation.", "publisher": "Crown Business"},
        {"name": "Deep Work", "author": "Cal Newport", "genre": "Business", "format": "Paperback", "price": 1699, "description": "Rules for focused success in a distracted world.", "publisher": "Grand Central"},
        
        # Historical Fiction
        {"name": "All the Light We Cannot See", "author": "Anthony Doerr", "genre": "Historical Fiction", "format": "Paperback", "price": 1799, "description": "WWII story of a blind French girl and a German boy.", "publisher": "Scribner"},
        {"name": "The Book Thief", "author": "Markus Zusak", "genre": "Historical Fiction", "format": "Paperback", "price": 1599, "description": "Death narrates a young girl's story in Nazi Germany.", "publisher": "Knopf"},
        {"name": "The Nightingale", "author": "Kristin Hannah", "genre": "Historical Fiction", "format": "Paperback", "price": 1799, "description": "Two sisters in France during WWII.", "publisher": "St. Martin's Press"},
        
        # Horror
        {"name": "The Shining", "author": "Stephen King", "genre": "Horror", "format": "Paperback", "price": 1699, "description": "A family's terrifying experience at an isolated hotel.", "publisher": "Doubleday"},
        {"name": "Mexican Gothic", "author": "Silvia Moreno-Garcia", "genre": "Horror", "format": "Hardcover", "price": 2699, "description": "Gothic horror in 1950s Mexico.", "publisher": "Del Rey"},
        
        # Biography
        {"name": "Steve Jobs", "author": "Walter Isaacson", "genre": "Biography", "format": "Hardcover", "price": 3499, "description": "The exclusive biography of Apple's co-founder.", "publisher": "Simon & Schuster"},
        {"name": "Becoming", "author": "Michelle Obama", "genre": "Biography", "format": "Hardcover", "price": 3299, "description": "Memoir of the former First Lady.", "publisher": "Crown"},
    ]
    
    return books

def add_electronics():
    """Add diverse electronics collection."""
    electronics = [
        # Gaming Laptops
        {"name": "ASUS ROG Strix G16", "brand": "ASUS", "product_type": "gaming_laptop", "subcategory": "Gaming", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 4070", "color": "Eclipse Gray", "price": 179999, "description": "Intel Core i9-13980HX, 32GB DDR5, 1TB SSD, 16\" 240Hz display", "tags": ["gaming", "high-performance"]},
        {"name": "MSI Raider GE78 HX", "brand": "MSI", "product_type": "gaming_laptop", "subcategory": "Gaming", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 4080", "color": "Black", "price": 249999, "description": "Intel Core i9, 64GB RAM, 2TB SSD, 17\" 240Hz", "tags": ["gaming", "premium"]},
        {"name": "Razer Blade 15", "brand": "Razer", "product_type": "gaming_laptop", "subcategory": "Gaming", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 4070", "color": "Black", "price": 209999, "description": "Intel Core i7, 16GB RAM, 1TB SSD, 15.6\" QHD 240Hz", "tags": ["gaming", "portable"]},
        {"name": "Alienware x17 R2", "brand": "Alienware", "product_type": "gaming_laptop", "subcategory": "Gaming", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 4090", "color": "Lunar Light", "price": 329999, "description": "Intel Core i9, 32GB RAM, 2TB SSD, 17.3\" 4K 120Hz", "tags": ["gaming", "luxury"]},
        {"name": "Lenovo Legion Pro 7i", "brand": "Lenovo", "product_type": "gaming_laptop", "subcategory": "Gaming", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 4080", "color": "Storm Grey", "price": 229999, "description": "Intel Core i9, 32GB RAM, 1TB SSD, 16\" WQXGA 240Hz", "tags": ["gaming", "performance"]},
        
        # Work/Productivity Laptops
        {"name": "Dell XPS 13 Plus", "brand": "Dell", "product_type": "laptop", "subcategory": "Work", "gpu_vendor": "Intel", "gpu_model": "Iris Xe", "color": "Platinum", "price": 139999, "description": "Intel Core i7, 16GB RAM, 512GB SSD, 13.4\" OLED", "tags": ["productivity", "premium"]},
        {"name": "Dell XPS 15", "brand": "Dell", "product_type": "laptop", "subcategory": "Work", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 3050", "color": "Silver", "price": 179999, "description": "Intel Core i7, 16GB RAM, 512GB SSD, 15.6\" 4K OLED", "tags": ["productivity", "creative"]},
        {"name": "HP Spectre x360 14", "brand": "HP", "product_type": "laptop", "subcategory": "Work", "gpu_vendor": "Intel", "gpu_model": "Iris Xe", "color": "Nightfall Black", "price": 149999, "description": "Intel Core i7, 16GB RAM, 1TB SSD, 13.5\" 3K2K OLED", "tags": ["productivity", "2-in-1"]},
        {"name": "HP EliteBook 840 G9", "brand": "HP", "product_type": "laptop", "subcategory": "Work", "gpu_vendor": "Intel", "gpu_model": "Iris Xe", "color": "Silver", "price": 129999, "description": "Intel Core i7, 16GB RAM, 512GB SSD, 14\" FHD", "tags": ["business", "durable"]},
        {"name": "Lenovo ThinkPad X1 Carbon Gen 11", "brand": "Lenovo", "product_type": "laptop", "subcategory": "Work", "gpu_vendor": "Intel", "gpu_model": "Iris Xe", "color": "Black", "price": 159999, "description": "Intel Core i7, 16GB RAM, 512GB SSD, 14\" WUXGA", "tags": ["business", "lightweight"]},
        {"name": "Lenovo Yoga 9i", "brand": "Lenovo", "product_type": "laptop", "subcategory": "Work", "gpu_vendor": "Intel", "gpu_model": "Iris Xe", "color": "Oat", "price": 144999, "description": "Intel Core i7, 16GB RAM, 512GB SSD, 14\" 4K OLED", "tags": ["productivity", "2-in-1"]},
        
        # MacBooks
        {"name": "MacBook Air M3", "brand": "Apple", "product_type": "laptop", "subcategory": "Work", "gpu_vendor": "Apple", "gpu_model": "M3", "color": "Midnight", "price": 129999, "description": "Apple M3, 16GB RAM, 512GB SSD, 13.6\" Liquid Retina", "tags": ["productivity", "lightweight"]},
        {"name": "MacBook Pro 14 M3 Pro", "brand": "Apple", "product_type": "laptop", "subcategory": "Creative", "gpu_vendor": "Apple", "gpu_model": "M3 Pro", "color": "Space Black", "price": 229999, "description": "Apple M3 Pro, 18GB RAM, 1TB SSD, 14.2\" Liquid Retina XDR", "tags": ["creative", "premium"]},
        {"name": "MacBook Pro 16 M3 Max", "brand": "Apple", "product_type": "laptop", "subcategory": "Creative", "gpu_vendor": "Apple", "gpu_model": "M3 Max", "color": "Space Black", "price": 349999, "description": "Apple M3 Max, 36GB RAM, 1TB SSD, 16.2\" Liquid Retina XDR", "tags": ["creative", "luxury"]},
        
        # Budget/School Laptops
        {"name": "ASUS VivoBook 15", "brand": "ASUS", "product_type": "laptop", "subcategory": "School", "gpu_vendor": "Intel", "gpu_model": "UHD Graphics", "color": "Silver", "price": 54999, "description": "Intel Core i5, 8GB RAM, 512GB SSD, 15.6\" FHD", "tags": ["budget", "student"]},
        {"name": "Acer Aspire 5", "brand": "Acer", "product_type": "laptop", "subcategory": "School", "gpu_vendor": "Intel", "gpu_model": "Iris Xe", "color": "Silver", "price": 59999, "description": "Intel Core i5, 8GB RAM, 512GB SSD, 15.6\" FHD", "tags": ["budget", "student"]},
        {"name": "HP Pavilion 15", "brand": "HP", "product_type": "laptop", "subcategory": "School", "gpu_vendor": "Intel", "gpu_model": "Iris Xe", "color": "Natural Silver", "price": 64999, "description": "Intel Core i5, 8GB RAM, 512GB SSD, 15.6\" FHD", "tags": ["budget", "multimedia"]},
        {"name": "Lenovo IdeaPad 3", "brand": "Lenovo", "product_type": "laptop", "subcategory": "School", "gpu_vendor": "Intel", "gpu_model": "UHD Graphics", "color": "Arctic Grey", "price": 49999, "description": "Intel Core i5, 8GB RAM, 256GB SSD, 15.6\" FHD", "tags": ["budget", "student"]},
        
        # Tablets
        {"name": "iPad Pro 12.9 M2", "brand": "Apple", "product_type": "tablet", "subcategory": "Creative", "gpu_vendor": "Apple", "gpu_model": "M2", "color": "Space Gray", "price": 119999, "description": "Apple M2, 8GB RAM, 256GB, 12.9\" Liquid Retina XDR", "tags": ["tablet", "premium"]},
        {"name": "iPad Air M1", "brand": "Apple", "product_type": "tablet", "subcategory": "Work", "gpu_vendor": "Apple", "gpu_model": "M1", "color": "Starlight", "price": 59999, "description": "Apple M1, 8GB RAM, 256GB, 10.9\" Liquid Retina", "tags": ["tablet", "portable"]},
        {"name": "Samsung Galaxy Tab S9 Ultra", "brand": "Samsung", "product_type": "tablet", "subcategory": "Creative", "gpu_vendor": "Qualcomm", "gpu_model": "Adreno 740", "color": "Graphite", "price": 119999, "description": "Snapdragon 8 Gen 2, 12GB RAM, 256GB, 14.6\" AMOLED", "tags": ["tablet", "android"]},
        {"name": "Microsoft Surface Pro 9", "brand": "Microsoft", "product_type": "tablet", "subcategory": "Work", "gpu_vendor": "Intel", "gpu_model": "Iris Xe", "color": "Platinum", "price": 99999, "description": "Intel Core i5, 8GB RAM, 256GB SSD, 13\" PixelSense", "tags": ["tablet", "2-in-1"]},
        
        # Phones
        {"name": "iPhone 15 Pro Max", "brand": "Apple", "product_type": "phone", "subcategory": "Mobile", "gpu_vendor": "Apple", "gpu_model": "A17 Pro", "color": "Titanium Blue", "price": 119999, "description": "256GB, 6.7\" Super Retina XDR, Pro camera system", "tags": ["smartphone", "premium"]},
        {"name": "iPhone 15", "brand": "Apple", "product_type": "phone", "subcategory": "Mobile", "gpu_vendor": "Apple", "gpu_model": "A16 Bionic", "color": "Pink", "price": 79999, "description": "128GB, 6.1\" Super Retina XDR", "tags": ["smartphone", "standard"]},
        {"name": "Samsung Galaxy S24 Ultra", "brand": "Samsung", "product_type": "phone", "subcategory": "Mobile", "gpu_vendor": "Qualcomm", "gpu_model": "Adreno 750", "color": "Titanium Black", "price": 119999, "description": "256GB, 6.8\" Dynamic AMOLED 2X, S Pen", "tags": ["smartphone", "premium"]},
        {"name": "Google Pixel 8 Pro", "brand": "Google", "product_type": "phone", "subcategory": "Mobile", "gpu_vendor": "Google", "gpu_model": "Tensor G3", "color": "Obsidian", "price": 99999, "description": "256GB, 6.7\" LTPO OLED, AI camera", "tags": ["smartphone", "photography"]},
        
        # Desktop PCs
        {"name": "Alienware Aurora R15", "brand": "Alienware", "product_type": "desktop_pc", "subcategory": "Gaming", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 4080", "color": "Dark Side of the Moon", "price": 249999, "description": "Intel Core i9, 32GB RAM, 2TB SSD, RTX 4080", "tags": ["gaming", "desktop"]},
        {"name": "HP Omen 45L", "brand": "HP", "product_type": "desktop_pc", "subcategory": "Gaming", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 4070 Ti", "color": "Black", "price": 199999, "description": "Intel Core i7, 32GB RAM, 1TB SSD, RTX 4070 Ti", "tags": ["gaming", "desktop"]},
        {"name": "ASUS ROG Strix G35", "brand": "ASUS", "product_type": "desktop_pc", "subcategory": "Gaming", "gpu_vendor": "NVIDIA", "gpu_model": "RTX 4090", "color": "Black", "price": 329999, "description": "Intel Core i9, 64GB RAM, 2TB SSD, RTX 4090", "tags": ["gaming", "premium"]},
        
        # Accessories
        {"name": "Sony WH-1000XM5", "brand": "Sony", "product_type": "accessory", "subcategory": "Audio", "gpu_vendor": None, "gpu_model": None, "color": "Black", "price": 39999, "description": "Premium noise-cancelling headphones, 30hr battery", "tags": ["audio", "wireless"]},
        {"name": "Apple AirPods Pro 2", "brand": "Apple", "product_type": "accessory", "subcategory": "Audio", "gpu_vendor": None, "gpu_model": None, "color": "White", "price": 24999, "description": "Active noise cancellation, spatial audio", "tags": ["audio", "wireless"]},
        {"name": "Logitech MX Master 3S", "brand": "Logitech", "product_type": "accessory", "subcategory": "Peripherals", "gpu_vendor": None, "gpu_model": None, "color": "Graphite", "price": 9999, "description": "Wireless ergonomic mouse, 8K DPI", "tags": ["productivity", "mouse"]},
        {"name": "Keychron K2 V2", "brand": "Keychron", "product_type": "accessory", "subcategory": "Peripherals", "gpu_vendor": None, "gpu_model": None, "color": "RGB", "price": 8999, "description": "Wireless mechanical keyboard, hot-swappable", "tags": ["productivity", "keyboard"]},
        {"name": "Samsung T7 Shield 2TB", "brand": "Samsung", "product_type": "accessory", "subcategory": "Storage", "gpu_vendor": None, "gpu_model": None, "color": "Black", "price": 19999, "description": "Portable SSD, 1050MB/s, rugged design", "tags": ["storage", "portable"]},
        {"name": "Anker 737 Power Bank", "brand": "Anker", "product_type": "accessory", "subcategory": "Power", "gpu_vendor": None, "gpu_model": None, "color": "Black", "price": 14999, "description": "24,000mAh, 140W output, fast charging", "tags": ["power", "portable"]},
        
        # Monitors
        {"name": "Dell UltraSharp U2723DE", "brand": "Dell", "product_type": "monitor", "subcategory": "Work", "gpu_vendor": None, "gpu_model": None, "color": "Silver", "price": 54999, "description": "27\" QHD IPS, USB-C hub, 99% sRGB", "tags": ["monitor", "productivity"]},
        {"name": "LG 27GN950-B", "brand": "LG", "product_type": "monitor", "subcategory": "Gaming", "gpu_vendor": None, "gpu_model": None, "color": "Black", "price": 69999, "description": "27\" 4K Nano IPS, 144Hz, G-SYNC", "tags": ["monitor", "gaming"]},
        {"name": "Samsung Odyssey OLED G8", "brand": "Samsung", "product_type": "monitor", "subcategory": "Gaming", "gpu_vendor": None, "gpu_model": None, "color": "Black", "price": 129999, "description": "34\" OLED, 175Hz, 0.1ms, curved", "tags": ["monitor", "premium"]},
    ]
    
    return electronics

def seed_all_products():
    """Seed all products into database."""
    db = SessionLocal()
    
    try:
        print("=" * 80)
        print("ADDING DIVERSE PRODUCTS TO DATABASE")
        print("=" * 80)
        
        # Add books
        books = add_books()
        print(f"\nAdding {len(books)} books...")
        
        for book in books:
            product_id = generate_product_id("Books", book["name"])
            
            # Check if already exists
            existing = db.query(Product).filter(Product.product_id == product_id).first()
            if existing:
                print(f"  [WARN] Skipping {book['name']} (already exists)")
                continue
            
            product = Product(
                product_id=product_id,
                name=book["name"],
                description=book["description"],
                category="Books",
                subcategory=book["genre"],
                brand=book.get("author", "Unknown"),
                product_type="book",
                color=None,
                gpu_vendor=None,
                gpu_model=None,
                tags=[book["genre"].lower(), book["format"].lower()],
                image_url=f"https://example.com/books/{product_id}.jpg"
            )
            
            price = Price(
                product_id=product_id,
                price_cents=book["price"],
                currency="USD"
            )
            
            inventory = Inventory(
                product_id=product_id,
                available_qty=25 if book["format"] == "Paperback" else 15
            )
            
            db.add(product)
            db.add(price)
            db.add(inventory)
            
            print(f"   Added: {book['name']} by {book.get('author', 'Unknown')} (${book['price']/100:.2f})")
        
        db.commit()
        print(f"\n Successfully added {len(books)} books")
        
        # Add electronics
        electronics = add_electronics()
        print(f"\nAdding {len(electronics)} electronics...")
        
        for elec in electronics:
            product_id = generate_product_id("Electronics", elec["name"])
            
            # Check if already exists
            existing = db.query(Product).filter(Product.product_id == product_id).first()
            if existing:
                print(f"  [WARN] Skipping {elec['name']} (already exists)")
                continue
            
            product = Product(
                product_id=product_id,
                name=elec["name"],
                description=elec["description"],
                category="Electronics",
                subcategory=elec.get("subcategory"),
                brand=elec["brand"],
                product_type=elec.get("product_type", "laptop"),
                color=elec.get("color"),
                gpu_vendor=elec.get("gpu_vendor"),
                gpu_model=elec.get("gpu_model"),
                tags=elec.get("tags", []),
                image_url=f"https://example.com/electronics/{product_id}.jpg"
            )
            
            price = Price(
                product_id=product_id,
                price_cents=elec["price"],
                currency="USD"
            )
            
            inventory = Inventory(
                product_id=product_id,
                available_qty=15
            )
            
            db.add(product)
            db.add(price)
            db.add(inventory)
            
            print(f"   Added: {elec['name']} - {elec['brand']} (${elec['price']/100:.2f})")
        
        db.commit()
        print(f"\n Successfully added {len(electronics)} electronics")
        
        # Final count
        total_books = db.query(Product).filter(Product.category == "Books").count()
        total_electronics = db.query(Product).filter(Product.category == "Electronics").count()
        
        print("\n" + "=" * 80)
        print("DATABASE SUMMARY")
        print("=" * 80)
        print(f"Total Books: {total_books}")
        print(f"Total Electronics: {total_electronics}")
        print(f"Grand Total: {total_books + total_electronics}")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed_all_products()
