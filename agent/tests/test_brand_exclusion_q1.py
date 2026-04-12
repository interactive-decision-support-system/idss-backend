"""
Q1 Scenarios 1–4 — Brand Exclusion Unit Tests
==============================================

Verifies that _detect_excluded_brands() correctly catches all four categories
of exclusion phrasing from Nikki's exam question list (issue #27).

  S1 — Direct negation phrase  : "steer clear of HP", "avoid Dell"
  S2 — Slang / informal hate   : "we hate ASUS", "I hate Apple"
  S3 — Bad-experience indirect : "terrible experience with Lenovo"
  S4 — Grouped / alias         : "no HP or Acer", "no mac" → Apple

Each test class isolates one scenario category.  The regex path is tested
deterministically by patching the LLM semantic fallback to return [].
A separate class exercises the negative cases (non-exclusion queries must
produce no results).

Design notes:
- We import _detect_excluded_brands directly so any production regex change
  is automatically exercised.
- _extract_excluded_brands_semantic is patched to return [] throughout so
  tests do not require a live OpenAI key or network connection.
- _filter_exclusions_by_message_mentions is NOT patched — it exercises the
  hallucination guard to ensure only brands present in text are returned.
"""
import pytest
from unittest.mock import patch

from agent.universal_agent import _detect_excluded_brands


# ---------------------------------------------------------------------------
# Module-level patch target for the LLM semantic fallback.
# All test classes that exercise the regex path use this.
# ---------------------------------------------------------------------------
_SEMANTIC_PATCH = "agent.universal_agent._extract_excluded_brands_semantic"


# ---------------------------------------------------------------------------
# S1 — Direct negation / avoidance phrases
# "steer clear of HP", "avoid Dell", "never ASUS" ...
# These should be caught by the regex without the LLM.
# ---------------------------------------------------------------------------

class TestS1DirectNegationPhrases:
    """S1: Direct keyword phrases — regex path only (LLM stubbed out)."""

    @pytest.mark.parametrize("message,expected_brand", [
        ("I want a laptop, steer clear of HP",          "HP"),
        ("steer clear of HP please",                    "HP"),
        ("avoid Dell at all costs",                     "Dell"),
        ("avoid Acer laptops",                          "Acer"),
        ("never ASUS for me",                           "ASUS"),
        ("anything but Lenovo",                         "Lenovo"),
        ("without Microsoft please",                    "Microsoft"),
        ("exclude HP from results",                     "HP"),
        ("skip Samsung",                                "Samsung"),
        ("refuse MSI",                                  "MSI"),
    ])
    def test_direct_phrase_extracts_brand(self, message: str, expected_brand: str):
        """Each direct exclusion phrase must yield the expected brand."""
        with patch(_SEMANTIC_PATCH, return_value=[]):
            result = _detect_excluded_brands(message)
        assert expected_brand in result, (
            f"Expected {expected_brand!r} in exclusions for {message!r}. Got: {result}"
        )

    @pytest.mark.parametrize("message,expected_brand", [
        ("steer clear of HP",  "HP"),
        ("avoid Dell laptops", "Dell"),
    ])
    def test_result_contains_only_canonical_names(self, message: str, expected_brand: str):
        """Results must use canonical brand names, not raw user text."""
        with patch(_SEMANTIC_PATCH, return_value=[]):
            result = _detect_excluded_brands(message)
        # Canonical names are title-cased known brands (HP, Dell, etc.)
        assert all(b.strip() == b and b[0].isupper() for b in result), (
            f"Non-canonical brand name in result: {result}"
        )


# ---------------------------------------------------------------------------
# S2 — Slang / informal hate phrases
# "we hate ASUS", "I hate Apple" — "hate" is in the regex alternation.
# ---------------------------------------------------------------------------

class TestS2SlangHatePhrases:
    """S2: Informal hatred phrases — caught by 'hate' in the regex."""

    @pytest.mark.parametrize("message,expected_brand", [
        ("we hate ASUS",              "ASUS"),
        ("I hate Apple products",     "Apple"),
        ("hate HP laptops",           "HP"),
        ("I really hate Dell",        "Dell"),
    ])
    def test_hate_phrase_extracts_brand(self, message: str, expected_brand: str):
        """'hate <Brand>' must land the brand in the exclusion list."""
        with patch(_SEMANTIC_PATCH, return_value=[]):
            result = _detect_excluded_brands(message)
        assert expected_brand in result, (
            f"Expected {expected_brand!r} excluded for {message!r}. Got: {result}"
        )

    def test_hate_phrase_deduplicated(self):
        """If the same brand appears twice (via regex + LLM mock), it must only appear once."""
        # Simulate LLM also returning ASUS (as if semantic path duplicated it)
        with patch(_SEMANTIC_PATCH, return_value=["ASUS"]):
            result = _detect_excluded_brands("we hate ASUS")
        assert result.count("ASUS") == 1, f"Duplicate ASUS in: {result}"


