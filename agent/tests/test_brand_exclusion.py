"""
Unit tests for brand exclusion, negation handling, and session preference reset.

Covers the 5 failure scenarios from Question 1:
1. "I want a laptop, no mac"              → Apple must be excluded, not preferred
2. "we hate ASUS, find me a gaming laptop" → ASUS must be excluded
3. "steer clear of HP, bad experience"     → HP excluded (indirect phrasing)
4. "I don't want a 14 inch screen"         → 14" must NOT be extracted as positive
5. "no Apple" (turn 1), "show me Apple" (turn 4) → exclusion cleared on mind change

Tests run without DB or OpenAI credentials by mocking LLM calls.
"""

import os
import re
import pytest
from unittest.mock import patch, MagicMock

os.environ.setdefault("OPENAI_API_KEY", "test-dummy-key-for-unit-tests")

from agent.universal_agent import (
    UniversalAgent,
    _BRAND_VALUE_ALIASES,
    _extract_excluded_brands_semantic,
)
from agent.domain_registry import get_domain_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(session_id="test-excl", domain="laptops", filters=None):
    """Create a UniversalAgent pre-configured for laptops domain."""
    agent = UniversalAgent(session_id=session_id)
    agent.domain = domain
    if filters:
        agent.filters = dict(filters)
    return agent


def _mock_extraction(criteria_list, wants_recs=True):
    """Build a mock ExtractedCriteria return value."""
    from agent.universal_agent import ExtractedCriteria, SlotValue
    items = [SlotValue(slot_name=k, value=v) for k, v in criteria_list]
    return ExtractedCriteria(
        criteria=items,
        reasoning="test",
        is_impatient=False,
        wants_recommendations=wants_recs,
    )


# ---------------------------------------------------------------------------
# Test 1: "no mac" — brand/exclusion conflict resolution (Fix A)
# ---------------------------------------------------------------------------

class TestBrandExclusionConflict:
    """When LLM extracts both brand=Apple and excluded_brands=Apple
    from "no mac", the exclusion must win."""

    def test_same_message_conflict_exclusion_wins(self):
        """Test _extract_criteria directly: brand should be dropped when it
        conflicts with excluded_brands extracted in the same message."""
        agent = _make_agent()
        schema = get_domain_schema("laptops")

        # Mock the OpenAI structured-output call to return both brand and exclusion
        from agent.universal_agent import ExtractedCriteria, SlotValue
        mock_parsed = ExtractedCriteria(
            criteria=[
                SlotValue(slot_name="brand", value="mac"),
                SlotValue(slot_name="excluded_brands", value="Apple"),
            ],
            reasoning="test",
            is_impatient=False,
            wants_recommendations=True,
        )
        mock_choice = MagicMock()
        mock_choice.message.parsed = mock_parsed
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            result = agent._extract_criteria("I want a laptop, no mac", schema)

        # Fix A: brand=Apple should be removed because it conflicts with excluded_brands
        assert agent.filters.get("brand") != "Apple", \
            "brand=Apple should be removed when it conflicts with excluded_brands"
        excl = agent.filters.get("excluded_brands")
        assert excl is not None, "excluded_brands should be set"
        excl_list = excl if isinstance(excl, list) else [b.strip() for b in str(excl).split(",")]
        assert any("Apple" == b for b in excl_list), "Apple should be in excluded_brands"

    def test_no_mac_search_filters_exclude_apple(self):
        """get_search_filters() should produce excluded_brands=['Apple'] for 'no mac'."""
        agent = _make_agent(filters={"excluded_brands": "Apple"})
        sf = agent.get_search_filters()
        assert "excluded_brands" in sf
        assert "Apple" in sf["excluded_brands"]
        assert sf.get("brand") is None


# ---------------------------------------------------------------------------
# Test 2: "we hate ASUS" — emotional exclusion (Fix A)
# ---------------------------------------------------------------------------

