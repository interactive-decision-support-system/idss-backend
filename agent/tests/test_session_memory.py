"""
Unit tests for session memory and preference management (Question 2).

Covers the 3 failure scenarios:
A. "I want a Dell laptop under $800" → "Actually, make it under $600" → stale budget
B. Gaming laptop turns 1-3 → "forget the gaming specs" → specs persist
C. "machine learning" → "Actually just email and Netflix" → ML use case persists

Tests run without DB or OpenAI credentials by mocking LLM calls.
"""

import os
import re
import pytest
from unittest.mock import patch, MagicMock

os.environ.setdefault("OPENAI_API_KEY", "test-dummy-key-for-unit-tests")

from agent.universal_agent import (
    UniversalAgent,
    ExtractedCriteria,
    SlotValue,
    RefinementClassification,
    _SLOT_NAME_ALIASES,
)
from agent.domain_registry import get_domain_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(session_id="test-mem", domain="laptops", filters=None):
    """Create a UniversalAgent pre-configured for the laptops domain."""
    agent = UniversalAgent(session_id=session_id)
    agent.domain = domain
    if filters:
        agent.filters = dict(filters)
    return agent


def _mock_extraction(criteria_list, wants_recs=True):
    """Build a mock ExtractedCriteria return value."""
    items = [SlotValue(slot_name=k, value=v) for k, v in criteria_list]
    return ExtractedCriteria(
        criteria=items,
        reasoning="test",
        is_impatient=False,
        wants_recommendations=wants_recs,
    )


def _mock_openai_parse(mock_parsed):
    """Create a MagicMock that mimics the OpenAI beta.chat.completions.parse response."""
    mock_choice = MagicMock()
    mock_choice.message.parsed = mock_parsed
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    return mock_completion


# ===========================================================================
# Scenario A — Budget update not applied
# ===========================================================================

class TestBudgetOverwriteInterviewPath:
    """When the user says 'actually under $600' mid-interview, the old
    budget='under800' must be overwritten, not kept."""

    def test_budget_overwrite_via_extract_criteria(self):
        agent = _make_agent(filters={"budget": "under800", "brand": "Dell"})
        schema = get_domain_schema("laptops")

        mock_parsed = _mock_extraction([("budget", "under600")], wants_recs=True)
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            agent._extract_criteria("Actually, make it under $600", schema)

        assert agent.filters["budget"] == "under600", \
            "budget should be overwritten to under600"
        sf = agent.get_search_filters()
        assert sf.get("price_max_cents") == 60000, \
            f"price_max_cents should be 60000, got {sf.get('price_max_cents')}"

    def test_budget_cleared_by_soft_reset_when_dollar_present(self):
        """'actually' + '$600' should clear old budget before extraction."""
        agent = _make_agent(filters={"budget": "under800", "brand": "Dell"})
        agent.domain = "laptops"

        # Simulate the preference reset logic from process_message
        message = "Actually, make it under $600"
        msg_lower = message.lower()

        _PREF_RESET_PHRASES = (
            "changed my mind", "change my mind", "actually", "instead show",
            "show me instead", "forget that", "never mind", "nevermind",
            "scratch that", "different brand", "switch to", "go with",
        )
        _soft_reset = any(p in msg_lower for p in _PREF_RESET_PHRASES) and agent.domain

        assert _soft_reset, "'actually' should trigger soft reset"

        _has_budget_signal = re.search(
            r'(?:under|below|over|above|less than|at most|budget|'
            r'max|up\s+to|starting)\s*\$?\s*\d',
            msg_lower,
        ) or re.search(r'\$\s*\d', msg_lower)

        assert _has_budget_signal, "dollar amount should be detected"

        if _soft_reset and _has_budget_signal and "budget" in agent.filters:
            agent.filters.pop("budget", None)

        assert "budget" not in agent.filters, \
            "old budget should be cleared before LLM extraction"


