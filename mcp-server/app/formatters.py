from typing import Dict, Any, Optional
from .schemas import UnifiedProduct, ProductType, ImageInfo, VehicleDetails, LaptopDetails, BookDetails, LaptopSpecs, RetailListing

def format_product(product: Dict[str, Any], domain: str) -> UnifiedProduct:
    """
    Format a raw product dictionary into the UnifiedProduct schema.
    
    Args:
        product: Raw product data (from DB or API)
        domain: Domain identifier ('vehicles', 'laptops', 'books')
        
    Returns:
        UnifiedProduct object
    """
    # 1. Determine Product Type
    product_type = ProductType.GENERIC
    category = product.get("category", "")
    
    if domain == "vehicles" or "vin" in product:
        product_type = ProductType.VEHICLE
    elif domain == "laptops" or category == "Electronics":
        product_type = ProductType.LAPTOP
    elif domain == "books" or category == "Books":
        product_type = ProductType.BOOK
    elif domain == "jewelry" or category == "Jewelry":
        product_type = ProductType.JEWELRY
    elif domain == "accessories" or category == "Accessories":
        product_type = ProductType.ACCESSORY

    # 2. Extract Common Fields
    # ID mapping: vehicles use 'vin', others use 'product_id' or 'id'
    p_id = str(product.get("product_id") or product.get("id") or product.get("vin") or product.get("@id") or "")
    
    src = product.get("vehicle", product)
    name = product.get("name") or src.get("name") or f"{src.get('year')} {src.get('make')} {src.get('model')}"
    
    # Extract price with fallbacks
    price_val = product.get("price")
    if price_val is None:
        if product.get("vehicle"):
             price_val = product["vehicle"].get("price")
        if price_val is None and product.get("retailListing"):
             price_val = product["retailListing"].get("price")
             
    # Fallback to the nested retailListing or vehicle explicitly since product gets flattened
    if price_val is None or price_val == 0:
        price_val = product.get("retailListing", {}).get("price")
    if price_val is None or price_val == 0:
        price_val = src.get("price")
        
    price = int(price_val or 0)
    
    
    # Image handling with fallbacks and placeholders
    image_url = product.get("image_url") or product.get("primary_image_url")
    if not image_url and product.get("retailListing"):
        image_url = product["retailListing"].get("primaryImage")
        
    # Inject placeholders if still missing
    if not image_url:
        if product_type == ProductType.LAPTOP:
            image_url = "https://placehold.co/600x400?text=Laptop"
        elif product_type == ProductType.BOOK:
            image_url = "https://placehold.co/400x600?text=Book+Cover"
        elif product_type == ProductType.VEHICLE:
             image_url = "https://placehold.co/600x400?text=Vehicle"
        
    image_info = ImageInfo(
        primary=image_url,
        count=1 if image_url else 0
    )

    # Base Object
    unified = UnifiedProduct(
        id=p_id,
        productType=product_type,
        name=name,
        brand=product.get("brand") or src.get("make"),
        price=price,
        image=image_info,
        url=product.get("url") or product.get("vdp") or src.get("vdp"),
        
        # Additional fields for frontend display
        description=product.get("description"),
        category=product.get("category") or src.get("bodyStyle") or src.get("body_style"),
        subcategory=product.get("subcategory"),
        color=product.get("color") or src.get("exteriorColor"),
        rating=product.get("rating") or _calculate_rating(product.get("reviews")),
        reviews_count=product.get("rating_count") or _count_reviews(product.get("reviews")),
        reviews=product.get("reviews"),  # Pass through for frontend to display
        available_qty=product.get("available_qty"),
        available=(product.get("available_qty") or 1) > 0,
    )
    
    # 3. Add Type-Specific Details
    if product_type == ProductType.VEHICLE:
        unified.vehicle = _extract_vehicle_details(product)
        unified.retailListing = _create_legacy_listing(product, price, image_url)
        
    elif product_type == ProductType.LAPTOP:
        unified.laptop = _extract_laptop_details(product)
        unified.retailListing = _create_legacy_listing_for_non_vehicle(product, price, image_url, "Electronics")
        
    elif product_type == ProductType.BOOK:
        unified.book = _extract_book_details(product)
        unified.retailListing = _create_legacy_listing_for_non_vehicle(product, price, image_url, "Books")
    elif product_type in (ProductType.JEWELRY, ProductType.ACCESSORY):
        unified.retailListing = _create_legacy_listing_for_non_vehicle(
            product, price, image_url, category or "Accessories"
        )

    return unified


