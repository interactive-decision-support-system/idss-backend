"""
Query-parser negation regressions.

Kept separate from the general parser test suite to isolate this behavior.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.query_parser import (
    _extract_excluded_screen_sizes,
    _extract_min_screen,
    enhance_search_request,
)


def test_negated_14_inch_should_not_extract_min_screen():
    """Negated screen preference should not be parsed as min screen."""
    assert _extract_min_screen("I don't want a 14 inch screen") is None


def test_negated_14_inch_should_extract_excluded_screen_size():
    """Negated screen preference should become explicit exclusion."""
    assert _extract_excluded_screen_sizes("I don't want a 14 inch screen") == [14.0]


def test_negated_14_inch_should_not_set_min_screen_inches():
    """Full parser path should emit excluded_screen_sizes, not min_screen_inches."""
    _, filters = enhance_search_request("I don't want a 14 inch screen laptop", {})
    assert "min_screen_inches" not in filters
    assert filters.get("excluded_screen_sizes") == [14.0]
