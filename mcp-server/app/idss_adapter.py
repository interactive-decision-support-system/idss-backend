"""
Universal Product Adapter - Bridges Multiple Backends to MCP Format

This adapter is designed to work with:
- IDSS Backend: Vehicle recommendations with advanced ranking
- E-commerce Backend: Standard product catalogs
- Future backends: Real estate, travel, etc.

Key Features:
- Automatic product category detection
- Flexible data transformation
- Maintains MCP response envelope pattern
- Extensible for new product types
"""

import time
import uuid
import httpx
from datetime import datetime
from typing import List, Optional, Dict, Any
from enum import Enum
import logging

from app.schemas import (
    ResponseStatus, ConstraintDetail, RequestTrace, VersionInfo, ProvenanceInfo,
    SearchProductsRequest, SearchProductsResponse, SearchResultsData, ProductSummary,
    GetProductRequest, GetProductResponse, ProductDetail, ShippingInfo,
)

# Set up logger
logger = logging.getLogger(__name__)


def _enriched_defaults() -> Dict[str, Any]:
    """Agent-ready enrichment (week6tips). Same defaults as endpoints."""
    return {
        "shipping": ShippingInfo(
            shipping_method="standard",
            estimated_delivery_days=5,
            shipping_cost_cents=599,
            shipping_region="US",
        ),
        "return_policy": "Free 30-day returns",
        "warranty": "1-year manufacturer warranty",
        "promotion_info": None,
    }


# ============================================================================
# Configuration
# ============================================================================

class ProductCategory(str, Enum):
    """Supported product categories."""
    VEHICLE = "vehicle"
    ECOMMERCE = "ecommerce"
    REAL_ESTATE = "real_estate"
    TRAVEL = "travel"
    

class BackendType(str, Enum):
    """Supported backend types."""
    IDSS = "idss"  # Vehicle recommendation system
    POSTGRES = "postgres"  # E-commerce database
    

# Backend configuration
BACKEND_CONFIGS = {
    BackendType.IDSS: {
        "url": "http://localhost:8000",
        "category": ProductCategory.VEHICLE,
        "search_endpoint": "/chat",
        "detail_endpoint": None,  # Use direct DB access
    },
    BackendType.POSTGRES: {
        "url": None,  # Direct DB access
        "category": ProductCategory.ECOMMERCE,
        "search_endpoint": None,
        "detail_endpoint": None,
    }
}

# Default backend
DEFAULT_BACKEND = BackendType.IDSS


# ============================================================================
# Helper Functions
# ============================================================================

def create_trace(
    request_id: str,
    cache_hit: bool,
    timings: dict,
    sources: List[str],
    metadata: Optional[Dict[str, Any]] = None
) -> RequestTrace:
    """Helper to create standardized request trace objects."""
    return RequestTrace(
        request_id=request_id,
        cache_hit=cache_hit,
        timings_ms=timings,
        sources=sources,
        metadata=metadata
    )


def apply_field_projection(
    product_detail: ProductDetail,
    fields: Optional[List[str]]
) -> ProductDetail:
    """
    Apply field projection to ProductDetail.
    
    If fields is None, returns full details.
    If fields is specified, returns only requested fields.
    
    Args:
        product_detail: Full product detail object
        fields: List of field names to include, or None for all
    
    Returns:
        ProductDetail with only requested fields (others set to None/default)
    """
    if not fields:
        return product_detail
    
    # Start with a minimal dict with required fields
    projected = {
        "product_id": product_detail.product_id,
        "name": product_detail.name if "name" in fields else None,
        "description": product_detail.description if "description" in fields else None,
        "category": product_detail.category if "category" in fields else None,
        "brand": product_detail.brand if "brand" in fields else None,
        "price_cents": product_detail.price_cents if "price_cents" in fields or "price" in fields else 0,
        "currency": product_detail.currency if "currency" in fields else "USD",
        "available_qty": product_detail.available_qty if "available_qty" in fields or "qty" in fields else 0,
        "created_at": product_detail.created_at,
        "updated_at": product_detail.updated_at,
        "product_type": product_detail.product_type if "product_type" in fields else None,
        "metadata": product_detail.metadata if "metadata" in fields else None,
        "provenance": product_detail.provenance if "provenance" in fields else None,
    }
    
    return ProductDetail(**projected)