class TestEmotionalExclusion:

    def test_hate_asus_excluded(self):
        """Test _extract_criteria directly: 'hate ASUS' should produce exclusion."""
        agent = _make_agent()
        schema = get_domain_schema("laptops")

        from agent.universal_agent import ExtractedCriteria, SlotValue
        mock_parsed = ExtractedCriteria(
            criteria=[
                SlotValue(slot_name="excluded_brands", value="ASUS"),
                SlotValue(slot_name="use_case", value="gaming"),
            ],
            reasoning="test",
            is_impatient=False,
            wants_recommendations=True,
        )
        mock_choice = MagicMock()
        mock_choice.message.parsed = mock_parsed
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            result = agent._extract_criteria("we hate ASUS, find me a gaming laptop", schema)

        excl = agent.filters.get("excluded_brands")
        assert excl is not None, "excluded_brands should be set for 'hate ASUS'"
        excl_list = excl if isinstance(excl, list) else [b.strip() for b in str(excl).split(",")]
        assert any("ASUS" == b for b in excl_list)


# ---------------------------------------------------------------------------
# Test 3: Indirect exclusion phrases — regex expansion (Fix B)
# ---------------------------------------------------------------------------

class TestIndirectExclusion:
    """The regex fallback must catch indirect/experiential exclusion phrases."""

    def test_steer_clear_of_hp(self):
        agent = _make_agent()
        schema = get_domain_schema("laptops")
        with patch("agent.universal_agent._extract_brand_semantic", return_value=None):
            with patch("agent.universal_agent._extract_excluded_brands_semantic", return_value=[]):
                result = agent._regex_extract_criteria(
                    "steer clear of HP, bad experience, find me a laptop under $800",
                    schema,
                )
        assert result is not None
        slot_map = {c.slot_name: c.value for c in result.criteria}
        assert "excluded_brands" in slot_map, \
            "'steer clear of HP' should produce excluded_brands"
        assert "HP" in slot_map["excluded_brands"]

    def test_bad_experience_with_dell(self):
        agent = _make_agent()
        schema = get_domain_schema("laptops")
        with patch("agent.universal_agent._extract_brand_semantic", return_value=None):
            with patch("agent.universal_agent._extract_excluded_brands_semantic", return_value=[]):
                result = agent._regex_extract_criteria(
                    "bad experience with Dell, need a work laptop",
                    schema,
                )
        slot_map = {c.slot_name: c.value for c in result.criteria}
        assert "excluded_brands" in slot_map
        assert "Dell" in slot_map["excluded_brands"]

    def test_stay_away_from_samsung(self):
        agent = _make_agent()
        schema = get_domain_schema("laptops")
        with patch("agent.universal_agent._extract_brand_semantic", return_value=None):
            with patch("agent.universal_agent._extract_excluded_brands_semantic", return_value=[]):
                result = agent._regex_extract_criteria(
                    "stay away from Samsung, need a budget laptop",
                    schema,
                )
        slot_map = {c.slot_name: c.value for c in result.criteria}
        assert "excluded_brands" in slot_map
        assert "Samsung" in slot_map["excluded_brands"]

    def test_dont_trust_lenovo(self):
        agent = _make_agent()
        schema = get_domain_schema("laptops")
        with patch("agent.universal_agent._extract_brand_semantic", return_value=None):
            with patch("agent.universal_agent._extract_excluded_brands_semantic", return_value=[]):
                result = agent._regex_extract_criteria(
                    "I don't trust Lenovo anymore, show me alternatives under $1000",
                    schema,
                )
        slot_map = {c.slot_name: c.value for c in result.criteria}
        assert "excluded_brands" in slot_map
        assert "Lenovo" in slot_map["excluded_brands"]


# ---------------------------------------------------------------------------
# Test 4: Negated screen size — must NOT be extracted as positive (Fix C/D)
# ---------------------------------------------------------------------------

class TestNegatedScreenSize:

    def test_regex_path_skips_negated_screen(self):
        """The regex fallback already has negation guard — verify it still works."""
        agent = _make_agent()
        schema = get_domain_schema("laptops")
        with patch("agent.universal_agent._extract_brand_semantic", return_value=None):
            with patch("agent.universal_agent._extract_excluded_brands_semantic", return_value=[]):
                result = agent._regex_extract_criteria(
                    "I don't want a 14 inch screen laptop",
                    schema,
                )
        slot_map = {c.slot_name: c.value for c in result.criteria}
        assert "screen_size" not in slot_map, \
            "Negated '14 inch' should NOT appear as screen_size"

    def test_no_14_inch_screen_not_in_filters(self):
        """After processing, filters should NOT contain screen_size or min/max around 14."""
        agent = _make_agent()
        mock_result = _mock_extraction([])
        with patch.object(agent, "_extract_criteria", return_value=mock_result):
            with patch.object(agent, "_should_recommend", return_value=False):
                with patch.object(agent, "_entropy_next_slot", return_value=None):
                    with patch.object(agent, "_handoff_to_search", return_value={"response_type": "recommendations", "message": "ok"}):
                        agent.process_message("I don't want a 14 inch screen laptop")
        sf = agent.get_search_filters()
        assert sf.get("min_screen_size") != 13.5, "Should not filter to 14-inch range"
        assert sf.get("max_screen_size") != 14.5, "Should not filter to 14-inch range"


