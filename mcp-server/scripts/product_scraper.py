#!/usr/bin/env python3
"""
Web scraper for fetching real product data from Temu, WooCommerce, and other e-commerce sites.

This module provides scrapers for various e-commerce platforms with proper error handling,
rate limiting, and data normalization.

WORKING SCRAPERS (use these for real data):
  - Shopify, WooCommerce, BigCommerce: requests-based, reliable.
  - Temu: use Selenium (see temu_selenium_scraper.py).

LIMITED SCRAPERS (do not rely on for production):
  - Etsy: often returns 403 (anti-scraping). Prefer Etsy Open API.
  - eBay: often 0 products or 500 (JS-rendered). Prefer eBay Finding API.

See project root: SCRAPING_LIMITATIONS_AND_SECURITY.md
"""

import time
import random
import re
import json
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urljoin
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@dataclass
class ScrapedProduct:
    """Normalized product data from scraping."""
    name: str
    description: str
    price_cents: int
    category: str
    subcategory: Optional[str] = None
    brand: Optional[str] = None
    image_url: Optional[str] = None
    available_qty: int = random.randint(5, 100)
    source_url: Optional[str] = None  # Page/store URL we scraped; used as scraped_from_url
    source: Optional[str] = None  # Platform: "WooCommerce", "Shopify", "Temu", "BigCommerce", etc.
    color: Optional[str] = None  # Product color when available


