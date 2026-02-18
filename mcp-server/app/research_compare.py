"""
Research and Compare functionalities (kg.txt step intents).

Research: explain features, check compatibility, summarize reviews
Compare: side-by-side between options
"""

import json
import re
from typing import Dict, List, Any, Optional


def _parse_reviews(reviews_raw: Optional[str]) -> Dict[str, Any]:
    """Parse reviews JSON and return summary."""
    if not reviews_raw:
        return {"average_rating": None, "review_count": 0, "summary": "No reviews yet.", "sample_comments": []}
    try:
        data = json.loads(reviews_raw) if isinstance(reviews_raw, str) else reviews_raw
        if isinstance(data, dict):
            avg = data.get("average_rating")
            count = data.get("review_count", 0)
            reviews_list = data.get("reviews", [])
        elif isinstance(data, list):
            reviews_list = data
            count = len(reviews_list)
            avg = sum(r.get("rating", 0) for r in reviews_list) / count if count else None
        else:
            return {"average_rating": None, "review_count": 0, "summary": "No reviews yet.", "sample_comments": []}
        sample = [r.get("comment", "") for r in reviews_list[:3] if r.get("comment")]
        summary = f"{count} reviews, average {avg:.1f}/5 stars." if avg else f"{count} reviews."
        return {
            "average_rating": round(avg, 1) if avg else None,
            "review_count": count,
            "summary": summary,
            "sample_comments": sample,
        }
    except Exception:
        return {"average_rating": None, "review_count": 0, "summary": "No reviews yet.", "sample_comments": []}


def _extract_features(product: Dict[str, Any]) -> List[str]:
    """Extract key features from product description and structured fields."""
    features = []
    desc = (product.get("description") or "").strip()
    if desc:
        # Take first 2-3 sentences as feature highlights
        sentences = re.split(r'[.!?]+', desc)
        for s in sentences[:3]:
            s = s.strip()
            if len(s) > 20:
                features.append(s + ("." if not s.endswith(".") else ""))
    # Structured specs
    if product.get("gpu_model"):
        features.append(f"Graphics: {product['gpu_model']}")
    if product.get("gpu_vendor"):
        features.append(f"GPU vendor: {product['gpu_vendor']}")
    if product.get("color"):
        features.append(f"Color: {product['color']}")
    if product.get("subcategory"):
        features.append(f"Type: {product['subcategory']}")
    if product.get("product_type"):
        features.append(f"Product type: {product['product_type']}")
    return features[:8]  # Cap at 8


def _extract_compatibility(product: Dict[str, Any]) -> str:
    """Check compatibility (OS, software, use case)."""
    cat = (product.get("category") or "").lower()
    ptype = (product.get("product_type") or "").lower()
    desc = (product.get("description") or "").lower()
    parts = []
    if "electronic" in cat or "laptop" in ptype or "laptop" in desc:
        if "apple" in desc or "mac" in desc or product.get("brand") == "Apple":
            parts.append("macOS compatible")
        if "windows" in desc or "intel" in desc or "amd" in desc:
            parts.append("Windows compatible")
        if "linux" in desc or "ubuntu" in desc or "fedora" in desc:
            parts.append("Linux compatible")
        if not parts:
            parts.append("Windows/macOS compatible (check specs)")
    if "book" in ptype or "book" in cat:
        fmt = product.get("format") or ""
        if fmt:
            parts.append(f"Format: {fmt}")
        else:
            parts.append("Standard print/ebook formats")
    if not parts:
        parts.append("Compatible with standard use cases")
    return "; ".join(parts)