def create_version_info(category: ProductCategory = ProductCategory.VEHICLE) -> VersionInfo:
    """Helper to create version information."""
    return VersionInfo(
        catalog_version=f"1.0.0-{category.value}",
        updated_at=datetime.utcnow()
    )


# ============================================================================
# Vehicle-Specific Transformations
# ============================================================================

def vehicle_to_product_summary(vehicle: Dict[str, Any]) -> ProductSummary:
    """
    Convert IDSS vehicle format to MCP ProductSummary format.
    
    Handles nested Auto.dev format:
    {
        "vehicle": {
            "vin": "123",
            "year": 2020,
            "make": "Toyota",
            "model": "Camry",
            ...
        },
        "retailListing": {
            "price": 25000,
            "miles": 30000,
            "dealer": "...",
            ...
        }
    }
    
    Output:
    {
        "product_id": "VIN-123",
        "name": "2020 Toyota Camry",
        "price_cents": 2500000,
        "currency": "USD",
        "category": "Sedan",
        "brand": "Toyota",
        "available_qty": 1
    }
    """
    # Extract vehicle info (handle both nested and flat formats)
    if "vehicle" in vehicle:
        v = vehicle["vehicle"]
        retail = vehicle.get("retailListing", {})
    else:
        # Flat format
        v = vehicle
        retail = vehicle
    
    # Get VIN (unique identifier for vehicles)
    vin = v.get("vin") or vehicle.get("vin") or str(uuid.uuid4())
    
    # Get basic info
    make = v.get("make", "Unknown")
    model = v.get("model", "Unknown")
    year = v.get("year", "")
    body_style = v.get("bodyStyle") or v.get("body_style") or v.get("norm_body_type", "Vehicle")
    
    # Get price (from retail listing or root)
    price = retail.get("price") or v.get("price") or vehicle.get("price", 0)
    
    # Format name: "2020 Toyota Camry"
    name = f"{year} {make} {model}".strip()
    
    # Get mileage for metadata
    mileage = retail.get("miles") or v.get("mileage") or vehicle.get("mileage")
    
    # Build vehicle-specific metadata
    vehicle_metadata = {
        "vin": vin,
        "year": year,
        "body_style": body_style,
    }
    
    if mileage:
        vehicle_metadata["mileage"] = int(mileage)
    
    # Add provenance tracking
    provenance = ProvenanceInfo(
        source="idss_sqlite",
        timestamp=datetime.utcnow()
    )
    
    enriched = _enriched_defaults()
    return ProductSummary(
        product_id=f"VIN-{vin}",
        name=name,
        price_cents=int(float(price) * 100),  # Convert dollars to cents
        currency="USD",
        category=body_style,
        brand=make,
        available_qty=1,  # Vehicles are unique items
        product_type="vehicle",
        metadata=vehicle_metadata,
        provenance=provenance,
        shipping=enriched["shipping"],
        return_policy=enriched["return_policy"],
        warranty=enriched["warranty"],
        promotion_info=enriched["promotion_info"],
    )


