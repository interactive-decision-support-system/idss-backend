"""
Query Specificity Detection

Determines if a query is specific enough to return results directly,
or if it needs follow-up questions.

Rules:
- Specific query (e.g., "mac pink laptop") → Return results directly
- Generic query (e.g., "laptop") → Ask follow-up questions
"""

from typing import Dict, Any, List, Tuple, Optional
import re


# Component vendors (GPU/CPU) — do NOT treat as Product.brand; use gpu_vendor/cpu_vendor
COMPONENT_BRANDS = {"nvidia", "amd", "intel", "geforce", "rtx", "gtx", "radeon", "ryzen"}

# Brand keywords (common brands for laptops/electronics) — only device/OEM brands
BRAND_KEYWORDS = {
    "apple": ["mac", "macbook", "apple", "imac"],
    "dell": ["dell", "xps", "alienware"],
    "hp": ["hp", "hewlett packard", "pavilion", "spectre"],
    "lenovo": ["lenovo", "thinkpad", "yoga"],
    "asus": ["asus", "rog", "zenbook"],
    "microsoft": ["microsoft", "surface"],
    "samsung": ["samsung", "galaxy"],
    "acer": ["acer", "predator"],
    # NVIDIA/AMD/Intel are in BRAND_KEYWORDS for extraction but moved to gpu_vendor/cpu_vendor in pipeline
    "nvidia": ["nvidia", "geforce", "rtx", "gtx", "nvidia type", "nvidia gpu"],
    "amd": ["amd", "radeon", "ryzen"],
    "intel": ["intel", "core"],
}

# Color keywords
COLOR_KEYWORDS = [
    "pink", "rose", "rose gold", "gold", "silver", "space gray", "space grey",
    "black", "white", "blue", "red", "green", "yellow", "purple", "orange",
    "gray", "grey", "beige", "brown", "navy", "maroon", "teal", "cyan"
]

# Product type keywords
# Order matters: more specific patterns first (e.g., "gaming pc" before "pc")
PRODUCT_TYPE_KEYWORDS = {
    "desktop": ["gaming pc", "gaming computer", "desktop pc", "desktop computer", "desktop", "pc", "workstation", "tower"],
    "laptop": ["laptop", "notebook", "macbook", "chromebook"],
    "book": ["book", "novel", "textbook", "ebook"],
    "headphone": ["headphone", "earphone", "earbud", "headset"],
    "gpu": ["gpu", "graphics card", "video card"],
    "cpu": ["cpu", "processor", "chip"],
}

# Attribute keywords (specific features)
ATTRIBUTE_KEYWORDS = {
    "gaming": ["gaming", "game", "gamer", "esports"],
    "school": ["school", "student", "education", "academic"],
    "work": ["work", "business", "office", "professional"],
    "creative": ["creative", "video editing", "photo editing", "design"],
    "budget": ["budget", "cheap", "affordable", "inexpensive"],
    "premium": ["premium", "luxury", "high-end", "expensive"],
}


def extract_brand(query: str) -> Optional[str]:
    """Extract brand from query."""
    query_lower = query.lower()
    
    for brand, keywords in BRAND_KEYWORDS.items():
        for keyword in keywords:
            if keyword in query_lower:
                return brand
    
    return None


def extract_color(query: str) -> Optional[str]:
    """Extract color from query. Uses word boundaries so 'mac' / 'macc' never match 'gray'."""
    query_lower = query.lower()
    # Match longer phrases first (e.g. "space gray" before "gray")
    for color in sorted(COLOR_KEYWORDS, key=len, reverse=True):
        if " " in color:
            if color in query_lower:
                return color
        else:
            if re.search(r"\b" + re.escape(color) + r"\b", query_lower):
                return color
    return None


def extract_product_type(query: str) -> Optional[str]:
    """
    Extract product type from query.
    
    Checks longer/more specific patterns first (e.g., "gaming pc" before "pc").
    """
    query_lower = query.lower()
    
    # Sort keywords by length (longest first) to match "gaming pc" before "pc"
    for product_type, keywords in PRODUCT_TYPE_KEYWORDS.items():
        sorted_keywords = sorted(keywords, key=len, reverse=True)
        for keyword in sorted_keywords:
            # For multi-word patterns like "gaming pc", use direct substring match
            if " " in keyword:
                if keyword in query_lower:
                    return product_type
            else:
                # For single words, use substring match (simpler than word boundaries here)
                if keyword in query_lower:
                    return product_type
    
    return None


