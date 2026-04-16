"""Tests for app.enriched_reader — disjoint-keys invariant + merge behavior."""

import pytest

from app.enriched_reader import combine_raw_and_enriched


def test_combine_unions_disjoint_keys():
    raw = {"description": "Fast laptop", "cpu": "i7", "ram_gb": 16}
    enriched = {"normalized_description": "Compact i7 with 16GB", "normalized_at": "2026-04-15T00:00:00"}
    result = combine_raw_and_enriched(raw, enriched)
    assert result == {
        "description": "Fast laptop",
        "cpu": "i7",
        "ram_gb": 16,
        "normalized_description": "Compact i7 with 16GB",
        "normalized_at": "2026-04-15T00:00:00",
    }


def test_combine_raises_on_overlap():
    raw = {"description": "raw text", "cpu": "i7"}
    enriched = {"description": "would overwrite raw"}
    with pytest.raises(ValueError, match="enriched must not duplicate raw keys"):
        combine_raw_and_enriched(raw, enriched)


def test_combine_overlap_message_lists_all_offending_keys():
    raw = {"description": "x", "cpu": "i7", "ram_gb": 16}
    enriched = {"description": "y", "cpu": "i9"}
    with pytest.raises(ValueError, match=r"\['cpu', 'description'\]"):
        combine_raw_and_enriched(raw, enriched)


def test_combine_handles_none_inputs():
    assert combine_raw_and_enriched(None, {"a": 1}) == {"a": 1}
    assert combine_raw_and_enriched({"a": 1}, None) == {"a": 1}
    assert combine_raw_and_enriched(None, None) == {}


def test_combine_handles_empty_dicts():
    assert combine_raw_and_enriched({}, {}) == {}
    assert combine_raw_and_enriched({"a": 1}, {}) == {"a": 1}
    assert combine_raw_and_enriched({}, {"b": 2}) == {"b": 2}