class BaseScraper:
    """Base class for all scrapers with common functionality."""
    
    def __init__(self, rate_limit_delay: float = 1.0):
        """
        Initialize scraper with rate limiting.
        
        Args:
            rate_limit_delay: Minimum seconds between requests
        """
        self.rate_limit_delay = rate_limit_delay
        self.last_request_time = 0
        
        # Setup session with retries
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Realistic browser headers (webscrape.txt: User-Agent + headers)
        uas = [
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ]
        self.session.headers.update({
            'User-Agent': random.choice(uas),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        })
    
    def _rate_limit(self):
        """Randomized delay between requests (per webscrape.txt)."""
        elapsed = time.time() - self.last_request_time
        delay = self.rate_limit_delay * (0.5 + random.random())
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self.last_request_time = time.time()
    
    def _parse_price(self, price_str: str) -> Optional[int]:
        """
        Parse price string to cents.
        
        Examples:
            "$29.99" -> 2999
            "£19.99" -> 1999 (assumes USD)
            "29.99" -> 2999
        """
        if not price_str:
            return None
        
        # Remove currency symbols and whitespace
        price_clean = re.sub(r'[^\d.]', '', price_str.strip())
        
        try:
            price_float = float(price_clean)
            return int(price_float * 100)
        except (ValueError, AttributeError):
            return None
    
    def _extract_color(self, data: Dict[str, Any]) -> Optional[str]:
        """Extract color from API/JSON-LD data. Returns None if not found."""
        # Direct keys
        for key in ("color", "colour", "Color", "Colour"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        # Nested: offers[0].color, etc.
        offers = data.get("offers")
        if isinstance(offers, dict) and offers.get("color"):
            return str(offers["color"]).strip()
        if isinstance(offers, list) and offers and isinstance(offers[0], dict) and offers[0].get("color"):
            return str(offers[0]["color"]).strip()
        # WooCommerce attributes
        for attr in data.get("attributes") or []:
            n = (attr.get("name") or attr.get("option")) or ""
            if n.lower() in ("color", "colour"):
                opts = attr.get("options") or []
                if isinstance(opts, list) and opts and opts[0]:
                    return str(opts[0]).strip()
                break
        return None

    def _normalize_category(self, category: str) -> str:
        """Normalize category names to standard categories."""
        category_lower = category.lower()
        
        # Map common categories
        category_map = {
            'electronics': 'Electronics',
            'computers': 'Electronics',
            'laptops': 'Electronics',
            'phones': 'Electronics',
            'smartphones': 'Electronics',
            'books': 'Books',
            'book': 'Books',
            'home': 'Home & Kitchen',
            'kitchen': 'Home & Kitchen',
            'home & kitchen': 'Home & Kitchen',
            'sports': 'Sports',
            'outdoors': 'Sports',
            'clothing': 'Clothing',
            'apparel': 'Clothing',
            'furniture': 'Furniture',
        }
        
        for key, value in category_map.items():
            if key in category_lower:
                return value
        
        return category.title() if category else 'Electronics'
    
    def scrape(self, url: str, max_products: int = 20, source: Optional[str] = None) -> List[ScrapedProduct]:
        """
        Scrape products from URL.
        
        Args:
            url: URL to scrape
            max_products: Max products to return
            source: Where we got it (WooCommerce, Shopify, Temu, etc.)
            
        Returns:
            List of scraped products
        """
        raise NotImplementedError("Subclasses must implement scrape()")


class TemuScraper(BaseScraper):
    """Scraper for Temu.com products."""
    
    def scrape(self, url: str, max_products: int = 20, source: Optional[str] = None) -> List[ScrapedProduct]:
        """
        Scrape products from Temu.
        
        Note: Temu uses heavy JavaScript, so this is a simplified version.
        For production, consider using Selenium or their API if available.
        """
        self._source = source or "Temu"
        self._rate_limit()
        
        try:
            # Temu product pages typically have product data in JSON-LD or script tags
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            products = []
            
            script_tags = soup.find_all('script', type='application/ld+json')
            for script in script_tags:
                try:
                    data = json.loads(script.string)
                    if isinstance(data, dict) and data.get('@type') == 'Product':
                        product = self._parse_temu_product(data, url, getattr(self, '_source', 'Temu'))
                        if product:
                            products.append(product)
                except (json.JSONDecodeError, KeyError):
                    continue
            
            if not products:
                products = self._parse_temu_html(soup, url, max_products)
            
            return products[:max_products]
            
        except requests.RequestException as e:
            print(f"Error scraping Temu: {e}")
            return []
    
    def _parse_temu_product(self, data: Dict[str, Any], source_url: str, source_label: str = "Temu") -> Optional[ScrapedProduct]:
        """Parse product from JSON-LD data."""
        try:
            name = data.get('name', '')
            description = data.get('description', name)
            price_str = data.get('offers', {}).get('price', '0')
            price_cents = self._parse_price(price_str)
            
            if not name or not price_cents:
                return None
            
            # Extract category from breadcrumbs or product type
            category = 'Electronics'  # Default
            if 'category' in data:
                category = self._normalize_category(data['category'])
            
            return ScrapedProduct(
                name=name,
                description=description,
                price_cents=price_cents,
                category=category,
                brand=data.get('brand', {}).get('name') if isinstance(data.get('brand'), dict) else data.get('brand'),
                image_url=data.get('image'),
                source_url=source_url,
                source=source_label,
                color=self._extract_color(data),
            )
        except Exception as e:
            print(f"Error parsing Temu product: {e}")
            return None
    
    def _parse_temu_html(self, soup: BeautifulSoup, source_url: str, max_products: int) -> List[ScrapedProduct]:
        """
        Fallback HTML parsing for Temu.
        
        Note: Temu uses heavy JavaScript rendering, so static HTML parsing is limited.
        For production, use Selenium/Playwright to render JavaScript.
        """
        products = []
        
        # Try multiple selectors - Temu's structure may vary
        # Look for data attributes, common class patterns, and script tags with product data
        
        # Method 1: Look for script tags with product data (Temu often embeds data in scripts)
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string:
                # Look for JSON data with product info
                text = script.string
                # Try to find product arrays or objects
                if 'product' in text.lower() or 'price' in text.lower():
                    try:
                        # Try to extract JSON-like structures
                        # Look for patterns like {"name": "...", "price": "..."}
                        json_matches = re.findall(r'\{[^{}]*"(?:name|title|price|cost)"[^{}]*\}', text, re.I)
                        for match in json_matches[:max_products]:
                            try:
                                data = json.loads(match)
                                name = data.get('name') or data.get('title') or ''
                                price_str = str(data.get('price') or data.get('cost') or '0')
                                if name and price_str:
                                    price_cents = self._parse_price(price_str)
                                    if price_cents:
                                        products.append(ScrapedProduct(
                                            name=name[:200],  # Limit length
                                            description=name,
                                            price_cents=price_cents,
                                            category='Electronics',
                                            source_url=source_url,
                                            source=getattr(self, '_source', 'Temu'),
                                        ))
                            except (json.JSONDecodeError, KeyError, ValueError):
                                continue
                    except Exception:
                        continue
        
        # Method 2: Look for product cards with various class patterns
        # Temu uses different class names, try common e-commerce patterns
        selectors = [
            ('div', {'class': re.compile(r'product|item|card|goods', re.I)}),
            ('article', {'class': re.compile(r'product|item', re.I)}),
            ('div', {'data-product-id': True}),  # Data attributes
            ('a', {'href': re.compile(r'/p/|/product/', re.I)}),  # Product links
        ]
        
        seen_names = set()
        for tag, attrs in selectors:
            if products:
                break  # Found products, stop trying other methods
            cards = soup.find_all(tag, attrs)
            for card in cards[:max_products * 2]:  # Get more candidates
                try:
                    # Try to find name/title
                    name_elem = (
                        card.find(['h2', 'h3', 'h4'], class_=re.compile(r'title|name|product', re.I)) or
                        card.find('a', class_=re.compile(r'title|name', re.I)) or
                        card.find('span', class_=re.compile(r'title|name', re.I)) or
                        card.find('div', class_=re.compile(r'title|name', re.I))
                    )
                    
                    # Try to find price
                    price_elem = (
                        card.find(['span', 'div', 'p'], class_=re.compile(r'price|cost|amount', re.I)) or
                        card.find('span', string=re.compile(r'\$|€|£|\d+\.\d+', re.I))
                    )
                    
                    if name_elem:
                        name = name_elem.get_text(strip=True)
                        if name and len(name) > 3 and name.lower() not in seen_names:
                            seen_names.add(name.lower())
                            
                            price_cents = None
                            if price_elem:
                                price_str = price_elem.get_text(strip=True)
                                price_cents = self._parse_price(price_str)
                            
                            # If no price found, try to extract from card text
                            if not price_cents:
                                card_text = card.get_text()
                                price_cents = self._parse_price(card_text)
                            
                            if name and price_cents:
                                products.append(ScrapedProduct(
                                    name=name[:200],
                                    description=name,
                                    price_cents=price_cents,
                                    category='Electronics',
                                    source_url=source_url,
                                    source=getattr(self, '_source', 'Temu'),
                                ))
                                if len(products) >= max_products:
                                    break
                except Exception:
                    continue
        
        return products[:max_products]


class WooCommerceScraper(BaseScraper):
    """Scraper for WooCommerce stores."""
    
    def scrape(self, url: str, max_products: int = 20, source: Optional[str] = None) -> List[ScrapedProduct]:
        """Scrape products from WooCommerce store (REST API → Store API → HTML)."""
        self._source = source or "WooCommerce"
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        products = self._scrape_via_api(url, base, max_products)
        if not products:
            products = self._scrape_via_store_api(base, max_products)
        if not products:
            products = self._scrape_via_html(url, max_products)
        return products

    def _scrape_via_api(self, page_url: str, base_url: str, max_products: int) -> List[ScrapedProduct]:
        """Scrape via WooCommerce REST API (wc/v3)."""
        self._rate_limit()
        api_url = urljoin(base_url.rstrip("/") + "/", "wp-json/wc/v3/products")
        try:
            params = {"per_page": min(max_products, 100), "status": "publish"}
            response = self.session.get(api_url, params=params, timeout=10)
            if response.status_code in (401, 404):
                return []
            response.raise_for_status()
            data = response.json()
            products = []
            for item in data:
                p = self._parse_woocommerce_product(item, base_url, getattr(self, "_source", "WooCommerce"))
                if p:
                    products.append(p)
            return products[:max_products]
        except requests.RequestException:
            return []

    def _scrape_via_store_api(self, base_url: str, max_products: int) -> List[ScrapedProduct]:
        """Scrape via WooCommerce Store API (wc/store/v1) when REST API is unavailable."""
        self._rate_limit()
        api_url = urljoin(base_url.rstrip("/") + "/", "wp-json/wc/store/v1/products")
        try:
            params = {"per_page": min(max_products, 100)}
            response = self.session.get(api_url, params=params, timeout=10)
            if response.status_code != 200:
                return []
            data = response.json()
            items = data if isinstance(data, list) else data.get("products", data.get("items", [])) or []
            products = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("title", "")
                prices = item.get("prices") or {}
                price_str = None
                if isinstance(prices, dict):
                    price_str = prices.get("price") or prices.get("regular_price") or prices.get("current")
                if not isinstance(price_str, str):
                    price_str = str(item.get("price") or item.get("regular_price") or "0")
                price_cents = self._parse_price(price_str)
                if not name or not price_cents:
                    continue
                desc = item.get("description") or item.get("short_description") or name
                permalink = item.get("permalink") or base_url
                _desc = name
                if isinstance(desc, str):
                    _desc = desc
                elif isinstance(desc, dict) and "raw" in desc:
                    _desc = str(desc["raw"]) or name
                products.append(
                    ScrapedProduct(
                        name=name,
                        description=_desc,
                        price_cents=price_cents,
                        category=self._normalize_category(
                    (item.get("categories") or [{}])[0].get("name", "Electronics") if item.get("categories") else "Electronics"
                ),
                        brand=None,
                        image_url=(item.get("images") or [{}])[0].get("src") if item.get("images") else None,
                        available_qty=item.get("stock_quantity", random.randint(5, 100)),
                        source_url=permalink,
                        source=getattr(self, "_source", "WooCommerce"),
                        color=self._extract_color(item),
                    )
                )
            return products[:max_products]
        except Exception:
            return []
    
    def _scrape_via_html(self, url: str, max_products: int) -> List[ScrapedProduct]:
        """Scrape products from HTML (fallback method)."""
        self._rate_limit()
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            products = []
            
            # WooCommerce products are typically in <li> with class 'product'
            product_items = soup.find_all('li', class_=re.compile(r'product|type-product', re.I))
            
            for item in product_items[:max_products]:
                try:
                    name_elem = item.find(['h2', 'h3', 'a'], class_=re.compile(r'title|product-title', re.I))
                    price_elem = item.find('span', class_=re.compile(r'price|amount', re.I))
                    desc_elem = item.find(['p', 'div'], class_=re.compile(r'description|excerpt', re.I))
                    
                    if name_elem:
                        name = name_elem.get_text(strip=True)
                        price_str = price_elem.get_text(strip=True) if price_elem else '0'
                        description = desc_elem.get_text(strip=True) if desc_elem else name
                        price_cents = self._parse_price(price_str)
                        
                        # Try to get category from breadcrumbs or product category
                        category_elem = item.find(['span', 'a'], class_=re.compile(r'category|cat', re.I))
                        category = self._normalize_category(
                            category_elem.get_text(strip=True) if category_elem else 'Electronics'
                        )
                        
                        if name and price_cents:
                            products.append(ScrapedProduct(
                                name=name,
                                description=description,
                                price_cents=price_cents,
                                category=category,
                                source_url=url,
                                source=getattr(self, '_source', 'WooCommerce'),
                            ))
                except Exception:
                    continue
            
            return products
            
        except requests.RequestException as e:
            print(f"Error scraping WooCommerce HTML: {e}")
            return []
    
    def _parse_woocommerce_product(self, data: Dict[str, Any], source_url: str, source_label: str = "WooCommerce") -> Optional[ScrapedProduct]:
        """Parse product from WooCommerce API response."""
        try:
            name = data.get('name', '')
            description = data.get('description', '') or data.get('short_description', '') or name
            
            # Parse price (WooCommerce uses 'price' or 'regular_price')
            price_str = data.get('price') or data.get('regular_price', '0')
            price_cents = self._parse_price(price_str)
            
            if not name or not price_cents:
                return None
            
            # Get category from categories array
            category = 'Electronics'  # Default
            if data.get('categories'):
                category_name = data['categories'][0].get('name', '')
                category = self._normalize_category(category_name)
            
            # Get brand from attributes or tags
            brand = None
            if data.get('attributes'):
                for attr in data['attributes']:
                    if attr.get('name', '').lower() == 'brand':
                        brand = attr.get('options', [None])[0]
                        break
            
            # Get image URL
            image_url = None
            if data.get('images'):
                image_url = data['images'][0].get('src')
            
            return ScrapedProduct(
                name=name,
                description=description,
                price_cents=price_cents,
                category=category,
                subcategory=data.get('categories', [{}])[0].get('name') if len(data.get('categories', [])) > 1 else None,
                brand=brand,
                image_url=image_url,
                available_qty=data.get('stock_quantity', random.randint(5, 100)),
                source_url=data.get('permalink', source_url),
                source=source_label,
                color=self._extract_color(data),
            )
        except Exception as e:
            print(f"Error parsing WooCommerce product: {e}")
            return None


class GenericEcommerceScraper(BaseScraper):
    """Generic scraper for common e-commerce patterns."""
    
    def scrape(self, url: str, max_products: int = 20, source: Optional[str] = None) -> List[ScrapedProduct]:
        """Generic HTML scraping for common e-commerce sites."""
        self._source = source or "Generic"
        self._rate_limit()
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            products = []
            
            script_tags = soup.find_all('script', type='application/ld+json')
            for script in script_tags:
                try:
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        data = data[0]
                    
                    if isinstance(data, dict):
                        if data.get('@type') in ['Product', 'ItemList']:
                            if data.get('@type') == 'ItemList':
                                items = data.get('itemListElement', [])
                                for item in items[:max_products]:
                                    product = self._parse_jsonld_product(item.get('item', item), url, getattr(self, '_source', 'Generic'))
                                    if product:
                                        products.append(product)
                            else:
                                product = self._parse_jsonld_product(data, url, getattr(self, '_source', 'Generic'))
                                if product:
                                    products.append(product)
                except (json.JSONDecodeError, KeyError):
                    continue
            
            if not products:
                products = self._parse_generic_html(soup, url, max_products)
            
            return products[:max_products]
            
        except requests.RequestException as e:
            print(f"Error scraping {url}: {e}")
            return []
    
    def _parse_jsonld_product(self, data: Dict[str, Any], source_url: str, source_label: str = "Generic") -> Optional[ScrapedProduct]:
        """Parse product from JSON-LD structured data."""
        try:
            name = data.get('name', '')
            description = data.get('description', name)
            
            # Parse price
            price_str = '0'
            if 'offers' in data:
                offers = data['offers']
                if isinstance(offers, list):
                    offers = offers[0]
                price_str = offers.get('price', '0')
            
            price_cents = self._parse_price(price_str)
            
            if not name or not price_cents:
                return None
            
            # Get category
            category = 'Electronics'
            if 'category' in data:
                category = self._normalize_category(data['category'])
            elif 'breadcrumb' in data:
                breadcrumb = data['breadcrumb']
                if isinstance(breadcrumb, dict) and 'itemListElement' in breadcrumb:
                    items = breadcrumb['itemListElement']
                    if items:
                        category = self._normalize_category(items[-1].get('name', ''))
            
            return ScrapedProduct(
                name=name,
                description=description,
                price_cents=price_cents,
                category=category,
                brand=data.get('brand', {}).get('name') if isinstance(data.get('brand'), dict) else data.get('brand'),
                image_url=data.get('image'),
                source_url=source_url,
                source=source_label,
                color=self._extract_color(data),
            )
        except Exception:
            return None

    def _parse_generic_html(self, soup: BeautifulSoup, source_url: str, max_products: int) -> List[ScrapedProduct]:
        """Generic HTML parsing for product listings."""
        products = []
        
        # Common product card selectors
        selectors = [
            ('div', {'class': re.compile(r'product', re.I)}),
            ('article', {'class': re.compile(r'product|item', re.I)}),
            ('li', {'class': re.compile(r'product', re.I)}),
        ]
        
        for tag, attrs in selectors:
            items = soup.find_all(tag, attrs)
            if items:
                for item in items[:max_products]:
                    try:
                        name_elem = item.find(['h1', 'h2', 'h3', 'a'], class_=re.compile(r'title|name', re.I))
                        price_elem = item.find(['span', 'div'], class_=re.compile(r'price', re.I))
                        
                        if name_elem:
                            name = name_elem.get_text(strip=True)
                            price_str = price_elem.get_text(strip=True) if price_elem else '0'
                            price_cents = self._parse_price(price_str)
                            
                            if name and price_cents:
                                products.append(ScrapedProduct(
                                    name=name,
                                    description=name,
                                    price_cents=price_cents,
                                    category='Electronics',
                                    source_url=source_url,
                                    source=getattr(self, '_source', 'Generic'),
                                ))
                    except Exception:
                        continue
                
                if products:
                    break
        
        return products


class BigCommerceScraper(BaseScraper):
    """BeautifulSoup-based scraper for BigCommerce category/list pages (e.g. mc-demo)."""

    def scrape(self, url: str, max_products: int = 20, source: Optional[str] = None) -> List[ScrapedProduct]:
        self._source = source or "BigCommerce"
        self._rate_limit()
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
        except Exception as e:
            print(f"Error fetching BigCommerce page: {e}")
            return []
        soup = BeautifulSoup(response.content, "html.parser")
        return self._parse_product_list(soup, url, max_products)

    def _parse_product_list(self, soup: BeautifulSoup, page_url: str, max_products: int) -> List[ScrapedProduct]:
        products = []
        base = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
        list_ul = soup.find("ul", class_=re.compile(r"ProductList", re.I))
        if not list_ul:
            list_ul = soup.find("ul", class_=re.compile(r"product-list|product-grid", re.I))
        if not list_ul:
            items = []
        else:
            items = list_ul.find_all("li", recursive=False)
        for li in items[: max_products * 2]:
            try:
                name, price_str, href = None, None, None
                details = li.find("div", class_=re.compile(r"ProductDetails", re.I))
                if details:
                    a = details.find("a", href=True)
                    if a:
                        name = a.get_text(strip=True)
                        href = a.get("href", "").strip()
                if not name:
                    a = li.find("a", class_=re.compile(r"bigProductLink|product-link", re.I))
                    if a:
                        name = a.get_text(strip=True)
                        href = (a.get("href") or "").strip()
                if not name:
                    a = li.find("a", href=re.compile(r"/products?/", re.I))
                    if a:
                        name = a.get_text(strip=True)
                        if not name:
                            img = li.find("img", alt=True)
                            if img and img.get("alt"):
                                name = img["alt"].strip()
                        href = (a.get("href") or "").strip()
                pr = li.find("div", class_=re.compile(r"ProductPriceRating", re.I))
                if pr:
                    em = pr.find("em")
                    if em:
                        price_str = em.get_text(strip=True)
                if not price_str:
                    span = li.find(["span", "div"], class_=re.compile(r"price|amount", re.I))
                    if span:
                        price_str = span.get_text(strip=True)
                if not name or not price_str:
                    continue
                price_cents = self._parse_price(price_str)
                if not price_cents:
                    continue
                source_url = urljoin(base, href) if href else page_url
                products.append(
                    ScrapedProduct(
                        name=name,
                        description=name,
                        price_cents=price_cents,
                        category="Electronics",
                        source_url=source_url,
                        source=getattr(self, "_source", "BigCommerce"),
                    )
                )
            except Exception:
                continue
        return products[:max_products]


class EtsyScraper(BaseScraper):
    """
    Scraper for Etsy marketplace.
    
    Etsy has structured product listings with:
    - Product titles, descriptions, prices
    - Seller information
    - Images and reviews
    - Categories and tags
    
    Rate limiting: 2-3 seconds between requests (respectful crawling per webscrape.txt)

    LIMITATION: Etsy often returns 403. For production, use Etsy Open API instead.
    See SCRAPING_LIMITATIONS_AND_SECURITY.md.
    """
    
    def __init__(self, rate_limit_delay: float = 2.5):
        super().__init__(rate_limit_delay)
        # Enhanced headers for Etsy (to reduce 403 errors)
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
        })
    
    def scrape(self, url: str, max_products: int = 20, source: Optional[str] = None) -> List[ScrapedProduct]:
        self._source = source or "Etsy"
        products = []
        
        # Etsy search/listings page
        # Examples:
        # - https://www.etsy.com/search?q=laptop
        # - https://www.etsy.com/c/home-and-living?ref=catnav-12111
        # - https://www.etsy.com/shop/ShopName
        
        self._rate_limit()
        
        try:
            r = self.session.get(url, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "html.parser")
            
            # Etsy uses various selectors - try multiple approaches
            # Modern Etsy uses data-testid attributes
            items = soup.find_all(['div', 'article'], class_=re.compile(r'listing|item|product|card', re.I))
            
            # Alternative: look for data-testid="listing-card"
            if not items:
                items = soup.find_all(attrs={'data-testid': re.compile(r'listing|item|product', re.I)})
            
            # Alternative: look for product links
            if not items:
                product_links = soup.find_all('a', href=re.compile(r'/listing/'))
                items = [link.find_parent(['div', 'article']) for link in product_links[:max_products]]
                items = [item for item in items if item is not None]
            
            for item in items[:max_products]:
                try:
                    # Extract product name
                    name_elem = (
                        item.find('h3') or
                        item.find('a', class_=re.compile(r'title|name', re.I)) or
                        item.find(attrs={'data-testid': re.compile(r'title|name', re.I)})
                    )
                    name = name_elem.get_text(strip=True) if name_elem else "Unknown Product"
                    
                    # Skip if name is too generic
                    if not name or name.lower() in ['shop', 'view', 'see more', '']:
                        continue
                    
                    # Extract price
                    price_elem = (
                        item.find('span', class_=re.compile(r'price|currency', re.I)) or
                        item.find(attrs={'data-testid': re.compile(r'price', re.I)}) or
                        item.find(string=re.compile(r'\$[\d,]+\.?\d*'))
                    )
                    price_cents = None
                    if price_elem:
                        price_text = price_elem.get_text(strip=True) if hasattr(price_elem, 'get_text') else str(price_elem)
                        price_cents = self._parse_price(price_text)
                    
                    # If no price found, try parent element
                    if not price_cents:
                        parent = item.find_parent()
                        if parent:
                            price_text = parent.get_text()
                            price_cents = self._parse_price(price_text)
                    
                    if not price_cents:
                        continue  # Skip products without price
                    
                    # Extract product URL
                    link_elem = item.find('a', href=re.compile(r'/listing/'))
                    if not link_elem:
                        link_elem = item.find('a', href=True)
                    product_url = None
                    if link_elem:
                        href = link_elem.get('href', '')
                        if href.startswith('/'):
                            product_url = f"https://www.etsy.com{href}"
                        elif href.startswith('http'):
                            product_url = href
                    
                    # Extract image
                    img_elem = item.find('img')
                    image_url = None
                    if img_elem:
                        image_url = img_elem.get('src') or img_elem.get('data-src') or img_elem.get('data-lazy-src')
                    
                    # Extract description (often in title or alt text)
                    desc = name
                    desc_elem = item.find('p') or item.find('div', class_=re.compile(r'description', re.I))
                    if desc_elem:
                        desc = desc_elem.get_text(strip=True)[:500]
                    
                    # Extract category from URL or breadcrumbs
                    category = "Handmade"  # Default for Etsy
                    category_elem = item.find(attrs={'data-testid': re.compile(r'category', re.I)})
                    if category_elem:
                        category = category_elem.get_text(strip=True)
                    
                    # Extract brand/shop name
                    brand = None
                    shop_elem = item.find('a', href=re.compile(r'/shop/'))
                    if shop_elem:
                        brand = shop_elem.get_text(strip=True)
                    
                    products.append(
                        ScrapedProduct(
                            name=name,
                            description=desc,
                            price_cents=price_cents,
                            category=category,
                            brand=brand,
                            image_url=image_url,
                            source_url=product_url or url,
                            source=self._source,
                        )
                    )
                except Exception as e:
                    print(f"  [WARN] Error parsing Etsy item: {e}")
                    continue
            
            print(f"  Found {len(products)} products from Etsy")
            return products[:max_products]
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                print(f"[FAIL] Etsy returned 403 Forbidden - anti-scraping detected")
                print(f"   → Etsy has strong anti-scraping measures")
                print(f"   → May require Selenium for JavaScript-rendered content")
                print(f"   → Consider using Etsy API or official data sources")
            else:
                print(f"[FAIL] Etsy HTTP error {e.response.status_code}: {e}")
            return []
        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg or "Forbidden" in error_msg:
                print(f"[FAIL] Etsy 403 Forbidden - anti-scraping detected")
                print(f"   → Etsy blocks automated scraping")
                print(f"   → Consider using Selenium or official API")
            else:
                print(f"[FAIL] Etsy scraping error: {e}")
            return []


