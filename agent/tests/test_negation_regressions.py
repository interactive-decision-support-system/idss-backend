"""
Regression tests for negation and exclusion behavior.

These tests are intentionally isolated from the core universal-agent test module
so this behavior is easy to track and maintain.
"""

from agent.universal_agent import UniversalAgent
from agent.domain_registry import get_domain_schema


def test_s2_negated_brand_string_should_be_exclusion_not_preference():
    """brand='not ASUS' should be converted to excluded_brands=['ASUS']."""
    agent = UniversalAgent(session_id="neg-s2")
    agent.domain = "laptops"
    agent.filters = {"brand": "not ASUS"}

    search_filters = agent.get_search_filters()

    assert "brand" not in search_filters
    assert search_filters.get("excluded_brands") == ["ASUS"]


def test_s3_regex_fallback_should_handle_indirect_exclusion_phrase(monkeypatch):
    """Indirect phrase 'steer clear of HP' should extract excluded_brands=['HP']."""
    monkeypatch.setattr("agent.universal_agent._extract_excluded_brands_semantic", lambda _m: [])

    agent = UniversalAgent(session_id="neg-s3")
    schema = get_domain_schema("laptops")
    result = agent._regex_extract_criteria("steer clear of HP, bad experience", schema)

    assert result is not None
    extracted = {c.slot_name: c.value for c in result.criteria}
    assert "excluded_brands" in extracted
    assert "HP" in extracted["excluded_brands"]


def test_s5_override_should_remove_apple_from_exclusions_when_brand_selected():
    """Explicit brand override should remove the same brand from exclusions."""
    agent = UniversalAgent(session_id="neg-s5")
    agent.domain = "laptops"
    agent.filters = {"excluded_brands": ["Apple"]}

    agent.filters["brand"] = "Apple"
    search_filters = agent.get_search_filters()

    assert search_filters.get("brand") == "Apple"
    assert "excluded_brands" not in search_filters or "Apple" not in search_filters.get("excluded_brands", [])


def test_s4_negated_screen_size_should_be_explicit_exclusion():
    """'don't want 14 inch screen' should become excluded_screen_sizes=[14.0]."""
    agent = UniversalAgent(session_id="neg-s4")
    agent.domain = "laptops"
    schema = get_domain_schema("laptops")

    result = agent._regex_extract_criteria("I don't want a 14 inch screen", schema)
    assert result is not None
    extracted = {c.slot_name: c.value for c in result.criteria}
    if "excluded_screen_sizes" in extracted:
        agent.filters["excluded_screen_sizes"] = extracted["excluded_screen_sizes"]
    if "screen_size" in extracted:
        agent.filters["screen_size"] = extracted["screen_size"]

    search_filters = agent.get_search_filters()
    assert search_filters.get("excluded_screen_sizes") == [14.0]
    assert "min_screen_size" not in search_filters
    assert "max_screen_size" not in search_filters


def test_s4_should_not_hallucinate_excluded_brand_when_no_brand_mentioned(monkeypatch):
    """Hallucinated excluded brand should be dropped without brand evidence."""
    monkeypatch.setattr("agent.universal_agent._extract_excluded_brands_semantic", lambda _m: ["HP"])
    agent = UniversalAgent(session_id="neg-s4-no-brand")
    schema = get_domain_schema("laptops")

    result = agent._regex_extract_criteria("I don't want a 14 inch screen", schema)
    assert result is not None
    extracted = {c.slot_name: c.value for c in result.criteria}
    assert "excluded_brands" not in extracted
