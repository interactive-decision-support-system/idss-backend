"""
Unit tests for the interview threshold fix — requiring at least one HARD
constraint slot to skip the interview (preference_discovery group Q212/Q214/Q218).

Background
----------
The prior heuristic in _regex_fallback_extract() fired when ≥1 substantive
criterion was extracted, which included soft slots like use_case.  This caused
"I need something for school" (use_case=school, no budget/brand) to skip the
interview and jump straight to recommendations.

The fix: the heuristic now requires at least one slot from _HARD_SLOTS_INTERVIEW
(budget, brand, excluded_brands, min_ram_gb, screen_size, storage_type, ...).
Use-case-only extraction → no hard slot → interview continues → agent asks budget.

Separately, the vagueness gate _has_spec_signal regex was trimmed to remove soft
use-case keywords ("gaming", "coding", "school", "work") so they no longer count
as hard spec signals and no longer suppress the vagueness gate.

Test strategy
--------------
All tests force the LLM extraction to raise RuntimeError so only the regex
fallback path runs.  This isolates the heuristic under test without needing a
real OpenAI key.  The returned response_type tells us whether the agent fired
the interview ("question") or skipped to search ("recommendations_ready").
"""

import pytest
from unittest.mock import MagicMock, patch

from agent.universal_agent import UniversalAgent, AgentState
from agent.query_rewriter import RewriteResult


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_fresh_agent(domain: str = "laptops") -> UniversalAgent:
    """
    Return a UniversalAgent in the INTERVIEW state with its OpenAI client mocked
    to always raise RuntimeError.  This forces every call through the regex
    fallback path, isolating the heuristic under test.
    """
    agent = UniversalAgent(session_id="test-interview", max_questions=3)
    agent.domain = domain
    # Mock the LLM client so it always fails → regex fallback runs
    agent.client = MagicMock()
    agent.client.beta.chat.completions.parse.side_effect = RuntimeError(
        "LLM unavailable (mocked for test)"
    )
    # Mock _handoff_to_search so it returns a stable recommendations_ready response
    # without hitting the real search backend
    agent._handoff_to_search = MagicMock(return_value={
        "response_type": "recommendations_ready",
        "message": "Here are some laptops.",
        "products": [],
    })
    # Mock _generate_question so the agent can always produce a question response
    # without hitting the LLM (needed for "question" branch tests)
    from agent.universal_agent import GeneratedQuestion
    mock_q = GeneratedQuestion(
        question="What is your budget?",
        quick_replies=["Under $500", "Under $800", "Under $1200"],
        topic="budget",
    )
    agent._generate_question = MagicMock(return_value=mock_q)
    return agent


def _response_type(message: str, domain: str = "laptops") -> str:
    """
    Process a message through a fresh cold-start agent and return the
    response_type string ("question" or "recommendations_ready").

    The query rewriter (_rewrite_query) is patched to return the original message
    unchanged so that a live OpenAI call is not required and the test does not
    depend on the rewriter's own LLM behaviour.
    """
    agent = _make_fresh_agent(domain)
    # Patch the query rewriter to return the message unchanged (passthrough).
    # This keeps the test focused on the extraction heuristic under test.
    passthrough = RewriteResult(rewritten=message, is_clarification=False)
    with patch("agent.universal_agent._rewrite_query", return_value=passthrough):
        result = agent.process_message(message)
    return result.get("response_type", "")


# ─── 1. Soft use-case only → should ask a question (interview required) ───────

class TestSoftCriteriaRequireInterview:
    """
    Use-case-only messages must NOT skip the interview — budget is still unknown.
    These correspond to preference_discovery queries Q212, Q214, Q218 that were
    previously failing because the agent gave recommendations without asking budget.
    """

    def test_school_use_case_triggers_question(self):
        # "I need something for school" → use_case=school only → ask budget
        rtype = _response_type("I need something for school")
        assert rtype == "question", (
            "use_case-only query should ask a clarifying question, not recommend"
        )

    def test_college_use_case_triggers_question(self):
        # "My daughter is starting college" → implicit student use_case, no budget
        rtype = _response_type("My daughter is starting college and needs a computer")
        assert rtype == "question"

    def test_everyday_use_triggers_question(self):
        # "I need a new one for everyday stuff" → vague use_case, no hard constraints
        rtype = _response_type("My old laptop broke, I need a new one for everyday stuff")
        assert rtype == "question"

    def test_bare_laptop_request_triggers_question(self):
        # "I need a laptop" → no criteria at all → must ask
        rtype = _response_type("I need a laptop")
        assert rtype == "question"


# ─── 2. Hard constraint present → may skip interview ─────────────────────────

class TestHardConstraintSkipsInterview:
    """
    When at least one HARD constraint (budget, brand, spec) is provided,
    the agent should proceed to recommendations without an extra question.
    These correspond to the one-liner and single-constraint eval queries that
    should NOT require multiple interview turns.
    """

    def test_budget_constraint_skips_interview(self):
        # "gaming laptop under $1000" → use_case + budget → hard constraint present
        rtype = _response_type("I need a gaming laptop under $1000")
        assert rtype == "recommendations_ready", (
            "budget + use_case should skip interview and go straight to search"
        )

    def test_brand_constraint_skips_interview(self):
        # "show me Dell laptops" → brand is a hard constraint
        rtype = _response_type("show me Dell laptops please")
        assert rtype == "recommendations_ready"

    def test_ram_constraint_skips_interview(self):
        # Explicit spec: 16GB RAM → hard constraint present
        rtype = _response_type("I need a laptop with 16GB RAM for programming")
        assert rtype == "recommendations_ready"

    def test_budget_only_skips_interview(self):
        # "laptop under $800" → budget without use_case → still hard constraint
        rtype = _response_type("I am looking for a laptop under $800")
        assert rtype == "recommendations_ready"

    def test_multi_constraint_skips_interview(self):
        # Multiple hard constraints → definitely skip
        rtype = _response_type("Dell laptop under $800 with at least 16GB RAM")
        assert rtype == "recommendations_ready"


# ─── 3. Vagueness gate — soft spec keywords no longer bypass gate ─────────────

class TestVaguenessGateKeywords:
    """
    The _has_spec_signal regex previously included "gaming", "coding", "school",
    "work" — causing the vagueness gate to be suppressed for 5-word messages.
    After the fix, these soft keywords are removed, so the gate fires correctly
    on short messages with no hard spec signals.

    The gate only applies when words ≤ 5, so we test with short messages.
    """

    def test_short_school_query_fires_gate(self):
        # "need laptop for school" — 4 words, "school" no longer bypasses gate
        rtype = _response_type("need laptop for school")
        assert rtype == "question"

    def test_short_gaming_query_fires_gate(self):
        # "gaming laptop please" — 3 words, "gaming" no longer bypasses gate
        rtype = _response_type("gaming laptop please")
        assert rtype == "question"

    def test_budget_signal_still_bypasses_gate(self):
        # A 4-word+ message with a budget signal should skip the interview.
        # (The heuristic also requires ≥4 words; 3-word messages like "laptop under $800"
        #  are intentionally held back by the word-count guard regardless of budget.)
        rtype = _response_type("I need a laptop under $800")
        assert rtype == "recommendations_ready"
