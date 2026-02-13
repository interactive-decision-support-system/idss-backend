"""
Unit tests for populate_real_only_db.py (§3.1).

Covers:
- _enrich_with_policy(p) – appends shipping/return/warranty text to descriptions
- DEFAULT_POLICY_SUFFIX – standard policy text
- _has_real_image(p) – detects real vs placeholder images
- filter_real_only(..., remove_missing_images=True) – drops products without real images
- --keep-missing-images – keeps products with placeholders when False for remove_missing_images
- fetch_open_library_books() – fallback when B&N times out
- Integration: _enrich_with_policy called before deduplication for every product
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add mcp-server to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.populate_real_only_db import (
    filter_real_only,
    _has_real_image,
    _enrich_with_policy,
    DEFAULT_POLICY_SUFFIX,
    fetch_open_library_books,
    OPEN_LIBRARY_SEARCH_QUERIES,
)


class TestHasRealImage:
    """Tests for _has_real_image()."""

    def test_empty_url_returns_false(self):
        assert _has_real_image({}) is False
        assert _has_real_image({"image_url": ""}) is False
        assert _has_real_image({"image_url": None}) is False

    def test_non_http_url_returns_false(self):
        assert _has_real_image({"image_url": "data:image/png;base64,abc"}) is False
        assert _has_real_image({"image_url": "/relative/path.png"}) is False
        assert _has_real_image({"image_url": "  "}) is False

    def test_unsplash_placeholder_returns_false(self):
        url = "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=800"
        assert _has_real_image({"image_url": url}) is False

    def test_placeholder_in_url_returns_false(self):
        assert _has_real_image({"image_url": "https://example.com/placeholder.png"}) is False
        assert _has_real_image({"image_url": "https://cdn.example.com/PLACEHOLDER-img.jpg"}) is False

    def test_real_image_url_returns_true(self):
        assert _has_real_image({"image_url": "https://cdn.example.com/product-123.jpg"}) is True
        assert _has_real_image({"image_url": "https://system76.com/img/lemp13-hero.png"}) is True
        assert _has_real_image({"image_url": "https://shop.fairphone.com/img/fp5.png"}) is True


class TestFilterRealOnly:
    """Tests for filter_real_only(..., remove_missing_images=...)."""

    def _make_product(self, category="Electronics", product_type="laptop", image_url=None):
        return {
            "name": "Test Laptop",
            "description": "A test product",
            "category": category,
            "product_type": product_type,
            "image_url": image_url,
        }

    def test_remove_missing_images_true_drops_products_without_real_images(self):
        products = [
            self._make_product(image_url=""),  # no image
            self._make_product(image_url="https://images.unsplash.com/photo-123?w=800"),  # placeholder
            self._make_product(image_url=None),  # missing
        ]
        result = filter_real_only(products, remove_missing_images=True)
        assert len(result) == 0

    def test_remove_missing_images_true_keeps_products_with_real_images(self):
        products = [
            self._make_product(image_url="https://cdn.example.com/laptop1.jpg"),
            self._make_product(image_url="https://system76.com/img/lemur.png", product_type="laptop"),
        ]
        result = filter_real_only(products, remove_missing_images=True)
        assert len(result) == 2

    def test_remove_missing_images_false_keeps_products_with_placeholders(self):
        products = [
            self._make_product(image_url="https://images.unsplash.com/photo-123?w=800"),  # placeholder
            self._make_product(image_url=""),  # empty
        ]
        result = filter_real_only(products, remove_missing_images=False)
        # Both get _ensure_image placeholder and are kept
        assert len(result) == 2
        assert result[0]["image_url"]  # placeholder assigned
        assert result[1]["image_url"]  # placeholder assigned

    def test_filters_by_category(self):
        products = [
            self._make_product(category="Electronics", image_url="https://cdn.example.com/a.jpg"),
            self._make_product(category="Clothing", image_url="https://cdn.example.com/b.jpg"),  # wrong category
        ]
        result = filter_real_only(products, categories={"Electronics"}, remove_missing_images=True)
        assert len(result) == 1
        assert result[0]["category"] == "Electronics"

    def test_mixed_real_and_placeholder_with_remove_true(self):
        products = [
            self._make_product(image_url="https://cdn.example.com/real.jpg"),
            self._make_product(image_url="https://images.unsplash.com/placeholder.png"),
        ]
        result = filter_real_only(products, remove_missing_images=True)
        assert len(result) == 1
        assert "unsplash" not in result[0]["image_url"]

    def test_phones_with_real_images_kept(self):
        products = [
            self._make_product(
                product_type="smartphone",
                image_url="https://shop.fairphone.com/img/fp5.png",
            ),
        ]
        result = filter_real_only(products, remove_missing_images=True)
        assert len(result) == 1
        assert result[0]["product_type"] == "smartphone"

    def test_keep_missing_images_flag_keeps_all(self):
        """--keep-missing-images => remove_missing_images=False => placeholders kept."""
        products = [
            self._make_product(image_url=""),
            self._make_product(image_url="https://images.unsplash.com/photo-123"),
        ]
        result = filter_real_only(products, remove_missing_images=False)
        assert len(result) == 2


class TestDefaultPolicySuffix:
    """Tests for DEFAULT_POLICY_SUFFIX."""

    def test_exists_and_contains_keywords(self):
        assert "Shipping" in DEFAULT_POLICY_SUFFIX
        assert "Returns" in DEFAULT_POLICY_SUFFIX
        assert "Warranty" in DEFAULT_POLICY_SUFFIX
        assert "30-day" in DEFAULT_POLICY_SUFFIX or "return" in DEFAULT_POLICY_SUFFIX.lower()


class TestEnrichWithPolicy:
    """Tests for _enrich_with_policy(p)."""

    def test_appends_policy_when_not_present(self):
        with patch("scripts.policy_scraper.get_policy_for_product") as mock_get:
            mock_get.return_value = " Shipping: Free. Returns: 30 days. Warranty: 1yr."
            p = {"description": "A laptop.", "source": "System76", "scraped_from_url": "https://system76.com/laptops"}
            _enrich_with_policy(p)
            assert "A laptop." in p["description"]
            assert "Shipping:" in p["description"]
            assert "30 days" in p["description"]

    def test_does_not_duplicate_if_policy_already_in_description(self):
        with patch("scripts.policy_scraper.get_policy_for_product") as mock_get:
            mock_get.return_value = " Shipping: Free."
            p = {"description": "A laptop. Shipping: Free.", "source": "X", "scraped_from_url": "https://x.com"}
            orig_len = len(p["description"])
            _enrich_with_policy(p)
            assert len(p["description"]) == orig_len

    def test_returns_product_dict(self):
        with patch("scripts.policy_scraper.get_policy_for_product", return_value=""):
            p = {"description": "Test", "source": "Y", "scraped_from_url": ""}
            result = _enrich_with_policy(p)
            assert result is p

    def test_uses_policy_cache(self):
        with patch("scripts.policy_scraper.get_policy_for_product") as mock_get:
            mock_get.return_value = " Shipping: Cached."
            cache = {}
            p1 = {"description": "P1", "source": "Z", "scraped_from_url": "https://z.com/page"}
            p2 = {"description": "P2", "source": "Z", "scraped_from_url": "https://z.com/page"}
            _enrich_with_policy(p1, cache)
            _enrich_with_policy(p2, cache)
            assert mock_get.call_count == 1


class TestFetchOpenLibraryBooks:
    """Tests for fetch_open_library_books() – fallback when B&N times out."""

    def test_returns_books_with_required_fields(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "docs": [
                {
                    "title": "Test Book",
                    "author_name": ["Author One"],
                    "cover_i": 12345,
                    "first_publish_year": 2020,
                    "key": "/works/OL123",
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_response):
            books = fetch_open_library_books(max_books=5)
        assert len(books) >= 1
        b = books[0]
        assert b["name"] == "Test Book"
        assert b["category"] == "Books"
        assert b["product_type"] == "book"
        assert b["source"] == "Open Library"
        assert "image_url" in b
        assert "covers.openlibrary.org" in b["image_url"]

    def test_skips_books_without_cover(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "docs": [
                {"title": "No Cover", "author_name": [], "cover_i": None, "key": "/works/OL456"},
            ]
        }
        mock_response.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_response):
            books = fetch_open_library_books(max_books=5)
        assert len(books) == 0

    def test_respects_max_books(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "docs": [
                {"title": f"Book {i}", "author_name": ["A"], "cover_i": 1000 + i, "key": f"/works/OL{i}"}
                for i in range(20)
            ]
        }
        mock_response.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_response):
            books = fetch_open_library_books(max_books=3)
        assert len(books) <= 3


class TestIntegrationEnrichBeforeDedupe:
    """Integration: _enrich_with_policy is called before deduplication for every product."""

    def test_enrich_preserves_dedupe_key(self):
        """Products can be deduped by (name, source) after enrichment; description is extended."""
        with patch("scripts.policy_scraper.get_policy_for_product") as mock_get:
            mock_get.return_value = " Shipping: Free."
            products = [
                {"name": "Laptop A", "description": "Desc", "source": "Shopify", "scraped_from_url": "https://a.com"},
                {"name": "Laptop A", "description": "Desc", "source": "Shopify", "scraped_from_url": "https://a.com"},
            ]
            seen = set()
            unique = []
            for p in products:
                _enrich_with_policy(p)
                key = (p.get("name", "").lower()[:60], p.get("source", ""))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(p)
            assert len(unique) == 1
            assert "Shipping:" in unique[0]["description"]
