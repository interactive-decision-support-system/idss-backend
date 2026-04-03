"""
Q2 — Intent Recognition Tests

Tests that the keyword fast-path in _handle_post_recommendation correctly
routes natural-language paraphrases that were previously missed.

Imports the actual keyword lists and regex from chat_endpoint.py so that
any production change is automatically covered by these tests.

Three categories:
  1. pros_cons — expanded natural paraphrases
  2. add_to_cart — casual purchase intent (ordinal + informal idioms)
  3. best_value — expanded keyword coverage for value/recommendation queries
"""
import pytest

from agent.chat_endpoint import (
    _FAST_PROS_CONS_KWS,
    _FAST_BEST_VALUE_KWS,
    _CASUAL_PURCHASE_RE,
)


# ---------------------------------------------------------------------------
# Helpers — thin wrappers that replicate the matching logic used in
# _handle_post_recommendation, using the production constants.
# ---------------------------------------------------------------------------

def _match_pros_cons(msg: str) -> bool:
    """Return True if msg would be routed to pros_cons by the fast-path."""
    return any(kw in msg.lower() for kw in _FAST_PROS_CONS_KWS)


def _match_best_value(msg: str) -> bool:
    """Return True if msg would be routed to best_value by the fast-path."""
    msg_lower = msg.lower()
    return any(kw in msg_lower for kw in _FAST_BEST_VALUE_KWS) and "similar" not in msg_lower


def _match_casual_purchase(msg: str) -> bool:
    """Return True if msg would be routed to add_to_cart via _CASUAL_PURCHASE_RE."""
    return bool(_CASUAL_PURCHASE_RE.search(msg.lower()))


# ---------------------------------------------------------------------------
# 1. pros_cons — natural paraphrases (Q2)
# ---------------------------------------------------------------------------

class TestProsConsExpanded:
    """Verify that natural paraphrases of 'pros and cons' hit the fast-path."""

    @pytest.mark.parametrize("phrase", [
        "What are the strengths and weaknesses of these laptops?",
        "Can you list the upsides and downsides?",
        "What are the advantages and disadvantages?",
        "What's good and bad about these options?",
        "Break it down for me",
        "Give me the rundown on these",
        "Walk me through the options",
    ])
    def test_natural_paraphrases_match(self, phrase):
        assert _match_pros_cons(phrase), f"Expected pros_cons match for: {phrase!r}"

    @pytest.mark.parametrize("phrase", [
        "compare these laptops",          # should be compare, not pros_cons
        "which has the best display?",    # should be targeted_qa
        "add the Dell to my cart",        # should be add_to_cart
    ])
    def test_non_pros_cons_do_not_match(self, phrase):
        assert not _match_pros_cons(phrase), f"Expected NO pros_cons match for: {phrase!r}"


# ---------------------------------------------------------------------------
# 2. add_to_cart — casual purchase intent (Q2)
# ---------------------------------------------------------------------------

class TestCasualPurchaseIntent:
    """Verify that informal purchase phrases trigger add_to_cart."""

    @pytest.mark.parametrize("phrase", [
        "I'll take it",
        "I'll take the second one",
        "let me get that one",
        "let me get the first",
        "give me the third one",
        "I'll get the 2nd",
        "I want that one",
        "I want the first one",
    ])
    def test_casual_purchase_matches(self, phrase):
        assert _match_casual_purchase(phrase), f"Expected add_to_cart match for: {phrase!r}"

    @pytest.mark.parametrize("phrase", [
        "I want a gaming laptop",         # should be new search, not cart
        "give me more options",           # should be refine, not cart
        "let me get a Dell under $800",   # search intent, not cart
    ])
    def test_non_purchase_do_not_match(self, phrase):
        assert not _match_casual_purchase(phrase), f"Expected NO add_to_cart match for: {phrase!r}"


# ---------------------------------------------------------------------------
# 3. best_value — expanded keyword coverage (Q2)
# ---------------------------------------------------------------------------

class TestBestValueExpanded:
    """Verify that natural value/recommendation queries hit the best_value fast-path."""

    @pytest.mark.parametrize("phrase", [
        "What's the top pick?",
        "Which gives the best bang for the buck?",
        "Best bang for your buck?",
        "Which has the best value for money?",
        "What's the best deal here?",
        "What's the best option overall?",
        "Which is the best overall?",
        "Which is the best one?",
        "Which do you recommend?",
        "What would you pick?",
        "What do you suggest?",
    ])
    def test_natural_paraphrases_match(self, phrase):
        assert _match_best_value(phrase), f"Expected best_value match for: {phrase!r}"

    @pytest.mark.parametrize("phrase", [
        "compare these laptops",          # should be compare
        "show me cheaper ones",           # should be refine
        "which has the best display?",    # should be targeted_qa (specific dimension)
        "show me the best similar ones",  # "similar" guard should exclude this
    ])
    def test_non_best_value_do_not_match(self, phrase):
        assert not _match_best_value(phrase), f"Expected NO best_value match for: {phrase!r}"
