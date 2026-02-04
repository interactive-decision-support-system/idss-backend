"""
Minimal query parser for extracting structured filters from normalized queries.

This lightweight implementation keeps behavior deterministic for tests.
"""

from __future__ import annotations

from typing import Dict, Tuple


def enhance_search_request(normalized_query: str, filters: Dict[str, object]) -> Tuple[str, Dict[str, object]]:
    """
    Return cleaned query and additional filters.

    Currently returns the query unchanged and no additional filters, but is
    structured so we can expand later.
    """
    cleaned_query = (normalized_query or "").strip()
    return cleaned_query, {}
