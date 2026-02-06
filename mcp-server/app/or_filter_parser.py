"""
OR Filter Parser - Detect and parse OR operations in queries.

Handles queries like:
- "Dell OR HP laptop"
- "NVIDIA or AMD graphics card"
- "gaming or work laptop under $2000"

Returns structured filter format with multiple values.
"""

import re
from typing import Dict, List, Any, Optional


def detect_or_operation(query: str) -> bool:
    """Check if query contains OR operation."""
    query_lower = query.lower()
    # Look for " or " with spaces to avoid matching words containing "or"
    return bool(re.search(r'\b(or|OR)\b', query))


def parse_or_filters(query: str, existing_filters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse OR operations from query and return enhanced filters.
    
    Examples:
        "Dell OR HP laptop" -> {"brand": ["Dell", "HP"]}
        "NVIDIA or AMD gaming" -> {"gpu_vendor": ["NVIDIA", "AMD"]}
        "gaming or work laptop" -> {"use_case": ["gaming", "work"]}
    
    Returns:
        Dict with list values for OR operations
    """
    enhanced_filters = dict(existing_filters)
    query_lower = query.lower()
    
    # Brand OR operations
    brand_patterns = [
        (r'\b(dell|hp|lenovo|asus|acer|apple|microsoft|samsung|sony|razer|msi|alienware)\s+or\s+(dell|hp|lenovo|asus|acer|apple|microsoft|samsung|sony|razer|msi|alienware)\b', 'brand'),
    ]
    
    for pattern, filter_key in brand_patterns:
        match = re.search(pattern, query_lower, re.IGNORECASE)
        if match:
            brands = []
            for group in match.groups():
                if group:
                    # Capitalize brand names
                    brand_map = {
                        "dell": "Dell",
                        "hp": "HP",
                        "lenovo": "Lenovo",
                        "asus": "ASUS",
                        "acer": "Acer",
                        "apple": "Apple",
                        "microsoft": "Microsoft",
                        "samsung": "Samsung",
                        "sony": "Sony",
                        "razer": "Razer",
                        "msi": "MSI",
                        "alienware": "Alienware",
                    }
                    brands.append(brand_map.get(group.lower(), group.capitalize()))
            
            if len(brands) >= 2:
                enhanced_filters[filter_key] = brands
                enhanced_filters["_or_operation"] = True
    
    # GPU Vendor OR operations
    gpu_patterns = [
        (r'\b(nvidia|amd|intel)\s+or\s+(nvidia|amd|intel)\b', 'gpu_vendor'),
    ]
    
    for pattern, filter_key in gpu_patterns:
        match = re.search(pattern, query_lower, re.IGNORECASE)
        if match:
            vendors = []
            for group in match.groups():
                if group:
                    vendor_map = {
                        "nvidia": "NVIDIA",
                        "amd": "AMD",
                        "intel": "Intel",
                    }
                    vendors.append(vendor_map.get(group.lower(), group.upper()))
            
            if len(vendors) >= 2:
                enhanced_filters[filter_key] = vendors
                enhanced_filters["_or_operation"] = True
    
    # Use case OR operations
    use_case_patterns = [
        (r'\b(gaming|work|school|creative)\s+or\s+(gaming|work|school|creative)\b', 'use_case'),
    ]
    
    for pattern, filter_key in use_case_patterns:
        match = re.search(pattern, query_lower, re.IGNORECASE)
        if match:
            use_cases = []
            for group in match.groups():
                if group:
                    use_cases.append(group.capitalize())
            
            if len(use_cases) >= 2:
                enhanced_filters[filter_key] = use_cases
                enhanced_filters["_or_operation"] = True
    
    return enhanced_filters


def apply_or_filters_to_query(query_obj: Any, filters: Dict[str, Any], model: Any) -> Any:
    """
    Apply OR filters to SQLAlchemy query.
    
    Args:
        query_obj: SQLAlchemy query object
        filters: Dict with potential list values for OR operations
        model: SQLAlchemy model (Product)
    
    Returns:
        Modified query object with OR conditions
    """
    from sqlalchemy import or_
    
    # Check if this is an OR operation
    if not filters.get("_or_operation"):
        return query_obj
    
    # Apply brand OR filter
    if "brand" in filters and isinstance(filters["brand"], list):
        brand_conditions = [model.brand == b for b in filters["brand"]]
        query_obj = query_obj.filter(or_(*brand_conditions))
    
    # Apply GPU vendor OR filter
    if "gpu_vendor" in filters and isinstance(filters["gpu_vendor"], list):
        gpu_conditions = [model.gpu_vendor == v for v in filters["gpu_vendor"]]
        query_obj = query_obj.filter(or_(*gpu_conditions))
    
    # Apply use case OR filter (subcategory)
    if "use_case" in filters and isinstance(filters["use_case"], list):
        use_case_conditions = [model.subcategory == u for u in filters["use_case"]]
        query_obj = query_obj.filter(or_(*use_case_conditions))
    
    return query_obj


def format_or_filter_description(filters: Dict[str, Any]) -> str:
    """
    Generate human-readable description of OR filters.
    
    Example:
        {"brand": ["Dell", "HP"], "_or_operation": True}
        -> "Dell OR HP"
    """
    descriptions = []
    
    if filters.get("_or_operation"):
        if "brand" in filters and isinstance(filters["brand"], list):
            descriptions.append(f"Brand: {' OR '.join(filters['brand'])}")
        
        if "gpu_vendor" in filters and isinstance(filters["gpu_vendor"], list):
            descriptions.append(f"GPU: {' OR '.join(filters['gpu_vendor'])}")
        
        if "use_case" in filters and isinstance(filters["use_case"], list):
            descriptions.append(f"Use Case: {' OR '.join(filters['use_case'])}")
    
    return " | ".join(descriptions) if descriptions else ""