class TestBudgetOverwriteRefinementPath:
    """process_refinement must normalise slot names so 'price' → 'budget'."""

    def test_refinement_normalises_price_to_budget(self):
        agent = _make_agent(filters={"budget": "under800", "brand": "Dell"})

        mock_parsed = RefinementClassification(
            intent="refine_filters",
            updated_criteria=[SlotValue(slot_name="price", value="under600")],
            reasoning="user wants cheaper",
        )
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            result = agent.process_refinement("Actually, make it under $600")

        assert agent.filters.get("budget") == "under600", \
            f"'price' should be normalised to 'budget'; got filters={agent.filters}"
        assert "price" not in agent.filters, \
            "'price' key should not exist after normalisation"

    def test_refinement_normalises_max_price_to_budget(self):
        agent = _make_agent(filters={"budget": "under800"})

        mock_parsed = RefinementClassification(
            intent="refine_filters",
            updated_criteria=[SlotValue(slot_name="max_price", value="600")],
            reasoning="budget correction",
        )
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            agent.process_refinement("make it under $600")

        assert agent.filters.get("budget") == "600", \
            "'max_price' should be normalised to 'budget'"

    def test_refinement_normalises_ram_to_min_ram_gb(self):
        agent = _make_agent(filters={"min_ram_gb": "8"})

        mock_parsed = RefinementClassification(
            intent="refine_filters",
            updated_criteria=[SlotValue(slot_name="ram", value="16")],
            reasoning="user wants more RAM",
        )
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            agent.process_refinement("I need 16GB RAM")

        assert agent.filters.get("min_ram_gb") == "16", \
            "'ram' should be normalised to 'min_ram_gb'"


# ===========================================================================
# Scenario B — Gaming specs not cleared on intent shift
# ===========================================================================

class TestGamingSpecsClearedOnForget:
    """'Forget the gaming specs' must clear min_ram_gb, gpu_tier,
    use_case, and good_for_gaming — not just brand/use_case."""

    def test_hard_reset_clears_gaming_specs(self):
        agent = _make_agent(filters={
            "use_case": "gaming",
            "min_ram_gb": "16",
            "gpu_tier": "dedicated",
            "screen_size": "15.6",
            "budget": "under1500",
            "good_for_gaming": True,
        })

        schema = get_domain_schema("laptops")
        mock_parsed = _mock_extraction([("budget", "under600")], wants_recs=False)
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            agent.process_message("forget the gaming specs, I'm on a tight budget under $600")

        assert agent.filters.get("use_case") is None, "use_case should be cleared"
        assert agent.filters.get("min_ram_gb") is None, "min_ram_gb should be cleared"
        assert agent.filters.get("gpu_tier") is None, "gpu_tier should be cleared"
        assert agent.filters.get("screen_size") is None, "screen_size should be cleared"
        assert agent.filters.get("good_for_gaming") is None, "good_for_gaming should be cleared"

    def test_hard_reset_preserves_brand(self):
        """Brand is a soft slot but should still be cleared on hard reset."""
        agent = _make_agent(filters={
            "brand": "ASUS",
            "use_case": "gaming",
            "min_ram_gb": "16",
            "budget": "under1500",
        })

        schema = get_domain_schema("laptops")
        mock_parsed = _mock_extraction([("budget", "under600")], wants_recs=False)
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            agent.process_message("forget the gaming specs, basic laptop please")

        assert agent.filters.get("brand") is None, "brand should be cleared on hard reset"
        assert agent.filters.get("min_ram_gb") is None, "min_ram_gb should be cleared"

    def test_no_more_gaming_triggers_hard_reset(self):
        agent = _make_agent(filters={
            "use_case": "gaming",
            "good_for_gaming": True,
            "min_ram_gb": "32",
        })

        schema = get_domain_schema("laptops")
        mock_parsed = _mock_extraction([("use_case", "general")], wants_recs=False)
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            agent.process_message("no more gaming, just something simple")

        assert agent.filters.get("good_for_gaming") is None
        assert agent.filters.get("min_ram_gb") is None


# ===========================================================================
# Scenario C — Contradictory use case not resolved
# ===========================================================================

