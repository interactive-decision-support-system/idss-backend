#!/usr/bin/env python3
"""
Scrape real books from Barnes & Noble and populate the database.

Usage:
    python scrape_barnes_noble_books.py [--max-books 50] [--urls URL1 URL2 ...]

Examples:
    # Scrape from default B&N URLs
    python scrape_barnes_noble_books.py
    
    # Scrape specific URLs
    python scrape_barnes_noble_books.py --urls "https://www.barnesandnoble.com/b/books/_/N-1fZ29Z8q8" "https://www.barnesandnoble.com/b/fiction/_/N-1fZ29Z8q8"
    
    # Limit number of books
    python scrape_barnes_noble_books.py --max-books 30
"""

import os
import sys
import argparse
from pathlib import Path
from typing import List

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.database import DATABASE_URL

# Import scraper
from scripts.product_scraper import scrape_products, ScrapedProduct
from scripts.populate_real_products import populate_database, generate_product_id, convert_scraped_to_dict

# Default B&N URLs for different book categories
DEFAULT_BARNES_NOBLE_URLS = [
    "https://www.barnesandnoble.com/b/books/_/N-1fZ29Z8q8",  # All Books
    "https://www.barnesandnoble.com/b/fiction/_/N-1fZ29Z8q8",  # Fiction
    "https://www.barnesandnoble.com/b/mystery-thriller/_/N-1fZ29Z8q8",  # Mystery & Thriller
    "https://www.barnesandnoble.com/b/science-fiction-fantasy/_/N-1fZ29Z8q8",  # Sci-Fi & Fantasy
    "https://www.barnesandnoble.com/b/non-fiction/_/N-1fZ29Z8q8",  # Non-Fiction
]


def main():
    parser = argparse.ArgumentParser(description="Scrape books from Barnes & Noble and populate database")
    parser.add_argument("--max-books", type=int, default=50, help="Maximum books to scrape per URL (default: 50)")
    parser.add_argument("--urls", nargs="+", help="Specific B&N URLs to scrape (overrides defaults)")
    parser.add_argument("--clear", action="store_true", help="Clear existing books before inserting")
    args = parser.parse_args()
    
    urls = args.urls if args.urls else DEFAULT_BARNES_NOBLE_URLS
    max_per_url = args.max_books
    
    print("=" * 70)
    print("Barnes & Noble Book Scraper")
    print("=" * 70)
    print(f"\nScraping from {len(urls)} URL(s)")
    print(f"Max books per URL: {max_per_url}")
    if args.clear:
        print("[WARN] Will clear existing books before inserting")
    print()
    
    # Scrape books
    print("Starting scraping...")
    all_products = []
    
    for url in urls:
        print(f"\nScraping: {url}")
        try:
            products = scrape_products([url], max_per_url=max_per_url)
            print(f"  [OK] Found {len(products)} books")
            all_products.extend(products)
        except Exception as e:
            print(f"  [FAIL] Error scraping {url}: {e}")
            continue
    
    if not all_products:
        print("\n[FAIL] No books found. Check URLs and network connection.")
        sys.exit(1)
    
    print(f"\n[OK] Total books scraped: {len(all_products)}")
    
    # Convert to dict format
    print("\nConverting to database format...")
    product_dicts = []
    for idx, product in enumerate(all_products):
        product_dict = convert_scraped_to_dict(product)
        # Ensure category is "Books"
        product_dict["category"] = "Books"
        # Ensure source is set
        product_dict["source"] = product_dict.get("source") or "Barnes & Noble"
        # Generate product_id
        product_dict["product_id"] = generate_product_id(
            "Books",
            product_dict.get("brand") or "Unknown",
            product_dict["name"],
            idx
        )
        product_dicts.append(product_dict)
    
    # Populate database
    print("\nPopulating database...")
    try:
        populate_database(product_dicts, clear_existing=args.clear)
        print(f"\n[OK] Successfully populated database with {len(product_dicts)} books!")
        print("\nYou can now search for books on the frontend!")
    except Exception as e:
        print(f"\n[FAIL] Error populating database: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