# ---------------------------------------------------------------------------
# S3 — Bad-experience / indirect experiential phrases
# "had a terrible experience with Lenovo" — regex covers "terrible experience with".
# ---------------------------------------------------------------------------

class TestS3BadExperiencePhrases:
    """S3: Indirect experiential phrases — caught by regex, no LLM needed."""

    @pytest.mark.parametrize("message,expected_brand", [
        ("had a terrible experience with Lenovo",  "Lenovo"),
        ("terrible experience with Samsung",        "Samsung"),
        ("bad experience with Dell",               "Dell"),
        ("I had a bad experience with HP",         "HP"),
    ])
    def test_bad_experience_extracts_brand(self, message: str, expected_brand: str):
        """'bad/terrible experience with <Brand>' must exclude that brand."""
        with patch(_SEMANTIC_PATCH, return_value=[]):
            result = _detect_excluded_brands(message)
        assert expected_brand in result, (
            f"Expected {expected_brand!r} excluded for {message!r}. Got: {result}"
        )


# ---------------------------------------------------------------------------
# S4 — Grouped exclusions ("no HP or Acer") and alias resolution ("no mac")
# ---------------------------------------------------------------------------

class TestS4GroupedAndAliasExclusions:
    """S4: Multi-brand and alias exclusions — split logic + _BRAND_VALUE_ALIASES."""

    @pytest.mark.parametrize("message,expected_brands", [
        ("no HP or Acer please",        ["HP", "Acer"]),
        ("avoid Dell and MSI",          ["Dell", "MSI"]),
        ("no HP, Acer, or Dell",        ["HP", "Acer", "Dell"]),
        ("anything but Lenovo or ASUS", ["Lenovo", "ASUS"]),
    ])
    def test_grouped_exclusion_captures_all_brands(self, message: str, expected_brands: list):
        """Comma- and 'or'/'and'-separated brands must all be captured."""
        with patch(_SEMANTIC_PATCH, return_value=[]):
            result = _detect_excluded_brands(message)
        for brand in expected_brands:
            assert brand in result, (
                f"Expected {brand!r} in exclusions for {message!r}. Got: {result}"
            )

    @pytest.mark.parametrize("message,expected_brand", [
        # "mac" alias → "Apple" via _BRAND_VALUE_ALIASES
        ("no mac laptops",         "Apple"),
        ("steer clear of macbook", "Apple"),
        # "thinkpad" alias → "Lenovo"
        ("no thinkpad please",     "Lenovo"),
        # "xps" alias → "Dell"
        ("avoid xps",              "Dell"),
    ])
    def test_alias_resolved_to_canonical_brand(self, message: str, expected_brand: str):
        """User shorthand/alias must be resolved to the canonical brand before returning."""
        with patch(_SEMANTIC_PATCH, return_value=[]):
            result = _detect_excluded_brands(message)
        assert expected_brand in result, (
            f"Expected canonical {expected_brand!r} for alias in {message!r}. Got: {result}"
        )

    def test_grouped_exclusion_no_duplicates(self):
        """Same brand appearing multiple times in a message must not be duplicated."""
        with patch(_SEMANTIC_PATCH, return_value=[]):
            result = _detect_excluded_brands("no HP and no HP please")
        assert result.count("HP") == 1, f"HP duplicated in: {result}"


# ---------------------------------------------------------------------------
# Negative cases — queries with NO exclusion intent must return []
# ---------------------------------------------------------------------------

class TestNoExclusionQueries:
    """Non-exclusion queries must not accidentally trigger brand exclusion."""

    @pytest.mark.parametrize("message", [
        "I need a gaming laptop under $800",
        "show me something fast for video editing",
        "I want a lightweight laptop for college",
        "best laptop for machine learning",
        "something under $1000 please",
        "compare Dell and HP laptops",          # mention without negation
        "tell me more about this HP laptop",    # asking about HP, not excluding it
    ])
    def test_non_exclusion_query_returns_empty(self, message: str):
        """Non-exclusion messages must not produce any excluded brands."""
        with patch(_SEMANTIC_PATCH, return_value=[]):
            result = _detect_excluded_brands(message)
        assert result == [], (
            f"Expected no exclusions for {message!r}, got: {result}"
        )

    def test_brand_mention_without_negation_not_excluded(self):
        """Mentioning a brand positively must NOT add it to exclusions."""
        with patch(_SEMANTIC_PATCH, return_value=[]):
            result = _detect_excluded_brands("I really like HP laptops")
        assert "HP" not in result, (
            f"HP should not be excluded for positive mention. Got: {result}"
        )