def _extract_vehicle_details(p: Dict[str, Any]) -> VehicleDetails:
    # Handle nested IDSS structure if present
    src = p.get("vehicle", p)
    
    price_val = src.get("price")
    if price_val is None: price_val = p.get("price")
    
    return VehicleDetails(
        year=src.get("year", 2024),
        make=src.get("make", ""),
        model=src.get("model", ""),
        trim=src.get("trim"),
        bodyStyle=src.get("bodyStyle") or src.get("body_style"),
        mileage=src.get("mileage"),
        price=int(price_val if price_val is not None else 0),
        vin=src.get("vin") or p.get("vin") or p.get("@id"),
        fuel=src.get("fuel"),
        transmission=src.get("transmission"),
        drivetrain=src.get("drivetrain"),
        engine=src.get("engine"),
        doors=src.get("doors"),
        seats=src.get("seats"),
        exteriorColor=src.get("exteriorColor"),
        mpg=src.get("mpg"),
        condition=src.get("condition", "used" if p.get("used") or src.get("norm_is_used") else "new"),
        dealer=p.get("dealer") if isinstance(p.get("dealer"), dict) else {"name": p.get("dealer")}
    )

def _extract_laptop_details(p: Dict[str, Any]) -> LaptopDetails:
    attrs = p.get("attributes") or {}
    def v(k: str, ak: Optional[str] = None) -> Optional[str]:
        val = p.get(k)
        if val is not None and val != "":
            return str(val) if not isinstance(val, str) else val
        val = attrs.get(ak or k)
        if val is not None and val != "":
            return str(val) if not isinstance(val, str) else val
        return None
    def v_num(k: str, ak: Optional[str] = None) -> Optional[int]:
        val = p.get(k) or attrs.get(ak or k)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
        return None
    ram = v("ram") or (f"{int(attrs['ram_gb'])} GB" if attrs.get("ram_gb") is not None else None)
    storage = v("storage") or (f"{int(attrs['storage_gb'])} GB" if attrs.get("storage_gb") is not None else None)
    battery = v("battery_life") or (f"{int(attrs['battery_life_hours'])} hrs" if attrs.get("battery_life_hours") is not None else None)
    screen = v("screen_size") or (str(attrs["screen_size"]) if attrs.get("screen_size") is not None else None)
    display = v("display") or screen or v("resolution")
    if screen and display and screen not in str(display):
        display = f"{screen}\" {display}".strip()
    return LaptopDetails(
        specs=LaptopSpecs(
            processor=v("processor") or v("cpu") or attrs.get("cpu"),
            ram=ram,
            storage=storage,
            storage_type=v("storage_type") or attrs.get("storage_type"),
            display=display,
            screen_size=screen,
            resolution=v("resolution") or attrs.get("resolution"),
            graphics=p.get("gpu") or p.get("gpu_model") or v("graphics") or attrs.get("gpu") or attrs.get("gpu_model"),
            battery_life=battery,
            os=v("os") or attrs.get("os") or attrs.get("operating_system"),
            weight=v("weight") or (str(attrs["weight"]) if attrs.get("weight") is not None else None),
            refresh_rate_hz=v_num("refresh_rate_hz") or (attrs.get("refresh_rate_hz") if isinstance(attrs.get("refresh_rate_hz"), int) else None),
        ),
        gpuVendor=p.get("gpu_vendor") or attrs.get("gpu_vendor"),
        gpuModel=p.get("gpu_model") or attrs.get("gpu_model"),
        color=p.get("color") or attrs.get("color"),
        tags=p.get("tags") or [],
        attributes=attrs if attrs else None,
    )

def _extract_book_details(p: Dict[str, Any]) -> BookDetails:
    return BookDetails(
        author=p.get("author") or _extract_spec(p, "author") or "Unknown Author",
        genre=p.get("subcategory") or p.get("genre"),
        format=p.get("format"),
        pages=p.get("pages"),
        isbn=p.get("isbn"),
        publisher=p.get("publisher"),
        publishedDate=p.get("published_date")
    )

def _create_legacy_listing(p: Dict, price: int, img: Optional[str]) -> RetailListing:
    return RetailListing(
        price=price,
        primaryImage=img,
        photoCount=1 if img else 0,
        dealer=p.get("dealer", {}).get("name") if isinstance(p.get("dealer"), dict) else p.get("dealer"),
        city=p.get("dealer", {}).get("city") if isinstance(p.get("dealer"), dict) else None,
        state=p.get("dealer", {}).get("state") if isinstance(p.get("dealer"), dict) else None,
        vdp=p.get("vdp"),
        used=p.get("used", False),
        cpo=p.get("cpo", False),
        carfaxUrl=p.get("carfaxUrl") or p.get("carfax_url")
    )

