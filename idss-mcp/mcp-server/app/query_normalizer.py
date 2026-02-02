"""
Query Normalization: Typo Correction & Synonym Expansion

Handles:
- Typos ("nvidiaa" → "nvidia")
- Synonyms ("GPU" → "graphics card")
- Creative variants ("coooool" → "cool")
- Brand/product name normalization

Uses:
- Levenshtein distance for typo detection
- Synonym dictionaries for expansion
- BPE/WordPiece via vector search (already implemented)
"""

from typing import List, Dict, Any, Optional, Tuple
import re
from difflib import SequenceMatcher


# Brand synonyms and common typos
BRAND_SYNONYMS = {
    "nvidia": ["geforce", "rtx", "gtx", "nvidiaa", "nvidya", "nvidie"],
    "amd": ["radeon", "ryzen", "amd", "amdd"],
    "intel": ["core", "xeon", "intell", "intle"],
    "apple": ["mac", "macbook", "iphone", "ipad", "appple", "aple"],
    "microsoft": ["ms", "msft", "microsft", "micorsoft"],
    "dell": ["delll", "del"],
    "hp": ["hewlett packard", "hewlett-packard", "hpp"],
    "lenovo": ["thinkpad", "lenovoo"],
    "asus": ["asus", "assus", "asuss"],
    "samsung": ["samsun", "samsuung"],
}

# Product type synonyms
PRODUCT_TYPE_SYNONYMS = {
    "gpu": ["graphics card", "video card", "graphics processing unit", "gpuu", "gp"],
    "cpu": ["processor", "chip", "cp", "cpuu"],
    "laptop": ["notebook", "computer", "pc", "laptopp", "lapto"],
    "headphone": ["earphone", "earbud", "headset", "headphon", "headphne"],
    "book": ["novel", "textbook", "literature", "boook", "bok"],
    "suv": ["sport utility", "crossover", "cuv", "suvv"],
    "sedan": ["car", "automobile", "sedann"],
}

# Common typo patterns (character repetition, missing letters)
TYPO_PATTERNS = [
    (r"(\w)\1{2,}", r"\1\1"),  # "coooool" → "cool"
    (r"(\w)\1{1,}", r"\1"),     # "bookss" → "books" (if not in dict)
]