def extract_attributes(query: str) -> List[str]:
    """Extract attributes from query."""
    query_lower = query.lower()
    attributes = []
    
    for attr, keywords in ATTRIBUTE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in query_lower:
                attributes.append(attr)
                break
    
    return attributes


def extract_price_range(query: str) -> Optional[Dict[str, int]]:
    """Extract price range from query. Supports: under/less than/below $X (max), over/above $X (min), $X-$Y (range)."""
    query_lower = query.lower()
    
    # Max price: "under $1000", "less than $1000", "below $1000", "under 1000"
    under_match = re.search(r'(?:under|less\s+than|below)\s+\$?(\d+)', query_lower)
    # Min price: "over $500", "above $500"
    over_match = re.search(r'(?:over|above)\s+\$?(\d+)', query_lower)
    # Range: "$500-$1000"
    range_match = re.search(r'\$?(\d+)\s*-\s*\$?(\d+)', query_lower)
    
    price_range = {}
    
    if under_match:
        price_range["max"] = int(under_match.group(1))
    
    if over_match:
        price_range["min"] = int(over_match.group(1))
    
    if range_match:
        price_range["min"] = int(range_match.group(1))
        price_range["max"] = int(range_match.group(2))
    
    return price_range if price_range else None


def is_specific_query(query: str, filters: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
    """
    Determine if a query is specific enough to return results directly.
    
    A query is considered specific if it contains:
    - Brand + Product Type (e.g., "mac laptop")
    - Brand + Color + Product Type (e.g., "mac pink laptop")
    - Product Type + Price Range (e.g., "laptop under $1000")
    - Product Type + Attribute (e.g., "gaming laptop")
    - Multiple attributes (e.g., "gaming laptop under $1000")
    
    Args:
        query: User query string
        filters: Existing filters (optional)
    
    Returns:
        Tuple of (is_specific, extracted_info)
        - is_specific: True if query is specific enough
        - extracted_info: Dict with extracted brand, color, product_type, attributes, price_range
    """
    if not query or len(query.strip()) < 3:
        return False, {}
    
    # Extract information from query
    brand = extract_brand(query)
    color = extract_color(query)
    product_type = extract_product_type(query)
    attributes = extract_attributes(query)
    price_range = extract_price_range(query)
    
    # Also check filters
    if filters:
        if not brand and filters.get("brand"):
            brand = filters["brand"].lower()
        if not product_type and filters.get("category"):
            # Map category to product type
            category = filters["category"].lower()
            if "electronics" in category:
                product_type = "laptop"  # Default for electronics
            elif "books" in category:
                product_type = "book"
        # Also check for use_case in filters (from previous answers)
        if filters.get("use_case") or filters.get("subcategory"):
            if not attributes:
                use_case = filters.get("use_case") or filters.get("subcategory")
                if use_case:
                    # Map use_case to attributes
                    use_case_lower = str(use_case).lower()
                    if use_case_lower in ["gaming", "work", "school", "creative", "entertainment", "education"]:
                        attributes.append(use_case_lower)
    
    # Separate component vendors (GPU/CPU) from device brand — don't use as Product.brand
    gpu_vendor = None
    cpu_vendor = None
    device_brand = brand
    if brand:
        brand_lower = brand.lower()
        if brand_lower in ("nvidia", "geforce", "rtx", "gtx") or "nvidia" in brand_lower or "geforce" in brand_lower:
            gpu_vendor = "NVIDIA"
            device_brand = None
        elif brand_lower in ("amd", "radeon", "ryzen"):
            gpu_vendor = "AMD"  # AMD does GPU and CPU; treat as GPU for filtering
            device_brand = None
        elif brand_lower in ("intel", "core"):
            cpu_vendor = "Intel"
            device_brand = None

    extracted_info = {
        "brand": device_brand,
        "gpu_vendor": gpu_vendor,
        "cpu_vendor": cpu_vendor,
        "color": color,
        "product_type": product_type,
        "attributes": attributes,
        "price_range": price_range,
    }
    
    # Component vendor (gpu/cpu) counts as "brand-like" for specificity
    has_component = bool(gpu_vendor or cpu_vendor)
    brand_like = brand or has_component

    # Determine specificity score
    specificity_score = 0

    # Brand + Product Type = specific (e.g., "mac laptop")
    if brand_like and product_type:
        specificity_score += 2

    # Brand + Color + Product Type = very specific (e.g., "mac pink laptop")
    if brand_like and color and product_type:
        specificity_score += 3

    # Brand + Color + Price = very specific (e.g., "pink mac under 2000")
    if brand_like and color and price_range:
        if product_type or (filters and filters.get("category")):
            specificity_score += 2.5  # Very specific

    # Brand + Price = specific enough (e.g., "mac under 2000")
    if brand_like and price_range:
        if product_type or (filters and filters.get("category")):
            specificity_score += 2

    # Color + Product Type = specific enough (e.g., "pink laptop")
    if color and product_type and not brand_like:
        specificity_score += 1.5

    # Product Type + Price Range = specific (e.g., "laptop under $1000")
    if product_type and price_range:
        specificity_score += 2

    # Product Type + Attribute = specific (e.g., "gaming laptop")
    if product_type and attributes:
        specificity_score += 1.5

    # Brand + Attribute = specific (e.g., "mac for gaming", "nvidia gaming")
    if brand_like and attributes:
        if product_type or (filters and filters.get("category")):
            specificity_score += 2

    # Brand + Attribute + Price = very specific (e.g., "gaming PC with NVIDIA GPU under $2000")
    if brand_like and attributes and price_range:
        if product_type or (filters and filters.get("category")):
            specificity_score += 3  # Very specific - should return results immediately

    # Product Type + Brand + Attribute = very specific (e.g., "gaming PC with NVIDIA")
    if product_type and brand_like and attributes:
        specificity_score += 2.5  # Very specific - should return results immediately

    # Product Type + Brand + Attribute + Price = extremely specific (e.g., "gaming PC with NVIDIA GPU under $2000")
    if product_type and brand_like and attributes and price_range:
        specificity_score += 4  # Extremely specific - definitely return results immediately

    # Multiple attributes = specific (e.g., "gaming laptop under $1000")
    if len(attributes) >= 2:
        specificity_score += 1

    # Category filter helps narrow results
    if filters and filters.get("category"):
        if brand_like or price_range or attributes:
            specificity_score += 1
    
    # Threshold: score >= 2.0 means specific enough to return results
    # Stricter threshold ensures generic queries like "laptop" trigger follow-up questions
    # Examples:
    #   - "laptop" (product_type only) = 0 → NOT specific → ask questions
    #   - "mac laptop" (brand + product_type) = 2 → specific → return results
    #   - "laptop under $1000" (product_type + price) = 2 → specific → return results
    #   - "gaming laptop" (product_type + attribute) = 1.5 → NOT specific → ask questions
    #   - "mac gaming laptop" (brand + product_type + attribute) = 3.5 → specific → return results
    # 
    # CRITICAL: For laptops/electronics, we need use_case, brand, AND budget to be specific
    # Just having product_type is NOT enough - force interview for generic queries
    is_specific = specificity_score >= 2.0
    
    # Special case: If this is a laptop/electronics query with only product_type (no brand, price, or attributes),
    # it's NOT specific enough - we need to ask questions
    if product_type in ["laptop", "electronics"] and not brand_like and not price_range and not attributes:
        is_specific = False

    # Special case: Desktop/PC queries are more specific by nature
    # "gaming PC" with brand/gpu_vendor + price should be considered specific
    if product_type == "desktop" and (brand_like or price_range or attributes):
        # Desktop queries with any additional info are more specific than laptop queries
        if specificity_score < 2.0:
            specificity_score = 2.0  # Boost to make it specific
    
    return is_specific, extracted_info


def should_ask_followup(query: str, filters: Optional[Dict[str, Any]] = None) -> Tuple[bool, List[str]]:
    """
    Determine if we should ask follow-up questions and what to ask.
    
    Multi-turn interview flow:
    - LAPTOPS: use_case → brand → price
    - BOOKS: genre → format → price
    
    Returns:
        Tuple of (should_ask, questions_to_ask)
        - should_ask: True if we should ask follow-up questions
        - questions_to_ask: List of question topics in priority order (e.g., ["genre", "format", "price"] for books)
    """
    is_specific, extracted_info = is_specific_query(query, filters)
    
    if is_specific:
        return False, []
    
    # CRITICAL: Determine product_type from extracted_info OR filters (category)
    product_type = extracted_info.get("product_type")
    if not product_type and filters:
        category = filters.get("category", "").lower()
        if "books" in category:
            product_type = "book"
        elif "electronics" in category:
            product_type = "laptop"  # Default for electronics
    
    # Check filters for what's already been answered
    has_use_case = False
    has_brand = False
    has_price = False
    has_genre = False
    has_format = False
    
    if filters:
        # Check for use_case in filters (could be in metadata or as subcategory)
        if filters.get("use_case") or filters.get("subcategory") or (filters.get("_metadata") or {}).get("use_case"):
            has_use_case = True
        # Check for brand (or brand_answered marker - user said "Any" or "Popular brands")
        if filters.get("brand") or filters.get("brand_answered"):
            has_brand = True  # Brand question was answered (even if no specific brand)
        # Check for price range
        if filters.get("price_min") or filters.get("price_max") or filters.get("price_min_cents") or filters.get("price_max_cents"):
            has_price = True
        # Check for book-specific filters
        if filters.get("genre"):
            has_genre = True
        if filters.get("format") or filters.get("author"):
            has_format = True
    
    # Also check extracted_info from current query
    if extracted_info.get("attributes"):
        has_use_case = True
    if extracted_info.get("brand"):
        has_brand = True
    if extracted_info.get("price_range"):
        has_price = True
    
    # CRITICAL: Determine product_type from extracted_info OR filters (category)
    # This ensures books are detected even if query doesn't contain "book" explicitly
    if not product_type and filters:
        category = filters.get("category", "").lower()
        if "books" in category:
            product_type = "book"
        elif "electronics" in category:
            product_type = "laptop"  # Default for electronics
    
    # Determine what information is missing (in priority order)
    # BOOKS: genre → format/author → price
    # LAPTOP/ELECTRONICS: use_case → price → brand (price question right after "What will you use it for?")
    questions_to_ask = []
    
    if product_type == "book":
        # Books: genre, then format (or author), then price
        has_genre = bool(filters and (filters.get("genre") or filters.get("use_case")))
        has_format = bool(filters and (filters.get("format") or filters.get("author")))
        if not has_genre:
            questions_to_ask.append("genre")
        if not has_format:
            questions_to_ask.append("format")
        if not has_price:
            questions_to_ask.append("price")
    else:
        # Laptops/electronics: use_case → price → brand
        if not has_use_case:
            questions_to_ask.append("use_case")
        if not has_price:
            questions_to_ask.append("price")
        if not has_brand:
            questions_to_ask.append("brand")
    
    return len(questions_to_ask) > 0, questions_to_ask


def generate_followup_question(product_type: Optional[str], missing_info: List[str], filters: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str]]:
    """
    Generate a follow-up question based on missing information.
    
    LAPTOPS: use_case → price → brand (price question right after use case)
    BOOKS: genre → format → price (different questions and options)
    
    Args:
        product_type: Product type (e.g., "laptop", "book")
        missing_info: List of missing information topics in priority order
        filters: Optional filters to help determine product_type if not provided
    
    Returns:
        Tuple of (question, quick_replies)
    """
    if not missing_info:
        return "I need more information to help you.", []
    
    # CRITICAL: If product_type is None but we have category filter, infer product_type
    if not product_type and filters:
        category = filters.get("category", "").lower()
        if "books" in category:
            product_type = "book"
        elif "electronics" in category:
            product_type = "laptop"
    
    # --- BOOKS: genre → format → price ---
    if product_type == "book":
        if "genre" in missing_info:
            return "What genre are you interested in?", [
                "Fiction", "Mystery", "Science Fiction", "Non-fiction", "Romance", "Thriller"
            ]
        if "format" in missing_info:
            return "What format do you prefer?", [
                "Paperback", "Hardcover", "E-book", "Audiobook", "Any"
            ]
        if "price" in missing_info:
            return "What's your budget for books?", [
                "Under $15", "$15-$30", "$30-$50", "Over $50"
            ]
        return "I need more information to help you find the right book.", []
    
    # --- LAPTOPS / ELECTRONICS: use_case → price → brand ---
    if "use_case" in missing_info:
        if product_type == "laptop":
            return "What will you primarily use the laptop for?", [
                "Gaming", "Work", "School", "Creative work", "Entertainment", "Education"
            ]
        return "What will you use it for?", [
            "Work", "Entertainment", "Education"
        ]
    
    if "brand" in missing_info:
        if product_type == "laptop":
            return "Any brand preferences for the laptop?", [
                "Apple", "Dell", "HP", "Lenovo", "ASUS", "Any"
            ]
        return "What brand are you looking for?", [
            "Any", "Popular brands"
        ]
    
    if "price" in missing_info:
        if product_type == "laptop":
            return "What's your budget for the laptop?", [
                "Under $500", "$500-$1000", "$1000-$2000", "Over $2000"
            ]
        return "What's your budget?", [
            "Under $500", "$500-$1000", "$1000-$2000", "Over $2000"
        ]
    
    return "I need more information to help you find the right product.", []
