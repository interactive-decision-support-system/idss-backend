"""
Unit tests for brand diversity in 'See similar items' feature.
Tests the _diversify_by_brand function to ensure interleaved brand ordering.
"""

import pytest
import sys
import os

# Add the mcp-server directory and repo root to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agent.chat_endpoint import _diversify_by_brand


class TestDiversifyByBrand:
    def test_interleaves_brands(self):
        products = [
            {"product_id": "d1", "brand": "Dell"},
            {"product_id": "d2", "brand": "Dell"},
            {"product_id": "d3", "brand": "Dell"},
            {"product_id": "h1", "brand": "HP"},
            {"product_id": "h2", "brand": "HP"},
            {"product_id": "a1", "brand": "Apple"},
        ]
        result = _diversify_by_brand(products)
        brands = [p["brand"] for p in result]
        # First 3 should all be different brands (round-robin)
        assert len(set(brands[:3])) == 3
        # All products preserved
        assert len(result) == 6

    def test_single_brand_unchanged(self):
        products = [
            {"product_id": "d1", "brand": "Dell"},
            {"product_id": "d2", "brand": "Dell"},
            {"product_id": "d3", "brand": "Dell"},
        ]
        result = _diversify_by_brand(products)
        assert result == products

    def test_two_products_unchanged(self):
        products = [
            {"product_id": "d1", "brand": "Dell"},
            {"product_id": "h1", "brand": "HP"},
        ]
        result = _diversify_by_brand(products)
        assert len(result) == 2

    def test_empty_list(self):
        assert _diversify_by_brand([]) == []

    def test_no_brand_field(self):
        products = [
            {"product_id": "p1"},
            {"product_id": "p2"},
            {"product_id": "p3"},
        ]
        result = _diversify_by_brand(products)
        assert len(result) == 3

    def test_preserves_all_products(self):
        products = [
            {"product_id": "d1", "brand": "Dell"},
            {"product_id": "d2", "brand": "Dell"},
            {"product_id": "h1", "brand": "HP"},
            {"product_id": "a1", "brand": "Apple"},
            {"product_id": "a2", "brand": "Apple"},
        ]
        result = _diversify_by_brand(products)
        result_ids = {p["product_id"] for p in result}
        original_ids = {p["product_id"] for p in products}
        assert result_ids == original_ids

    def test_adjacent_brands_differ_when_possible(self):
        """After diversification, no two adjacent products should share a brand
        (when there are at least 2 brands and products aren't heavily skewed)."""
        products = [
            {"product_id": "d1", "brand": "Dell"},
            {"product_id": "d2", "brand": "Dell"},
            {"product_id": "h1", "brand": "HP"},
            {"product_id": "h2", "brand": "HP"},
        ]
        result = _diversify_by_brand(products)
        brands = [p["brand"] for p in result]
        # With 2 Dell + 2 HP, round-robin gives: Dell, HP, Dell, HP
        for i in range(len(brands) - 1):
            assert brands[i] != brands[i + 1], f"Adjacent brands same at index {i}: {brands}"
