"""
Lightweight query specificity helpers.

These defaults treat most queries as specific enough to search directly,
which keeps tests deterministic without requiring interview logic.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple, Optional

from app.domain_registry import get_domain_schema


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
    if "book" in category or "book" in text or "novel" in text:
        return "books"
    if "electronic" in category or "laptop" in text or "computer" in text or "pc" in text:
        return "laptops"
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

    # Product type
    if "desktop" in text or "tower" in text or "gaming pc" in text:
        extracted_info["product_type"] = "desktop"
    elif "laptop" in text or "notebook" in text or "computer" in text:
        extracted_info["product_type"] = "laptop"
    elif "book" in text or "novel" in text:
        extracted_info["product_type"] = "book"

    # Brand detection
    for key, brand in BRAND_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", text):
            extracted_info["brand"] = brand
            break

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

    missing_info = _missing_slots(query, filters, extracted_info)
    return len(missing_info) == 0, extracted_info


def _missing_slots(query: str, filters: Dict[str, object], extracted_info: Dict[str, object]) -> List[str]:
    domain = _detect_domain(query, filters)
    if not domain:
        return []

    # Required slots by week4tips flow
    if domain == "books":
        required = ["genre", "budget"]
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
            if not (filters.get("genre") or filters.get("subcategory")):
                missing.append("genre")

    return missing


def should_ask_followup(query: str, filters: Dict[str, object]) -> Tuple[bool, List[str]]:
    """Determine if a follow-up question is needed."""
    _, extracted_info = is_specific_query(query, filters)
    missing = _missing_slots(query, filters, extracted_info)
    return (len(missing) > 0), missing


def generate_followup_question(product_type: str, missing_info: List[str], filters: Dict[str, object]) -> Tuple[str, List[str]]:
    """Return a follow-up question using domain registry slot definitions."""
    domain = "books" if product_type == "book" else "laptops"
    schema = get_domain_schema(domain)
    if not schema or not missing_info:
        return "Could you clarify what you are looking for?", ["Laptops", "Books", "Electronics"]

    slot_name = missing_info[0]
    slot = next((s for s in schema.slots if s.name == slot_name), None)
    if slot:
        return slot.example_question, slot.example_replies

    return "Could you clarify what you are looking for?", ["Laptops", "Books", "Electronics"]
