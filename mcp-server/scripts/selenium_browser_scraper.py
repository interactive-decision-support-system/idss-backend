#!/usr/bin/env python3
"""
Selenium-based scrapers for sites that block requests (403): Framework, Back Market, Fairphone.

Uses real browser to bypass anti-bot measures.
"""

import time
import re
import json
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse, urljoin

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.common.exceptions import TimeoutException, WebDriverException
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

from scripts.product_scraper import ScrapedProduct, BaseScraper

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


def _get_driver(headless: bool = True):
    """Create Chrome WebDriver."""
    if not SELENIUM_AVAILABLE:
        return None
    options = ChromeOptions()
    if headless:
        options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    try:
        return webdriver.Chrome(options=options)
    except WebDriverException:
        return None


class SeleniumFrameworkScraper(BaseScraper):
    """Selenium scraper for Framework (frame.work) - custom marketplace, not Shopify."""

    def scrape(self, url: str, max_products: int = 50, source: Optional[str] = None) -> List[ScrapedProduct]:
        self._source = source or "Framework"
        if not SELENIUM_AVAILABLE or not BeautifulSoup:
            return []
        driver = None
        try:
            driver = _get_driver()
            base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
            # Framework uses /marketplace for product listing (not products.json)
            marketplace_url = urljoin(base, "/marketplace")
            driver.get(marketplace_url)
            time.sleep(5)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            products = []
            for article in soup.select("article.product-tile")[:max_products]:
                try:
                    name_el = article.find(class_=re.compile(r"product|title|name", re.I))
                    name = (name_el.get_text(strip=True) if name_el else "").replace("Starting at", "").strip()
                    name = re.sub(r"\s*[Ss]tarting\s*(at)?\s*$", "", name)
                    if not name:
                        name = article.get_text(strip=True)[:120]
                    if len(name) < 5:
                        continue
                    text = article.get_text()
                    price_m = re.search(r"\$[\d,]+(?:\.\d{2})?", text)
                    price_str = price_m.group(0) if price_m else ""
                    price_cents = self._parse_price(price_str)
                    if not price_cents or price_cents < 10000:
                        continue
                    link = article.find("a", href=True)
                    rel_href = link["href"] if link else ""
                    source_url = urljoin(base, rel_href) if rel_href else marketplace_url
                    img = article.find("img", src=True)
                    img_url = img["src"] if img else None
                    if img_url and not img_url.startswith("http"):
                        img_url = urljoin(base, img_url)
                    products.append(ScrapedProduct(
                        name=name[:200],
                        description=f"Framework {name}. Repairable, modular laptop.",
                        price_cents=price_cents,
                        category="Electronics",
                        subcategory="Laptop",
                        brand="Framework",
                        image_url=img_url,
                        source_url=source_url,
                        source=getattr(self, "_source", "Framework"),
                    ))
                except Exception:
                    continue
            return products[:max_products]
        except Exception as e:
            print(f"  [WARN] Selenium Framework: {e}")
        finally:
            if driver:
                driver.quit()
        return []


class SeleniumBackMarketScraper(BaseScraper):
    """Selenium scraper for Back Market - refurbished electronics."""

    def scrape(self, url: str, max_products: int = 50, source: Optional[str] = None) -> List[ScrapedProduct]:
        self._source = source or "Back Market"
        if not SELENIUM_AVAILABLE or not BeautifulSoup:
            return []
        driver = None
        try:
            driver = _get_driver()
            driver.get(url)
            time.sleep(5)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            products = []
            for card in soup.select("[class*='product'], [class*='listing'], [data-testid*='product']")[:max_products * 2]:
                try:
                    name_el = card.find(["h2", "h3", "a"], class_=re.compile(r"title|name|product", re.I))
                    name = name_el.get_text(strip=True) if name_el else ""
                    if not name or len(name) < 5:
                        continue
                    price_el = card.find(["span", "div"], class_=re.compile(r"price|amount", re.I))
                    price_str = price_el.get_text(strip=True) if price_el else ""
                    price_cents = self._parse_price(price_str)
                    if not price_cents or price_cents < 10000:
                        continue
                    a = card.find("a", href=True)
                    source_url = urljoin(url, a["href"]) if a else url
                    products.append(ScrapedProduct(
                        name=name[:200],
                        description=f"Refurbished {name}. Back Market certified.",
                        price_cents=price_cents,
                        category="Electronics",
                        subcategory="Refurbished",
                        brand="Various",
                        source_url=source_url,
                        source=getattr(self, "_source", "Back Market"),
                    ))
                except Exception:
                    continue
            seen = set()
            out = []
            for p in products:
                k = (p.name.lower()[:50], p.price_cents)
                if k not in seen:
                    seen.add(k)
                    out.append(p)
            return out[:max_products]
        except Exception as e:
            print(f"  [WARN] Selenium Back Market: {e}")
        finally:
            if driver:
                driver.quit()
        return []


class SeleniumFairphoneScraper(BaseScraper):
    """Selenium scraper for Fairphone (shop.fairphone.com) - sustainable smartphones."""

    def scrape(self, url: str, max_products: int = 30, source: Optional[str] = None) -> List[ScrapedProduct]:
        self._source = source or "Fairphone"
        if not SELENIUM_AVAILABLE or not BeautifulSoup:
            return []
        driver = None
        try:
            driver = _get_driver()
            driver.get(url)
            time.sleep(4)
            base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
            soup = BeautifulSoup(driver.page_source, "html.parser")
            products = []
            # Fairphone uses f_h_card_wrapper for product cards (products.json returns HTML)
            for card in soup.select(".f_h_card_wrapper, .f_grid_wrapper")[:max_products * 2]:
                try:
                    text = card.get_text()
                    name_m = re.search(
                        r"The Fairphone[^\n]+|Fairbuds[^\n]*|[Ff]airphone[^\n]{5,60}",
                        text,
                        re.DOTALL,
                    )
                    name = (name_m.group(0).strip() if name_m else "")[:200]
                    if not name or len(name) < 5:
                        continue
                    price_m = re.search(r"€[\d,.]+", text)
                    price_str = price_m.group(0) if price_m else ""
                    price_cents = self._parse_price(price_str)
                    if not price_cents or price_cents < 10000:
                        continue
                    link = card.find("a", href=True)
                    rel_href = link["href"] if link else ""
                    source_url = urljoin(base, rel_href) if rel_href else url
                    img = card.find("img", src=True)
                    img_url = urljoin(base, img["src"]) if img and img.get("src") else None
                    subcat = "Audio" if "Fairbuds" in name or "fairbuds" in name.lower() else "Smartphone"
                    products.append(ScrapedProduct(
                        name=name,
                        description=f"Fairphone {name}. Repairable, sustainable smartphone.",
                        price_cents=price_cents,
                        category="Electronics",
                        subcategory=subcat,
                        brand="Fairphone",
                        image_url=img_url,
                        source_url=source_url,
                        source=getattr(self, "_source", "Fairphone"),
                    ))
                except Exception:
                    continue
            seen = set()
            out = []
            for p in products:
                k = (p.name.lower()[:50], p.price_cents)
                if k not in seen:
                    seen.add(k)
                    out.append(p)
            return out[:max_products]
        except Exception as e:
            print(f"  [WARN] Selenium Fairphone: {e}")
        finally:
            if driver:
                driver.quit()
        return []
