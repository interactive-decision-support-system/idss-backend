"""
Input validation and semantic matching for user queries.

Handles:
1. Invalid/gibberish input detection
2. Fuzzy matching for misspellings (booksss → books, computr → laptop)
3. Semantic normalization (computer → laptop, notebook → laptop)
"""

import re
from typing import Optional, Tuple
from app.query_normalizer import normalize_query, levenshtein_distance


# Valid domain keywords with fuzzy matching tolerance
# Include common variations and misspellings
# NOTE: Order matters - more specific/common terms first
DOMAIN_KEYWORDS = {
    "vehicles": ["vehicle", "vehicles", "vehicl", "car", "cars", "truck", "trucks", "suv", "suvs", "sedan", "sedans", "auto", "automobile"],
    "laptops": [
        # Direct matches first (no fuzzy needed)
        "laptop", "laptops", "notebook", "notebooks", "computer", "computers",
        "macbook", "chromebook", "pc", "pcs",
        # Common misspellings
        "lapto", "lpatop", "notbook", "notbooks", "computr", "comp",
    ],
    "books": ["book", "books", "novel", "novels", "boks", "bok", "reading", "ebook", "ebooks"],
    "jewelry": ["jewelry", "jewellery", "jewlry", "jewlery", "necklace", "earrings", "bracelet", "ring", "pendant"],
    "accessories": ["accessories", "accessory", "scarf", "hat", "belt", "bag", "watch", "sunglasses"],
    "clothing": ["clothing", "clothes", "apparel", "dress", "shirt", "pants", "jacket", "fashion"],
    "beauty": ["beauty", "cosmetics", "makeup", "lipstick", "eyeshadow", "skincare"],
}

# Invalid/gibberish patterns (but not price ranges or valid short patterns)
INVALID_PATTERNS = [
    r"^[a-z]{1}$",  # Single letter only: "a"
    r"^(hi|hello|hey|sup|yo)$",  # Greetings alone (ok in context)
    r"^[^a-zA-Z0-9$-]+$",  # Only special characters (except $ and -): "!!!", "..."
]

# Valid patterns that look short but are OK
VALID_PATTERNS = [
    r"\$\d+",  # Price: "$500"
    r"\d+\s*[-–]\s*\$?\d+",  # Price range: "700-1200", "$700-$1200"
    r"\$\d+\s*[-–]\s*\$\d+",  # Price range with both $: "$700-$1200"
    r"^\d+$",  # Just numbers in context (might be price)
    r"^(yes|no|yep|nah|ok|nope)$",  # Valid responses in context
]

# Greetings are OK but should trigger domain selection
GREETINGS = ["hi", "hello", "hey", "hi there", "hello there", "hey there", "sup", "yo", "good morning", "good afternoon"]

# Common valid intents (these are OK even if short)
VALID_SHORT_INTENTS = [
    "help", "start", "restart", "reset", "clear", "more",
    "next", "back", "skip", "done", "thanks", "thank you",
    "start over", "new search"
]


def is_valid_input(message: str) -> Tuple[bool, Optional[str]]:
    """
    Check if user input is valid (not gibberish).
    
    Returns:
        (is_valid, error_message)
        - (True, None) if valid
        - (False, "error message") if invalid
    """
    if not message or not message.strip():
        return False, "Please enter a message."
    
    normalized = message.lower().strip()
    
    # Check for valid patterns first (prices, ranges, etc.)
    for pattern in VALID_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return True, None
    
    # Check for greetings - these are valid but need domain selection
    if normalized in GREETINGS or any(normalized.startswith(g + " ") for g in GREETINGS):
        return True, None
    
    # Check for valid short intents
    if normalized in VALID_SHORT_INTENTS:
        return True, None
    
    # Check for invalid patterns
    for pattern in INVALID_PATTERNS:
        if re.match(pattern, normalized):
            return False, "I didn't understand that. Please tell me what you're looking for (vehicles, laptops, books, jewelry, accessories, clothing, or beauty)."
    
    # Check if message has at least one alphabetic word (prevents "!!!")
    has_word = bool(re.search(r'[a-zA-Z]{2,}', message))
    if not has_word and not re.search(r'\d', message):  # Unless it has numbers (prices)
        return False, "I didn't understand that. What are you looking for?"
    
    # Check for excessive gibberish (random characters)
    words = re.findall(r'\b[a-zA-Z]+\b', normalized)
    if words:
        # Check if at least one word looks reasonable (not random keyboard mashing)
        has_reasonable_word = False
        for word in words:
            if len(word) >= 3:
                # Check vowel ratio (random keyboard mashing usually has low vowel count)
                vowels = sum(1 for c in word if c in 'aeiou')
                vowel_ratio = vowels / len(word)
                # English words typically have 30-50% vowels
                if 0.2 <= vowel_ratio <= 0.7:
                    has_reasonable_word = True
                    break
                # Also accept known patterns
                if word in ["pc", "cpu", "gpu", "rtx", "gtx"]:
                    has_reasonable_word = True
                    break
        
        if not has_reasonable_word and len(words[0]) >= 4:
            return False, "I didn't understand that. Please tell me what you're looking for."
    
    return True, None


