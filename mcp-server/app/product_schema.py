"""
Standardized Product Schema
===========================
Domain-agnostic Pydantic model for all product types in the IDSS catalog.

Domain-specific specs are held as typed Optional fields rather than
raw JSONB so callers get autocomplete and validation.  When written to
Supabase the spec fields are serialised into the ``attributes`` JSONB
column (via ``to_attributes_dict()``), and the handful of top-level
columns (title, price, brand, …) go into the matching Product ORM fields.

Supported product types
-----------------------
  Electronics: "laptop", "phone", "tablet", "camera"
  Books:       "book"
  Vehicles:    "vehicle", "car", "truck", "suv"
  Other:       anything — unknown fields land in ``extra_attributes``

User reviews
------------
``reviews`` is a ``List[str]`` — a list of short review snippets
(1-2 sentences each).  They may be real scraped reviews or LLM-generated
summaries.  Stored in ``attributes["reviews"]``.

Usage
-----
    from app.product_schema import ProductSchema

    p = ProductSchema(
        title="Sony Alpha A7 IV",
        product_type="camera",
        price=2498.00,
        brand="Sony",
        megapixels=33.0,
        sensor_type="Full Frame",
        reviews=["Great dynamic range.", "Excellent autofocus for video."],
    )
    row = p.to_product_row()  # dict ready for Supabase upsert
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Core schema
# ---------------------------------------------------------------------------

class ProductSchema(BaseModel):
    """Canonical product representation for the IDSS catalog.

    Fields are split into three groups:
    1. Top-level DB columns  — written to the ``products`` table directly.
    2. Common spec fields    — stored in ``attributes`` JSONB, shared across
                               multiple product types.
    3. Domain-specific fields — stored in ``attributes`` JSONB, only relevant
                                for one product type (camera, book, vehicle …).
    """

    # ── 1. Top-level DB columns ────────────────────────────────────────────

    title: str
    product_type: str = Field(
        ...,
        description=(
            "One of: laptop, phone, tablet, camera, book, vehicle, car, truck, suv. "
            "Used for category routing and domain-specific filtering."
        ),
    )
    price: Optional[float] = None          # USD, decimal
    brand: Optional[str] = None
    image_url: Optional[str] = None
    rating: Optional[float] = None         # 0–5 scale
    rating_count: Optional[int] = None     # number of ratings
    source: Optional[str] = None           # "amazon", "bestbuy", "csv-cameras", …
    link: Optional[str] = None             # product page URL
    ref_id: Optional[str] = None           # merchant's own product ID

    # ── 2. Common spec fields (written to attributes JSONB) ─────────────────

    description: Optional[str] = None
    normalized_description: Optional[str] = None  # populated via enriched hydration (strategy='normalizer_v1' in merchants.products_enriched_default), not from raw

    # Reviews — list of short snippet strings (real or LLM-generated)
    reviews: List[str] = Field(default_factory=list)

    color: Optional[str] = None
    weight_lbs: Optional[float] = None
    weight_kg: Optional[float] = None
    dimensions: Optional[str] = None       # "W × D × H mm"
    release_year: Optional[int] = None

    # ── 3a. Electronics specs (laptop / phone / tablet / camera) ────────────

    cpu: Optional[str] = None              # e.g. "Intel Core i7-13700H"
    gpu: Optional[str] = None              # e.g. "NVIDIA RTX 4060"
    ram_gb: Optional[int] = None
    storage_gb: Optional[int] = None
    storage_type: Optional[str] = None     # "SSD" | "HDD"
    screen_size: Optional[float] = None    # inches (diagonal)
    resolution: Optional[str] = None       # e.g. "1920x1080"
    refresh_rate_hz: Optional[int] = None
    battery_life_hours: Optional[int] = None
    os: Optional[str] = None              # "Windows 11", "macOS", "Android", …

    # ── 3b. Camera-specific ──────────────────────────────────────────────────

    megapixels: Optional[float] = None
    sensor_type: Optional[str] = None     # "Full Frame" | "APS-C" | "Micro Four Thirds" | "1-inch"
    lens_mount: Optional[str] = None      # "Sony E" | "Canon RF" | "Nikon Z" | …
    video_resolution: Optional[str] = None  # "4K 60fps" | "8K 30fps" | …
    image_stabilization: Optional[bool] = None
    weather_sealed: Optional[bool] = None
    burst_fps: Optional[float] = None     # continuous shooting speed

    # ── 3c. Book-specific ────────────────────────────────────────────────────

    genre: Optional[str] = None           # "Fiction" | "Mystery" | "Sci-Fi" | …
    format: Optional[str] = None          # "Hardcover" | "Paperback" | "E-book"
    author: Optional[str] = None
    isbn: Optional[str] = None
    pages: Optional[int] = None
    publisher: Optional[str] = None

    # ── 3d. Vehicle-specific ─────────────────────────────────────────────────

    body_style: Optional[str] = None      # "SUV" | "Sedan" | "Pickup" | …
    fuel_type: Optional[str] = None       # "Gasoline" | "Electric" | "Hybrid" | …
    mileage: Optional[int] = None         # miles (used cars)
    year: Optional[int] = None            # model year
    transmission: Optional[str] = None    # "Automatic" | "Manual"
    drivetrain: Optional[str] = None      # "FWD" | "RWD" | "AWD" | "4WD"
    engine: Optional[str] = None          # e.g. "3.5L V6"

    # ── Catch-all for CSV columns that don't map to known fields ─────────────

    extra_attributes: Dict[str, Any] = Field(default_factory=dict)

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("reviews", mode="before")
    @classmethod
    def coerce_reviews(cls, v: Any) -> List[str]:
        """Accept a list of strings, a newline-delimited string, or None."""
        if v is None:
            return []
        if isinstance(v, str):
            return [line.strip() for line in v.splitlines() if line.strip()]
        if isinstance(v, list):
            return [str(item).strip() for item in v if str(item).strip()]
        return []

    @field_validator("product_type", mode="before")
    @classmethod
    def normalise_product_type(cls, v: Any) -> str:
        """Lower-case and strip the product_type value."""
        return str(v).strip().lower()

    # ── Serialisation helpers ─────────────────────────────────────────────────

    # Spec fields that live in JSONB, keyed by the column name used in Supabase
    _SPEC_FIELDS: tuple[str, ...] = (
        "description", "normalized_description", "reviews",
        "color", "weight_lbs", "weight_kg", "dimensions", "release_year",
        # Electronics
        "cpu", "gpu", "ram_gb", "storage_gb", "storage_type",
        "screen_size", "resolution", "refresh_rate_hz", "battery_life_hours", "os",
        # Camera
        "megapixels", "sensor_type", "lens_mount", "video_resolution",
        "image_stabilization", "weather_sealed", "burst_fps",
        # Book
        "genre", "format", "author", "isbn", "pages", "publisher",
        # Vehicle
        "body_style", "fuel_type", "mileage", "year", "transmission",
        "drivetrain", "engine",
    )

    def to_attributes_dict(self) -> Dict[str, Any]:
        """Build the ``attributes`` JSONB dict from spec fields."""
        attrs: Dict[str, Any] = {}
        for field in self._SPEC_FIELDS:
            val = getattr(self, field, None)
            if val is not None and val != [] and val != {}:
                attrs[field] = val
        attrs.update(self.extra_attributes)
        return attrs

    def to_product_row(self, product_id: Optional[str] = None) -> Dict[str, Any]:
        """Return a dict ready for Supabase upsert into the ``products`` table.

        Top-level ORM columns are mapped to their Supabase column names.
        Everything else goes into ``attributes`` (JSONB).

        Args:
            product_id: UUID string.  Auto-generated if not provided.
        """
        if product_id is None:
            product_id = str(uuid.uuid4())

        # Map product_type → category. Values are lowercase because the
        # search filter in endpoints.search_products compares
        # `Product.category == filters["category"]` case-sensitively and the
        # agent emits lowercase slot values ("electronics").
        _TYPE_TO_CATEGORY: Dict[str, str] = {
            "laptop": "electronics",
            "phone": "electronics",
            "tablet": "electronics",
            "camera": "electronics",
            "book": "books",
            "vehicle": "vehicles",
            "car": "vehicles",
            "truck": "vehicles",
            "suv": "vehicles",
        }
        category = _TYPE_TO_CATEGORY.get(self.product_type, "other")

        return {
            "id": product_id,
            "title": self.title,
            "category": category,
            "product_type": self.product_type,
            "brand": self.brand,
            "price": self.price,
            "imageurl": self.image_url,
            "rating": self.rating,
            "rating_count": self.rating_count,
            "source": self.source,
            "link": self.link,
            "ref_id": self.ref_id,
            "attributes": self.to_attributes_dict(),
        }
