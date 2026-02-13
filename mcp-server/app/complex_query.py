"""
Complex vs simple query detection for half-hybrid routing (week6tips tip 71).

For complex queries: use universal_agent (or LLM→filters).
For simple queries: use existing MCP filter path (no LLM, faster).

Thomas can use is_complex_query() to decide whether to call UniversalAgent
or the current chat/search flow.
"""

import re
from typing import Dict, Any, Optional


# Phrases that often indicate a complex, natural-language need (Reddit-style)
COMPLEX_PHRASE_PATTERNS = [
    r"\bgood for\b",
    r"\bneed to (run|handle|support)\b",
    r"\bwithout (issues|lag|problems)\b",
    r"\bfor (web development|machine learning|deep learning|gaming|creative work)\b",
    r"\bruns? (Linux|Windows)\b",
    r"\bexcellent (keyboard|battery|screen)\b",
    r"\bbattery (life|lasting)\b",
    r"\b(at least|minimum|around)\s+\d+\s*(GB|TB|hours|inches)",
    r"\buse(d)? for\b",
    r"\bI need\b",
    r"\blooking for\b",
    r"\brecommend\b",
    r"\bwhich (one|laptop|book|phone)\b",
    r"\b(repairable|sustainable|modular)\b",
    r"\bFairphone\b",
]


def is_complex_query(query: str, filters: Optional[Dict[str, Any]] = None) -> bool:
    """
    Heuristic: is this a complex query that should use LLM/UniversalAgent?

    Simple: short query + few filters (e.g. "laptop", category + price).
    Complex: long or multi-sentence, or contains use-case/requirement language.

    Args:
        query: Raw user message or search query.
        filters: Optional already-extracted filters (many keys → more likely complex).

    Returns:
        True if routing to UniversalAgent (or LLM→filters) is recommended.
    """
    if not query or not isinstance(query, str):
        return False
    text = query.strip()
    if not text:
        return False

    # Vehicles/cars: ALWAYS use simple path → detect_domain → IDSS backend. Never send to UniversalAgent.
    text_lower = text.lower()
    vehicle_terms = ("car", "cars", "vehicle", "vehicles", "suv", "suvs", "truck", "trucks", "sedan", "van", "automobile")
    if any(t in text_lower for t in vehicle_terms):
        return False

    # "Show me all X" and simple domain selection - always use simple path; never route to UniversalAgent
    if text_lower in ("show me all laptops", "show me all books", "show me all phones",
                     "phones", "phone", "show me phones", "laptops", "books"):
        return False

    # Multiple sentences → likely complex
    if text.count(".") + text.count("?") >= 2:
        return True
    # Long single sentence (e.g. > 15 words)
    words = len(text.split())
    if words > 15:
        return True

    # Contains complex phrasing (use-case, requirements)
    for pat in COMPLEX_PHRASE_PATTERNS:
        if re.search(pat, text_lower, re.IGNORECASE):
            return True

    # Many filters already present → might be from a complex flow
    if filters and isinstance(filters, dict):
        # Exclude internal keys
        n = len([k for k in filters if not k.startswith("_")])
        if n >= 4:
            return True

    return False
