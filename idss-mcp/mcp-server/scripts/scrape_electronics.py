"""
Web Scraper for Real Electronics Products.

Per week4notes.txt: Use real product data, not synthetic.
Scrapes from: Amazon, Shopify, Temu, eBay, WooCommerce stores.

Focus: Electronics (laptops, components, accessories).
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import re

logger = logging.getLogger(__name__)


class ElectronicsScraper:
    """
    Scraper for real electronics products from various e-commerce platforms.
    
    Per week4notes.txt: Focus on electronics, use real data.
    """
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
    
    def scrape_amazon_laptops(self, max_pages: int = 5) -> List[Dict[str, Any]]:
        """
        Scrape laptop products from Amazon.
        
        Note: Amazon has strong anti-scraping measures. This may return 503 errors.
        For production, use Amazon Product Advertising API instead.
        
        Args:
            max_pages: Maximum number of pages to scrape
        
        Returns:
            List of product dictionaries
        """
        products = []
        
        try:
            # Amazon search URL for laptops
            base_url = "https://www.amazon.com/s?k=laptops&page={}"
            
            for page in range(1, max_pages + 1):
                url = base_url.format(page)
                logger.info(f"Scraping Amazon page {page}: {url}")
                
                try:
                    # Add delay to avoid rate limiting
                    time.sleep(2)
                    response = self.session.get(url, timeout=10)
                    
                    # Handle 503 errors (Amazon blocking)
                    if response.status_code == 503:
                        logger.warning(f"Amazon returned 503 (blocked) for page {page}. Amazon has anti-scraping measures.")
                        logger.info("Tip: Use Amazon Product Advertising API for production scraping.")
                        continue
                    
                    response.raise_for_status()
                    
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    # Find product containers (Amazon's structure)
                    product_divs = soup.find_all('div', {'data-component-type': 's-search-result'})
                    
                    for div in product_divs:
                        product = self._parse_amazon_product(div)
                        if product:
                            products.append(product)
                    
                    # Rate limiting
                    time.sleep(2)
                    
                except Exception as e:
                    logger.error(f"Error scraping Amazon page {page}: {e}")
                    continue
            
            logger.info(f"Scraped {len(products)} products from Amazon")
            return products
            
        except Exception as e:
            logger.error(f"Amazon scraping failed: {e}")
            return products
    
    def _parse_amazon_product(self, div) -> Optional[Dict[str, Any]]:
        """Parse a single Amazon product div."""
        try:
            # Extract product name
            name_elem = div.find('h2', class_='a-size-mini')
            if not name_elem:
                name_elem = div.find('span', class_='a-text-normal')
            name = name_elem.get_text(strip=True) if name_elem else "Unknown Product"
            
            # Extract price
            price_elem = div.find('span', class_='a-price-whole')
            price_cents = 0
            if price_elem:
                price_str = price_elem.get_text(strip=True).replace(',', '').replace('$', '')
                try:
                    price_cents = int(float(price_str) * 100)
                except:
                    pass
            
            # Extract rating
            rating = None
            rating_elem = div.find('span', class_='a-icon-alt')
            if rating_elem:
                rating_text = rating_elem.get_text(strip=True)
                match = re.search(r'(\d+\.?\d*)', rating_text)
                if match:
                    rating = float(match.group(1))
            
            # Extract image URL
            img_elem = div.find('img', class_='s-image')
            image_url = img_elem.get('src') if img_elem else None
            
            # Extract product URL
            link_elem = div.find('a', class_='a-link-normal')
            product_url = None
            if link_elem:
                href = link_elem.get('href')
                if href:
                    product_url = f"https://www.amazon.com{href}" if not href.startswith('http') else href
            
            # Extract brand (try to infer from name)
            brand = self._extract_brand_from_name(name)
            
            return {
                'name': name,
                'price_cents': price_cents,
                'currency': 'USD',
                'category': 'Electronics',
                'subcategory': 'Laptop',
                'brand': brand,
                'rating': rating,
                'image_url': image_url,
                'product_url': product_url,
                'source': 'amazon',
                'scraped_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error parsing Amazon product: {e}")
            return None
    
    def scrape_shopify_store(self, store_url: str) -> List[Dict[str, Any]]:
        """
        Scrape products from a Shopify store.
        
        Args:
            store_url: Base URL of Shopify store (e.g., "https://store.example.com")
        
        Returns:
            List of product dictionaries
        """
        products = []
        
        try:
            # Shopify products API endpoint
            api_url = f"{store_url}/products.json"
            
            logger.info(f"Scraping Shopify store: {api_url}")
            
            response = self.session.get(api_url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            for product_data in data.get('products', []):
                product = self._parse_shopify_product(product_data)
                if product:
                    products.append(product)
            
            logger.info(f"Scraped {len(products)} products from Shopify")
            return products
            
        except Exception as e:
            logger.error(f"Shopify scraping failed: {e}")
            return products
    
    def _parse_shopify_product(self, product_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a Shopify product JSON."""
        try:
            name = product_data.get('title', 'Unknown Product')
            
            # Get price from variants
            variants = product_data.get('variants', [])
            price_cents = 0
            if variants:
                price_str = variants[0].get('price', '0')
                try:
                    price_cents = int(float(price_str) * 100)
                except:
                    pass
            
            # Extract brand from vendor
            brand = product_data.get('vendor', 'Unknown')
            
            # Get images
            images = product_data.get('images', [])
            image_url = images[0].get('src') if images else None
            
            # Extract category from product type
            product_type = product_data.get('product_type', 'Electronics')
            
            # Determine if it's a laptop
            is_laptop = any(keyword in name.lower() for keyword in [
                'laptop', 'notebook', 'macbook', 'thinkpad', 'xps', 'chromebook'
            ])
            
            if not is_laptop:
                return None  # Skip non-laptop products for now
            
            return {
                'name': name,
                'price_cents': price_cents,
                'currency': 'USD',
                'category': 'Electronics',
                'subcategory': 'Laptop',
                'brand': brand,
                'description': product_data.get('body_html', ''),
                'image_url': image_url,
                'product_url': product_data.get('handle', ''),
                'source': 'shopify',
                'scraped_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error parsing Shopify product: {e}")
            return None
    
    def scrape_temu_electronics(self, category: str = "laptops", max_items: int = 50) -> List[Dict[str, Any]]:
        """
        Scrape electronics products from Temu.
        
        Temu uses dynamic content loading, so we need to handle JavaScript-rendered content.
        For production, consider using Selenium or Playwright.
        
        Args:
            category: Product category (laptops, phones, tablets, etc.)
            max_items: Maximum number of items to scrape
        
        Returns:
            List of product dictionaries
        """
        products = []
        
        try:
            # Temu search URL for electronics
            # Note: Temu's URL structure may change frequently
            base_url = f"https://www.temu.com/search_result.html?search_key={category}"
            
            logger.info(f"Scraping Temu: {base_url}")
            
            # Temu requires proper headers to avoid blocking
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            
            response = self.session.get(base_url, headers=headers, timeout=15)
            
            # Temu may return 403 or redirect - handle gracefully
            if response.status_code == 403:
                logger.warning("Temu returned 403 (blocked). Temu has strong anti-scraping measures.")
                logger.info("Tip: Use Temu's official API if available, or use Selenium/Playwright for browser automation.")
                return products
            
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Temu product structure (may need adjustment based on actual HTML)
            # Products are typically in containers with data attributes
            product_containers = soup.find_all(['div', 'article'], class_=lambda x: x and ('product' in x.lower() or 'item' in x.lower()) if x else False)
            
            # Alternative: Look for product links
            if not product_containers:
                product_links = soup.find_all('a', href=lambda x: x and '/goods/' in x if x else False)
                for link in product_links[:max_items]:
                    product = self._parse_temu_product_from_link(link)
                    if product:
                        products.append(product)
            else:
                for container in product_containers[:max_items]:
                    product = self._parse_temu_product(container)
                    if product:
                        products.append(product)
            
            logger.info(f"Scraped {len(products)} products from Temu")
            return products
            
        except Exception as e:
            logger.error(f"Temu scraping failed: {e}")
            return products
    
    def _parse_temu_product(self, container) -> Optional[Dict[str, Any]]:
        """Parse a Temu product container."""
        try:
            # Extract product name
            name_elem = container.find(['h2', 'h3', 'span'], class_=lambda x: x and ('title' in x.lower() or 'name' in x.lower()) if x else False)
            if not name_elem:
                name_elem = container.find('a', class_=lambda x: x and 'title' in x.lower() if x else False)
            name = name_elem.get_text(strip=True) if name_elem else "Unknown Product"
            
            # Extract price
            price_elem = container.find(['span', 'div'], class_=lambda x: x and ('price' in x.lower() or 'cost' in x.lower()) if x else False)
            price_cents = 0
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                # Extract numeric price
                match = re.search(r'[\$]?(\d+\.?\d*)', price_text.replace(',', ''))
                if match:
                    try:
                        price_cents = int(float(match.group(1)) * 100)
                    except:
                        pass
            
            # Extract image
            img_elem = container.find('img')
            image_url = img_elem.get('src') or img_elem.get('data-src') if img_elem else None
            
            # Extract product URL
            link_elem = container.find('a', href=True)
            product_url = None
            if link_elem:
                href = link_elem.get('href')
                if href:
                    product_url = f"https://www.temu.com{href}" if not href.startswith('http') else href
            
            # Extract brand
            brand = self._extract_brand_from_name(name)
            
            # Determine if it's a laptop/electronics
            name_lower = name.lower()
            is_electronics = any(keyword in name_lower for keyword in [
                'laptop', 'notebook', 'computer', 'pc', 'macbook', 'thinkpad',
                'phone', 'smartphone', 'tablet', 'ipad', 'monitor', 'keyboard',
                'mouse', 'headphone', 'earbud', 'speaker', 'camera'
            ])
            
            if not is_electronics:
                return None  # Skip non-electronics
            
            return {
                'name': name,
                'price_cents': price_cents,
                'currency': 'USD',
                'category': 'Electronics',
                'subcategory': 'Laptop' if 'laptop' in name_lower or 'notebook' in name_lower else 'Electronics',
                'brand': brand,
                'image_url': image_url,
                'product_url': product_url,
                'source': 'temu',
                'scraped_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error parsing Temu product: {e}")
            return None
    
    def _parse_temu_product_from_link(self, link) -> Optional[Dict[str, Any]]:
        """Parse Temu product from a link element."""
        try:
            name = link.get_text(strip=True) or "Unknown Product"
            href = link.get('href', '')
            product_url = f"https://www.temu.com{href}" if href and not href.startswith('http') else href
            
            # Try to find price in nearby elements
            parent = link.find_parent()
            price_cents = 0
            if parent:
                price_elem = parent.find(['span', 'div'], class_=lambda x: x and 'price' in x.lower() if x else False)
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    match = re.search(r'[\$]?(\d+\.?\d*)', price_text.replace(',', ''))
                    if match:
                        try:
                            price_cents = int(float(match.group(1)) * 100)
                        except:
                            pass
            
            brand = self._extract_brand_from_name(name)
            
            return {
                'name': name,
                'price_cents': price_cents,
                'currency': 'USD',
                'category': 'Electronics',
                'subcategory': 'Laptop',
                'brand': brand,
                'product_url': product_url,
                'source': 'temu',
                'scraped_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error parsing Temu product from link: {e}")
            return None
    
    def scrape_ebay_laptops(self, max_items: int = 50) -> List[Dict[str, Any]]:
        """
        Scrape laptop products from eBay.
        
        Uses eBay's Finding API (requires API key) or web scraping.
        
        Args:
            max_items: Maximum number of items to scrape
        
        Returns:
            List of product dictionaries
        """
        products = []
        
        try:
            # eBay search URL
            url = "https://www.ebay.com/sch/i.html?_nkw=laptops&_pgn=1"
            
            logger.info(f"Scraping eBay: {url}")
            
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find product items
            items = soup.find_all('div', class_='s-item')
            
            for item in items[:max_items]:
                product = self._parse_ebay_item(item)
                if product:
                    products.append(product)
            
            logger.info(f"Scraped {len(products)} products from eBay")
            return products
            
        except Exception as e:
            logger.error(f"eBay scraping failed: {e}")
            return products
    
    def _parse_ebay_item(self, item) -> Optional[Dict[str, Any]]:
        """Parse a single eBay item."""
        try:
            # Extract name
            name_elem = item.find('h3', class_='s-item__title')
            name = name_elem.get_text(strip=True) if name_elem else "Unknown Product"
            
            # Extract price
            price_elem = item.find('span', class_='s-item__price')
            price_cents = 0
            if price_elem:
                price_str = price_elem.get_text(strip=True).replace(',', '').replace('$', '')
                match = re.search(r'(\d+\.?\d*)', price_str)
                if match:
                    try:
                        price_cents = int(float(match.group(1)) * 100)
                    except:
                        pass
            
            # Extract image
            img_elem = item.find('img', class_='s-item__image-img')
            image_url = img_elem.get('src') if img_elem else None
            
            # Extract link
            link_elem = item.find('a', class_='s-item__link')
            product_url = link_elem.get('href') if link_elem else None
            
            # Extract brand
            brand = self._extract_brand_from_name(name)
            
            return {
                'name': name,
                'price_cents': price_cents,
                'currency': 'USD',
                'category': 'Electronics',
                'subcategory': 'Laptop',
                'brand': brand,
                'image_url': image_url,
                'product_url': product_url,
                'source': 'ebay',
                'scraped_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error parsing eBay item: {e}")
            return None
    
    def _extract_brand_from_name(self, name: str) -> str:
        """Extract brand name from product name."""
        brands = [
            'Apple', 'Dell', 'HP', 'Lenovo', 'ASUS', 'Acer', 'MSI', 'Razer',
            'Samsung', 'Microsoft', 'LG', 'Sony', 'Toshiba', 'Fujitsu',
            'Framework', 'System76', 'Alienware', 'ROG', 'ThinkPad', 'MacBook'
        ]
        
        name_lower = name.lower()
        for brand in brands:
            if brand.lower() in name_lower:
                return brand
        
        return 'Unknown'
    
    def save_to_json(self, products: List[Dict[str, Any]], filename: str):
        """Save scraped products to JSON file."""
        with open(filename, 'w') as f:
            json.dump(products, f, indent=2)
        logger.info(f"Saved {len(products)} products to {filename}")


    def scrape_woocommerce_store(self, store_url: str, api_key: str = None, api_secret: str = None) -> List[Dict[str, Any]]:
        """
        Scrape products from a WooCommerce store.
        
        Uses WooCommerce REST API if credentials provided, otherwise web scraping.
        
        Args:
            store_url: Base URL of WooCommerce store
            api_key: WooCommerce API key (optional, for API access)
            api_secret: WooCommerce API secret (optional, for API access)
        
        Returns:
            List of product dictionaries
        """
        products = []
        
        try:
            if api_key and api_secret:
                # Use WooCommerce REST API
                api_url = f"{store_url}/wp-json/wc/v3/products"
                auth = (api_key, api_secret)
                
                logger.info(f"Scraping WooCommerce via API: {api_url}")
                
                page = 1
                while True:
                    response = self.session.get(
                        api_url,
                        params={'page': page, 'per_page': 100, 'category': 'electronics'},
                        auth=auth,
                        timeout=10
                    )
                    response.raise_for_status()
                    
                    data = response.json()
                    if not data:
                        break
                    
                    for product_data in data:
                        product = self._parse_woocommerce_product(product_data)
                        if product:
                            products.append(product)
                    
                    page += 1
                    if len(data) < 100:  # Last page
                        break
            else:
                # Fallback to web scraping
                logger.info(f"Scraping WooCommerce via web scraping: {store_url}")
                # WooCommerce stores typically have /shop/ or /products/ pages
                shop_url = f"{store_url}/shop/" if not store_url.endswith('/') else f"{store_url}shop/"
                response = self.session.get(shop_url, timeout=10)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Find product containers (WooCommerce structure)
                product_divs = soup.find_all(['div', 'li'], class_=lambda x: x and 'product' in x.lower() if x else False)
                
                for div in product_divs:
                    product = self._parse_woocommerce_product_html(div)
                    if product:
                        products.append(product)
            
            logger.info(f"Scraped {len(products)} products from WooCommerce")
            return products
            
        except Exception as e:
            logger.error(f"WooCommerce scraping failed: {e}")
            return products
    
    def _parse_woocommerce_product(self, product_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a WooCommerce product from API JSON."""
        try:
            name = product_data.get('name', 'Unknown Product')
            
            # Get price
            price_cents = 0
            regular_price = product_data.get('regular_price')
            if regular_price:
                try:
                    price_cents = int(float(regular_price) * 100)
                except:
                    pass
            
            # Get images
            images = product_data.get('images', [])
            image_url = images[0].get('src') if images else None
            
            # Get categories
            categories = product_data.get('categories', [])
            category = 'Electronics'
            if categories:
                category = categories[0].get('name', 'Electronics')
            
            # Check if it's a laptop/electronics
            name_lower = name.lower()
            is_electronics = any(keyword in name_lower for keyword in [
                'laptop', 'notebook', 'computer', 'pc', 'macbook', 'thinkpad',
                'phone', 'smartphone', 'tablet', 'ipad', 'monitor', 'keyboard'
            ])
            
            if not is_electronics:
                return None
            
            brand = self._extract_brand_from_name(name)
            
            return {
                'name': name,
                'price_cents': price_cents,
                'currency': product_data.get('currency', 'USD'),
                'category': 'Electronics',
                'subcategory': 'Laptop' if 'laptop' in name_lower or 'notebook' in name_lower else 'Electronics',
                'brand': brand,
                'description': product_data.get('description', ''),
                'image_url': image_url,
                'product_url': product_data.get('permalink', ''),
                'source': 'woocommerce',
                'scraped_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error parsing WooCommerce product: {e}")
            return None
    
    def _parse_woocommerce_product_html(self, div) -> Optional[Dict[str, Any]]:
        """Parse a WooCommerce product from HTML."""
        try:
            # Extract product name
            name_elem = div.find(['h2', 'h3', 'a'], class_=lambda x: x and 'product' in x.lower() if x else False)
            if not name_elem:
                name_elem = div.find('a', class_=lambda x: x and 'woocommerce-LoopProduct-link' in x if x else False)
            name = name_elem.get_text(strip=True) if name_elem else "Unknown Product"
            
            # Extract price
            price_elem = div.find(['span', 'div'], class_=lambda x: x and 'price' in x.lower() if x else False)
            price_cents = 0
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                match = re.search(r'[\$]?(\d+\.?\d*)', price_text.replace(',', ''))
                if match:
                    try:
                        price_cents = int(float(match.group(1)) * 100)
                    except:
                        pass
            
            # Extract image
            img_elem = div.find('img')
            image_url = img_elem.get('src') or img_elem.get('data-src') if img_elem else None
            
            # Extract link
            link_elem = div.find('a', href=True)
            product_url = link_elem.get('href') if link_elem else None
            
            brand = self._extract_brand_from_name(name)
            
            return {
                'name': name,
                'price_cents': price_cents,
                'currency': 'USD',
                'category': 'Electronics',
                'subcategory': 'Laptop',
                'brand': brand,
                'image_url': image_url,
                'product_url': product_url,
                'source': 'woocommerce',
                'scraped_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error parsing WooCommerce product HTML: {e}")
            return None


def main():
    """Main scraping function."""
    logging.basicConfig(level=logging.INFO)
    
    scraper = ElectronicsScraper()
    all_products = []
    
    # Scrape from multiple sources
    logger.info("Starting electronics product scraping...")
    logger.info("Note: Some sites (Amazon, Temu) have anti-scraping measures. Use official APIs when possible.")
    
    # Amazon (may be blocked)
    logger.info("\n=== Scraping Amazon ===")
    amazon_products = scraper.scrape_amazon_laptops(max_pages=2)
    all_products.extend(amazon_products)
    logger.info(f"Amazon: {len(amazon_products)} products")
    
    # eBay
    logger.info("\n=== Scraping eBay ===")
    ebay_products = scraper.scrape_ebay_laptops(max_items=30)
    all_products.extend(ebay_products)
    logger.info(f"eBay: {len(ebay_products)} products")
    
    # Temu (may be blocked)
    logger.info("\n=== Scraping Temu ===")
    temu_products = scraper.scrape_temu_electronics(category="laptops", max_items=30)
    all_products.extend(temu_products)
    logger.info(f"Temu: {len(temu_products)} products")
    
    # Shopify (example store - replace with actual store URL)
    logger.info("\n=== Scraping Shopify ===")
    # Example: scraper.scrape_shopify_store("https://store.example.com")
    # Uncomment and add actual Shopify store URLs
    logger.info("Shopify: Add store URLs to scrape")
    
    # WooCommerce (example store - replace with actual store URL)
    logger.info("\n=== Scraping WooCommerce ===")
    # Example: scraper.scrape_woocommerce_store("https://store.example.com", api_key="...", api_secret="...")
    # Uncomment and add actual WooCommerce store URLs
    logger.info("WooCommerce: Add store URLs to scrape")
    
    # Save results
    output_file = f"scraped_electronics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    scraper.save_to_json(all_products, output_file)
    
    logger.info(f"\n=== Scraping Complete ===")
    logger.info(f"Total products scraped: {len(all_products)}")
    logger.info(f"Results saved to: {output_file}")
    
    # Print summary by source
    from collections import Counter
    source_counts = Counter(p.get('source', 'unknown') for p in all_products)
    logger.info("\nProducts by source:")
    for source, count in source_counts.items():
        logger.info(f"  {source}: {count}")


if __name__ == "__main__":
    main()
