"""
Tests for query_parser: hardware spec extraction from natural-language queries.

Covers RAM, storage, screen size, battery, and use-case extraction —
including Reddit-style complex multi-constraint queries from week7NOTEs.txt.
"""

import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.query_parser import (
    enhance_search_request,
    _extract_min_ram,
    _extract_min_storage,
    _extract_min_screen,
    _extract_min_battery,
    _extract_year,
    _extract_use_cases,
)


#  RAM extraction 

class TestExtractRam:
    def test_at_least_16gb_ram(self):
        assert _extract_min_ram("at least 16 GB of RAM") == 16

    def test_16gb_ram_no_prefix(self):
        assert _extract_min_ram("16GB RAM") == 16

    def test_32gb_ram_with_space(self):
        assert _extract_min_ram("32 GB RAM") == 32

    def test_minimum_8_gigs_ram(self):
        assert _extract_min_ram("minimum 8 gigs of ram") == 8

    def test_no_ram_mention(self):
        assert _extract_min_ram("a fast laptop for gaming") is None

    def test_invalid_ram_value_rejected(self):
        # 500 GB RAM is nonsensical — should be rejected
        assert _extract_min_ram("500 GB RAM") is None


#  Storage extraction 

class TestExtractStorage:
    def test_512gb_ssd(self):
        assert _extract_min_storage("512 GB SSD") == 512

    def test_at_least_256gb_storage(self):
        assert _extract_min_storage("at least 256 GB storage") == 256

    def test_1tb_storage(self):
        assert _extract_min_storage("1TB SSD") == 1024

    def test_no_storage_mention(self):
        assert _extract_min_storage("a fast laptop") is None


#  Screen size extraction 

class TestExtractScreen:
    def test_15_6_inch(self):
        assert _extract_min_screen('15.6" screen') == 15.6

    def test_16_inch_hyphenated(self):
        assert _extract_min_screen("16-inch display") == 16.0

    def test_14_inch_with_word(self):
        assert _extract_min_screen("14 inch laptop") == 14.0

    def test_no_screen_mention(self):
        assert _extract_min_screen("a good laptop") is None


#  Battery extraction 

class TestExtractBattery:
    def test_8_hours_battery_life(self):
        assert _extract_min_battery("8 hours of battery life") == 8

    def test_at_least_10_hours(self):
        assert _extract_min_battery("at least 10 hours battery") == 10

    def test_8_plus_hours(self):
        assert _extract_min_battery("8+ hours battery") == 8

    def test_no_battery_mention(self):
        assert _extract_min_battery("a good laptop") is None


#  Year extraction 

class TestExtractYear:
    def test_2024_laptop(self):
        assert _extract_year("2024 laptop for students") == 2024

    def test_laptop_2023_in_parens(self):
        assert _extract_year("HP Chromebook (2023)") == 2023

    def test_2025_model(self):
        assert _extract_year("ROG Strix G16 2025 edition") == 2025

    def test_no_year(self):
        assert _extract_year("a fast laptop for gaming") is None

    def test_year_too_old(self):
        # 2010 is outside our 2015-2026 range
        assert _extract_year("model from 2010") is None


#  Use-case extraction 

class TestExtractUseCases:
    def test_ml_and_web_dev(self):
        cases = _extract_use_cases("I need it for machine learning and web development")
        assert "ml" in cases
        assert "web_dev" in cases

    def test_gaming(self):
        cases = _extract_use_cases("for gaming and streaming")
        assert "gaming" in cases

    def test_linux(self):
        cases = _extract_use_cases("must run Linux well")
        assert "linux" in cases

    def test_pytorch_and_figma(self):
        cases = _extract_use_cases("I use PyTorch and Figma daily")
        assert "ml" in cases
        assert "web_dev" in cases

    def test_no_use_case(self):
        assert _extract_use_cases("I want a good laptop") == []


#  Full enhance_search_request (Reddit-style queries) 