class eBayScraper(BaseScraper):
    """
    Scraper for eBay marketplace.
    
    eBay has structured listings with:
    - Product titles, prices, images
    - Seller ratings and shipping info
    - Categories and search filters
    
    Rate limiting: 2-3 seconds between requests (respectful crawling per webscrape.txt)

    LIMITATION: eBay often returns 0 products (JS-rendered). For production, use eBay Finding API.
    See SCRAPING_LIMITATIONS_AND_SECURITY.md.
    """
    
    def __init__(self, rate_limit_delay: float = 2.5):
        super().__init__(rate_limit_delay)
        # Enhanced headers for eBay (to avoid detection)
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
            'Referer': 'https://www.ebay.com/',
        })
    
    def scrape(self, url: str, max_products: int = 20, source: Optional[str] = None) -> List[ScrapedProduct]:
        self._source = source or "eBay"
        products = []
        
        # eBay search/listings page
        # Examples:
        # - https://www.ebay.com/sch/i.html?_nkw=laptops
        # - https://www.ebay.com/b/Laptops/bn_7000259124
        # - https://www.ebay.com/itm/1234567890 (individual listing)
        
        self._rate_limit()
        
        try:
            r = self.session.get(url, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "html.parser")
            
            # Debug: Check if page loaded
            if len(soup.get_text()) < 1000:
                print(f"  [WARN]  Page content seems too short ({len(soup.get_text())} chars) - may be JavaScript-rendered")
            
            # eBay uses class="s-item" for search results (most common)
            items = soup.find_all('div', class_='s-item')
            
            # Alternative: look for ul.srp-results > li.s-item
            if not items:
                ul = soup.find('ul', class_='srp-results')
                if ul:
                    items = ul.find_all('li', class_='s-item')
            
            # Alternative: look for listing cards with data-view attribute
            if not items:
                items = soup.find_all('div', attrs={'data-view': re.compile(r'item', re.I)})
            
            # Alternative: look for product links and get parent containers
            if not items:
                product_links = soup.find_all('a', href=re.compile(r'/itm/'))
                if product_links:
                    items = []
                    for link in product_links[:max_products * 2]:  # Get more to filter
                        parent = link.find_parent(['div', 'li'])
                        if parent and parent not in items:
                            items.append(parent)
            
            # Debug output
            if not items:
                print(f"  [WARN]  No items found. Page title: {soup.title.string if soup.title else 'N/A'}")
                print(f"  [WARN]  Trying to find any product-related elements...")
                # Last resort: find any div with price-like text
                all_divs = soup.find_all('div', limit=100)
                for div in all_divs:
                    text = div.get_text()
                    if '$' in text and any(char.isdigit() for char in text):
                        # Check if it looks like a product listing
                        if len(text) > 20 and len(text) < 500:
                            items.append(div)
                            if len(items) >= max_products:
                                break
            
            for item in items[:max_products]:
                try:
                    # Extract product name - try multiple selectors
                    name_elem = (
                        item.find('h3', class_='s-item__title') or
                        item.find('h3', class_=re.compile(r'title', re.I)) or
                        item.find('h2', class_=re.compile(r'title', re.I)) or
                        item.find('a', class_=re.compile(r'title|link', re.I)) or
                        item.find('a', href=re.compile(r'/itm/')) or
                        item.find('h3') or
                        item.find('h2')
                    )
                    name = name_elem.get_text(strip=True) if name_elem else "Unknown Product"
                    
                    # Skip "Shop on eBay" or other non-product items
                    skip_phrases = ['shop on ebay', 'see more', 'view all', 'new listing', 'sponsored', '']
                    if not name or any(phrase in name.lower() for phrase in skip_phrases):
                        continue
                    
                    # Skip if name is too short (likely not a product)
                    if len(name) < 10:
                        continue
                    
                    # Extract price - try multiple selectors
                    price_elem = (
                        item.find('span', class_='s-item__price') or
                        item.find('span', class_=re.compile(r'price|currency|amount', re.I)) or
                        item.find('div', class_=re.compile(r'price', re.I)) or
                        item.find(string=re.compile(r'\$[\d,]+\.?\d*'))
                    )
                    price_cents = None
                    if price_elem:
                        if hasattr(price_elem, 'get_text'):
                            price_text = price_elem.get_text(strip=True)
                        else:
                            price_text = str(price_elem).strip()
                        price_cents = self._parse_price(price_text)
                    
                    # If no price found, try searching in all text
                    if not price_cents:
                        item_text = item.get_text()
                        price_match = re.search(r'\$[\d,]+\.?\d*', item_text)
                        if price_match:
                            price_cents = self._parse_price(price_match.group(0))
                    
                    if not price_cents:
                        continue  # Skip products without price
                    
                    # Extract product URL
                    link_elem = (
                        item.find('a', class_='s-item__link') or
                        item.find('a', href=re.compile(r'/itm/'))
                    )
                    product_url = None
                    if link_elem:
                        href = link_elem.get('href', '')
                        if href.startswith('/'):
                            product_url = f"https://www.ebay.com{href}"
                        elif href.startswith('http'):
                            product_url = href
                    
                    # Extract image
                    img_elem = (
                        item.find('img', class_='s-item__image-img') or
                        item.find('img', class_='s-item__image') or
                        item.find('img')
                    )
                    image_url = None
                    if img_elem:
                        image_url = img_elem.get('src') or img_elem.get('data-src') or img_elem.get('data-lazy-src')
                    
                    # Extract description (often just the title)
                    desc = name
                    desc_elem = item.find('div', class_=re.compile(r'description|details', re.I))
                    if desc_elem:
                        desc = desc_elem.get_text(strip=True)[:500]
                    
                    # Extract category
                    category = "Electronics"  # Default
                    category_elem = item.find('span', class_=re.compile(r'category', re.I))
                    if category_elem:
                        category = category_elem.get_text(strip=True)
                    
                    # Extract brand from name
                    brand = self._extract_brand_from_name(name)
                    
                    # Extract condition (eBay specific)
                    condition = None
                    condition_elem = item.find('span', class_=re.compile(r'condition', re.I))
                    if condition_elem:
                        condition = condition_elem.get_text(strip=True)
                    
                    products.append(
                        ScrapedProduct(
                            name=name,
                            description=desc,
                            price_cents=price_cents,
                            category=category,
                            brand=brand,
                            image_url=image_url,
                            source_url=product_url or url,
                            source=self._source,
                        )
                    )
                except Exception as e:
                    print(f"  [WARN]  Error parsing eBay item: {e}")
                    continue
            
            print(f"  Found {len(products)} products from eBay")
            return products[:max_products]
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                print(f"[FAIL] eBay returned 403 Forbidden - anti-scraping detected")
                print(f"   → eBay may require JavaScript rendering (consider Selenium)")
                print(f"   → Try using search URLs instead of category pages")
            elif e.response.status_code == 500:
                print(f"[FAIL] eBay returned 500 error - may be blocking requests")
                print(f"   → Try again later or use different URL format")
            else:
                print(f"[FAIL] eBay HTTP error {e.response.status_code}: {e}")
            return []
        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg or "Forbidden" in error_msg:
                print(f"[FAIL] eBay 403 Forbidden - anti-scraping detected")
                print(f"   → Consider using Selenium for JavaScript-rendered content")
            elif "500" in error_msg:
                print(f"[FAIL] eBay 500 error - server blocking requests")
                print(f"   → Try search URLs: https://www.ebay.com/sch/i.html?_nkw=laptops")
            else:
                print(f"[FAIL] eBay scraping error: {e}")
            return []
    
    def _extract_brand_from_name(self, name: str) -> Optional[str]:
        """Extract brand name from product name."""
        brands = [
            'Apple', 'Dell', 'HP', 'Lenovo', 'ASUS', 'Acer', 'MSI', 'Razer',
            'Samsung', 'Microsoft', 'LG', 'Sony', 'Toshiba', 'Fujitsu',
            'Framework', 'System76', 'Alienware', 'ROG', 'ThinkPad', 'MacBook',
            'Nike', 'Adidas', 'Puma', 'Under Armour', 'Reebok',
            'Canon', 'Nikon', 'Sony', 'Fujifilm', 'Olympus',
            'Bose', 'Sony', 'JBL', 'Beats', 'Sennheiser'
        ]
        
        name_lower = name.lower()
        for brand in brands:
            if brand.lower() in name_lower:
                return brand
        return None


