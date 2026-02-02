#!/usr/bin/env python3
"""
Integration with ShopifyScraper GitHub package for reliable Shopify scraping.

Uses: pip install git+https://github.com/practical-data-science/ShopifyScraper.git

This provides a more reliable way to scrape Shopify stores using the proven package.
"""

import sys
from pathlib import Path
from typing import List, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from shopify_scraper import scraper
    SHOPIFY_SCRAPER_AVAILABLE = True
except ImportError:
    SHOPIFY_SCRAPER_AVAILABLE = False
    print("[WARN] ShopifyScraper package not installed. Install with:")
    print("   pip install git+https://github.com/practical-data-science/ShopifyScraper.git")

from scripts.product_scraper import ScrapedProduct


def scrape_shopify_with_package(store_url: str, max_products: int = 50) -> List[ScrapedProduct]:
    """
    Scrape Shopify store using the ShopifyScraper GitHub package.
    
    This is more reliable than custom implementation because:
    - It's a proven package used by many projects
    - Handles edge cases and Shopify API quirks
    - Returns Pandas dataframes for easy processing
    
    Args:
        store_url: Shopify store URL (e.g., "https://store.myshopify.com")
        max_products: Maximum products to scrape
        
    Returns:
        List of ScrapedProduct objects
    """
    if not SHOPIFY_SCRAPER_AVAILABLE:
        raise ImportError("ShopifyScraper package not installed")
    
    print(f"Scraping {store_url} using ShopifyScraper package...")
    
        # Get products using the package
    try:
        parents_df = scraper.get_products(store_url)
        
        if parents_df is None or len(parents_df) == 0:
            print(f"[WARN]  No products found or store returned empty response")
            return []
        
        print(f"Found {len(parents_df)} products")
        
        # Get variants (for price/inventory) - may fail due to pandas compatibility
        children_df = None
        try:
            children_df = scraper.get_variants(parents_df) if len(parents_df) > 0 else None
        except (AttributeError, TypeError) as e:
            if 'iteritems' in str(e):
                print("[WARN]  ShopifyScraper package has pandas compatibility issue (iteritems)")
                print("   → Using products data only (variants may be missing)")
            else:
                raise
        
        # Get images - may also fail
        images_df = None
        try:
            images_df = scraper.get_images(parents_df) if len(parents_df) > 0 else None
        except (AttributeError, TypeError) as e:
            if 'iteritems' in str(e):
                print("[WARN]  Images extraction failed due to pandas compatibility")
            else:
                raise
        
        # Convert to ScrapedProduct format
        scraped_products = []
        
        # Use iterrows() which is compatible with pandas 2.0+
        for idx, row in parents_df.head(max_products).iterrows():
            # Get product data
            title = str(row.get('title', ''))
            body_html = str(row.get('body_html', ''))
            vendor = str(row.get('vendor', ''))
            product_type = str(row.get('product_type', ''))
            handle = str(row.get('handle', ''))
            
            # Get variant data (first variant for price)
            variant_price = None
            variant_inventory = None
            if children_df is not None and len(children_df) > 0:
                try:
                    # Try to match by parent_id
                    product_id = row.get('id', '')
                    if product_id:
                        # Use .loc or boolean indexing instead of .get() on DataFrame
                        if 'parent_id' in children_df.columns:
                            product_variants = children_df[children_df['parent_id'] == product_id]
                            if len(product_variants) > 0:
                                first_variant = product_variants.iloc[0]
                                variant_price = first_variant.get('price') if hasattr(first_variant, 'get') else first_variant.get('price', None)
                                variant_inventory = first_variant.get('inventory_quantity', 0) if hasattr(first_variant, 'get') else 0
                except Exception as e:
                    # If variant lookup fails, try to get price from row directly
                    variant_price = row.get('price') or row.get('min_price')
                    pass
            
            # Get image URL
            image_url = None
            if images_df is not None and len(images_df) > 0:
                try:
                    product_id = row.get('id', '')
                    if product_id and 'parent_id' in images_df.columns:
                        product_images = images_df[images_df['parent_id'] == product_id]
                        if len(product_images) > 0:
                            first_image = product_images.iloc[0]
                            image_url = first_image.get('src') if hasattr(first_image, 'get') else first_image.get('src', None)
                except Exception:
                    # Fallback: try to get image from row directly
                    image_url = row.get('image') or row.get('featured_image')
                    pass
            
            # Parse price - try variant first, then row data
            price_cents = None
            if variant_price:
                try:
                    price_float = float(str(variant_price))
                    price_cents = int(price_float * 100)
                except (ValueError, TypeError):
                    pass
            
            # Fallback: try to get price from row directly
            if not price_cents:
                for price_key in ['price', 'min_price', 'max_price', 'variants_price']:
                    price_val = row.get(price_key)
                    if price_val:
                        try:
                            price_float = float(str(price_val))
                            price_cents = int(price_float * 100)
                            break
                        except (ValueError, TypeError):
                            continue
            
            if not price_cents:
                continue  # Skip products without price
            
            # Build product URL
            product_url = f"{store_url}/products/{handle}" if handle else store_url
            
            # Determine category
            category = "Electronics"  # Default
            if product_type:
                category = product_type
            
            scraped_products.append(
                ScrapedProduct(
                    name=title,
                    description=body_html[:500] if body_html else title,
                    price_cents=price_cents,
                    category=category,
                    brand=vendor if vendor else None,
                    image_url=image_url,
                    available_qty=int(variant_inventory) if variant_inventory else 0,
                    source_url=product_url,
                    source="Shopify",
                )
            )
        
        print(f"[OK] Converted {len(scraped_products)} products")
        return scraped_products
        
    except Exception as e:
        error_msg = str(e)
        print(f"[FAIL] Error scraping with ShopifyScraper package: {error_msg}")
        
        # Check for pandas compatibility issue
        if "iteritems" in error_msg:
            print("   → Pandas compatibility issue: ShopifyScraper package uses deprecated 'iteritems()'")
            print("   → This is a known issue with pandas 3.0+")
            print("   → Falling back to custom implementation (works fine)")
            return []  # Let custom implementation handle it
        
        # Check for common errors
        if "401" in error_msg or "Unauthorized" in error_msg:
            print("   → Store may require authentication or doesn't exist")
            print("   → Try a different Shopify store URL")
        elif "404" in error_msg or "Not Found" in error_msg:
            print("   → Store URL not found - check the URL")
        elif "your-store" in store_url.lower():
            print("   → You're using a placeholder URL!")
            print("   → Replace 'your-store.myshopify.com' with a real Shopify store URL")
        
        return []


if __name__ == "__main__":
    # Test scraping
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python shopify_scraper_integration.py <shopify_store_url> [max_products]")
        sys.exit(1)
    
    store_url = sys.argv[1]
    max_products = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    
    products = scrape_shopify_with_package(store_url, max_products)
    
    print(f"\nScraped {len(products)} products:")
    for p in products[:5]:
        print(f"  - {p.name}: ${p.price_cents/100:.2f} (Source: {p.source})")
