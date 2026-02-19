"""
Query normalization utilities: typo correction and synonym expansion.

This module keeps logic lightweight and deterministic for tests and local usage.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple


DEFAULT_DICTIONARY = [
    "nvidia",
    "amd",
    "intel",
    "laptop",
    "laptops",
    "gpu",
    "cpu",
    "graphics",
    "card",
    "video",
    "geforce",
    "rtx",
    "gtx",
    "radeon",
    "book",
    "books",
    "computer",
    "computers",
    "desktop",
    "desktops",
    "pc",
    "pcs",
    "gaming",
    "work",
    "school",
]

DEFAULT_SYNONYMS: Dict[str, List[str]] = {
    "gpu": ["graphics card", "video card", "graphics", "video"],
    "graphics": ["gpu", "graphics card", "video card"],
    "video": ["gpu", "graphics card", "video card"],
    "nvidia": ["geforce", "rtx", "gtx"],
    "amd": ["radeon"],
    "laptop": ["notebook", "computer"],
    "computers": ["computer", "laptop"],
    "computer": ["laptop", "pc"],
    "pc": ["computer"],
}


def levenshtein_distance(a: str, b: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    a = a.lower()
    b = b.lower()
    rows = len(a) + 1
    cols = len(b) + 1
    dist = [[0] * cols for _ in range(rows)]

    for i in range(rows):
        dist[i][0] = i
    for j in range(cols):
        dist[0][j] = j

    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dist[i][j] = min(
                dist[i - 1][j] + 1,  # deletion
                dist[i][j - 1] + 1,  # insertion
                dist[i - 1][j - 1] + cost,  # substitution
            )
    return dist[-1][-1]


def similarity_ratio(a: str, b: str) -> float:
    """Compute similarity ratio in [0,1] based on Levenshtein distance."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    dist = levenshtein_distance(a, b)
    return 1.0 - dist / max(len(a), len(b))


def normalize_typos(text: str) -> str:
    """Reduce 3+ repeated characters to 2 (e.g., coooool -> cool).

    Skips digit sequences so prices like '2000' or '10000' are preserved.
    """
    return re.sub(r"([^\d])\1{2,}", r"\1\1", text)


def correct_typo(word: str, dictionary: Iterable[str]) -> Optional[str]:
    """Return corrected word if close to dictionary entry, else None."""
    if not word:
        return None
    word_lower = word.lower()
    dictionary = [w.lower() for w in dictionary]

    if word_lower in dictionary:
        return word_lower

    best_match = None
    best_distance = None

    for candidate in dictionary:
        dist = levenshtein_distance(word_lower, candidate)
        if best_distance is None or dist < best_distance:
            best_distance = dist
            best_match = candidate

    if best_distance is None or best_match is None:
        return None

    ratio = similarity_ratio(word_lower, best_match)
    if best_distance <= 2 and ratio >= 0.7:
        return best_match

    return None


def expand_synonyms(word: str, synonyms_map: Dict[str, List[str]]) -> List[str]:
    """Expand a word into synonyms with reverse lookup support."""
    if not word:
        return []
    word_lower = word.lower()

    if word_lower in synonyms_map:
        expanded = [word_lower] + synonyms_map[word_lower]
    else:
        expanded = [word_lower]
        for key, values in synonyms_map.items():
            if word_lower in [v.lower() for v in values]:
                expanded = [word_lower, key] + values
                break

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for term in expanded:
        term_lower = term.lower()
        if term_lower not in seen:
            seen.add(term_lower)
            unique.append(term_lower)
    return unique


def normalize_query(query: str) -> Tuple[str, Dict[str, object]]:
    """Normalize query string by fixing typos and expanding synonyms."""
    original = query or ""
    if not original:
        return "", {
            "changed": False,
            "original_query": "",
            "normalized_query": "",
            "corrections": {},
            "expansions": {},
        }

    normalized = normalize_typos(original)
    changed = normalized != original

    words = re.findall(r"\b[\w']+\b", normalized)
    corrections: Dict[str, str] = {}
    expansions: Dict[str, List[str]] = {}

    for word in words:
        corrected = correct_typo(word, DEFAULT_DICTIONARY)
        if corrected and corrected != word.lower():
            corrections[word] = corrected
            normalized = re.sub(rf"\b{re.escape(word)}\b", corrected, normalized, flags=re.IGNORECASE)
            changed = True

    for word in re.findall(r"\b[\w']+\b", normalized):
        expanded = expand_synonyms(word, DEFAULT_SYNONYMS)
        if len(expanded) > 1:
            expansions[word] = expanded
            # Don't set changed=True for expansions - only for text modifications

    metadata = {
        "changed": changed,
        "original_query": original,
        "normalized_query": normalized,
        "corrections": corrections,
        "expansions": expansions,
    }

    return normalized, metadata


def enhance_query_for_search(query: str) -> Tuple[str, List[str]]:
    """Return normalized query and flattened synonym expansions for search."""
    normalized, metadata = normalize_query(query)
    expanded_terms: List[str] = []

    expansions = metadata.get("expansions", {})
    if isinstance(expansions, dict):
        for _, synonyms in expansions.items():
            expanded_terms.extend(synonyms)

    # Remove duplicates while preserving order
    seen = set()
    unique_terms = []
    for term in expanded_terms:
        term_lower = term.lower()
        if term_lower not in seen:
            seen.add(term_lower)
            unique_terms.append(term_lower)

    return normalized, unique_terms