class ShopifyScraper(BaseScraper):
    """
    Scraper for Shopify stores.
    
    Uses the same approach as ShopifyScraper GitHub package:
    - Uses /products.json endpoint (public API, no auth needed)
    - Handles pagination via page parameter
    - Extracts variants, images, tags, and product types
    - Falls back to HTML parsing if JSON endpoint unavailable
    
    Note: For production, consider using the GitHub package directly:
    pip install git+https://github.com/practical-data-science/ShopifyScraper.git
    See: scripts/shopify_scraper_integration.py for integration example
    """

    def scrape(self, url: str, max_products: int = 20, source: Optional[str] = None) -> List[ScrapedProduct]:
        self._source = source or "Shopify"
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        
        # Try using GitHub package first (more reliable)
        try:
            from scripts.shopify_scraper_integration import scrape_shopify_with_package, SHOPIFY_SCRAPER_AVAILABLE
            if SHOPIFY_SCRAPER_AVAILABLE:
                print(f"Using ShopifyScraper GitHub package for {base}...")
                products = scrape_shopify_with_package(base, max_products)
                if products and len(products) > 0:
                    return products
                else:
                    print("[WARN]  GitHub package returned no products, falling back to custom implementation...")
        except ImportError:
            pass  # Fall back to custom implementation
        except Exception as e:
            print(f"[WARN]  GitHub package failed: {e}, falling back to custom implementation...")
        
        # Fallback: Custom implementation using /products.json
        products = self._scrape_products_json(base, max_products)
        
        # Fallback to HTML parsing if JSON endpoint fails
        if not products:
            self._rate_limit()
            try:
                r = self.session.get(url, timeout=15)
                r.raise_for_status()
                soup = BeautifulSoup(r.content, "html.parser")
                products = GenericEcommerceScraper._parse_generic_html(
                    GenericEcommerceScraper(rate_limit_delay=1.0), soup, url, max_products
                )
                for p in products:
                    p.source = getattr(self, "_source", "Shopify")
            except Exception as e:
                print(f"Shopify HTML fallback error: {e}")
        
        return products

    def _scrape_products_json(self, base_url: str, max_products: int) -> List[ScrapedProduct]:
        """
        Scrape via /products.json endpoint.
        
        Per shopifywebscrape.txt:
        - Shopify stores expose /products.json publicly
        - Returns structured JSON with products, variants, images
        - Supports pagination with ?page= parameter
        - Limit per page: 250 products (Shopify API limit)
        """
        all_products = []
        page = 1
        per_page = min(max_products, 250)  # Shopify max is 250 per page
        
        while len(all_products) < max_products:
            self._rate_limit()
            url = urljoin(base_url.rstrip("/") + "/", "products.json")
            
            try:
                # Use page parameter for pagination (per ShopifyScraper GitHub repo)
                params = {"page": page, "limit": per_page}
                r = self.session.get(url, params=params, timeout=15)
                
                if r.status_code != 200:
                    # If first page fails, return empty (endpoint not available)
                    if page == 1:
                        return []
                    # Otherwise, we've reached the end
                    break
                
                data = r.json()
                
                # Handle both dict and list responses
                if isinstance(data, dict):
                    items = data.get("products", [])
                elif isinstance(data, list):
                    items = data
                else:
                    break
                
                # If no products returned, we've reached the end
                if not items:
                    break
                
                # Parse products from this page
                for obj in items:
                    if len(all_products) >= max_products:
                        break
                    
                    if not isinstance(obj, dict):
                        continue
                    
                    product = self._parse_shopify_product(obj, base_url)
                    if product:
                        all_products.append(product)
                
                # If we got fewer products than requested, we've reached the end
                if len(items) < per_page:
                    break
                
                page += 1
                
            except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
                print(f"Error scraping Shopify page {page}: {e}")
                if page == 1:
                    return []  # First page failed, endpoint not available
                break
        
        return all_products[:max_products]

    def _parse_shopify_product(self, obj: Dict[str, Any], base_url: str) -> Optional[ScrapedProduct]:
        """
        Parse a single Shopify product from JSON.
        
        Extracts:
        - Title, description (body_html)
        - Variants (price, inventory, SKU)
        - Images (first image URL)
        - Vendor (brand)
        - Product type (category)
        - Tags (for subcategory)
        - Handle (for product URL)
        """
        try:
            title = obj.get("title", "").strip()
            if not title:
                return None
            
            # Description: use body_html, fallback to title
            body_html = obj.get("body_html", "")
            description = body_html[:500] if body_html else title
            
            # Get variants (Shopify products can have multiple variants)
            variants = obj.get("variants", [])
            if not variants:
                return None
            
            # Use first variant for price and inventory
            # In production, you might want to use the cheapest variant or average
            first_variant = variants[0]
            price_str = str(first_variant.get("price", "0"))
            price_cents = self._parse_price(price_str)
            
            if not price_cents:
                return None
            
            # Inventory: sum across all variants if available
            inventory_qty = 0
            for variant in variants:
                inv = variant.get("inventory_quantity")
                if inv is not None:
                    inventory_qty += inv
            
            # If no inventory data, use random default
            if inventory_qty == 0:
                inventory_qty = random.randint(5, 100)
            
            # Images: get first image URL
            images = obj.get("images", [])
            img_url = None
            if images and isinstance(images[0], dict):
                img_url = images[0].get("src")
            
            # Brand/Vendor
            brand = obj.get("vendor", "").strip() or None
            
            # Category: use product_type, fallback to first tag, then default
            product_type = obj.get("product_type", "").strip()
            tags = obj.get("tags", "")
            if isinstance(tags, str):
                tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            else:
                tag_list = []
            
            category = "Electronics"  # Default
            if product_type:
                category = self._normalize_category(product_type)
            elif tag_list:
                category = self._normalize_category(tag_list[0])
            
            # Subcategory: use first tag if available
            subcategory = tag_list[0] if tag_list else None
            
            # Product URL: construct from handle
            handle = obj.get("handle", "")
            if handle:
                link = f"{base_url}/products/{handle}"
            else:
                link = base_url
            
            # Extract color from variant options or tags
            color = None
            variant_options = first_variant.get("option1") or first_variant.get("option2") or first_variant.get("option3")
            if variant_options and any(c in variant_options.lower() for c in ["color", "colour", "red", "blue", "black", "white", "silver", "gray"]):
                color = variant_options
            elif tag_list:
                for tag in tag_list:
                    tag_lower = tag.lower()
                    if any(c in tag_lower for c in ["red", "blue", "black", "white", "silver", "gray", "gold", "pink"]):
                        color = tag
                        break
            
            return ScrapedProduct(
                name=title,
                description=description,
                price_cents=price_cents,
                category=category,
                subcategory=subcategory,
                brand=brand,
                image_url=img_url,
                available_qty=inventory_qty,
                source_url=link,
                source=getattr(self, "_source", "Shopify"),
                color=color,
            )
        except Exception as e:
            print(f"Error parsing Shopify product: {e}")
            return None


