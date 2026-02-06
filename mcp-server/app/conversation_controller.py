"""
Conversation controller: single source of truth for domain routing and session state.

Implements deterministic router first, embeddings second (per bigerrorjan29.txt):
1. Explicit keywords (vehicle/laptop/book)
2. Active session domain continuation
3. Embeddings only if ambiguous and confidence > threshold

Domain switch resets interview state. Session stores active_domain, stage, question_index.
"""

import re
from typing import Optional, Tuple, Any, Dict
from enum import Enum

from app.structured_logger import StructuredLogger
from app.input_validator import fuzzy_match_domain, normalize_domain_keywords

logger = StructuredLogger("conversation_controller")

# --- Domain enum ---
class Domain(str, Enum):
    NONE = "none"
    VEHICLES = "vehicles"
    LAPTOPS = "laptops"
    BOOKS = "books"
    JEWELRY = "jewelry"
    ACCESSORIES = "accessories"
    CLOTHING = "clothing"
    BEAUTY = "beauty"


# --- Hard domain keywords (deterministic, checked first) ---
# Order: vehicle > desktop/PC > laptop > book (so "gaming PC" is not routed as laptop)
VEHICLE_KEYWORDS = [
    "car", "cars", "vehicle", "vehicles", "auto", "automobile", "automobiles",
    "suv", "suvs", "truck", "trucks", "sedan", "sedans", "van", "vans",
    "coupe", "hatchback", "wagon", "fuel efficient", "family suv",
    "honda", "toyota", "ford", "bmw", "tesla", "vin"  # brand/vin hint
]
# Desktop/PC first — check before laptop so "gaming PC" / "desktop" don't match "pc" -> laptop
DESKTOP_PC_PHRASES = [
    "gaming pc", "gaming computer", "desktop pc", "desktop computer",
    "desktop", "desktops", "tower", "workstation"
]
LAPTOP_KEYWORDS = [
    "laptop", "laptops", "lapto", "lpatop",
    "computer", "computers", "computr",
    "notebook", "notebooks", "notbook", "notbooks",
    "macbook", "chromebook", "thinkpad", "xps", 
    "pc", "pcs"
]
BOOK_KEYWORDS = [
    "book", "books", "novel", "novels", "textbook", "textbooks", "reading",
    "genre", "author", "fiction", "mystery", "romance", "looking for books",
    "show me books", "find books"
]
JEWELRY_KEYWORDS = [
    "jewelry", "jewellery", "necklace", "necklaces", "earrings", "bracelet",
    "bracelets", "ring", "rings", "pendant", "chain", "brooch"
]
ACCESSORIES_KEYWORDS = [
    "accessories", "accessory", "scarf", "scarves", "hat", "hats", "belt",
    "belts", "bag", "bags", "watch", "watches", "sunglasses"
]
CLOTHING_KEYWORDS = [
    "clothing", "clothes", "apparel", "dress", "dresses", "shirt", "shirts",
    "pants", "jeans", "blouse", "blouses", "t-shirt", "tshirt", "hoodie",
    "jacket", "jackets", "shorts", "skirt", "tops", "fashion"
]
BEAUTY_KEYWORDS = [
    "beauty", "cosmetics", "makeup", "lipstick", "lipstick", "eyeshadow",
    "mascara", "foundation", "blush", "skincare", "moisturizer", "serum"
]

# Short domain intents: treat as mode switch → start interview Q1
# Include common misspellings
DOMAIN_INTENT_PATTERNS = {
    Domain.BOOKS: re.compile(
        r"^(books?s*|novels?|reading|looking for books?|show me books?|find books?|bookss+|boks?|buk)$",
        re.IGNORECASE
    ),
    Domain.LAPTOPS: re.compile(
        r"^(laptops?s*|computers?|computr?|show me laptops?|show me computers?|looking for (a )?laptop(s)?|lapto|lpatop)$",
        re.IGNORECASE
    ),
    Domain.VEHICLES: re.compile(
        r"^(cars?s*|vehicles?|vehicl|suvs?|trucks?|show me (cars?|vehicles?|suvs?)|looking for (a )?car)$",
        re.IGNORECASE
    ),
    Domain.JEWELRY: re.compile(
        r"^(jewelry|jewellery|jewelries|show me jewelry|looking for jewelry)$",
        re.IGNORECASE
    ),
    Domain.ACCESSORIES: re.compile(
        r"^(accessories|accessory|show me accessories|looking for accessories)$",
        re.IGNORECASE
    ),
    Domain.CLOTHING: re.compile(
        r"^(clothing|clothes|apparel|show me clothing|looking for (clothing|clothes|apparel))$",
        re.IGNORECASE
    ),
    Domain.BEAUTY: re.compile(
        r"^(beauty|cosmetics|makeup|show me beauty|looking for (beauty|cosmetics|makeup))$",
        re.IGNORECASE
    ),
}


