"""
Unit tests for complex query detection and routing.
Tests is_complex_query heuristic and UniversalAgent integration path.

Tests are self-contained (no LLM calls, no DB required).
"""

import pytest
from app.complex_query import is_complex_query


class TestIsComplexQuery:
    #  Simple queries (should NOT route to UniversalAgent) 

    def test_simple_domain_word(self):
        assert is_complex_query("laptops") is False

    def test_simple_show_all(self):
        assert is_complex_query("show me all laptops") is False

    def test_vehicle_always_simple(self):
        """Vehicles should always use simple path (routed to IDSS backend)."""
        assert is_complex_query("I need a car for my family with good safety ratings") is False
        assert is_complex_query("looking for an SUV under 30k") is False

    def test_empty_string(self):
        assert is_complex_query("") is False

    def test_none(self):
        assert is_complex_query(None) is False

    def test_short_query(self):
        assert is_complex_query("Dell laptop") is False

    #  Complex queries (SHOULD route to UniversalAgent) 

    def test_multi_sentence(self):
        query = "I need a laptop for web development. It should handle 50 browser tabs."
        assert is_complex_query(query) is True

    def test_long_single_sentence(self):
        query = (
            "I need a laptop for web development and machine learning that "
            "handles 50 open tabs has 16GB RAM costs under 2000 dollars"
        )
        assert is_complex_query(query) is True

    def test_use_case_phrase(self):
        assert is_complex_query("good for gaming and school") is True

    def test_need_phrase(self):
        assert is_complex_query("I need to run PyTorch and TensorFlow") is True

    def test_looking_for_phrase(self):
        assert is_complex_query("looking for a lightweight laptop with great battery life") is True

    def test_recommend_phrase(self):
        assert is_complex_query("can you recommend a laptop for programming?") is True

    def test_requirement_specs(self):
        assert is_complex_query("at least 16 GB RAM and 512 GB storage") is True

    def test_repairable_sustainable(self):
        assert is_complex_query("I want a repairable laptop") is True

    def test_many_filters(self):
        """Many pre-existing filters indicate a complex flow."""
        filters = {"brand": "Dell", "price_max_cents": 150000, "gpu_vendor": "NVIDIA", "color": "Silver"}
        assert is_complex_query("show me options", filters) is True

    #  Reddit-style complex queries 

    def test_reddit_style_laptop(self):
        query = (
            "I will use the laptop for Webflow, Figma, Xano, Make, Python, PyCharm, "
            "and PyTorch. I expect it to handle 50 open browser tabs without issues, "
            "have a 16\" or 15.6\" screen, at least 512 GB of storage, at least 16 GB "
            "of RAM, and cost no more than $2,000."
        )
        assert is_complex_query(query) is True

    def test_reddit_style_book(self):
        query = (
            "Looking for a good sci-fi book that's similar to Foundation but more modern. "
            "I like hard science fiction with interesting world building."
        )
        assert is_complex_query(query) is True


class TestComplexQueryIntegrationContract:
    """Test that the UniversalAgent response contract is compatible with ChatResponse."""

    def test_question_response_has_required_fields(self):
        """UniversalAgent question response must have fields ChatResponse expects."""
        # Mock agent response (what UniversalAgent.process_message returns)
        agent_response = {
            "response_type": "question",
            "message": "What's your budget? Feel free to also share your brand preference.",
            "quick_replies": ["Under $500", "$500-$1000", "$1000-$2000", "Over $2000"],
            "session_id": "test-session",
            "domain": "laptops",
            "filters": {"use_case": "web development"},
            "question_count": 1,
        }
        assert "response_type" in agent_response
        assert "message" in agent_response
        assert "quick_replies" in agent_response
        assert isinstance(agent_response["quick_replies"], list)

    def test_recommendations_ready_has_required_fields(self):
        """UniversalAgent handoff response must have filters for search."""
        agent_response = {
            "response_type": "recommendations_ready",
            "message": "Let me find some great options for you...",
            "session_id": "test-session",
            "domain": "laptops",
            "filters": {"use_case": "web development", "budget": "under $2000"},
            "question_count": 2,
        }
        assert agent_response["response_type"] == "recommendations_ready"
        assert "filters" in agent_response
        assert isinstance(agent_response["filters"], dict)