def vehicle_to_product_detail(vehicle: Dict[str, Any]) -> ProductDetail:
    """
    Convert IDSS vehicle format to MCP ProductDetail format.
    
    Includes full vehicle details as description and metadata.
    """
    # Extract vehicle info
    if "vehicle" in vehicle:
        v = vehicle["vehicle"]
        retail = vehicle.get("retailListing", {})
    else:
        v = vehicle
        retail = vehicle
    
    # Get VIN
    vin = v.get("vin") or vehicle.get("vin") or str(uuid.uuid4())
    
    # Get basic info
    make = v.get("make", "Unknown")
    model = v.get("model", "Unknown")
    year = v.get("year", "")
    trim = v.get("trim", "")
    body_style = v.get("bodyStyle") or v.get("body_style") or v.get("norm_body_type", "Vehicle")
    
    # Get price
    price = retail.get("price") or v.get("price") or vehicle.get("price", 0)
    
    # Get mileage
    mileage = retail.get("miles") or v.get("mileage") or vehicle.get("mileage", 0)
    
    # Get additional details
    fuel_type = v.get("fuel") or v.get("fuel_type", "")
    drivetrain = v.get("drivetrain", "")
    transmission = v.get("transmission", "")
    exterior_color = v.get("exteriorColor") or v.get("exterior_color", "")
    
    # Format name
    name_parts = [str(year), make, model]
    if trim:
        name_parts.append(trim)
    name = " ".join(name_parts).strip()
    
    # Create detailed description
    description_parts = []
    
    if body_style:
        description_parts.append(f"{body_style}")
    if fuel_type:
        description_parts.append(f"{fuel_type}")
    if drivetrain:
        description_parts.append(f"{drivetrain}")
    if transmission:
        description_parts.append(f"{transmission}")
    if mileage:
        description_parts.append(f"{int(mileage):,} miles")
    if exterior_color:
        description_parts.append(f"{exterior_color}")
    
    description = " • ".join(description_parts)
    
    # Build comprehensive vehicle metadata
    vehicle_metadata = {
        "vin": vin,
        "year": year,
        "make": make,
        "model": model,
        "body_style": body_style,
    }
    
    # Add optional fields
    if trim:
        vehicle_metadata["trim"] = trim
    if mileage:
        vehicle_metadata["mileage"] = int(mileage)
    if fuel_type:
        vehicle_metadata["fuel_type"] = fuel_type
    if drivetrain:
        vehicle_metadata["drivetrain"] = drivetrain
    if transmission:
        vehicle_metadata["transmission"] = transmission
    if exterior_color:
        vehicle_metadata["exterior_color"] = exterior_color
    
    # Add dealer information if available
    dealer = retail.get("dealer")
    dealer_city = retail.get("city")
    dealer_state = retail.get("state")
    if dealer:
        vehicle_metadata["dealer"] = dealer
    if dealer_city and dealer_state:
        vehicle_metadata["location"] = f"{dealer_city}, {dealer_state}"
    
    # Add image if available
    primary_image = retail.get("primaryImage")
    if primary_image:
        vehicle_metadata["primary_image"] = primary_image
    
    # Add VDP URL if available
    vdp_url = retail.get("vdp")
    if vdp_url:
        vehicle_metadata["listing_url"] = vdp_url
    
    # Add provenance tracking
    provenance = ProvenanceInfo(
        source="idss_sqlite",
        timestamp=datetime.utcnow()
    )
    enriched = _enriched_defaults()
    return ProductDetail(
        product_id=f"VIN-{vin}",
        name=name,
        description=description,
        category=body_style,
        brand=make,
        price_cents=int(float(price) * 100),
        currency="USD",
        available_qty=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        product_type="vehicle",
        metadata=vehicle_metadata,
        provenance=provenance,
        shipping=enriched["shipping"],
        return_policy=enriched["return_policy"],
        warranty=enriched["warranty"],
        promotion_info=enriched["promotion_info"],
    )


# ============================================================================
# Real Estate Transformations
# ============================================================================

def property_to_product_summary(property_data: Dict[str, Any]) -> ProductSummary:
    """Convert real estate property to ProductSummary."""
    address = property_data["address"]
    name = f"{address['street']}, {address['city']}, {address['state']} {address['zip']}"
    
    # Build metadata
    metadata = {
        "mls_id": property_data["mls_id"],
        "bedrooms": property_data["bedrooms"],
        "bathrooms": property_data["bathrooms"],
        "square_feet": property_data["square_feet"],
        "property_type": property_data["property_type"],
        "year_built": property_data.get("year_built"),
        "lot_size": property_data.get("lot_size"),
        "days_on_market": property_data.get("days_on_market", 0),
        "hoa_fees": property_data.get("hoa_fees"),
        "status": property_data.get("status", "active"),
        "listing_agent": property_data.get("listing_agent", {}).get("name"),
        "features": property_data.get("features", [])
    }
    
    # Add provenance tracking
    provenance = ProvenanceInfo(
        source="real_estate_api",
        timestamp=datetime.utcnow()
    )
    
    enriched = _enriched_defaults()
    return ProductSummary(
        product_id=f"PROP-{property_data['property_id'].split('-')[1]}",
        name=name,
        price_cents=int(property_data["price"] * 100),
        currency="USD",
        category=property_data["property_type"],
        brand=None,
        available_qty=1,
        product_type="real_estate",
        metadata=metadata,
        provenance=provenance,
        shipping=enriched["shipping"],
        return_policy=enriched["return_policy"],
        warranty=enriched["warranty"],
        promotion_info=enriched["promotion_info"],
    )