def _normalize(s: str) -> str:
    return (s or "").lower().strip()


def detect_domain(
    message: str,
    active_domain: Optional[str] = None,
    filters_category: Optional[str] = None,
) -> Tuple[Domain, str]:
    """
    Deterministic domain detection. Priority:
    1. Explicit keywords in message (vehicle > laptop > book for conflict)
    2. Category from filters (Books → books, Electronics → laptops)
    3. Active session domain continuation
    4. Return NONE if ambiguous (caller can ask "What category?")

    Returns:
        (detected_domain, route_reason)
    """
    msg = _normalize(message)
    if not msg:
        if active_domain:
            return Domain(active_domain), "session_continuation"
        if filters_category:
            cat = filters_category.lower()
            if "book" in cat:
                return Domain.BOOKS, "filter_category"
            if "electronic" in cat:
                return Domain.LAPTOPS, "filter_category"
        return Domain.NONE, "empty"

    # 1) Short domain intents (exact match first - highest confidence)
    for domain, pat in DOMAIN_INTENT_PATTERNS.items():
        if pat.match(msg):
            return domain, "domain_intent"
    
    # 2) Fuzzy matching for misspellings (booksss → books, computr → laptop, notbook → laptop)
    # Do this BEFORE keyword matching to avoid false matches like "book" in "notbook"
    fuzzy_domain = fuzzy_match_domain(message)
    if fuzzy_domain:
        logger.info("fuzzy_domain_match", f"Fuzzy matched '{message}' to {fuzzy_domain}", {
            "original": message,
            "matched_domain": fuzzy_domain
        })
        return Domain(fuzzy_domain), "fuzzy_match"

    # 3) Explicit keywords — vehicle first, then desktop/PC (so "gaming PC" is not laptop)
    # Use word boundaries to avoid false matches (e.g., "book" in "notebook")
    for kw in VEHICLE_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', msg):
            return Domain.VEHICLES, "keyword_vehicle"

    for phrase in DESKTOP_PC_PHRASES:
        if phrase in msg:
            return Domain.LAPTOPS, "keyword_desktop"

    for kw in LAPTOP_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', msg):
            return Domain.LAPTOPS, "keyword_laptop"

    for kw in BOOK_KEYWORDS:
        # Use word boundaries to prevent "book" matching in "notebook"
        if re.search(rf'\b{re.escape(kw)}\b', msg):
            return Domain.BOOKS, "keyword_book"

    for kw in JEWELRY_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', msg):
            return Domain.JEWELRY, "keyword_jewelry"

    for kw in ACCESSORIES_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', msg):
            return Domain.ACCESSORIES, "keyword_accessories"

    for kw in CLOTHING_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', msg):
            return Domain.CLOTHING, "keyword_clothing"

    for kw in BEAUTY_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', msg):
            return Domain.BEAUTY, "keyword_beauty"

    # 4) Filters category
    if filters_category:
        cat = filters_category.lower()
        if "book" in cat:
            return Domain.BOOKS, "filter_category"
        if "electronic" in cat:
            return Domain.LAPTOPS, "filter_category"
        if "jewelry" in cat or "jewellery" in cat:
            return Domain.JEWELRY, "filter_category"
        if "accessor" in cat:
            return Domain.ACCESSORIES, "filter_category"
        if "cloth" in cat or "apparel" in cat:
            return Domain.CLOTHING, "filter_category"
        if "beauty" in cat or "cosmetic" in cat:
            return Domain.BEAUTY, "filter_category"
    
    # 5) Active session continuation (quick replies like "School", "$500-$1000")
    if active_domain:
        return Domain(active_domain), "session_continuation"

    return Domain.NONE, "ambiguous"


def is_domain_switch(active_domain: Optional[str], detected: Domain) -> bool:
    """True if user switched to a different domain (should reset session)."""
    if not active_domain or detected == Domain.NONE:
        return False
    return active_domain != detected.value


def is_short_domain_intent(message: str) -> bool:
    """True if message is just a domain selection (books, laptop, show me books, etc.)."""
    msg = _normalize(message)
    for _, pat in DOMAIN_INTENT_PATTERNS.items():
        if pat.match(msg):
            return True
    return False


def is_greeting_or_ambiguous(message: str) -> bool:
    """True if we should ask 'What category?' (hi, hello, short non-domain)."""
    msg = _normalize(message)
    if len(msg) <= 2:
        return True
    greetings = ("hi", "hello", "hey", "hi there", "hello there")
    if msg in greetings or msg.split()[0] in greetings:
        return True
    # Not a domain keyword
    detected, _ = detect_domain(message, None, None)
    return detected == Domain.NONE and len(msg) < 10