class BarnesNobleScraper(BaseScraper):
    """
    Scraper for Barnes & Noble books.
    
    Note: B&N may have anti-scraping measures. This scraper:
    - Uses realistic browser headers
    - Implements rate limiting (2-3 second delays)
    - Handles pagination
    - Extracts book title, author, price, format, genre
    
    For production, consider using B&N API if available.
    """
    
    def __init__(self, rate_limit_delay: float = 2.5):
        super().__init__(rate_limit_delay)
        self._source = "Barnes & Noble"
        # Enhanced headers for B&N
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
            'Referer': 'https://www.barnesandnoble.com/',
        })
    
    def scrape(self, url: str, max_products: int = 20, source: Optional[str] = None) -> List[ScrapedProduct]:
        """
        Scrape books from Barnes & Noble.
        
        Args:
            url: B&N URL (category page, search results, or product listing)
            max_products: Maximum products to return
            source: Override source label (default: "Barnes & Noble")
        
        Returns:
            List of ScrapedProduct objects
        """
        if source:
            self._source = source
        
        self._rate_limit()
        
        try:
            print(f"Scraping Barnes & Noble: {url}")
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            products = []
            
            # B&N uses various selectors - try multiple strategies
            # Strategy 1: Product cards with class patterns
            product_selectors = [
                ('div', {'class': re.compile(r'product-shelf-item|product-item|book-item|product-tile', re.I)}),
                ('div', {'class': re.compile(r'product-info|product-detail', re.I)}),
                ('article', {'class': re.compile(r'product|book', re.I)}),
                ('li', {'class': re.compile(r'product|book|item', re.I)}),
            ]
            
            seen_titles = set()
            
            for tag, attrs in product_selectors:
                items = soup.find_all(tag, attrs)
                if items:
                    print(f"  Found {len(items)} potential book items using {tag} selector")
                    break
            
            if not items:
                # Fallback: Look for any div/article with book-related content
                items = soup.find_all(['div', 'article', 'li'], class_=re.compile(r'.*', re.I))
                items = [item for item in items if self._looks_like_book_item(item)]
            
            for item in items[:max_products * 2]:  # Get more candidates
                try:
                    # Extract title
                    title_elem = (
                        item.find('h3', class_=re.compile(r'title|name|product-title', re.I)) or
                        item.find('h2', class_=re.compile(r'title|name', re.I)) or
                        item.find('a', class_=re.compile(r'title|product-title', re.I)) or
                        item.find('span', class_=re.compile(r'title|name', re.I)) or
                        item.find('div', class_=re.compile(r'title|name', re.I))
                    )
                    
                    if not title_elem:
                        continue
                    
                    title = title_elem.get_text(strip=True)
                    if not title or len(title) < 3 or title.lower() in seen_titles:
                        continue
                    seen_titles.add(title.lower())
                    
                    # Extract author
                    author_elem = (
                        item.find('div', class_=re.compile(r'author|contributor|writer', re.I)) or
                        item.find('span', class_=re.compile(r'author', re.I)) or
                        item.find('p', class_=re.compile(r'author', re.I))
                    )
                    author = author_elem.get_text(strip=True) if author_elem else None
                    
                    # Extract price
                    price_elem = (
                        item.find('span', class_=re.compile(r'price|cost|amount', re.I)) or
                        item.find('div', class_=re.compile(r'price', re.I)) or
                        item.find('p', class_=re.compile(r'price', re.I)) or
                        item.find(string=re.compile(r'\$[\d,]+\.?\d*'))
                    )
                    
                    price_cents = None
                    if price_elem:
                        if hasattr(price_elem, 'get_text'):
                            price_text = price_elem.get_text(strip=True)
                        else:
                            price_text = str(price_elem).strip()
                        price_cents = self._parse_price(price_text)
                    
                    # If no price found, search in item text
                    if not price_cents:
                        item_text = item.get_text()
                        price_match = re.search(r'\$[\d,]+\.?\d*', item_text)
                        if price_match:
                            price_cents = self._parse_price(price_match.group(0))
                    
                    if not price_cents:
                        continue  # Skip books without price
                    
                    # Extract format (Paperback, Hardcover, E-book, etc.)
                    format_elem = (
                        item.find('span', class_=re.compile(r'format|edition|binding', re.I)) or
                        item.find('div', class_=re.compile(r'format', re.I))
                    )
                    format_type = format_elem.get_text(strip=True) if format_elem else None
                    
                    # Extract genre/category
                    genre_elem = (
                        item.find('span', class_=re.compile(r'genre|category|subject', re.I)) or
                        item.find('div', class_=re.compile(r'genre|category', re.I))
                    )
                    genre = genre_elem.get_text(strip=True) if genre_elem else None
                    
                    # Extract image
                    img_elem = item.find('img')
                    image_url = None
                    if img_elem:
                        image_url = img_elem.get('src') or img_elem.get('data-src') or img_elem.get('data-lazy-src')
                    
                    # Extract product URL
                    link_elem = item.find('a', href=True)
                    product_url = None
                    if link_elem:
                        href = link_elem.get('href', '')
                        if href.startswith('/'):
                            product_url = f"https://www.barnesandnoble.com{href}"
                        elif href.startswith('http'):
                            product_url = href
                    
                    # Build description
                    description_parts = []
                    if author:
                        description_parts.append(f"By {author}")
                    if format_type:
                        description_parts.append(f"Format: {format_type}")
                    if genre:
                        description_parts.append(f"Genre: {genre}")
                    description = ". ".join(description_parts) if description_parts else title
                    
                    # Map genre to subcategory
                    subcategory = None
                    if genre:
                        genre_lower = genre.lower()
                        if any(g in genre_lower for g in ['fiction', 'novel', 'literature']):
                            subcategory = "Fiction"
                        elif any(g in genre_lower for g in ['mystery', 'thriller', 'suspense']):
                            subcategory = "Mystery"
                        elif any(g in genre_lower for g in ['sci-fi', 'science fiction', 'fantasy']):
                            subcategory = "Science Fiction"
                        elif any(g in genre_lower for g in ['non-fiction', 'nonfiction', 'biography', 'history']):
                            subcategory = "Non-fiction"
                    
                    products.append(
                        ScrapedProduct(
                            name=title,
                            description=description,
                            price_cents=price_cents,
                            category="Books",
                            subcategory=subcategory,
                            brand=author,  # Use author as "brand" for books
                            image_url=image_url,
                            source_url=product_url or url,
                            source=self._source,
                        )
                    )
                except Exception as e:
                    print(f"  [WARN]  Error parsing B&N book: {e}")
                    continue
            
            print(f"  Found {len(products)} books from Barnes & Noble")
            return products[:max_products]
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                print(f"[FAIL] Barnes & Noble returned 403 Forbidden - anti-scraping detected")
                print(f"   → B&N may require JavaScript rendering (consider Selenium)")
                print(f"   → Try using search URLs: https://www.barnesandnoble.com/b/books/_/N-1fZ29Z8q8")
            else:
                print(f"[FAIL] B&N HTTP error {e.response.status_code}: {e}")
            return []
        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg or "Forbidden" in error_msg:
                print(f"[FAIL] B&N 403 Forbidden - anti-scraping detected")
                print(f"   → Consider using Selenium for JavaScript-rendered content")
            else:
                print(f"[FAIL] B&N scraping error: {e}")
            return []
    
    def _looks_like_book_item(self, item) -> bool:
        """Check if an element looks like a book product item."""
        text = item.get_text().lower()
        # Look for book-related keywords
        book_keywords = ['book', 'author', 'publisher', 'isbn', 'edition', 'paperback', 'hardcover']
        return any(keyword in text for keyword in book_keywords) and len(text) > 20