def levenshtein_distance(s1: str, s2: str) -> int:
    """
    Calculate Levenshtein distance between two strings.
    
    Returns: Minimum number of single-character edits needed
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


def similarity_ratio(s1: str, s2: str) -> float:
    """Calculate similarity ratio between two strings (0-1)."""
    return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()


def correct_typo(word: str, dictionary: List[str], max_distance: int = 2, min_similarity: float = 0.8) -> Optional[str]:
    """
    Correct a typo by finding closest match in dictionary.
    
    Args:
        word: Word to correct
        dictionary: List of valid words
        max_distance: Maximum Levenshtein distance
        min_similarity: Minimum similarity ratio (0-1)
    
    Returns:
        Corrected word or None if no good match
    """
    word_lower = word.lower()
    
    # Exact match
    if word_lower in dictionary:
        return word_lower
    
    best_match = None
    best_score = 0.0
    
    for candidate in dictionary:
        candidate_lower = candidate.lower()
        
        # Check Levenshtein distance
        distance = levenshtein_distance(word_lower, candidate_lower)
        if distance > max_distance:
            continue
        
        # Check similarity ratio
        similarity = similarity_ratio(word_lower, candidate_lower)
        if similarity < min_similarity:
            continue
        
        # Score combines distance and similarity
        score = similarity / (1 + distance)
        if score > best_score:
            best_score = score
            best_match = candidate_lower
    
    return best_match


def expand_synonyms(word: str, synonym_dict: Dict[str, List[str]]) -> List[str]:
    """
    Expand word with synonyms.
    
    Returns: List including original word + synonyms
    """
    word_lower = word.lower()
    expanded = [word_lower]
    
    # Check if word is a synonym (reverse lookup)
    for key, synonyms in synonym_dict.items():
        if word_lower in synonyms or word_lower == key:
            expanded.append(key)
            expanded.extend(synonyms)
            break
    
    # Check if word is a key
    if word_lower in synonym_dict:
        expanded.extend(synonym_dict[word_lower])
    
    return list(set(expanded))  # Remove duplicates


def normalize_typos(text: str) -> str:
    """
    Normalize common typo patterns (character repetition).
    
    Example: "coooool" → "cool", "tastyyy" → "tasty"
    """
    normalized = text
    
    # Apply typo patterns (in order: more specific first)
    # First: reduce 3+ repetitions to 2
    normalized = re.sub(r"(\w)\1{2,}", r"\1\1", normalized)
    # Then: reduce 2 repetitions to 1 (but be careful - "book" vs "books")
    # Actually, let's be conservative - only reduce 3+ repetitions
    # "bookss" → "books" (keep plural), but "booksss" → "books"
    
    return normalized


def normalize_query(query: str, correct_brands: bool = True, expand_synonyms_flag: bool = True) -> Tuple[str, Dict[str, Any]]:
    """
    Normalize query with typo correction and synonym expansion.
    
    Args:
        query: Original query string
        correct_brands: Whether to correct brand typos
        expand_synonyms_flag: Whether to expand synonyms
    
    Returns:
        Tuple of (normalized_query, metadata)
        - normalized_query: Query with typos corrected
        - metadata: Dict with corrections and expansions applied
    """
    if not query or not query.strip():
        return query, {
            "original_query": query,
            "normalized_query": query,
            "corrections": {},
            "expansions": {},
            "changed": False
        }
    
    original_query = query
    normalized = query
    corrections = {}
    expansions = {}
    
    # Step 1: Normalize character repetition ("coooool" → "cool")
    normalized = normalize_typos(normalized)
    
    # Step 2: Extract words
    words = re.findall(r'\b\w+\b', normalized.lower())
    
    # Step 3: Correct brand typos
    if correct_brands:
        brand_dict = list(BRAND_SYNONYMS.keys())
        # Also add product type dict for correction (e.g., "laptopp" → "laptop")
        product_dict = list(PRODUCT_TYPE_SYNONYMS.keys())
        combined_dict = brand_dict + product_dict
        
        # Extract words with original case for replacement
        words_original = re.findall(r'\b\w+\b', normalized)
        for word_lower, word_original in zip(words, words_original):
            corrected = correct_typo(word_lower, combined_dict, max_distance=2, min_similarity=0.75)
            if corrected and corrected != word_lower:
                corrections[word_lower] = corrected
                # Replace case-insensitively
                normalized = re.sub(r'\b' + re.escape(word_original) + r'\b', corrected, normalized, flags=re.IGNORECASE)
    
    # Step 4: Expand synonyms
    if expand_synonyms_flag:
        for word in words:
            # Expand product type synonyms
            product_synonyms = expand_synonyms(word, PRODUCT_TYPE_SYNONYMS)
            if len(product_synonyms) > 1:
                expansions[word] = product_synonyms
            
            # Expand brand synonyms
            brand_synonyms = expand_synonyms(word, BRAND_SYNONYMS)
            if len(brand_synonyms) > 1:
                expansions[word] = brand_synonyms
    
    metadata = {
        "original_query": original_query,
        "normalized_query": normalized,
        "corrections": corrections,
        "expansions": expansions,
        "changed": normalized != original_query.lower()
    }
    
    return normalized, metadata


def enhance_query_for_search(query: str) -> Tuple[str, List[str]]:
    """
    Enhance query for search with synonym expansion.
    
    Returns:
        Tuple of (normalized_query, expanded_terms)
        - normalized_query: Typo-corrected query
        - expanded_terms: List of additional search terms from synonyms
    """
    normalized, metadata = normalize_query(query)
    
    # Collect all expanded terms
    expanded_terms = []
    for word, synonyms in metadata.get("expansions", {}).items():
        expanded_terms.extend(synonyms)
    
    return normalized, expanded_terms


# Example usage
if __name__ == "__main__":
    # Test cases
    test_queries = [
        "laptop with nvidiaa gpu",
        "gaming laptopp",
        "coooool headphones",
        "bookss for school",
        "suvv for family"
    ]
    
    for query in test_queries:
        normalized, metadata = normalize_query(query)
        print(f"Original: {query}")
        print(f"Normalized: {normalized}")
        print(f"Corrections: {metadata.get('corrections', {})}")
        print(f"Expansions: {metadata.get('expansions', {})}")
        print()
