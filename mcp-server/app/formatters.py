from typing import Dict, Any, Optional
from .schemas import UnifiedProduct, ProductType, ImageInfo, VehicleDetails, LaptopDetails, BookDetails, LaptopSpecs

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

    # 2. Extract Common Fields
    # ID mapping: vehicles use 'vin', others use 'product_id' or 'id'
    p_id = str(product.get("product_id") or product.get("id") or product.get("vin") or product.get("@id") or "")
    name = product.get("name") or f"{product.get('year')} {product.get('make')} {product.get('model')}"
    
    # Extract price with fallbacks
    price_val = product.get("price")
    if price_val is None:
        if product.get("vehicle"):
             price_val = product["vehicle"].get("price")
        if price_val is None and product.get("retailListing"):
             price_val = product["retailListing"].get("price")
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
        brand=product.get("brand") or product.get("make"),
        price=price,
        image=image_info,
        url=product.get("url") or product.get("vdp")
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

    return unified


def _extract_vehicle_details(p: Dict[str, Any]) -> VehicleDetails:
    # Handle nested IDSS structure if present
    src = p.get("vehicle", p)
    
    return VehicleDetails(
        year=src.get("year", 2024),
        make=src.get("make", ""),
        model=src.get("model", ""),
        trim=src.get("trim"),
        bodyStyle=src.get("bodyStyle") or src.get("body_style"),
        mileage=src.get("mileage"),
        price=int(src.get("price") or p.get("price") or 0), # Price might be top-level
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
    return LaptopDetails(
        specs=LaptopSpecs(
            processor=p.get("processor") or _extract_spec(p, "processor"),
            ram=p.get("ram") or _extract_spec(p, "ram"),
            storage=p.get("storage") or _extract_spec(p, "storage"),
            display=p.get("display") or _extract_spec(p, "display"),
            graphics=p.get("gpu_model") or p.get("graphics")
        ),
        gpuVendor=p.get("gpu_vendor"),
        gpuModel=p.get("gpu_model"),
        color=p.get("color"),
        tags=p.get("tags") or []
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

def _create_legacy_listing(p: Dict, price: int, img: Optional[str]):
    return {
        "price": price,
        "primaryImage": img,
        "photoCount": 1 if img else 0,
        "dealer": p.get("dealer", {}).get("name") if isinstance(p.get("dealer"), dict) else p.get("dealer"),
        "city": p.get("dealer", {}).get("city") if isinstance(p.get("dealer"), dict) else None,
        "state": p.get("dealer", {}).get("state") if isinstance(p.get("dealer"), dict) else None,
        "vdp": p.get("vdp"),
        "used": p.get("used", False)
    }

def _create_legacy_listing_for_non_vehicle(p: Dict, price: int, img: Optional[str], dealer_name: str):
    return {
        "price": price,
        "primaryImage": img,
        "photoCount": 1 if img else 0,
        "dealer": p.get("brand") or dealer_name,
        "used": False
    }

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