def _create_legacy_listing_for_non_vehicle(p: Dict, price: int, img: Optional[str], dealer_name: str) -> RetailListing:
    return RetailListing(
        price=price,
        primaryImage=img,
        photoCount=1 if img else 0,
        dealer=p.get("brand") or dealer_name,
        used=False,
        cpo=False,
    )

def _extract_spec(p: Dict, key: str) -> Optional[str]:
    """Helper to extract specs from description using regex if not in top level."""
    import re
    desc = p.get("description", "")
    if not desc:
        return None
        
    # Common patterns in description: "Processor: Intel i7", "RAM: 16GB", "Author: Jane Austen"
    # Case insensitive
    pattern = rf"{key}[:\s-]+([^\n,]+)"
    match = re.search(pattern, desc, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None

def _calculate_rating(reviews: Optional[str]) -> Optional[float]:
    """Calculate average rating from reviews text or JSON."""
    if not reviews:
        return None
    
    import json
    import re
    
    # Try parsing as JSON first (new format)
    try:
        reviews_list = json.loads(reviews)
        if isinstance(reviews_list, list) and len(reviews_list) > 0:
            ratings = [r.get("rating", 0) for r in reviews_list if isinstance(r, dict) and r.get("rating")]
            if ratings:
                avg_rating = sum(ratings) / len(ratings)
                return round(avg_rating, 1)
    except (json.JSONDecodeError, ValueError):
        pass
    
    # Fallback: parse as plain text (old format)
    # Look for ratings like "Rating: 4.5" or "4.5/5" or ""
    rating_pattern = r"(?:rating[:\s]+)?(\d+(?:\.\d+)?)\s*(?:/\s*5)?(?:\s*stars?)?"
    matches = re.findall(rating_pattern, reviews, re.IGNORECASE)
    
    if matches:
        ratings = [float(m) for m in matches if 0 <= float(m) <= 5]
        if ratings:
            avg_rating = sum(ratings) / len(ratings)
            return round(avg_rating, 1)
    
    # Count star symbols
    star_count = reviews.count("") + reviews.count("")
    if star_count > 0:
        return min(5.0, float(star_count))
    
    return None

def _count_reviews(reviews: Optional[str]) -> Optional[int]:
    """Count number of reviews from reviews text or JSON."""
    if not reviews:
        return None
    
    import json
    import re
    
    # Try parsing as JSON first (new format)
    try:
        reviews_list = json.loads(reviews)
        if isinstance(reviews_list, list):
            return len(reviews_list)
    except (json.JSONDecodeError, ValueError):
        pass
    
    # Fallback: parse as plain text (old format)
    # Look for explicit review count like "23 reviews" or "Review #5"
    count_pattern = r"(\d+)\s*reviews?"
    matches = re.findall(count_pattern, reviews, re.IGNORECASE)
    
    if matches:
        return int(matches[0])
    
    # Count number of review entries (simple heuristic: newlines or separators)
    review_separators = reviews.count("\n\n") + reviews.count("---")
    if review_separators > 0:
        return review_separators + 1
    
    # If reviews exist but no clear count, assume 1 review
    return 1 if reviews.strip() else None


def _extract_policy_from_description(description: str) -> Dict[str, Optional[str]]:
    """Extract shipping, return policy, warranty, and promotion info from a product description."""
    import re

    result: Dict[str, Optional[str]] = {
        "shipping": None,
        "return_policy": None,
        "warranty": None,
        "promotion_info": None,
    }
    if not description:
        return result

    # Promotion keywords (checked across whole description first for multi-word patterns)
    promo_patterns = [
        r"(\d+%\s*off[^.]*)",
        r"(save\s+\$?\d+[^.]*)",
        r"(free\s+(?:shipping|gift|bonus)[^.]*)",
        r"(limited[- ]time\s+(?:offer|deal|sale)[^.]*)",
        r"(buy\s+\d+\s+get\s+\d+[^.]*)",
        r"(coupon|promo(?:tion)?|discount|clearance|sale\s+price)[^.]*",
    ]
    desc_lower = description.lower()
    for pat in promo_patterns:
        m = re.search(pat, desc_lower)
        if m and result["promotion_info"] is None:
            # Find the original-case version of the matched text
            start, end = m.start(), m.end()
            result["promotion_info"] = description[start:end].strip().rstrip(".")
            break

    # Match sentences/phrases containing each keyword
    sentences = re.split(r'[.\n]+', description)
    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue
        s_lower = s.lower()
        if "shipping" in s_lower and result["shipping"] is None:
            result["shipping"] = s
        elif "return" in s_lower and result["return_policy"] is None:
            result["return_policy"] = s
        elif "warranty" in s_lower and result["warranty"] is None:
            result["warranty"] = s

    return result
