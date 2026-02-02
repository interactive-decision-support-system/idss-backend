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

from sqlalchemy import Column, String, Integer, Numeric, DateTime, ForeignKey, Text, Boolean, ARRAY, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Product(Base):
    """
    Product catalog - the canonical source of truth for product attributes.
    Structured fields (product_type, gpu_vendor) enable hard-filtering so "gaming PC NVIDIA"
    does not return MacBooks.
    """
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_category_product_type", "category", "product_type"),
        Index("ix_products_category_brand", "category", "brand"),
        Index("ix_products_category_color", "category", "color"),
        Index("ix_products_category_gpu_vendor", "category", "gpu_vendor"),
    )

    # Primary identifier - always use this for cart/checkout operations
    product_id = Column(String(50), primary_key=True, index=True)

    # Basic product information
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    category = Column(String(100), index=True)
    subcategory = Column(String(100), index=True)  # For books: genre (Mystery, Sci-Fi, etc.)
    brand = Column(String(100))
    source = Column(String(100), index=True)  # Platform: "WooCommerce", "Shopify", "Temu", "Seed", etc.
    color = Column(String(80))
    scraped_from_url = Column(String(512), index=True)  # URL we scraped from. Null for Seed.
    reviews = Column(Text)

    # Structured specs for hard-filtering (NVIDIA gaming PC must not return laptops without GPU)
    product_type = Column(String(50), index=True)  # laptop, desktop_pc, gaming_laptop, book, etc.
    gpu_vendor = Column(String(50), index=True)  # NVIDIA, AMD, Apple, Intel; NULL = unknown (must not pass when required)
    gpu_model = Column(String(100))
    tags = Column(ARRAY(Text))  # e.g. ["gaming"]
    image_url = Column(String(512))
    source_product_id = Column(String(255))  # Stable id per source for upsert; NULL for seed.

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    price_info = relationship("Price", back_populates="product", uselist=False)
    inventory_info = relationship("Inventory", back_populates="product", uselist=False)


class Price(Base):
    """
    Authoritative pricing information.
    Redis may cache this, but Postgres is the source of truth.
    """
    __tablename__ = "prices"
    __table_args__ = (Index("ix_prices_product_id_price_cents", "product_id", "price_cents"),)

    price_id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String(50), ForeignKey("products.product_id"), nullable=False, unique=True, index=True)
    
    # Pricing in cents to avoid floating point issues
    price_cents = Column(Integer, nullable=False)
    currency = Column(String(3), default="USD")
    
    # Track when price was last updated
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationship
    product = relationship("Product", back_populates="price_info")


class Inventory(Base):
    """
    Authoritative inventory levels.
    Redis may cache this, but Postgres is the source of truth.
    """
    __tablename__ = "inventory"
    
    inventory_id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String(50), ForeignKey("products.product_id"), nullable=False, unique=True, index=True)
    
    # Available quantity
    available_qty = Column(Integer, nullable=False, default=0)
    
    # Reserved quantity (in carts but not yet checked out)
    reserved_qty = Column(Integer, nullable=False, default=0)
    
    # Track when inventory was last updated
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationship
    product = relationship("Product", back_populates="inventory_info")


class Cart(Base):
    """
    Shopping cart state.
    """
    __tablename__ = "carts"
    
    cart_id = Column(String(50), primary_key=True, index=True)
    
    # Cart status: active, abandoned, checked_out
    status = Column(String(20), default="active", index=True)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    items = relationship("CartItem", back_populates="cart", cascade="all, delete-orphan")


class CartItem(Base):
    """
    Individual items in a shopping cart.
    Note: Only accepts product_id, never product name (IDs-only execution rule).
    Unique (cart_id, product_id) for upsert / idempotent AddToCart.
    """
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("cart_id", "product_id", name="ux_cart_items_cart_product"),)

    cart_item_id = Column(Integer, primary_key=True, autoincrement=True)
    cart_id = Column(String(50), ForeignKey("carts.cart_id"), nullable=False, index=True)
    
    # IDs-only execution: we only store product_id, never free-text product names
    product_id = Column(String(50), ForeignKey("products.product_id"), nullable=False)
    
    quantity = Column(Integer, nullable=False)
    
    # Metadata
    added_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    cart = relationship("Cart", back_populates="items")
    product = relationship("Product")


class Order(Base):
    """
    Completed orders from checkout.
    Includes logistics/shipping (week4notes.txt: shipping time, cost, location; synthetic OK).
    """
    __tablename__ = "orders"
    
    order_id = Column(String(50), primary_key=True, index=True)
    cart_id = Column(String(50), ForeignKey("carts.cart_id"), nullable=False)
    
    # Checkout information (IDs only, not actual payment processing)
    payment_method_id = Column(String(50), nullable=False)
    address_id = Column(String(50), nullable=False)
    
    # Order total in cents
    total_cents = Column(Integer, nullable=False)
    currency = Column(String(3), default="USD")
    
    # Order status: pending, confirmed, shipped, delivered, cancelled
    status = Column(String(20), default="pending", index=True)
    
    # Logistics/shipping (per week4notes.txt; may be synthetic)
    shipping_method = Column(String(50), nullable=True)       # e.g. standard, express
    estimated_delivery_days = Column(Integer, nullable=True)   # days to delivery
    shipping_cost_cents = Column(Integer, nullable=True)      # shipping cost in cents
    shipping_region = Column(String(20), nullable=True)      # e.g. US, EU
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationship
    cart = relationship("Cart")