class TestEnhanceSearchRequest:
    """Test the full pipeline with Reddit-style multi-constraint queries."""

    def test_reddit_query_1(self):
        """Week7NOTEs line 578: Webflow, Figma, PyTorch, 16GB RAM, 512GB SSD, 15.6-inch, under $2000."""
        query = (
            "I will use the laptop for Webflow, Figma, Xano, Make, Python, PyCharm, "
            "and PyTorch (machine and deep learning). I expect it to handle 50 open "
            'browser tabs without issues, have a 16" or 15.6" screen, at least 512 GB '
            "of storage, at least 16 GB of RAM, and cost no more than $2,000."
        )
        _, filters = enhance_search_request(query, {})
        assert filters["min_ram_gb"] == 16
        assert filters["min_storage_gb"] == 512
        # Screen: should extract 16 or 15.6 (first match)
        assert filters["min_screen_inches"] in (15.6, 16.0)
        use_cases = filters["use_cases"]
        assert "ml" in use_cases  # PyTorch, deep learning
        assert "web_dev" in use_cases  # Webflow, Figma, Xano
        assert "programming" in use_cases  # Python, PyCharm

    def test_reddit_query_2(self):
        """Week7NOTEs line 579: Linux, keyboard, 8h battery, 32GB RAM, 5K monitor."""
        query = (
            "I need a laptop for productive work—web development, QGIS, and possibly "
            "Godot or Unity—that runs Linux well, has an excellent keyboard, provides "
            "at least 8 hours of battery life, includes 32 GB of RAM, and supports a "
            "5K ultrawide external monitor."
        )
        _, filters = enhance_search_request(query, {})
        assert filters["min_ram_gb"] == 32
        assert filters["min_battery_hours"] == 8
        use_cases = filters["use_cases"]
        assert "web_dev" in use_cases
        assert "linux" in use_cases
        assert "gaming" in use_cases  # Godot, Unity

    def test_simple_query_no_specs(self):
        """Simple queries should not extract any specs."""
        _, filters = enhance_search_request("dell laptop", {})
        assert filters == {}

    def test_empty_query(self):
        _, filters = enhance_search_request("", {})
        assert filters == {}

    def test_none_query(self):
        _, filters = enhance_search_request(None, {})
        assert filters == {}

    def test_ram_and_storage_combined(self):
        _, filters = enhance_search_request("laptop with 16GB RAM and 512GB SSD under $1500", {})
        assert filters["min_ram_gb"] == 16
        assert filters["min_storage_gb"] == 512
        assert "use_cases" not in filters  # no use-case keywords

    def test_year_extraction(self):
        _, filters = enhance_search_request("2024 gaming laptop with 16GB RAM", {})
        assert filters["min_year"] == 2024
        assert filters["min_ram_gb"] == 16
        assert "gaming" in filters["use_cases"]


#  Recommendation reasons with spec context 

class TestRecommendationReasonsWithSpecs:
    """Verify generate_recommendation_reasons includes spec/use-case context."""

    def test_reasons_include_specs(self):
        from app.research_compare import generate_recommendation_reasons

        products = [
            {"product_id": "p1", "brand": "Dell", "price_cents": 150000},
            {"product_id": "p2", "brand": "HP", "price_cents": 120000},
        ]
        filters = {
            "brand": "Dell",
            "price_max_cents": 200000,
            "min_ram_gb": 16,
            "min_storage_gb": 512,
            "use_cases": ["ml", "web_dev"],
        }
        generate_recommendation_reasons(products, filters, kg_candidate_ids=None)

        # First product: brand match + budget + specs + use-case
        reason1 = products[0]["_reason"]
        assert "Brand match" in reason1
        assert "Within budget" in reason1
        assert "16GB+ RAM" in reason1
        assert "512GB+ storage" in reason1
        assert "ML/AI" in reason1
        assert "Web dev" in reason1

        # Second product: no brand match, but has budget + specs
        reason2 = products[1]["_reason"]
        assert "Brand match" not in reason2
        assert "Within budget" in reason2

    def test_reasons_without_specs(self):
        from app.research_compare import generate_recommendation_reasons

        products = [{"product_id": "p1", "brand": "Dell"}]
        generate_recommendation_reasons(products, {}, None)
        assert products[0]["_reason"] == "Top match"

    def test_reasons_kg_match(self):
        from app.research_compare import generate_recommendation_reasons

        products = [{"product_id": "p1", "brand": "Dell"}]
        generate_recommendation_reasons(products, {}, kg_candidate_ids=["p1"])
        assert "Knowledge graph match" in products[0]["_reason"]


#  ProductSummary schema has reason field 

class TestProductSummaryReasonField:
    def test_reason_field_exists(self):
        from app.schemas import ProductSummary

        fields = ProductSummary.model_fields
        assert "reason" in fields, "ProductSummary must have a 'reason' field"

    def test_reason_field_optional(self):
        from app.schemas import ProductSummary

        s = ProductSummary(
            product_id="test",
            name="Test",
            price_cents=100,
            available_qty=1,
        )
        assert s.reason is None  # optional, defaults to None