# ---------------------------------------------------------------------------
# Test 5: Multi-turn mind change — exclusion must be clearable (Fix E)
# ---------------------------------------------------------------------------

class TestMindChangeExclusion:

    def test_preference_reset_clears_excluded_brands(self):
        """When user says 'actually show me Apple', excluded_brands must be cleared."""
        agent = _make_agent(filters={
            "excluded_brands": "Apple",
            "budget": "under1000",
        })
        # "actually" triggers the preference reset block
        mock_result = _mock_extraction([("brand", "Apple")])
        with patch.object(agent, "_extract_criteria", return_value=mock_result):
            with patch.object(agent, "_should_recommend", return_value=True):
                with patch.object(agent, "_handoff_to_search", return_value={"response_type": "recommendations", "message": "ok"}):
                    agent.process_message("actually show me Apple laptops")

        # excluded_brands should be gone (cleared by preference reset + not re-set)
        assert agent.filters.get("excluded_brands") is None, \
            "excluded_brands should be cleared after 'actually show me Apple'"
        # budget should survive the soft reset
        assert agent.filters.get("budget") == "under1000", \
            "Budget (hard constraint) should survive preference reset"

    def test_excluded_brands_not_set_to_none_string(self):
        """The literal string 'none' must never be stored as an excluded brand."""
        agent = _make_agent(filters={"excluded_brands": "none"})
        sf = agent.get_search_filters()
        excl = sf.get("excluded_brands")
        assert excl is None or "none" not in [b.lower() for b in excl], \
            "Literal 'none' must be filtered out of excluded_brands"


# ---------------------------------------------------------------------------
# Test 6: Type safety — get_search_filters handles list and str (Fix F)
# ---------------------------------------------------------------------------

class TestExcludedBrandsTypeSafety:

    def test_list_type_handled(self):
        """excluded_brands stored as list should produce correct search filters."""
        agent = _make_agent(filters={"excluded_brands": ["HP", "Acer"]})
        sf = agent.get_search_filters()
        assert sf["excluded_brands"] == ["HP", "Acer"]

    def test_string_type_handled(self):
        """excluded_brands stored as comma string should produce correct list."""
        agent = _make_agent(filters={"excluded_brands": "HP,Acer"})
        sf = agent.get_search_filters()
        assert set(sf["excluded_brands"]) == {"HP", "Acer"}

    def test_alias_resolution_in_get_search_filters(self):
        """'mac' in excluded_brands should resolve to 'Apple'."""
        agent = _make_agent(filters={"excluded_brands": "mac"})
        sf = agent.get_search_filters()
        assert "Apple" in sf.get("excluded_brands", [])

    def test_none_value_filtered_out(self):
        """excluded_brands='none' (from LLM) should produce empty exclusion."""
        agent = _make_agent(filters={"excluded_brands": "none"})
        sf = agent.get_search_filters()
        assert sf.get("excluded_brands") is None or len(sf.get("excluded_brands", [])) == 0


# ---------------------------------------------------------------------------
# Test 7: Multiple brand exclusion ("no HP or Acer")
# ---------------------------------------------------------------------------

class TestMultipleBrandExclusion:

    def test_no_hp_or_acer_regex(self):
        agent = _make_agent()
        schema = get_domain_schema("laptops")
        with patch("agent.universal_agent._extract_brand_semantic", return_value=None):
            with patch("agent.universal_agent._extract_excluded_brands_semantic", return_value=[]):
                result = agent._regex_extract_criteria(
                    "no HP or Acer, budget $900, 16GB RAM",
                    schema,
                )
        slot_map = {c.slot_name: c.value for c in result.criteria}
        assert "excluded_brands" in slot_map
        excl_val = slot_map["excluded_brands"]
        assert "HP" in excl_val
        assert "Acer" in excl_val
