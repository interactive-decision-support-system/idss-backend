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

logger = StructuredLogger("conversation_controller")

# --- Domain enum ---
class Domain(str, Enum):
    NONE = "none"
    VEHICLES = "vehicles"
    LAPTOPS = "laptops"
    BOOKS = "books"


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
    "laptop", "laptops", "computer", "computers", "macbook", "notebook",
    "notebooks", "chromebook", "thinkpad", "xps", "pc", "pcs"
]
BOOK_KEYWORDS = [
    "book", "books", "novel", "novels", "textbook", "textbooks", "reading",
    "genre", "author", "fiction", "mystery", "romance", "looking for books",
    "show me books", "find books"
]

# Short domain intents: treat as mode switch → start interview Q1
DOMAIN_INTENT_PATTERNS = {
    Domain.BOOKS: re.compile(
        r"^(books?|novels?|reading|looking for books?|show me books?|find books?|bookss?)$",
        re.IGNORECASE
    ),
    Domain.LAPTOPS: re.compile(
        r"^(laptops?|computers?|show me laptops?|show me computers?|looking for (a )?laptop(s)?)$",
        re.IGNORECASE
    ),
    Domain.VEHICLES: re.compile(
        r"^(cars?|vehicles?|suvs?|trucks?|show me (cars?|vehicles?|suvs?)|looking for (a )?car)$",
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

    # 1) Explicit keywords — vehicle first, then desktop/PC (so "gaming PC" is not laptop)
    for kw in VEHICLE_KEYWORDS:
        if kw in msg:
            return Domain.VEHICLES, "keyword_vehicle"

    for phrase in DESKTOP_PC_PHRASES:
        if phrase in msg:
            return Domain.LAPTOPS, "keyword_desktop"

    for kw in LAPTOP_KEYWORDS:
        if kw in msg:
            return Domain.LAPTOPS, "keyword_laptop"

    for kw in BOOK_KEYWORDS:
        if kw in msg:
            return Domain.BOOKS, "keyword_book"

    # 2) Short domain intents (exact or near-exact)
    for domain, pat in DOMAIN_INTENT_PATTERNS.items():
        if pat.match(msg):
            return domain, "domain_intent"

    # 3) Filters category
    if filters_category:
        cat = filters_category.lower()
        if "book" in cat:
            return Domain.BOOKS, "filter_category"
        if "electronic" in cat:
            return Domain.LAPTOPS, "filter_category"

    # 4) Active session continuation (quick replies like "School", "$500-$1000")
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