def infer_source_from_url(url: str) -> str:
    """Infer source label (WooCommerce, Shopify, Temu, BigCommerce, Etsy, eBay, Barnes & Noble, etc.) from URL."""
    domain = urlparse(url).netloc.lower()
    if 'temu.com' in domain or 'temu.' in domain:
        return 'Temu'
    if 'woocommerce' in domain or 'wp-json' in url or 'wordpress' in domain:
        return 'WooCommerce'
    if 'shopify' in domain or 'myshopify.com' in domain:
        return 'Shopify'
    if 'bigcommerce' in domain or 'mybigcommerce.com' in domain:
        return 'BigCommerce'
    if 'etsy.com' in domain or 'etsy.' in domain:
        return 'Etsy'
    if 'ebay.com' in domain or 'ebay.' in domain:
        return 'eBay'
    if 'barnesandnoble.com' in domain or 'bn.com' in domain:
        return 'Barnes & Noble'
    if 'amazon.' in domain:
        return 'Amazon'
    return 'Generic'


def get_scraper(url: str, use_selenium_for_temu: bool = False) -> BaseScraper:
    """
    Get appropriate scraper for URL.
    
    Args:
        url: URL to scrape
        use_selenium_for_temu: If True, use Selenium for Temu (requires selenium package)
        
    Returns:
        Appropriate scraper instance
    """
    domain = urlparse(url).netloc.lower()
    
    if 'temu.com' in domain or 'temu.' in domain:
        if use_selenium_for_temu:
            try:
                from scripts.temu_selenium_scraper import TemuSeleniumScraper
                return TemuSeleniumScraper(rate_limit_delay=3.0, headless=True)
            except (ImportError, RuntimeError) as e:
                print(f"[WARN]  Selenium not available for Temu: {e}")
                print("   Falling back to static HTML scraper (may return 0 products)")
                return TemuScraper(rate_limit_delay=2.0)
        else:
            return TemuScraper(rate_limit_delay=2.0)
    if 'etsy.com' in domain or 'etsy.' in domain:
        # Etsy: 2-3 second delays (respectful crawling per webscrape.txt)
        return EtsyScraper(rate_limit_delay=2.5)
    if 'ebay.com' in domain or 'ebay.' in domain:
        # eBay: 2-3 second delays (respectful crawling, anti-scraping measures)
        return eBayScraper(rate_limit_delay=2.5)
    if 'woocommerce' in domain or 'wp-json' in url or 'wordpress' in domain:
        return WooCommerceScraper(rate_limit_delay=1.0)
    if 'shopify' in domain or 'myshopify.com' in domain:
        return ShopifyScraper(rate_limit_delay=1.0)
    if 'bigcommerce' in domain or 'mybigcommerce.com' in domain:
        return BigCommerceScraper(rate_limit_delay=1.0)
    if 'barnesandnoble.com' in domain or 'bn.com' in domain:
        # B&N: 2-3 second delays (respectful crawling, anti-scraping measures)
        return BarnesNobleScraper(rate_limit_delay=2.5)
    return GenericEcommerceScraper(rate_limit_delay=1.0)


