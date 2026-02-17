"""
Lightweight query specificity helpers.

These defaults treat most queries as specific enough to search directly,
which keeps tests deterministic without requiring interview logic.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple, Optional

from agent import get_domain_schema


BRAND_MAP = {
    "apple": "Apple",
    "mac": "Apple",
    "macbook": "Apple",
    "dell": "Dell",
    "hp": "HP",
    "lenovo": "Lenovo",
    "asus": "ASUS",
    "acer": "Acer",
    "msi": "MSI",
    "razer": "Razer",
    "samsung": "Samsung",
    "microsoft": "Microsoft",
}

GPU_VENDOR_MAP = {
    "nvidia": "NVIDIA",
    "geforce": "NVIDIA",
    "rtx": "NVIDIA",
    "gtx": "NVIDIA",
    "amd": "AMD",
    "radeon": "AMD",
    "intel": "Intel",
}

CPU_VENDOR_MAP = {
    "intel": "Intel",
    "amd": "AMD",
    "apple": "Apple",
    "m1": "Apple",
    "m2": "Apple",
    "m3": "Apple",
}

USE_CASE_KEYWORDS = {
    "gaming": "gaming",
    "work": "work",
    "school": "school",
    "student": "school",
    "creative": "creative",
    "design": "creative",
    "video editing": "creative",
    "programming": "work",
    "business": "work",
    "education": "education",
}

SOFT_PREFERENCE_KEYWORDS = {
    "luxury": "luxury",
    "premium": "luxury",
    "high-end": "luxury",
    "family safe": "family_safe",
    "family": "family_safe",
    "kid": "family_safe",
    "safe": "family_safe",
    "durable": "durable",
    "rugged": "durable",
    "portable": "portable",
    "lightweight": "portable",
}

COLOR_KEYWORDS = {
    "black": "black",
    "white": "white",
    "silver": "silver",
    "gray": "gray",
    "grey": "gray",
    "blue": "blue",
    "red": "red",
    "gold": "gold",
    "pink": "pink",
}


def _normalize(text: str) -> str:
    return (text or "").lower().strip()


def _extract_price_range(query: str) -> Optional[Dict[str, int]]:
    text = _normalize(query)
    range_match = re.search(r"\$?(\d{2,5})\s*[-–]\s*\$?(\d{2,5})", text)
    if range_match:
        return {"min": int(range_match.group(1)), "max": int(range_match.group(2))}

    under_match = re.search(r"(under|below|<=)\s*\$?(\d{2,5})", text)
    if under_match:
        return {"max": int(under_match.group(2))}

    over_match = re.search(r"(over|above|>=)\s*\$?(\d{2,5})", text)
    if over_match:
        return {"min": int(over_match.group(2))}

    return None


def _detect_domain(query: str, filters: Dict[str, object]) -> Optional[str]:
    text = _normalize(query)
    category = str(filters.get("category", "")).lower()
    
    # Use word boundaries to avoid false matches (e.g., "book" in "notebook")
    if "book" in category or re.search(r'\bbook\b', text) or re.search(r'\bnovel\b', text):
        return "books"
    if "electronic" in category or re.search(r'\blaptop\b', text) or re.search(r'\bcomputer\b', text) or re.search(r'\bpc\b', text) or re.search(r'\bnotebook\b', text):
        return "laptops"
    if "jewelry" in category or "jewellery" in category or re.search(r'\bjewelry\b', text) or re.search(r'\bnecklace\b', text) or re.search(r'\bearrings?\b', text) or re.search(r'\bbracelet\b', text) or re.search(r'\brings?\b', text) or re.search(r'\bpendant\b', text):
        return "jewelry"
    if "accessor" in category or re.search(r'\baccessories?\b', text) or re.search(r'\bscarf\b', text) or re.search(r'\bhats?\b', text) or re.search(r'\bbelts?\b', text) or re.search(r'\bwatches?\b', text) or re.search(r'\bsunglasses\b', text):
        return "accessories"
    if "cloth" in category or re.search(r'\bclothing\b', text) or re.search(r'\bclothes\b', text) or re.search(r'\bapparel\b', text) or re.search(r'\bdress(es)?\b', text) or re.search(r'\bshirt(s)?\b', text) or re.search(r'\bpants?\b', text):
        return "clothing"
    if "beauty" in category or re.search(r'\bbeauty\b', text) or re.search(r'\bcosmetics?\b', text) or re.search(r'\bmakeup\b', text) or re.search(r'\blipstick\b', text) or re.search(r'\beyeshadow\b', text) or re.search(r'\bskincare\b', text):
        return "beauty"
    return None


def _collect_attributes(text: str) -> List[str]:
    attributes: List[str] = []
    for key, attr in USE_CASE_KEYWORDS.items():
        if key in text and attr not in attributes:
            attributes.append(attr)
    return attributes


def _collect_soft_preferences(text: str) -> Dict[str, List[str]]:
    liked_features: List[str] = []
    for key, pref in SOFT_PREFERENCE_KEYWORDS.items():
        if key in text and pref not in liked_features:
            liked_features.append(pref)
    return {"liked_features": liked_features} if liked_features else {}


def is_specific_query(query: str, filters: Dict[str, object]) -> Tuple[bool, Dict[str, object]]:
    """Return whether query is specific and extracted info for constraints."""
    text = _normalize(query)
    extracted_info: Dict[str, object] = {}

    # Check if query contains a specific book title (proper nouns, multiple capitalized words)
    # Examples: "Dune", "The Hobbit", "Project Hail Mary", "Gone Girl"
    domain = _detect_domain(query, filters)
    if domain == "books":
        # Look for capitalized words that indicate a specific book title
        original_query = query.strip()
        words = original_query.split()
        # If query has 1-5 words and starts with capital letter, likely a book title
        # Filter out common noise words
        noise_words = {"i", "want", "need", "show", "me", "find", "get", "the", "a", "an", "book", "books", "novel"}
        meaningful_words = [w for w in words if w.lower() not in noise_words]
        # If we have 1-4 meaningful words and at least one starts with capital, it's likely a specific title
        has_capital_word = any(w[0].isupper() for w in meaningful_words if w)
        if 1 <= len(meaningful_words) <= 5 and has_capital_word:
            # This looks like a specific book title search - skip interview
            extracted_info["specific_title_search"] = True
            extracted_info["title_query"] = " ".join(meaningful_words)
        
        # Extract genre for books (handles single-word genre responses like "Sci-Fi", "Mystery")
        # Order matters! Check longer phrases first to avoid false matches
        genre_keywords = [
            ("science fiction", "Science Fiction"),
            ("non-fiction", "Non-Fiction"),
            ("nonfiction", "Non-Fiction"),
            ("young adult", "Young Adult"),
            ("self-help", "Self-Help"),
            ("selfhelp", "Self-Help"),
            ("sci-fi", "Sci-Fi"),
            ("scifi", "Sci-Fi"),
            ("fiction", "Fiction"),  # Check after "science fiction" and "non-fiction"
            ("mystery", "Mystery"),
            ("thriller", "Thriller"),
            ("fantasy", "Fantasy"),
            ("romance", "Romance"),
            ("horror", "Horror"),
            ("biography", "Biography"),
            ("memoir", "Memoir"),
            ("business", "Business"),
            ("history", "History"),
            ("travel", "Travel"),
            ("cooking", "Cooking"),
            ("poetry", "Poetry"),
            ("children", "Children's"),
            ("ya", "Young Adult"),
        ]
        
        for keyword, genre_name in genre_keywords:
            if keyword in text:
                extracted_info["genre"] = genre_name
                break
        
        # Extract format for books (hardcover, paperback, ebook, audiobook)
        format_keywords = {
            "hardcover": "Hardcover",
            "hard cover": "Hardcover",
            "hardback": "Hardcover",
            "paperback": "Paperback",
            "paper back": "Paperback",
            "softcover": "Paperback",
            "soft cover": "Paperback",
            "ebook": "E-book",
            "e-book": "E-book",
            "digital": "E-book",
            "kindle": "E-book",
            "audiobook": "Audiobook",
            "audio book": "Audiobook",
            "audible": "Audiobook",
        }
        
        for keyword, format_name in format_keywords.items():
            if keyword in text:
                extracted_info["format"] = format_name
                break

    # Product type - use word boundaries to avoid false matches
    if "desktop" in text or "tower" in text or "gaming pc" in text:
        extracted_info["product_type"] = "desktop"
    elif re.search(r'\blaptop\b', text) or re.search(r'\bnotebook\b', text) or re.search(r'\bcomputer\b', text):
        extracted_info["product_type"] = "laptop"
    elif re.search(r'\bbook\b', text) or re.search(r'\bnovel\b', text):
        extracted_info["product_type"] = "book"

    # Brand detection
    for key, brand in BRAND_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", text):
            extracted_info["brand"] = brand
            break

    # Jewelry brand detection (when category is jewelry/accessories)
    jewelry_brands = {
        "pandora": "Pandora", "tiffany": "Tiffany & Co", "tiffany and co": "Tiffany & Co", "tiffany & co": "Tiffany & Co",
        "swarovski": "Swarovski", "kay jewelers": "Kay Jewelers", "kay": "Kay Jewelers",
        "zales": "Zales", "jared": "Jared"
    }
    for key, brand in jewelry_brands.items():
        if re.search(rf"\b{re.escape(key)}\b", text):
            extracted_info["brand"] = brand
            break

    # "No preference" / "Specific brand" for brand question - satisfies brand slot without filtering
    if re.search(r'\bno\s+preference\b', text) or re.search(r'\bspecific\s+brand\b', text):
        extracted_info["brand"] = "No preference"

    # "Any price" for budget question - satisfies budget slot without price filter
    if re.search(r'\bany\s+price\b', text):
        extracted_info["price_range"] = {}  # Empty = no limit, satisfies slot

    # Jewelry/Accessories item type (subcategory)
    jewelry_types = [
        ("necklace", "Necklace"), ("necklaces", "Necklace"), ("earrings", "Earrings"),
        ("earring", "Earrings"), ("bracelet", "Bracelet"), ("bracelets", "Bracelet"),
        ("ring", "Ring"), ("rings", "Ring"), ("pendant", "Pendant"), ("pendants", "Pendant"),
        ("brooch", "Brooch"), ("anklet", "Anklet"), ("charm", "Charm")
    ]
    accessory_types = [
        ("scarf", "Scarf"), ("scarves", "Scarf"), ("hat", "Hat"), ("hats", "Hat"),
        ("belt", "Belt"), ("belts", "Belt"), ("bag", "Bag"), ("bags", "Bag"),
        ("watch", "Watch"), ("watches", "Watch"), ("sunglasses", "Sunglasses")
    ]
    clothing_types = [
        ("dress", "Dresses"), ("dresses", "Dresses"), ("shirt", "Shirts & Blouses"),
        ("shirts", "Shirts & Blouses"), ("blouse", "Shirts & Blouses"), ("blouses", "Shirts & Blouses"),
        ("pants", "Pants"), ("jeans", "Mens Pants"), ("graphic tee", "Graphic Tees"),
        ("t-shirt", "Graphic Tees"), ("tshirt", "Graphic Tees"), ("shorts", "Shorts"),
        ("jacket", "Jackets"), ("jackets", "Jackets"), ("top", "Womens LS Tops"),
        ("tops", "Womens LS Tops"), ("tank", "Womens Tank"), ("hoodie", "Graphic Tees")
    ]
    beauty_types = [
        ("lipstick", "Lipstick"), ("lip", "Lipstick"), ("eyeshadow", "Eyeshadow"),
        ("shadow", "Eyeshadow"), ("mascara", "Mascara"), ("foundation", "Foundation"),
        ("blush", "Blush"), ("skincare", "Skincare"), ("moisturizer", "Skincare"),
        ("serum", "Skincare"), ("palette", "Eyeshadow")
    ]
    for keyword, type_name in jewelry_types + accessory_types + clothing_types + beauty_types:
        if re.search(rf"\b{re.escape(keyword)}\b", text):
            extracted_info["subcategory"] = type_name
            extracted_info["item_type"] = type_name
            break

    # Clothing/Beauty brand detection (when in that domain)
    if domain == "clothing":
        clothing_brands = {"nike": "Nike", "patagonia": "Patagonia", "uniqlo": "Uniqlo"}
        for key, brand in clothing_brands.items():
            if re.search(rf"\b{re.escape(key)}\b", text):
                extracted_info["brand"] = brand
                break
    if domain == "beauty":
        beauty_brands = {"nars": "NARS", "colourpop": "ColourPop", "fenty": "Fenty Beauty", "nyx": "NYX"}
        for key, brand in beauty_brands.items():
            if re.search(rf"\b{re.escape(key)}\b", text):
                extracted_info["brand"] = brand
                break
        if re.search(r"\bmac\b", text) and "macbook" not in text:  # MAC cosmetics, not Apple
            extracted_info["brand"] = "MAC"

    # GPU/CPU vendors
    for key, vendor in GPU_VENDOR_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", text):
            extracted_info["gpu_vendor"] = vendor
            break

    for key, vendor in CPU_VENDOR_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", text):
            extracted_info["cpu_vendor"] = vendor
            break

    # Price
    price_range = _extract_price_range(text)
    if price_range:
        extracted_info["price_range"] = price_range

    # Attributes / use cases
    attributes = _collect_attributes(text)
    if attributes:
        extracted_info["attributes"] = attributes

    # Soft preferences for electronics
    soft_preferences = _collect_soft_preferences(text)
    if soft_preferences:
        extracted_info["soft_preferences"] = soft_preferences

    # Color
    for key, color in COLOR_KEYWORDS.items():
        if re.search(rf"\b{re.escape(key)}\b", text):
            extracted_info["color"] = color
            break

    # If this is a specific title search for books, skip the interview
    if extracted_info.get("specific_title_search"):
        return True, extracted_info
    
    missing_info = _missing_slots(query, filters, extracted_info)
    return len(missing_info) == 0, extracted_info


def _missing_slots(query: str, filters: Dict[str, object], extracted_info: Dict[str, object], product_type: Optional[str] = None) -> List[str]:
    """
    Determine which required slots are missing for the current domain.
    
    CRITICAL: product_type parameter MUST be passed from the active domain to prevent
    cross-domain contamination (e.g., asking book questions when user selected laptops).
    Only auto-detect domain when product_type is None (initial query parsing).
    """
    # Use explicit product_type if provided (from active domain), otherwise detect
    if product_type:
        if product_type == "book":
            domain = "books"
        elif product_type == "jewelry":
            domain = "jewelry"
        elif product_type == "accessory":
            domain = "accessories"
        elif product_type == "clothing":
            domain = "clothing"
        elif product_type == "beauty":
            domain = "beauty"
        else:
            domain = "laptops"
    else:
        domain = _detect_domain(query, filters)
    
    if not domain:
        return []

    # Required slots by domain
    if domain == "books":
        required = ["genre", "format", "budget"]
    elif domain in ("jewelry", "accessories", "clothing", "beauty"):
        required = ["item_type", "budget", "brand"]
    else:
        required = ["use_case", "brand", "budget"]

    missing: List[str] = []
    for slot in required:
        if slot == "use_case":
            has_use_case = bool(filters.get("use_case") or filters.get("subcategory") or extracted_info.get("attributes"))
            if not has_use_case:
                missing.append("use_case")
        elif slot == "brand":
            if not (filters.get("brand") or extracted_info.get("brand")):
                missing.append("brand")
        elif slot == "budget":
            if not (filters.get("price_min_cents") or filters.get("price_max_cents") or extracted_info.get("price_range")):
                missing.append("budget")
        elif slot == "genre":
            if not (filters.get("genre") or filters.get("subcategory") or extracted_info.get("genre")):
                missing.append("genre")
        elif slot == "format":
            if not (filters.get("format") or extracted_info.get("format")):
                missing.append("format")
        elif slot == "item_type":
            if not (filters.get("subcategory") or filters.get("item_type") or extracted_info.get("subcategory") or extracted_info.get("item_type")):
                missing.append("item_type")

    return missing


def should_ask_followup(query: str, filters: Dict[str, object], product_type: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Determine if a follow-up question is needed."""
    _, extracted_info = is_specific_query(query, filters)
    missing = _missing_slots(query, filters, extracted_info, product_type)
    return (len(missing) > 0), missing


def generate_followup_question(product_type: str, missing_info: List[str], filters: Dict[str, object]) -> Tuple[str, List[str]]:
    """Return a follow-up question using domain registry slot definitions."""
    if product_type == "book":
        domain = "books"
    elif product_type == "jewelry":
        domain = "jewelry"
    elif product_type == "accessory":
        domain = "accessories"
    elif product_type == "clothing":
        domain = "clothing"
    elif product_type == "beauty":
        domain = "beauty"
    else:
        domain = "laptops"
    schema = get_domain_schema(domain)
    fallback_replies = ["Vehicles", "Laptops", "Books", "Jewelry", "Accessories", "Clothing", "Beauty"]
    if not schema or not missing_info:
        return "Could you clarify what you are looking for?", fallback_replies

    slot_name = missing_info[0]
    slot = next((s for s in schema.slots if s.name == slot_name), None)
    if slot:
        return slot.example_question, slot.example_replies

    return "Could you clarify what you are looking for?", fallback_replies