def fuzzy_match_domain(message: str) -> Optional[str]:
    """
    Fuzzy match message to a domain (vehicles, laptops, books).
    Handles misspellings like "booksss" → "books", "computr" → "laptop", "notbook" → "laptop".
    
    Returns:
        domain name if matched, None if no match
    """
    normalized = message.lower().strip()
    
    # Direct keyword match first
    for domain, keywords in DOMAIN_KEYWORDS.items():
        for keyword in keywords:
            if keyword in normalized:
                return domain
    
    # Fuzzy matching for misspellings
    words = re.findall(r'\b[a-zA-Z]+\b', normalized)
    best_match = None
    best_distance = float('inf')
    best_domain = None
    
    for word in words:
        if len(word) < 3:  # Skip short words
            continue
            
        for domain, keywords in DOMAIN_KEYWORDS.items():
            for keyword in keywords:
                distance = levenshtein_distance(word, keyword)
                
                # More lenient matching based on word length
                # Short words (3-5 chars): allow 1-2 char difference
                # Medium words (6-8 chars): allow 2-3 char difference  
                # Long words (9+ chars): allow 3 char difference
                if len(word) <= 5:
                    max_distance = 2
                elif len(word) <= 8:
                    max_distance = 3
                else:
                    max_distance = 3
                
                # Also check similarity ratio
                similarity = 1.0 - (distance / max(len(word), len(keyword)))
                
                if distance <= max_distance and similarity >= 0.6 and distance < best_distance:
                    best_distance = distance
                    best_match = keyword
                    best_domain = domain
    
    return best_domain


def normalize_domain_keywords(message: str) -> str:
    """
    Normalize domain-related keywords in the message.
    ONLY normalizes actual misspellings of domain keywords, NOT regular words.
    
    Examples:
        "booksss" → "books" (repeated letter)
        "computr" → "laptop" (missing letter)
        "notbook" → "laptop" (transposition)
        "Work" → "Work" (NOT "book" - completely different word!)
    
    Returns:
        normalized message
    """
    normalized_msg, metadata = normalize_query(message)
    
    # Also check for domain-specific fuzzy matches
    words = re.findall(r'\b[a-zA-Z]+\b', message)
    result = message
    
    for word in words:
        word_lower = word.lower()
        if len(word) < 3:
            continue
        
        # Skip common non-domain words to avoid false matches
        common_words = {'work', 'gaming', 'school', 'creative', 'business', 'home', 'office',
                       'fiction', 'mystery', 'romance', 'fantasy', 'thriller', 'scifi', 'horror',
                       'under', 'over', 'around', 'about', 'show', 'find', 'looking', 'want',
                       'need', 'prefer', 'like', 'love', 'hate', 'good', 'best', 'cheap', 'expensive'}
        if word_lower in common_words:
            continue
        
        best_replacement = None
        best_distance = float('inf')
        best_similarity = 0.0
        
        for domain, keywords in DOMAIN_KEYWORDS.items():
            for keyword in keywords:
                distance = levenshtein_distance(word_lower, keyword)
                similarity = 1.0 - (distance / max(len(word_lower), len(keyword)))
                
                # Balanced matching that allows typos but prevents false matches:
                # - Very close matches (distance 1): require 60%+ similarity
                # - Medium matches (distance 2): require 70%+ similarity
                # This allows "lapto" → "laptop" (83% similar) but prevents "work" → "book" (50% similar)
                if distance == 1 and similarity >= 0.60:
                    if distance < best_distance or (distance == best_distance and similarity > best_similarity):
                        best_distance = distance
                        best_replacement = keyword
                        best_similarity = similarity
                elif distance == 2 and similarity >= 0.70:
                    if distance < best_distance or (distance == best_distance and similarity > best_similarity):
                        best_distance = distance
                        best_replacement = keyword
                        best_similarity = similarity
        
        if best_replacement:
            # Replace the word with the normalized version
            result = re.sub(rf'\b{re.escape(word)}\b', best_replacement, result, flags=re.IGNORECASE)
    
    return result


def should_reject_input(message: str, active_domain: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Determine if input should be rejected with an error message.
    
    Returns:
        (should_reject, error_message)
        - (True, "error") if should reject
        - (False, None) if input is OK
    """
    # Check basic validity
    is_valid, error = is_valid_input(message)
    if not is_valid:
        return True, error
    
    # If user has an active domain and gives short input, it might be a filter response
    if active_domain:
        # Short responses like "$1000", "Gaming", "Dell" are OK in context
        if len(message.strip()) >= 2:
            return False, None
    
    # Check if it's a greeting without domain context
    normalized = message.lower().strip()
    if normalized in GREETINGS:
        # Greetings are OK - they'll trigger domain selection
        return False, None
    
    # If message is very short and not domain-related, might be gibberish
    if len(normalized) < 3 and normalized not in VALID_SHORT_INTENTS:
        # But check if it's in an active conversation
        if not active_domain:
            return True, "Please tell me what you're looking for (vehicles, laptops, books, jewelry, or accessories)."
    
    return False, None