def build_research_summary(product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build research summary for a product: features, compatibility, review summary.
    """
    features = _extract_features(product)
    compatibility = _extract_compatibility(product)
    review_data = _parse_reviews(product.get("reviews"))
    return {
        "product_id": product.get("product_id"),
        "name": product.get("name"),
        "brand": product.get("brand"),
        "category": product.get("category"),
        "price": product.get("price"),
        "features": features,
        "compatibility": compatibility,
        "review_summary": review_data,
    }


def _get_comparable_attributes() -> List[str]:
    """Attribute keys to show in comparison table (in order)."""
    return [
        "name", "brand", "price", "category", "subcategory", "color",
        "gpu_model", "gpu_vendor", "product_type", "description",
    ]


def _product_to_flat_dict(p: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten product for comparison (include nested fields)."""
    flat = {}
    for k in ["product_id", "name", "brand", "price", "category", "subcategory", "color",
              "gpu_model", "gpu_vendor", "product_type", "description"]:
        v = p.get(k) or p.get("_product", {}).get(k)
        if v is not None:
            flat[k] = str(v)[:100] if k == "description" else v
    # Reviews summary
    reviews = _parse_reviews(p.get("reviews"))
    flat["review_rating"] = reviews.get("average_rating")
    flat["review_count"] = reviews.get("review_count")
    return flat


def parse_compare_by(message: str) -> Optional[List[str]]:
    """Parse 'compare by X and Y' from user message. Returns list of attribute keys or None."""
    m = re.search(r'compare\s+by\s+(.+)', message, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).lower()
    # Split on "and", ",", "&"
    parts = re.split(r'\s+and\s+|,\s*|&\s*', raw)
    attr_map = {
        "price": "price", "cost": "price",
        "brand": "brand", "manufacturer": "brand",
        "rating": "review_rating", "ratings": "review_rating", "reviews": "review_rating",
        "color": "color", "colour": "color",
        "gpu": "gpu_model", "graphics": "gpu_model",
        "type": "product_type", "category": "category",
    }
    result = []
    for part in parts:
        part = part.strip().rstrip(".")
        if part in attr_map:
            result.append(attr_map[part])
        elif part in _get_comparable_attributes():
            result.append(part)
    return result if result else None


def generate_recommendation_reasons(
    product_dicts: List[Dict[str, Any]],
    filters: Optional[Dict[str, Any]] = None,
    kg_candidate_ids: Optional[List[str]] = None,
) -> None:
    """
    Annotate each product dict in-place with a '_reason' key explaining
    why it was recommended (e.g. 'KG match', 'Brand match', 'Best price').
    """
    filters = filters or {}
    kg_set = set(kg_candidate_ids) if kg_candidate_ids else set()
    brand_filter = (filters.get("brand") or "").lower()

    for i, p in enumerate(product_dicts):
        reasons = []
        pid = p.get("product_id", "")
        if pid in kg_set:
            reasons.append("Knowledge graph match")
        if brand_filter and (p.get("brand") or "").lower() == brand_filter:
            reasons.append("Brand match")
        if i == 0:
            reasons.append("Best price")
        p["_reason"] = "; ".join(reasons) if reasons else "Relevant match"


def build_comparison_table(products: List[Dict[str, Any]], compare_by: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Build side-by-side comparison data.
    Returns: { "attributes": [...], "products": [{"product_id", "name", "values": {...}}] }
    """
    if not products:
        return {"attributes": [], "products": []}
    if compare_by:
        attrs = [a for a in compare_by if a in _get_comparable_attributes() + ["review_rating", "review_count"]]
        if not attrs:
            attrs = _get_comparable_attributes()
    else:
        attrs = _get_comparable_attributes()
    # Add review fields
    if "review_rating" not in attrs:
        attrs = attrs + ["review_rating", "review_count"]
    result_products = []
    for p in products:
        flat = _product_to_flat_dict(p)
        values = {a: flat.get(a, "—") for a in attrs}
        result_products.append({
            "product_id": p.get("product_id"),
            "name": p.get("name", "Unknown"),
            "brand": p.get("brand"),
            "values": values,
            "image_url": p.get("image_url"),
        })
    return {
        "attributes": attrs,
        "attribute_labels": {
            "name": "Product",
            "brand": "Brand",
            "price": "Price",
            "category": "Category",
            "subcategory": "Type",
            "color": "Color",
            "gpu_model": "Graphics",
            "gpu_vendor": "GPU Vendor",
            "product_type": "Product Type",
            "description": "Description",
            "review_rating": "Rating",
            "review_count": "Reviews",
        },
        "products": result_products,
    }
