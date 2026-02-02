"""
Intelligent query parsing for complex multi-attribute queries.

Handles queries like "family suv fuel efficient" by extracting:
- Product type (suv, sedan, truck)
- Use case (family, work, weekend)
- Attributes (fuel efficient, spacious, luxury)
"""

import re
from typing import Dict, Any, Optional, List, Tuple


# Product type keywords
# Order matters: more specific patterns first (e.g., "gaming pc" before "pc")
PRODUCT_TYPE_KEYWORDS = {
    "suv": ["suv", "sport utility", "crossover", "cuv"],
    "sedan": ["sedan", "car", "automobile"],
    "truck": ["truck", "pickup", "pick-up"],
    "van": ["van", "minivan"],
    "desktop": ["gaming pc", "gaming computer", "desktop pc", "desktop computer", "desktop", "pc", "workstation", "tower"],
    "laptop": ["laptop", "notebook", "macbook", "chromebook"],
    "book": ["book", "novel", "textbook"],
    "headphone": ["headphone", "earphone", "earbud", "headset"]
}

# Use case keywords
USE_CASE_KEYWORDS = {
    "family": ["family", "families", "kids", "children", "parent"],
    "work": ["work", "business", "office", "professional", "corporate"],
    "gaming": ["gaming", "game", "gamer", "esports"],
    "school": ["school", "student", "education", "academic"],
    "creative": ["creative", "video editing", "photo editing", "design", "art"],
    "weekend": ["weekend", "fun", "recreation", "leisure"],
    "commuter": ["commuter", "commute", "daily", "city"]
}

# Attribute keywords
ATTRIBUTE_KEYWORDS = {
    "fuel_efficient": ["fuel efficient", "fuel economy", "mpg", "gas mileage", "efficient"],
    "spacious": ["spacious", "roomy", "large", "big", "space"],
    "luxury": ["luxury", "premium", "high-end", "upscale"],
    "affordable": ["affordable", "cheap", "budget", "inexpensive", "low cost"],
    "powerful": ["powerful", "performance", "fast", "speed", "high performance"],
    "portable": ["portable", "lightweight", "light", "compact"],
    "durable": ["durable", "reliable", "tough", "sturdy"]
}

# Category mappings
CATEGORY_MAPPINGS = {
    "suv": "SUV",
    "sedan": "Sedan",
    "truck": "Truck",
    "van": "Van",
    "desktop": "Electronics",
    "laptop": "Electronics",
    "book": "Books",
    "headphone": "Electronics"
}


def extract_product_type(query: str) -> Optional[str]:
    """
    Extract product type from query.
    
    Returns: Product type (e.g., "suv", "laptop", "desktop") or None
    Uses word-boundary matching so "MacBook" does not match "book".
    Checks longer/more specific patterns first (e.g., "gaming pc" before "pc").
    """
    query_lower = query.lower()
    
    # Sort keywords by length (longest first) to match "gaming pc" before "pc"
    for product_type, keywords in PRODUCT_TYPE_KEYWORDS.items():
        # Sort keywords by length descending
        sorted_keywords = sorted(keywords, key=len, reverse=True)
        for keyword in sorted_keywords:
            # For multi-word patterns like "gaming pc", use direct substring match
            if " " in keyword:
                if keyword in query_lower:
                    return product_type
            else:
                # For single words, use word boundaries: \bpc\b matches "pc" but not "space"
                pattern = r"\b" + re.escape(keyword) + r"\b"
                if re.search(pattern, query_lower):
                    return product_type
    
    return None


def extract_use_case(query: str) -> Optional[str]:
    """
    Extract use case from query.
    
    Returns: Use case (e.g., "family", "work", "gaming") or None
    """
    query_lower = query.lower()
    
    for use_case, keywords in USE_CASE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in query_lower:
                return use_case
    
    return None


def extract_attributes(query: str) -> List[str]:
    """
    Extract attributes from query.
    
    Returns: List of attributes (e.g., ["fuel_efficient", "spacious"])
    """
    query_lower = query.lower()
    attributes = []
    
    for attr, keywords in ATTRIBUTE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in query_lower:
                attributes.append(attr)
                break  # Only add each attribute once
    
    return attributes


def parse_complex_query(query: str) -> Dict[str, Any]:
    """
    Parse a complex query into structured filters and metadata.
    
    Example:
        Input: "family suv fuel efficient"
        Output: {
            "category": "SUV",
            "metadata": {
                "use_case": "family",
                "fuel_efficiency": "high"
            },
            "original_query": "family suv fuel efficient",
            "parsed_components": {
                "product_type": "suv",
                "use_case": "family",
                "attributes": ["fuel_efficient"]
            }
        }
    
    Args:
        query: User query string
        
    Returns:
        Dict with filters and metadata
    """
    if not query or len(query.strip()) < 3:
        return {
            "original_query": query,
            "filters": {},
            "metadata": {}
        }
    
    # Extract components
    product_type = extract_product_type(query)
    use_case = extract_use_case(query)
    attributes = extract_attributes(query)
    
    # Build filters
    filters = {}
    metadata = {}
    
    # Map product type to category
    if product_type:
        category = CATEGORY_MAPPINGS.get(product_type)
        if category:
            filters["category"] = category
        metadata["product_type"] = product_type
    
    # Add use case to metadata
    if use_case:
        metadata["use_case"] = use_case
    
    # Map attributes to metadata
    if "fuel_efficient" in attributes:
        metadata["fuel_efficiency"] = "high"
    
    if "spacious" in attributes:
        metadata["spaciousness"] = "high"
    
    if "luxury" in attributes:
        metadata["luxury"] = True
    
    if "affordable" in attributes:
        # Could set price_max filter, but better to let agent handle
        metadata["price_range"] = "budget"
    
    if "powerful" in attributes:
        metadata["performance"] = "high"
    
    if "portable" in attributes:
        metadata["portability"] = "high"
    
    if "durable" in attributes:
        metadata["durability"] = "high"
    
    return {
        "original_query": query,
        "filters": filters,
        "metadata": metadata,
        "parsed_components": {
            "product_type": product_type,
            "use_case": use_case,
            "attributes": attributes
        }
    }


def enhance_search_request(query: str, existing_filters: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
    """
    Enhance a search request with parsed query information.
    
    Merges parsed filters/metadata with existing filters.
    
    Args:
        query: Original query string
        existing_filters: Existing filters dict (optional)
        
    Returns:
        Tuple of (cleaned_query, enhanced_filters)
    """
    parsed = parse_complex_query(query)
    
    # Start with existing filters or empty dict
    enhanced_filters = existing_filters.copy() if existing_filters else {}
    
    # Merge parsed filters (parsed takes precedence for category)
    if parsed["filters"]:
        enhanced_filters.update(parsed["filters"])
    
    # Add metadata to filters (for KG/vector search)
    if parsed["metadata"]:
        enhanced_filters["_metadata"] = parsed["metadata"]
    
    # Clean query: remove extracted keywords to avoid double-matching
    cleaned_query = query
    # Optionally clean, but keep original for semantic search
    
    return cleaned_query, enhanced_filters