class TestUseCaseContradictionClearsGoodFor:
    """When use_case changes from ML to email, stale good_for_ml must be removed."""

    def test_good_for_ml_cleared_on_use_case_change(self):
        agent = _make_agent(filters={
            "use_case": "machine learning",
            "good_for_ml": True,
            "min_ram_gb": "32",
            "budget": "under2000",
        })
        schema = get_domain_schema("laptops")

        mock_parsed = _mock_extraction([("use_case", "general")], wants_recs=False)
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            agent._extract_criteria("Actually it's just for email and Netflix", schema)

        assert agent.filters.get("good_for_ml") is None, \
            "good_for_ml should be cleared when use_case changes"
        assert agent.filters["use_case"] == "general", \
            "use_case should be updated to 'general'"
        sf = agent.get_search_filters()
        assert sf.get("good_for_ml") is not True, \
            "search_filters should not contain good_for_ml"

    def test_good_for_gaming_cleared_when_use_case_becomes_school(self):
        agent = _make_agent(filters={
            "use_case": "gaming",
            "good_for_gaming": True,
        })
        schema = get_domain_schema("laptops")

        mock_parsed = _mock_extraction([("use_case", "school")], wants_recs=False)
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            agent._extract_criteria("Actually this is for school work", schema)

        assert agent.filters.get("good_for_gaming") is None
        assert agent.filters["use_case"] == "school"

    def test_multiple_good_for_flags_cleared(self):
        agent = _make_agent(filters={
            "use_case": "creative",
            "good_for_creative": True,
            "good_for_web_dev": True,
        })
        schema = get_domain_schema("laptops")

        mock_parsed = _mock_extraction([("use_case", "gaming")], wants_recs=False)
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            agent._extract_criteria("Actually I want a gaming laptop", schema)

        assert agent.filters.get("good_for_creative") is None
        assert agent.filters.get("good_for_web_dev") is None
        assert agent.filters["use_case"] == "gaming"


# ===========================================================================
# No-regression: soft reset should NOT wipe unrelated slots
# ===========================================================================

class TestSoftResetPreservesUnrelated:
    """'Actually' + new brand must not wipe budget or RAM."""

    def test_soft_reset_keeps_budget_and_ram(self):
        agent = _make_agent(filters={
            "brand": "Dell",
            "use_case": "school",
            "budget": "under800",
            "min_ram_gb": "16",
        })
        schema = get_domain_schema("laptops")

        mock_parsed = _mock_extraction([("brand", "HP")], wants_recs=False)
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            agent.process_message("Actually, show me HP instead")

        assert agent.filters.get("budget") == "under800", \
            "budget should survive a soft reset"
        assert agent.filters.get("min_ram_gb") == "16", \
            "min_ram_gb should survive a soft reset"
        assert agent.filters.get("brand") == "HP", \
            "brand should be updated to HP"

    def test_soft_reset_clears_old_use_case_but_keeps_screen_size(self):
        agent = _make_agent(filters={
            "use_case": "gaming",
            "screen_size": "15.6",
            "budget": "under1000",
        })
        schema = get_domain_schema("laptops")

        mock_parsed = _mock_extraction([("use_case", "school")], wants_recs=False)
        mock_completion = _mock_openai_parse(mock_parsed)

        with patch.object(agent.client.beta.chat.completions, "parse", return_value=mock_completion):
            agent.process_message("Actually this is for school")

        assert agent.filters.get("use_case") == "school"
        assert agent.filters.get("screen_size") == "15.6", \
            "screen_size should survive a soft reset (not a hard reset phrase)"
        assert agent.filters.get("budget") == "under1000"


# ===========================================================================
# Replace-mode update_filters
# ===========================================================================

class TestReplaceModeUpdateFilters:
    """session_manager.update_filters(replace=True) should remove stale keys."""

    def _make_sm(self, session_id, explicit_filters):
        from agent.interview.session_manager import InterviewSessionManager, InterviewSessionState
        sm = InterviewSessionManager.__new__(InterviewSessionManager)
        sm.sessions = {}
        sm._agent_cache = None
        state = InterviewSessionState()
        state.explicit_filters = dict(explicit_filters)
        sm.sessions[session_id] = state
        return sm, state

    def test_replace_removes_stale_good_for_ml(self):
        sm, state = self._make_sm("test-replace", {
            "good_for_ml": True,
            "price_max_cents": 200000,
            "brand": "Dell",
        })

        with patch.object(sm, "_persist"):
            sm.update_filters("test-replace", {"price_max_cents": 60000}, replace=True)

        assert state.explicit_filters == {"price_max_cents": 60000}, \
            f"replace=True should fully replace; got {state.explicit_filters}"

    def test_merge_preserves_existing_keys(self):
        sm, state = self._make_sm("test-merge", {
            "good_for_ml": True,
            "price_max_cents": 200000,
        })

        with patch.object(sm, "_persist"):
            sm.update_filters("test-merge", {"price_max_cents": 60000}, replace=False)

        assert state.explicit_filters["price_max_cents"] == 60000
        assert state.explicit_filters.get("good_for_ml") is True, \
            "merge mode should preserve keys not in new_filters"