def property_to_product_detail(property_data: Dict[str, Any]) -> ProductDetail:
    """Convert real estate property to ProductDetail."""
    address = property_data["address"]
    name = f"{address['street']}, {address['city']}, {address['state']} {address['zip']}"
    
    # Build description
    desc_parts = [
        f"{property_data['bedrooms']} bed, {property_data['bathrooms']} bath",
        f"{property_data['property_type']}",
        f"{property_data['square_feet']:,} sq ft"
    ]
    if property_data.get("lot_size"):
        desc_parts.append(f"{property_data['lot_size']} acres")
    
    description = property_data.get("description", " • ".join(desc_parts))
    enriched = _enriched_defaults()
    # Build comprehensive metadata
    metadata = {
        "mls_id": property_data["mls_id"],
        "address": address,
        "bedrooms": property_data["bedrooms"],
        "bathrooms": property_data["bathrooms"],
        "square_feet": property_data["square_feet"],
        "property_type": property_data["property_type"],
        "year_built": property_data.get("year_built"),
        "lot_size": property_data.get("lot_size"),
        "days_on_market": property_data.get("days_on_market", 0),
        "hoa_fees": property_data.get("hoa_fees"),
        "status": property_data.get("status", "active"),
        "listing_agent": property_data.get("listing_agent"),
        "features": property_data.get("features", []),
        "images": property_data.get("images", [])
    }
    
    # Add provenance tracking
    provenance = ProvenanceInfo(
        source="real_estate_api",
        timestamp=datetime.utcnow()
    )
    
    return ProductDetail(
        product_id=f"PROP-{property_data['property_id'].split('-')[1]}",
        name=name,
        description=description,
        category=property_data["property_type"],
        brand=None,
        price_cents=int(property_data["price"] * 100),
        currency="USD",
        available_qty=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        product_type="real_estate",
        metadata=metadata,
        provenance=provenance,
        shipping=enriched["shipping"],
        return_policy=enriched["return_policy"],
        warranty=enriched["warranty"],
        promotion_info=enriched["promotion_info"],
    )


# ============================================================================
# Travel Transformations
# ============================================================================

def travel_to_product_summary(travel_data: Dict[str, Any]) -> ProductSummary:
    """Convert travel listing to ProductSummary."""
    dest = travel_data["destination"]
    dest_name = dest.get("city", dest.get("region", "Multi-City"))
    
    # Build metadata
    metadata = {
        "booking_type": travel_data["booking_type"],
        "destination": dest,
        "dates": travel_data.get("dates"),
        "provider": travel_data["provider"],
        "rating": travel_data.get("rating"),
        "reviews_count": travel_data.get("reviews_count"),
        "features": travel_data.get("features", []),
        "details_summary": {}
    }
    
    # Add type-specific summary details
    details = travel_data.get("details", {})
    if travel_data["booking_type"] == "Flight":
        metadata["details_summary"] = {
            "airline": details.get("airline"),
            "duration": details.get("duration"),
            "cabin_class": details.get("cabin_class")
        }
    elif travel_data["booking_type"] == "Hotel":
        metadata["details_summary"] = {
            "hotel_class": details.get("hotel_class"),
            "room_type": details.get("room_type")
        }
    elif travel_data["booking_type"] == "Package":
        metadata["details_summary"] = {
            "package_type": details.get("package_type")
        }
    
    # Add provenance tracking
    provenance = ProvenanceInfo(
        source="travel_api",
        timestamp=datetime.utcnow()
    )
    enriched = _enriched_defaults()
    return ProductSummary(
        product_id=travel_data["booking_id"],
        name=travel_data["name"],
        price_cents=int(travel_data["price"] * 100),
        currency=travel_data.get("currency", "USD"),
        category=travel_data["booking_type"],
        brand=travel_data["provider"],
        available_qty=1,
        product_type="travel",
        metadata=metadata,
        provenance=provenance,
        shipping=enriched["shipping"],
        return_policy=enriched["return_policy"],
        warranty=enriched["warranty"],
        promotion_info=enriched["promotion_info"],
    )


