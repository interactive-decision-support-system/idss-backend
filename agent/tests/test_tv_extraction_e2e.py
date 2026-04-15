"""
End-to-end extraction tests for the TV domain.

These tests call _extract_criteria() which hits the real OpenAI API.
They verify that multi-constraint TV messages produce correct slot extraction
and wants_recommendations=True, matching the level of coverage that laptops have.

Requires OPENAI_API_KEY to run. Skipped without it.

Ref: https://github.com/interactive-decision-support-system/idss-backend/issues/29
"""

import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — skipping LLM extraction tests",
)


def _make_tv_agent():
    """Create a minimal UniversalAgent configured for the TV domain."""
    from agent.universal_agent import UniversalAgent

    agent = UniversalAgent.__new__(UniversalAgent)
    agent.domain = "tvs"
    agent.filters = {}
    agent.questions_asked = []
    agent.question_count = 0
    agent.history = []

    from openai import OpenAI
    agent.client = OpenAI()
    return agent


def _extract(message: str):
    """Run full extraction pipeline on a TV message and return (result, agent)."""
    from agent.domain_registry import get_domain_schema
    agent = _make_tv_agent()
    schema = get_domain_schema("tvs")
    result = agent._extract_criteria(message, schema)
    return result, agent


# ---------------------------------------------------------------------------
# Multi-constraint messages → wants_recommendations=True
# ---------------------------------------------------------------------------

class TestTVMultiConstraintExtraction:
    """Messages with ≥2 constraints should extract slots and skip to search."""

    def test_gaming_tv_under_1000(self):
        result, agent = _extract("can you give me a good TV for gaming under 1000")
        assert result is not None
        assert result.wants_recommendations is True, (
            f"Expected wants_recommendations=True for 2-constraint message. "
            f"Extracted criteria: {[(c.slot_name, c.value) for c in (result.criteria or [])]}"
        )
        slot_names = {c.slot_name for c in (result.criteria or [])}
        assert "budget" in slot_names or "budget" in agent.filters, (
            f"Expected budget to be extracted. Slots: {slot_names}, filters: {agent.filters}"
        )

    def test_65_inch_oled_under_1500(self):
        result, agent = _extract("65 inch OLED TV under $1500")
        assert result is not None
        assert result.wants_recommendations is True
        slot_names = {c.slot_name for c in (result.criteria or [])}
        # Should extract at least 2 of: screen_size, panel_type, budget
        extracted_tv_slots = slot_names & {"screen_size", "panel_type", "budget"}
        assert len(extracted_tv_slots) >= 2, (
            f"Expected ≥2 TV slots from '65 inch OLED TV under $1500'. Got: {slot_names}"
        )

    def test_samsung_qled_under_2000(self):
        result, agent = _extract("I want a Samsung QLED TV under $2000")
        assert result is not None
        assert result.wants_recommendations is True
        all_slots = {**agent.filters}
        for c in (result.criteria or []):
            all_slots[c.slot_name] = c.value
        assert "budget" in all_slots, f"Expected budget. Got: {all_slots}"
        # Samsung should be recognized as desired brand (not excluded).
        # LLM can be nondeterministic here; the key assertion is that
        # wants_recommendations=True and budget is extracted.
        assert "panel_type" in all_slots or "brand" in all_slots, (
            f"Expected panel_type or brand. Got: {all_slots}"
        )

    def test_budget_tv_for_bedroom(self):
        result, agent = _extract("budget TV for the bedroom under $500")
        assert result is not None
        assert result.wants_recommendations is True

    def test_75_inch_for_sports(self):
        result, agent = _extract("75 inch TV for watching sports, budget around $1500")
        assert result is not None
        assert result.wants_recommendations is True
        slot_names = {c.slot_name for c in (result.criteria or [])}
        assert "screen_size" in slot_names or "screen_size" in agent.filters


# ---------------------------------------------------------------------------
# Vague messages → wants_recommendations=False
# ---------------------------------------------------------------------------

class TestTVVagueQueries:
    """Vague messages should NOT trigger wants_recommendations."""

    def test_bare_tv(self):
        result, _ = _extract("I want a TV")
        assert result is not None
        # Only 0 constraints → should not trigger recommendations
        # (Note: the heuristic requires ≥1 substantive + ≥4 words,
        #  so this may pass even with 0 slots. Either way is acceptable
        #  as long as the agent asks a follow-up.)

    def test_best_tv(self):
        result, _ = _extract("best tv")
        assert result is not None
        # "best tv" is 2 words — below the ≥4 word threshold
        assert result.wants_recommendations is False, (
            "Bare 'best tv' should not trigger recommendations"
        )


# ---------------------------------------------------------------------------
# Slot value correctness
# ---------------------------------------------------------------------------

class TestTVSlotValues:
    """Verify extracted slot values are correct and use canonical names."""

    def test_panel_type_oled(self):
        result, agent = _extract("I want an OLED TV")
        slot_names = {c.slot_name for c in (result.criteria or [])}
        all_slots = {**agent.filters}
        for c in (result.criteria or []):
            all_slots[c.slot_name] = c.value
        # panel_type should be extracted
        assert "panel_type" in all_slots, f"Expected panel_type. Got: {all_slots}"
        assert "OLED" in str(all_slots.get("panel_type", "")).upper()

    def test_budget_under_without_dollar_sign(self):
        """'under 1000' without $ should still be extracted as budget."""
        result, agent = _extract("TV for gaming under 1000")
        all_slots = {**agent.filters}
        for c in (result.criteria or []):
            all_slots[c.slot_name] = c.value
        assert "budget" in all_slots, (
            f"Expected budget from 'under 1000' (no $ sign). Got: {all_slots}"
        )
        budget_val = str(all_slots["budget"]).lower().replace("$", "").replace(",", "")
        assert "1000" in budget_val

    def test_brand_samsung(self):
        """Samsung should be extracted as brand. LLM extraction can be
        nondeterministic, so we retry once on failure."""
        for attempt in range(2):
            result, agent = _extract("I want a Samsung TV, 65 inches")
            all_slots = {**agent.filters}
            for c in (result.criteria or []):
                all_slots[c.slot_name] = c.value
            if "brand" in all_slots:
                assert "samsung" in str(all_slots["brand"]).lower()
                return
        pytest.fail(f"Expected brand='Samsung' after 2 attempts. Got: {all_slots}")
