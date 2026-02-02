#!/usr/bin/env python3
"""
Selenium-based scraper for Temu.com products.

Temu uses heavy JavaScript rendering, so we need a real browser to render the page
before we can extract product data.
"""

import time
import re
import json
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.common.exceptions import TimeoutException, WebDriverException
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    print("[WARN] Selenium not installed. Install with: pip install selenium")
    print("   Also need ChromeDriver: brew install chromedriver (Mac) or download from https://chromedriver.chromium.org/")

from scripts.product_scraper import ScrapedProduct, BaseScraper

# Import BaseScraper methods we need
import re


class TemuSeleniumScraper(BaseScraper):
    """
    Selenium-based scraper for Temu.com.
    
    Uses a real browser to render JavaScript, then extracts product data.
    """
    
    def __init__(self, rate_limit_delay: float = 3.0, headless: bool = True):
        """
        Initialize Selenium scraper.
        
        Args:
            rate_limit_delay: Minimum seconds between requests (Temu needs longer delays)
            headless: Run browser in headless mode (no GUI)
        """
        super().__init__(rate_limit_delay)
        self.headless = headless
        self.driver = None
        
        if not SELENIUM_AVAILABLE:
            raise ImportError("Selenium not installed. Run: pip install selenium")
    
    def _get_driver(self):
        """Get or create Chrome WebDriver."""
        if self.driver is None:
            options = ChromeOptions()
            if self.headless:
                options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # Realistic user agent
            options.add_argument('user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            
            try:
                self.driver = webdriver.Chrome(options=options)
                # Hide automation indicators
                self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                    'source': '''
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined
                        })
                    '''
                })
            except WebDriverException as e:
                raise RuntimeError(f"Failed to start ChromeDriver: {e}. Make sure ChromeDriver is installed and in PATH.")
        
        return self.driver
    
    def _close_driver(self):
        """Close the WebDriver."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
    
    def scrape(self, url: str, max_products: int = 20, source: Optional[str] = None) -> List[ScrapedProduct]:
        """
        Scrape products from Temu using Selenium.
        
        Args:
            url: Temu category or product page URL
            max_products: Maximum products to scrape
            source: Source label (default: "Temu")
            
        Returns:
            List of ScrapedProduct objects
        """
        self._source = source or "Temu"
        self._rate_limit()
        
        if not SELENIUM_AVAILABLE:
            print("[FAIL] Selenium not available - cannot scrape Temu")
            return []
        
        driver = None
        try:
            driver = self._get_driver()
            print(f"Loading Temu page: {url}...")
            driver.get(url)
            
            # Wait for page to load (Temu uses JavaScript)
            time.sleep(3)  # Give JavaScript time to render
            
            # Try multiple selectors for product cards
            products = []
            
            # Method 1: Look for product cards in the rendered HTML
            try:
                # Wait for products to appear (various possible selectors)
                wait = WebDriverWait(driver, 10)
                
                # Try common Temu product selectors
                selectors = [
                    "[data-product-id]",
                    ".product-item",
                    ".goods-item",
                    "[class*='product']",
                    "[class*='item']",
                ]
                
                product_elements = []
                for selector in selectors:
                    try:
                        elements = driver.find_elements(By.CSS_SELECTOR, selector)
                        if elements:
                            product_elements = elements[:max_products * 2]
                            print(f"Found {len(elements)} elements with selector: {selector}")
                            break
                    except Exception:
                        continue
                
                # If no specific selector works, get all clickable links that might be products
                if not product_elements:
                    print("Trying to find product links...")
                    links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/p/'], a[href*='/product/']")
                    product_elements = links[:max_products * 2]
                
                # Extract product data from elements
                seen_names = set()
                for elem in product_elements[:max_products * 3]:
                    try:
                        product = self._extract_product_from_element(elem, url)
                        if product and product.name.lower() not in seen_names:
                            seen_names.add(product.name.lower())
                            products.append(product)
                            if len(products) >= max_products:
                                break
                    except Exception as e:
                        continue
                
            except TimeoutException:
                print("[WARN]  Timeout waiting for products - page may have loaded differently")
            except Exception as e:
                print(f"[WARN]  Error extracting products: {e}")
            
            # Method 2: Try to extract from page source (JSON data)
            if not products:
                print("Trying to extract from page source...")
                page_source = driver.page_source
                products = self._extract_from_page_source(page_source, url, max_products)
            
            print(f"[OK] Extracted {len(products)} products from Temu")
            return products[:max_products]
            
        except Exception as e:
            print(f"[FAIL] Error scraping Temu with Selenium: {e}")
            return []
        finally:
            # Don't close driver - reuse it for multiple requests
            pass
    
    def _extract_product_from_element(self, element, source_url: str) -> Optional[ScrapedProduct]:
        """Extract product data from a Selenium WebElement."""
        try:
            # Get text content
            text = element.text.strip()
            if not text or len(text) < 5:
                return None
            
            # Try to find name
            name = None
            try:
                # Look for title/name in nested elements
                title_elem = element.find_element(By.CSS_SELECTOR, "[class*='title'], [class*='name'], h2, h3, h4")
                name = title_elem.text.strip()
            except Exception:
                # Use first line of text as name
                lines = text.split('\n')
                name = lines[0].strip() if lines else text[:100]
            
            if not name or len(name) < 3:
                return None
            
            # Try to find price
            price_cents = None
            try:
                price_elem = element.find_element(By.CSS_SELECTOR, "[class*='price'], [class*='cost'], [class*='amount']")
                price_str = price_elem.text.strip()
                price_cents = self._parse_price(price_str)
            except Exception:
                # Try to extract price from text
                price_match = re.search(r'\$?(\d+\.?\d*)', text)
                if price_match:
                    price_cents = self._parse_price(price_match.group(1))
            
            if not price_cents:
                # Default price if not found (better than nothing)
                price_cents = 999  # $9.99 placeholder
            
            # Try to find image
            image_url = None
            try:
                img_elem = element.find_element(By.CSS_SELECTOR, "img")
                image_url = img_elem.get_attribute("src") or img_elem.get_attribute("data-src")
            except Exception:
                pass
            
            # Try to find product URL
            product_url = None
            try:
                if element.tag_name == 'a':
                    product_url = element.get_attribute("href")
                else:
                    link = element.find_element(By.CSS_SELECTOR, "a")
                    product_url = link.get_attribute("href")
            except Exception:
                pass
            
            return ScrapedProduct(
                name=name[:200],
                description=name,
                price_cents=price_cents,
                category='Electronics',
                source_url=product_url or source_url,
                source=getattr(self, '_source', 'Temu'),
                image_url=image_url,
            )
        except Exception as e:
            return None
    
    def _extract_from_page_source(self, page_source: str, source_url: str, max_products: int) -> List[ScrapedProduct]:
        """Extract products from page source (look for JSON data)."""
        products = []
        
        # Look for JSON-LD or script tags with product data
        json_ld_pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
        matches = re.findall(json_ld_pattern, page_source, re.DOTALL)
        
        for match in matches[:max_products]:
            try:
                data = json.loads(match)
                if isinstance(data, dict) and data.get('@type') == 'Product':
                    product = self._parse_json_ld_product(data, source_url)
                    if product:
                        products.append(product)
            except (json.JSONDecodeError, KeyError):
                continue
        
        # Look for product arrays in script tags
        script_pattern = r'<script[^>]*>(.*?)</script>'
        scripts = re.findall(script_pattern, page_source, re.DOTALL)
        
        for script in scripts:
            # Look for product-like objects
            product_pattern = r'\{"name":\s*"([^"]+)",\s*"price":\s*"?(\d+\.?\d*)"?\}'
            matches = re.findall(product_pattern, script, re.I)
            for name, price_str in matches[:max_products]:
                try:
                    price_cents = self._parse_price(price_str)
                    if price_cents:
                        products.append(ScrapedProduct(
                            name=name[:200],
                            description=name,
                            price_cents=price_cents,
                            category='Electronics',
                            source_url=source_url,
                            source=getattr(self, '_source', 'Temu'),
                        ))
                except Exception:
                    continue
        
        return products
    
    def _parse_json_ld_product(self, data: Dict[str, Any], source_url: str) -> Optional[ScrapedProduct]:
        """Parse product from JSON-LD data."""
        try:
            name = data.get('name', '')
            description = data.get('description', name)
            price_str = str(data.get('offers', {}).get('price', '0'))
            price_cents = self._parse_price(price_str)
            
            if not name or not price_cents:
                return None
            
            return ScrapedProduct(
                name=name[:200],
                description=description[:500] if description else name,
                price_cents=price_cents,
                category='Electronics',
                brand=data.get('brand', {}).get('name') if isinstance(data.get('brand'), dict) else data.get('brand'),
                image_url=data.get('image'),
                source_url=source_url,
                source=getattr(self, '_source', 'Temu'),
            )
        except Exception:
            return None
    
    def __del__(self):
        """Cleanup: close driver when scraper is destroyed."""
        self._close_driver()