def travel_to_product_detail(travel_data: Dict[str, Any]) -> ProductDetail:
    """Convert travel listing to ProductDetail."""
    dest = travel_data["destination"]
    dest_name = dest.get("city", dest.get("region", "Multi-City"))
    
    # Build comprehensive metadata
    metadata = {
        "booking_type": travel_data["booking_type"],
        "destination": dest,
        "dates": travel_data.get("dates"),
        "provider": travel_data["provider"],
        "rating": travel_data.get("rating"),
        "reviews_count": travel_data.get("reviews_count"),
        "features": travel_data.get("features", []),
        "details": travel_data.get("details", {}),
        "images": travel_data.get("images", []),
        "availability": travel_data.get("availability", "available")
    }
    
    # Add provenance tracking
    provenance = ProvenanceInfo(
        source="travel_api",
        timestamp=datetime.utcnow()
    )
    enriched = _enriched_defaults()
    return ProductDetail(
        product_id=travel_data["booking_id"],
        name=travel_data["name"],
        description=travel_data["description"],
        category=travel_data["booking_type"],
        brand=travel_data["provider"],
        price_cents=int(travel_data["price"] * 100),
        currency=travel_data.get("currency", "USD"),
        available_qty=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        product_type="travel",
        metadata=metadata,
        provenance=provenance,
        shipping=enriched["shipping"],
        return_policy=enriched["return_policy"],
        warranty=enriched["warranty"],
        promotion_info=enriched["promotion_info"],
    )


# ============================================================================
# Generic Product Search (Multi-Backend Support)
# ============================================================================

async def search_products_universal(
    request: SearchProductsRequest,
    backend: BackendType = DEFAULT_BACKEND,
    product_type: Optional[str] = None
) -> SearchProductsResponse:
    """
    Universal product search that routes to appropriate backend.
    
    Supports:
    - IDSS: Vehicle recommendations with AI-powered ranking
    - Postgres: E-commerce product catalog
    - Real Estate: Property listings
    - Travel: Flight, hotel, package bookings
    """
    request_id = str(uuid.uuid4())
    start_time = time.time()
    
    timings = {}
    backend_config = BACKEND_CONFIGS.get(backend, BACKEND_CONFIGS[DEFAULT_BACKEND])
    category = backend_config["category"]
    sources = []  # Will be set by specific search function
    cache_hit = False
    
    # Route based on product_type if specified, otherwise use backend
    if product_type:
        if product_type.lower() == "vehicle":
            return await _search_vehicles_mcp(request, request_id, start_time, timings, sources, cache_hit)
        elif product_type.lower() == "real_estate":
            return await _search_real_estate(request, request_id, start_time, timings, sources, cache_hit)
        elif product_type.lower() == "travel":
            return await _search_travel(request, request_id, start_time, timings, sources, cache_hit)
    
    # Route based on backend
    if backend == BackendType.IDSS:
        return await _search_vehicles_mcp(request, request_id, start_time, timings, sources, cache_hit)
    elif backend == BackendType.POSTGRES:
        # Future: Implement e-commerce search from database
        pass
    else:
        raise ValueError(f"Unsupported backend: {backend}")