def scrape_products(urls: List[str], max_per_url: int = 20, use_selenium_for_temu: bool = False) -> List[ScrapedProduct]:
    """
    Scrape products from multiple URLs.
    
    Args:
        urls: List of URLs to scrape
        max_per_url: Maximum products per URL
        use_selenium_for_temu: If True, use Selenium for Temu URLs (requires selenium package)
        
    Returns:
        List of all scraped products
    """
    all_products = []
    
    for url in urls:
        source = infer_source_from_url(url)
        print(f"Scraping {url} (source={source})...")
        # Auto-detect Temu and use Selenium if requested
        is_temu = 'temu.com' in url.lower() or 'temu.' in url.lower()
        use_selenium = use_selenium_for_temu and is_temu
        scraper = get_scraper(url, use_selenium_for_temu=use_selenium)
        products = scraper.scrape(url, max_products=max_per_url, source=source)
        all_products.extend(products)
        print(f"  Found {len(products)} products")
        
        # Rate limiting: Temu/Etsy/eBay need longer delays (2-3 seconds per webscrape.txt)
        is_etsy = 'etsy.com' in url.lower() or 'etsy.' in url.lower()
        is_ebay = 'ebay.com' in url.lower() or 'ebay.' in url.lower()
        if is_temu:
            delay = 3.0
        elif is_etsy or is_ebay:
            delay = 2.5 + random.random() * 0.5  # 2.5-3.0 seconds
        else:
            delay = 1 + random.random() * 2  # 1-3 seconds
        time.sleep(delay)
    
    return all_products
