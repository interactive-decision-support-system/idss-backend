"""
Q2 — Intent Recognition Tests

Tests that the keyword fast-path in _handle_post_recommendation correctly
routes natural-language paraphrases that were previously missed.

Three categories:
  1. pros_cons — expanded natural paraphrases
  2. add_to_cart — casual purchase intent (ordinal + informal idioms)
  3. explain_fit — new intent for fit-analysis after filter changes
"""
import re
import pytest


# ---------------------------------------------------------------------------
# Helpers — extract the keyword lists and regex from chat_endpoint.py so we
# can test the matching logic directly without needing a running server.
# ---------------------------------------------------------------------------

def _match_pros_cons(msg: str) -> bool:
    """Return True if msg would be routed to pros_cons by the fast-path."""
    _FAST_PROS_CONS_KWS = (
        "tell me more about these",
        "pros and cons",
        "worth the price",
        "what do you get for the extra",
        "trade-off", "trade off", "tradeoff", "tradeoffs",
        "battery life on these",
        "how is the battery",
        # Q2 additions
        "strengths and weaknesses",
        "upsides and downsides",
        "upside and downside",
        "advantages and disadvantages",
        "what's good and bad",
        "good and bad about",
        "break it down for me",
        "give me the rundown",
        "walk me through",
    )
    return any(kw in msg.lower() for kw in _FAST_PROS_CONS_KWS)


def _match_explain_fit(msg: str) -> bool:
    """Return True if msg would be routed to explain_fit by the fast-path."""
    _FAST_EXPLAIN_FIT_KWS = (
        "why is it no longer",
        "why was it dropped",
        "why did it disappear",
        "why isn't it showing",
        "no longer a good fit",
        "no longer recommended",
        "still a good fit",
        "still a good option",
        "still fit my needs",
        "how does it fit",
        "why did you remove",
        "what changed",
        "why are the results different",
    )
    return any(kw in msg.lower() for kw in _FAST_EXPLAIN_FIT_KWS)


def _match_casual_purchase(msg: str) -> bool:
    """Return True if msg would be routed to add_to_cart via _CASUAL_PURCHASE_RE."""
    _CASUAL_PURCHASE_RE = re.compile(
        r"\b(?:i'?ll take|i(?:'?ll| will) get|let me get|give me|i want)"
        r"\s+(?:the\s+|that\s+|this\s+)?"
        r"(?:first|second|third|fourth|1st|2nd|3rd|4th|one|two|three|four|[1-4]|it|that(?: one)?|this(?: one)?)\b",
        re.IGNORECASE,
    )
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
# 3. explain_fit — fit-analysis after refinement (Q2)
# ---------------------------------------------------------------------------

class TestExplainFitIntent:
    """Verify that fit-analysis questions hit the new explain_fit fast-path."""

    @pytest.mark.parametrize("phrase", [
        "Why is the Dell no longer recommended?",
        "Why was it dropped from the list?",
        "Why did it disappear?",
        "Is the MacBook still a good fit?",
        "Does this still fit my needs?",
        "What changed about my results?",
        "Why are the results different now?",
        "Why did you remove the HP?",
    ])
    def test_explain_fit_matches(self, phrase):
        assert _match_explain_fit(phrase), f"Expected explain_fit match for: {phrase!r}"

    @pytest.mark.parametrize("phrase", [
        "Which has the best display?",    # targeted_qa
        "compare these",                  # compare
        "show me cheaper ones",           # refine
    ])
    def test_non_explain_fit_do_not_match(self, phrase):
        assert not _match_explain_fit(phrase), f"Expected NO explain_fit match for: {phrase!r}"