async def _search_vehicles_mcp(
    request: SearchProductsRequest,
    request_id: str,
    start_time: float,
    timings: dict,
    sources: List[str],
    cache_hit: bool
) -> SearchProductsResponse:
    """
    Search for vehicles using MCP vehicle_search tool (Supabase). No IDSS server (8000).
    """
    sources.append("supabase")
    try:
        from app.tools.vehicle_search import search_vehicles, VehicleSearchRequest

        filters = request.filters or {}
        vehicle_filters = {k: v for k, v in filters.items() if k != "_soft_preferences"}
        if filters.get("price_max_cents") or filters.get("price_min_cents"):
            pmin = (filters.get("price_min_cents") or 0) // 100
            pmax = (filters.get("price_max_cents") or 999999) // 100
            vehicle_filters["price"] = f"{pmin}-{pmax}"
        n = max(1, min(request.limit or 9, 50))
        n_rows = 3
        n_per_row = max(1, (n + n_rows - 1) // n_rows)

        result = search_vehicles(VehicleSearchRequest(
            filters=vehicle_filters,
            preferences=filters.get("_soft_preferences") or {},
            method="embedding_similarity",
            n_rows=n_rows,
            n_per_row=n_per_row,
            limit=n * 3,
        ))
        products = []
        for row in result.recommendations:
            for v in row:
                try:
                    products.append(vehicle_to_product_summary(v))
                except Exception:
                    pass
        timings["total"] = (time.time() - start_time) * 1000
        return SearchProductsResponse(
            status=ResponseStatus.OK,
            data=SearchResultsData(products=products, total_count=len(products), next_cursor=None),
            constraints=[],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info(ProductCategory.VEHICLE)
        )
    except Exception as e:
        timings["total"] = (time.time() - start_time) * 1000
        return SearchProductsResponse(
            status=ResponseStatus.INVALID,
            data=SearchResultsData(products=[], total_count=0, next_cursor=None),
            constraints=[
                ConstraintDetail(
                    code="BACKEND_CONNECTION_ERROR",
                    message=f"Vehicle search failed: {str(e)}",
                    details={"error": str(e), "backend": "supabase"},
                    allowed_fields=None,
                    suggested_actions=["Check Supabase configuration", "Retry search"],
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info(ProductCategory.VEHICLE)
        )


async def _search_real_estate(
    request: SearchProductsRequest,
    request_id: str,
    start_time: float,
    timings: dict,
    sources: List[str],
    cache_hit: bool
) -> SearchProductsResponse:
    """Search real estate properties via backend API."""
    sources.append("real_estate_api")  # Provenance tracking
    try:
        # Build query parameters
        params = {
            "q": request.query or "",
            "limit": request.limit
        }
        
        # Add filters
        if request.filters:
            if "price_min" in request.filters:
                params["min_price"] = request.filters["price_min"]
            if "price_max" in request.filters:
                params["max_price"] = request.filters["price_max"]
            if "category" in request.filters:
                params["property_type"] = request.filters["category"]
        
        # Call real estate backend
        backend_start = time.time()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "http://localhost:9000/api/search",
                params=params
            )
            response.raise_for_status()
            data = response.json()
        
        timings["backend"] = (time.time() - backend_start) * 1000
        
        # Convert properties to product summaries
        properties = data.get("listings", [])
        product_summaries = [property_to_product_summary(p) for p in properties]
        
        timings["total"] = (time.time() - start_time) * 1000
        
        return SearchProductsResponse(
            status=ResponseStatus.OK,
            data=SearchResultsData(
                products=product_summaries,
                total_count=len(product_summaries),
                next_cursor=None
            ),
            constraints=[],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info(ProductCategory.REAL_ESTATE)
        )
        
    except httpx.HTTPError as e:
        timings["total"] = (time.time() - start_time) * 1000
        
        return SearchProductsResponse(
            status=ResponseStatus.INVALID,
            data=SearchResultsData(products=[], total_count=0, next_cursor=None),
            constraints=[
                ConstraintDetail(
                    code="BACKEND_CONNECTION_ERROR",
                    message=f"Could not connect to Real Estate backend: {str(e)}",
                    details={"error": str(e), "backend": "real_estate"},
                    allowed_fields=None,
                    suggested_actions=[
                        "Ensure Real Estate backend is running on port 9000",
                        "Check backend logs for errors"
                    ]
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info(ProductCategory.REAL_ESTATE)
        )


async def _search_travel(
    request: SearchProductsRequest,
    request_id: str,
    start_time: float,
    timings: dict,
    sources: List[str],
    cache_hit: bool
) -> SearchProductsResponse:
    """Search travel listings via backend API."""
    sources.append("travel_api")  # Provenance tracking
    try:
        # Build query parameters
        params = {
            "q": request.query or "",
            "limit": request.limit
        }
        
        # Add filters
        if request.filters:
            if "price_min" in request.filters:
                params["min_price"] = request.filters["price_min"]
            if "price_max" in request.filters:
                params["max_price"] = request.filters["price_max"]
            if "category" in request.filters:
                # Category maps to booking_type for travel
                params["booking_type"] = request.filters["category"]
        
        # Call travel backend
        backend_start = time.time()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "http://localhost:9001/api/search",
                params=params
            )
            response.raise_for_status()
            data = response.json()
        
        timings["backend"] = (time.time() - backend_start) * 1000
        
        # Convert travel listings to product summaries
        listings = data.get("listings", [])
        product_summaries = [travel_to_product_summary(t) for t in listings]
        
        timings["total"] = (time.time() - start_time) * 1000
        
        return SearchProductsResponse(
            status=ResponseStatus.OK,
            data=SearchResultsData(
                products=product_summaries,
                total_count=len(product_summaries),
                next_cursor=None
            ),
            constraints=[],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info(ProductCategory.TRAVEL)
        )
        
    except httpx.HTTPError as e:
        timings["total"] = (time.time() - start_time) * 1000
        
        return SearchProductsResponse(
            status=ResponseStatus.INVALID,
            data=SearchResultsData(products=[], total_count=0, next_cursor=None),
            constraints=[
                ConstraintDetail(
                    code="BACKEND_CONNECTION_ERROR",
                    message=f"Could not connect to Travel backend: {str(e)}",
                    details={"error": str(e), "backend": "travel"},
                    allowed_fields=None,
                    suggested_actions=[
                        "Ensure Travel backend is running on port 9001",
                        "Check backend logs for errors"
                    ]
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info(ProductCategory.TRAVEL)
        )


# ============================================================================
# Product Detail Retrieval
# ============================================================================

async def get_product_universal(
    request: GetProductRequest,
    backend: BackendType = DEFAULT_BACKEND
) -> GetProductResponse:
    """
    Universal product detail retrieval for non-vehicle backends.
    
    Routes to appropriate backend based on product_id prefix:
    - PROP-*: Real estate
    - BOOK-*: Travel
    - VIN-* and other IDs: NOT_FOUND (vehicles are served by main get_product via cache + Supabase).
    """
    request_id = str(uuid.uuid4())
    start_time = time.time()
    timings = {}
    cache_hit = False

    if request.product_id.startswith("PROP-"):
        return await _get_property_detail(request, request_id, start_time, timings, cache_hit)
    elif request.product_id.startswith("BOOK-"):
        return await _get_travel_detail(request, request_id, start_time, timings, cache_hit)
    else:
        # VIN- and e-commerce: handled by main get_product (cache + Supabase or Postgres)
        timings["total"] = (time.time() - start_time) * 1000
        return GetProductResponse(
            status=ResponseStatus.NOT_FOUND,
            data=None,
            constraints=[
                ConstraintDetail(
                    code="PRODUCT_NOT_FOUND",
                    message=f"Product '{request.product_id}' not found. Use /api/get-product or UCP get-product for vehicles and e-commerce.",
                    details={"product_id": request.product_id},
                    allowed_fields=None,
                    suggested_actions=["Use /api/get-product or /ucp/get-product for this product type"],
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, []),
            version=create_version_info(),
        )


async def _get_property_detail(
    request: GetProductRequest,
    request_id: str,
    start_time: float,
    timings: dict,
    cache_hit: bool
) -> GetProductResponse:
    """Get property details from real estate backend."""
    sources = ["real_estate_api"]  # Provenance tracking
    
    try:
        # Extract property ID
        property_id = request.product_id  # PROP-001
        
        api_start = time.time()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"http://localhost:9000/api/properties/{property_id}"
            )
            response.raise_for_status()
            data = response.json()
        
        timings["api"] = (time.time() - api_start) * 1000
        
        property_data = data.get("property")
        if not property_data:
            timings["total"] = (time.time() - start_time) * 1000
            
            return GetProductResponse(
                status=ResponseStatus.NOT_FOUND,
                data=None,
                constraints=[
                    ConstraintDetail(
                        code="PROPERTY_NOT_FOUND",
                        message=f"Property with ID '{request.product_id}' does not exist",
                        details={"product_id": request.product_id},
                        allowed_fields=None,
                        suggested_actions=["SearchProducts to find available properties"]
                    )
                ],
                trace=create_trace(request_id, cache_hit, timings, sources),
                version=create_version_info(ProductCategory.REAL_ESTATE)
            )
        
        # Convert property to product detail
        product_detail = property_to_product_detail(property_data)
        
        # Apply field projection if requested
        if request.fields:
            product_detail = apply_field_projection(product_detail, request.fields)
        
        timings["total"] = (time.time() - start_time) * 1000
        
        return GetProductResponse(
            status=ResponseStatus.OK,
            data=product_detail,
            constraints=[],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info(ProductCategory.REAL_ESTATE)
        )
        
    except httpx.HTTPError as e:
        timings["total"] = (time.time() - start_time) * 1000
        
        return GetProductResponse(
            status=ResponseStatus.INVALID,
            data=None,
            constraints=[
                ConstraintDetail(
                    code="API_ERROR",
                    message=f"Error retrieving property: {str(e)}",
                    details={"error": str(e), "product_id": request.product_id},
                    allowed_fields=None,
                    suggested_actions=["Ensure Real Estate backend is running on port 9000"]
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info(ProductCategory.REAL_ESTATE)
        )


async def _get_travel_detail(
    request: GetProductRequest,
    request_id: str,
    start_time: float,
    timings: dict,
    cache_hit: bool
) -> GetProductResponse:
    """Get travel booking details from travel backend."""
    sources = ["travel_api"]  # Provenance tracking
    
    try:
        # Extract booking ID
        booking_id = request.product_id  # BOOK-FL-001
        
        api_start = time.time()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"http://localhost:9001/api/bookings/{booking_id}"
            )
            response.raise_for_status()
            data = response.json()
        
        timings["api"] = (time.time() - api_start) * 1000
        
        travel_data = data.get("booking")
        if not travel_data:
            timings["total"] = (time.time() - start_time) * 1000
            
            return GetProductResponse(
                status=ResponseStatus.NOT_FOUND,
                data=None,
                constraints=[
                    ConstraintDetail(
                        code="BOOKING_NOT_FOUND",
                        message=f"Travel booking with ID '{request.product_id}' does not exist",
                        details={"product_id": request.product_id},
                        allowed_fields=None,
                        suggested_actions=["SearchProducts to find available travel options"]
                    )
                ],
                trace=create_trace(request_id, cache_hit, timings, sources),
                version=create_version_info(ProductCategory.TRAVEL)
            )
        
        # Convert travel booking to product detail
        product_detail = travel_to_product_detail(travel_data)
        
        # Apply field projection if requested
        if request.fields:
            product_detail = apply_field_projection(product_detail, request.fields)
        
        timings["total"] = (time.time() - start_time) * 1000
        
        return GetProductResponse(
            status=ResponseStatus.OK,
            data=product_detail,
            constraints=[],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info(ProductCategory.TRAVEL)
        )
        
    except httpx.HTTPError as e:
        timings["total"] = (time.time() - start_time) * 1000
        
        return GetProductResponse(
            status=ResponseStatus.INVALID,
            data=None,
            constraints=[
                ConstraintDetail(
                    code="API_ERROR",
                    message=f"Error retrieving travel booking: {str(e)}",
                    details={"error": str(e), "product_id": request.product_id},
                    allowed_fields=None,
                    suggested_actions=["Ensure Travel backend is running on port 9001"]
                )
            ],
            trace=create_trace(request_id, cache_hit, timings, sources),
            version=create_version_info(ProductCategory.TRAVEL)
        )


# ============================================================================
# Convenience Aliases (Backward Compatibility)
# ============================================================================

async def search_products_idss(request: SearchProductsRequest) -> SearchProductsResponse:
    """Convenience function for IDSS vehicle search."""
    return await search_products_universal(request, backend=BackendType.IDSS)


async def get_product_idss(request: GetProductRequest) -> GetProductResponse:
    """Convenience function for IDSS vehicle detail."""
    return await get_product_universal(request, backend=BackendType.IDSS)
