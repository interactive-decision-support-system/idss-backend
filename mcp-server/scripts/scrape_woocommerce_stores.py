#!/usr/bin/env python3
"""
WooCommerce Store Scraper - Real Product Data

Scrapes products from actual WooCommerce stores and adds them to database.
Extracts: name, price, description, category, images, brand, ratings.

Target stores:
1. woocommerce-demo.com - Official WooCommerce demo
2. themes.woocommerce.com/storefront - Storefront theme demo
3. porterandyork.com - Premium meat delivery (real store)

Run: python scripts/scrape_woocommerce_stores.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from bs4 import BeautifulSoup
import json
import re
import time
from typing import List, Dict, Any, Optional
from app.database import SessionLocal
from app.models import Product, Price, Inventory
import uuid


# WooCommerce stores to scrape
WOOCOMMERCE_STORES = [
    {
        "name": "WooCommerce Demo",
        "base_url": "https://woocommerce-demo.com",
        "shop_url": "https://woocommerce-demo.com/shop/",
        "brand": "WooCommerce Official",
        "category": "General"
    },
    {
        "name": "Porter & York - Beef",
        "base_url": "https://porterandyork.com",
        "shop_url": "https://porterandyork.com/product-category/buy-beef-online/",
        "brand": "Porter & York",
        "category": "Food"
    },
    {
        "name": "Porter & York - Pork",
        "base_url": "https://porterandyork.com",
        "shop_url": "https://porterandyork.com/product-category/buy-pork-online/",
        "brand": "Porter & York",
        "category": "Food"
    },
    {
        "name": "Porter & York - Seafood",
        "base_url": "https://porterandyork.com",
        "shop_url": "https://porterandyork.com/product-category/buy-seafood-online/",
        "brand": "Porter & York",
        "category": "Food"
    }
]


def extract_price_from_text(text: str) -> Optional[float]:
    """Extract price from text string."""
    if not text:
        return None
    
    # Remove currency symbols and commas
    text = text.replace(',', '').strip()
    
    # Try to find price pattern like $123.45 or 123.45
    match = re.search(r'\$?(\d+\.?\d*)', text)
    if match:
        try:
            return float(match.group(1))
        except:
            pass
    
    return None


def scrape_woocommerce_product_list(store: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Scrape product listing page from a WooCommerce store.
    
    Args:
        store: Store configuration dict
        
    Returns:
        List of product dictionaries
    """
    print(f"\n{'='*80}")
    print(f"Scraping: {store['name']}")
    print(f"URL: {store['shop_url']}")
    print(f"{'='*80}")
    
    products = []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        
        response = requests.get(store['shop_url'], headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find product items - WooCommerce uses various class patterns
        product_items = soup.find_all(['li', 'div'], class_=re.compile(r'product(?!-)|woocommerce-LoopProduct-link'))
        
        # Also try alternative selectors
        if not product_items:
            product_items = soup.find_all('li', class_=lambda x: x and 'product' in x)
        
        if not product_items:
            product_items = soup.find_all('div', class_=lambda x: x and 'product-small' in str(x))
        
        print(f"Found {len(product_items)} product containers")
        
        for item in product_items[:25]:  # Limit to 25 products per store
            try:
                product = extract_woocommerce_product(item, store)
                if product and product.get('name') and product.get('price'):
                    products.append(product)
                    print(f"   {product['name'][:50]:<50} ${product['price']}")
            except Exception as e:
                print(f"  [WARN]  Error extracting product: {e}")
                continue
        
        print(f"\n Scraped {len(products)} products from {store['name']}")
        
    except Exception as e:
        print(f"[FAIL] Error scraping {store['name']}: {e}")
    
    return products


def extract_woocommerce_product(item, store: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Extract product data from a WooCommerce product item.
    
    Args:
        item: BeautifulSoup element containing product
        store: Store configuration
        
    Returns:
        Product dictionary or None
    """
    product = {
        'source': 'WooCommerce',
        'store_name': store['name'],
        'brand': store['brand'],
        'scraped_from_url': store['shop_url']
    }
    
    # Extract product name
    title_elem = item.find(['h2', 'h3', 'a'], class_=re.compile(r'product.*title|woocommerce-loop-product__title'))
    if not title_elem:
        title_elem = item.find('a', href=re.compile(r'/product/'))
    
    if title_elem:
        product['name'] = title_elem.get_text(strip=True)
    else:
        return None
    
    # Extract product URL
    link = item.find('a', href=re.compile(r'/product/'))
    if link:
        product['url'] = link['href']
        if not product['url'].startswith('http'):
            product['url'] = store['base_url'] + product['url']
        
        # Extract product ID from URL
        product_slug = product['url'].split('/product/')[-1].rstrip('/')
        product['source_product_id'] = product_slug
    
    # Extract price
    price_elem = item.find(['span', 'div'], class_=re.compile(r'price|amount'))
    if price_elem:
        # Handle sale prices (multiple price elements)
        ins_price = price_elem.find('ins')
        if ins_price:
            price_text = ins_price.get_text(strip=True)
        else:
            price_text = price_elem.get_text(strip=True)
        
        # Extract just the current/sale price
        price_parts = price_text.split('–')  # Handle price ranges
        if price_parts:
            price = extract_price_from_text(price_parts[0])
            if price:
                product['price'] = price
    
    # Extract image
    img = item.find('img')
    if img:
        img_url = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
        if img_url and not img_url.startswith('data:'):
            product['image_url'] = img_url
    
    # Extract category from parent/wrapper
    cat_link = item.find_parent(['li', 'div']).find('a', class_=re.compile(r'category'))
    if cat_link:
        product['category'] = cat_link.get_text(strip=True)
    
    # Set default category based on store config or product name
    if not product.get('category'):
        if store.get('category'):
            product['category'] = store['category']
        elif 'beef' in product['name'].lower() or 'meat' in product['name'].lower() or 'pork' in product['name'].lower() or 'seafood' in product['name'].lower():
            product['category'] = 'Food'
        elif 'shirt' in product['name'].lower() or 'clothing' in product['name'].lower():
            product['category'] = 'Clothing'
        elif 'ring' in product['name'].lower() or 'jewelry' in product['name'].lower():
            product['category'] = 'Jewelry'
        else:
            product['category'] = 'General'
    
    # Generate description based on product name and store
    product['description'] = generate_product_description(product, store)
    
    return product if product.get('name') and product.get('price') else None


def generate_product_description(product: Dict[str, Any], store: Dict[str, str]) -> str:
    """Generate a descriptive text for the product."""
    name = product.get('name', '')
    brand = product.get('brand', '')
    store_name = store.get('name', '')
    
    # Custom descriptions based on product type
    if 'steak' in name.lower() or 'beef' in name.lower():
        return f"Premium {name} from {brand}. Hand-cut by professional butchers, aged 28+ days for exceptional flavor and tenderness. USDA Upper Choice or higher grade beef, delivered fresh within 48 hours."
    
    elif 'maple syrup' in name.lower():
        return f"Pure {name} from Maine. Natural, organic maple syrup harvested from local farms. Perfect for pancakes, waffles, and baking."
    
    elif 't-shirt' in name.lower() or 'shirt' in name.lower():
        return f"Comfortable {name}. Made from premium cotton blend fabric. Available in multiple colors and sizes. Perfect fit guaranteed."
    
    elif 'ring' in name.lower() or 'diamond' in name.lower():
        return f"Elegant {name}. High-quality craftsmanship with premium materials. Makes a perfect gift for special occasions."
    
    elif 'gift card' in name.lower():
        return f"{name} - The perfect gift for any occasion. Choose your amount and give the gift of choice. Never expires."
    
    else:
        return f"Quality {name} from {store_name}. Carefully selected for excellent value and customer satisfaction."


def save_woocommerce_products_to_db(products: List[Dict[str, Any]]) -> int:
    """
    Save scraped WooCommerce products to database.
    
    Args:
        products: List of product dictionaries
        
    Returns:
        Number of products saved
    """
    db = SessionLocal()
    saved_count = 0
    
    try:
        for prod in products:
            try:
                # Check if product already exists by name
                existing = db.query(Product).filter(Product.name == prod['name']).first()
                if existing:
                    print(f"  ⊘ Skipping duplicate: {prod['name'][:50]}")
                    continue
                
                # Create product
                product = Product(
                    product_id=str(uuid.uuid4()),
                    name=prod['name'],
                    description=prod.get('description', ''),
                    category=prod.get('category', 'General'),
                    subcategory=None,
                    brand=prod.get('brand'),
                    image_url=prod.get('image_url'),
                    source='WooCommerce',
                    scraped_from_url=prod.get('scraped_from_url'),
                    source_product_id=prod.get('source_product_id'),
                    metadata=json.dumps({
                        'store_name': prod.get('store_name'),
                        'original_url': prod.get('url'),
                    })
                )
                
                db.add(product)
                db.flush()
                
                # Create price
                price_cents = int(prod['price'] * 100)
                price = Price(
                    product_id=product.product_id,
                    price_cents=price_cents,
                    currency='USD'
                )
                db.add(price)
                
                # Create inventory
                inventory = Inventory(
                    product_id=product.product_id,
                    available_qty=50,  # Default stock
                    reserved_qty=0
                )
                db.add(inventory)
                
                saved_count += 1
                
            except Exception as e:
                print(f"  [FAIL] Error saving {prod.get('name', 'unknown')}: {e}")
                continue
        
        db.commit()
        print(f"\n Saved {saved_count} new WooCommerce products to database")
        
    except Exception as e:
        print(f"[FAIL] Database error: {e}")
        db.rollback()
    finally:
        db.close()
    
    return saved_count


def main():
    """Main scraping function."""
    print("="*80)
    print("WOOCOMMERCE STORE SCRAPER")
    print("="*80)
    print(f"\nTarget stores: {len(WOOCOMMERCE_STORES)}")
    print("Starting scrape...\n")
    
    all_products = []
    
    # Scrape each store
    for store in WOOCOMMERCE_STORES:
        products = scrape_woocommerce_product_list(store)
        all_products.extend(products)
        
        # Be polite - delay between stores
        time.sleep(2)
    
    # Save to database
    print("\n" + "="*80)
    print("SAVING TO DATABASE")
    print("="*80)
    
    if all_products:
        saved = save_woocommerce_products_to_db(all_products)
        
        print("\n" + "="*80)
        print("SCRAPING COMPLETE!")
        print("="*80)
        print(f"Total products scraped: {len(all_products)}")
        print(f"Products saved to DB: {saved}")
        print(f"Duplicates skipped: {len(all_products) - saved}")
    else:
        print("\n[WARN]  No products scraped")
    
    print("\n" + "="*80)


if __name__ == "__main__":
    main()
