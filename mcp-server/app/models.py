"""
SQLAlchemy database models.
These are the authoritative source of truth for all e-commerce data.

Postgres is authoritative for:
- Products (canonical attributes)
- Prices (current pricing)
- Inventory (stock levels)
- Carts and cart items
- Orders and checkout state
"""

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, JSON, Index, UniqueConstraint, Numeric, BigInteger, SmallInteger
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY, UUID as PG_UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Product(Base):
    """
    Product catalog - maps to Supabase 'products' table.
    Column('db_name', ...) maps Supabase column names to Python attribute names
    so existing code continues to work without changes.
    """
    __tablename__ = "products"
    __table_args__ = {"extend_existing": True}

    # Primary identifier — Supabase uses 'id' (UUID), we keep attribute name 'product_id'
    product_id = Column("id", PG_UUID(as_uuid=True), primary_key=True, index=True)

    # Basic product information — Supabase uses 'title', we keep attribute name 'name'
    name = Column("title", Text, nullable=True, index=True)
    category = Column(String(100), index=True)
    brand = Column(String(100))
    source = Column(String(100), index=True)

    # Supabase has price directly on products (decimal dollars, not cents)
    price_value = Column("price", Numeric, nullable=True)

    # Supabase uses 'imageurl' (no underscore)
    image_url = Column("imageurl", Text, nullable=True)

    # Product type for filtering (laptop, book, etc.)
    product_type = Column(String(50), index=True)

    # Supabase columns that exist
    series = Column(String(255), nullable=True)
    model = Column(String(255), nullable=True)
    link = Column(Text, nullable=True)
    rating = Column(Numeric, nullable=True)
    rating_count = Column(BigInteger, nullable=True)
    ref_id = Column(String(255), nullable=True)
    variant = Column(String(255), nullable=True)
    inventory = Column(BigInteger, nullable=True)
    release_year = Column(SmallInteger, nullable=True)
    delivery_promise = Column(Text, nullable=True)
    return_policy = Column(Text, nullable=True)
    warranty = Column(Text, nullable=True)
    promotions_discounts = Column(Text, nullable=True)
    merchant_product_url = Column(Text, nullable=True)
    attributes = Column(JSON, nullable=True)  # JSONB with product specs

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Compatibility properties for code that expects old field names
    @property
    def description(self):
        """Extract description from attributes JSON if available."""
        if self.attributes and isinstance(self.attributes, dict):
            return self.attributes.get("description")
        return None

    @property
    def subcategory(self):
        """Supabase has no subcategory column; return None."""
        return None

    @property
    def color(self):
        if self.attributes and isinstance(self.attributes, dict):
            return self.attributes.get("color")
        return None

    @property
    def gpu_vendor(self):
        if self.attributes and isinstance(self.attributes, dict):
            return self.attributes.get("gpu_vendor")
        return None

    @property
    def gpu_model(self):
        if self.attributes and isinstance(self.attributes, dict):
            return self.attributes.get("gpu_model")
        return None

    @property
    def tags(self):
        if self.attributes and isinstance(self.attributes, dict):
            return self.attributes.get("tags")
        return None

    @property
    def reviews(self):
        if self.attributes and isinstance(self.attributes, dict):
            return self.attributes.get("reviews")
        return None

    @property
    def kg_features(self):
        if self.attributes and isinstance(self.attributes, dict):
            return self.attributes.get("kg_features")
        return None


# Stub classes for code that imports Price/Inventory/Cart/CartItem/Order
# These tables don't exist in Supabase — stubs prevent import errors

class Price:
    """Stub — Supabase stores price directly on products table."""
    pass

class Inventory:
    """Stub — Supabase stores inventory directly on products table."""
    pass

class Cart:
    """Stub — cart table in Supabase has different schema."""
    pass

class CartItem:
    """Stub — not used in Supabase."""
    pass

class Order:
    """Stub — not used in Supabase."""
    pass
