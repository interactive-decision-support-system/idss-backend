"""
Tests for comparison logic accuracy, including Supabase text-format reviews.

Verifies that _parse_reviews, build_comparison_table, and _product_to_flat_dict
work correctly for both JSON and Supabase text review formats.
"""

import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.research_compare import (
    _parse_reviews,
    _product_to_flat_dict,
    build_comparison_table,
    build_research_summary,
    parse_compare_by,
)


#  _parse_reviews 

class TestParseReviews:
    def test_supabase_text_format(self):
        """Supabase stores reviews as 'Average rating: 4.5/5 (1000 reviews)'."""
        result = _parse_reviews("Average rating: 4.5/5 (1000 reviews)")
        assert result["average_rating"] == 4.5
        assert result["review_count"] == 1000
        assert "4.5" in result["summary"]
        assert "1000" in result["summary"]

    def test_supabase_text_format_single_review(self):
        result = _parse_reviews("Average rating: 3.0/5 (1 review)")
        assert result["average_rating"] == 3.0
        assert result["review_count"] == 1

    def test_supabase_text_format_decimal_rating(self):
        result = _parse_reviews("Average rating: 4.78/5 (523 reviews)")
        assert result["average_rating"] == 4.8  # rounded to 1 decimal
        assert result["review_count"] == 523

    def test_json_dict_format(self):
        """Existing JSON dict format still works."""
        import json
        raw = json.dumps({
            "average_rating": 4.2,
            "review_count": 50,
            "reviews": [
                {"rating": 5, "comment": "Great!"},
                {"rating": 3, "comment": "OK"},
            ]
        })
        result = _parse_reviews(raw)
        assert result["average_rating"] == 4.2
        assert result["review_count"] == 50
        assert len(result["sample_comments"]) == 2

    def test_json_list_format(self):
        """JSON list of reviews."""
        import json
        raw = json.dumps([
            {"rating": 5, "comment": "Excellent"},
            {"rating": 4, "comment": "Good"},
        ])
        result = _parse_reviews(raw)
        assert result["average_rating"] == 4.5
        assert result["review_count"] == 2

    def test_empty_returns_no_reviews(self):
        result = _parse_reviews(None)
        assert result["average_rating"] is None
        assert result["review_count"] == 0
        assert "No reviews" in result["summary"]

    def test_empty_string_returns_no_reviews(self):
        result = _parse_reviews("")
        assert result["average_rating"] is None
        assert result["review_count"] == 0

    def test_garbage_string_returns_no_reviews(self):
        result = _parse_reviews("not a valid review format")
        assert result["average_rating"] is None
        assert result["review_count"] == 0


#  _product_to_flat_dict 

class TestProductToFlatDict:
    def test_with_supabase_reviews(self):
        """Flat dict correctly extracts rating from Supabase text reviews."""
        product = {
            "product_id": "supa-123",
            "name": "Dell XPS 15",
            "brand": "Dell",
            "price": 129900,
            "category": "Electronics",
            "reviews": "Average rating: 4.6/5 (812 reviews)",
        }
        flat = _product_to_flat_dict(product)
        assert flat["review_rating"] == 4.6
        assert flat["review_count"] == 812

    def test_with_no_reviews(self):
        product = {
            "product_id": "p1",
            "name": "Test Product",
            "brand": "TestBrand",
        }
        flat = _product_to_flat_dict(product)
        assert flat["review_rating"] is None
        assert flat["review_count"] == 0

    def test_description_truncated(self):
        product = {
            "product_id": "p1",
            "name": "Test",
            "description": "A" * 200,
        }
        flat = _product_to_flat_dict(product)
        assert len(flat["description"]) == 100


#  build_comparison_table 

class TestBuildComparisonTable:
    def _make_products(self):
        return [
            {
                "product_id": "p1",
                "name": "Dell XPS 15",
                "brand": "Dell",
                "price": 129900,
                "category": "Electronics",
                "reviews": "Average rating: 4.6/5 (812 reviews)",
            },
            {
                "product_id": "p2",
                "name": "HP Envy 16",
                "brand": "HP",
                "price": 109900,
                "category": "Electronics",
                "reviews": "Average rating: 4.2/5 (345 reviews)",
            },
        ]

    def test_includes_ratings(self):
        table = build_comparison_table(self._make_products())
        assert "review_rating" in table["attributes"]
        assert "review_count" in table["attributes"]
        # Check that actual rating values are present
        p1_values = table["products"][0]["values"]
        assert p1_values["review_rating"] == 4.6
        assert p1_values["review_count"] == 812

    def test_compare_by_price(self):
        table = build_comparison_table(self._make_products(), compare_by=["price"])
        assert "price" in table["attributes"]
        assert table["products"][0]["values"]["price"] == 129900
        assert table["products"][1]["values"]["price"] == 109900

    def test_compare_by_brand(self):
        table = build_comparison_table(self._make_products(), compare_by=["brand"])
        assert table["products"][0]["values"]["brand"] == "Dell"
        assert table["products"][1]["values"]["brand"] == "HP"

    def test_empty_products(self):
        table = build_comparison_table([])
        assert table["attributes"] == []
        assert table["products"] == []


#  parse_compare_by 

class TestParseCompareBy:
    def test_compare_by_price_and_rating(self):
        result = parse_compare_by("compare by price and rating")
        assert "price" in result
        assert "review_rating" in result

    def test_compare_by_brand(self):
        result = parse_compare_by("compare by brand")
        assert result == ["brand"]

    def test_no_compare_returns_none(self):
        result = parse_compare_by("show me laptops")
        assert result is None


#  build_research_summary 

class TestBuildResearchSummary:
    def test_supabase_product(self):
        product = {
            "product_id": "supa-456",
            "name": "MacBook Air M3",
            "brand": "Apple",
            "category": "Electronics",
            "price": 109900,
            "reviews": "Average rating: 4.8/5 (2500 reviews)",
            "description": "Powerful laptop with M3 chip. Great for everyday use. Long battery life.",
        }
        summary = build_research_summary(product)
        assert summary["review_summary"]["average_rating"] == 4.8
        assert summary["review_summary"]["review_count"] == 2500
        assert len(summary["features"]) > 0
