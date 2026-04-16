"""
SQLAlchemy database models.
These are the authoritative source of truth for all e-commerce data.

Postgres is authoritative for:
- Products (canonical attributes)
- Prices (current pricing)
- Inventory (stock levels)
- Carts and cart items
- Orders and checkout state

Per-merchant catalogs live in the `merchants` schema as
``merchants.products_<id>`` + ``merchants.products_enriched_<id>``. The
``Product`` / ``ProductEnriched`` module-level classes are the default
merchant's mapping, kept as stable import names for the call sites that
haven't been threaded through ``make_product_model(merchant_id)`` yet.
"""
from typing import Any, Dict

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY  # noqa: F401  (re-exported)
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.sql import func

from app.database import Base

_SCHEMA = "merchants"


# ---------------------------------------------------------------------------
# Column definitions — returned fresh per call so every per-merchant model
# gets its own Column instances (SQLAlchemy binds a Column to one Table).
# ---------------------------------------------------------------------------

def _product_columns() -> Dict[str, Any]:
    return dict(
        # Supabase uses 'id' (UUID); Python attribute stays `product_id`.
        product_id=Column("id", PG_UUID(as_uuid=True), primary_key=True, index=True),
        # Supabase uses 'title'; Python attribute stays `name`.
        name=Column("title", Text, nullable=True, index=True),
        category=Column(String(100), index=True),
        brand=Column(String(100)),
        source=Column(String(100), index=True),
        # Supabase stores price in dollars directly on the products row.
        price_value=Column("price", Numeric, nullable=True),
        # Supabase uses 'imageurl' (no underscore).
        image_url=Column("imageurl", Text, nullable=True),
        product_type=Column(String(50), index=True),
        series=Column(String(255), nullable=True),
        model=Column(String(255), nullable=True),
        link=Column(Text, nullable=True),
        rating=Column(Numeric, nullable=True),
        rating_count=Column(BigInteger, nullable=True),
        ref_id=Column(String(255), nullable=True),
        variant=Column(String(255), nullable=True),
        inventory=Column(BigInteger, nullable=True),
        release_year=Column(SmallInteger, nullable=True),
        delivery_promise=Column(Text, nullable=True),
        return_policy=Column(Text, nullable=True),
        warranty=Column(Text, nullable=True),
        promotions_discounts=Column(Text, nullable=True),
        merchant_product_url=Column(Text, nullable=True),
        attributes=Column(PG_JSONB, nullable=True),
        # Per-merchant scoping column (NULL = legacy/default catalog).
        merchant_id=Column(String, nullable=True, index=True),
        created_at=Column(DateTime(timezone=True), server_default=func.now()),
        updated_at=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
    )


def _enriched_columns(raw_table_fqn: str) -> Dict[str, Any]:
    return dict(
        product_id=Column(
            PG_UUID(as_uuid=True),
            ForeignKey(f"{raw_table_fqn}.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        strategy=Column(Text, primary_key=True),
        attributes=Column(PG_JSONB, nullable=False, default=dict),
        model=Column(Text, nullable=True),
        updated_at=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
    )


# ---------------------------------------------------------------------------
# Mixin: compatibility @property helpers shared by every per-merchant model.
# Lives on a non-mapped mixin so the factory can combine it with fresh
# column dicts at each call site.
# ---------------------------------------------------------------------------

class _ProductProperties:
    """Backwards-compatible accessors for fields that live inside ``attributes``."""

    @property
    def description(self):
        """Raw scraped description from attributes JSON.

        The normalized form lives in ``products_enriched_<id>`` under
        strategy='normalizer_v1' and is read via ``enriched_reader.hydrate_batch``
        in the merchant-agent search path — not from the raw attributes here.
        """
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


# ---------------------------------------------------------------------------
# Default-merchant mapped classes.
#
# Built via ``type()`` from the same column-dict factory used by
# ``make_product_model`` / ``make_enriched_model`` so every per-merchant
# model shares a single column definition. Call sites that haven't been
# threaded through the factory yet import these names directly.
# ---------------------------------------------------------------------------

Product = type(
    "Product",
    (_ProductProperties, Base),
    {
        "__tablename__": "products_default",
        "__table_args__": {"extend_existing": True, "schema": _SCHEMA},
        **_product_columns(),
    },
)

ProductEnriched = type(
    "ProductEnriched",
    (Base,),
    {
        "__tablename__": "products_enriched_default",
        "__table_args__": {"extend_existing": True, "schema": _SCHEMA},
        **_enriched_columns(f"{_SCHEMA}.products_default"),
    },
)


# ---------------------------------------------------------------------------
# Factories.
# ---------------------------------------------------------------------------

_product_model_cache: Dict[str, Any] = {"default": Product}
_enriched_model_cache: Dict[str, Any] = {"default": ProductEnriched}


def _validate(merchant_id: str) -> str:
    # Lazy import — ``app.merchant_agent`` top-level imports
    # ``app.endpoints``, which top-level imports this module. Importing
    # merchant_agent at module load would create a cycle.
    from app.merchant_agent import validate_merchant_id
    return validate_merchant_id(merchant_id)


def make_product_model(merchant_id: str):
    """Return the SQLAlchemy Product model mapped to ``merchants.products_<id>``.

    Cached per merchant_id — repeated calls return the same class so
    SQLAlchemy's metadata stays consistent under server reload.
    """
    cached = _product_model_cache.get(merchant_id)
    if cached is not None:
        return cached
    merchant_id = _validate(merchant_id)
    attrs = {
        "__tablename__": f"products_{merchant_id}",
        "__table_args__": {"extend_existing": True, "schema": _SCHEMA},
        **_product_columns(),
    }
    cls = type(f"Product_{merchant_id}", (_ProductProperties, Base), attrs)
    _product_model_cache[merchant_id] = cls
    return cls


def make_enriched_model(merchant_id: str):
    """Return the SQLAlchemy ProductEnriched model for this merchant."""
    cached = _enriched_model_cache.get(merchant_id)
    if cached is not None:
        return cached
    merchant_id = _validate(merchant_id)
    raw_fqn = f"{_SCHEMA}.products_{merchant_id}"
    attrs = {
        "__tablename__": f"products_enriched_{merchant_id}",
        "__table_args__": {"extend_existing": True, "schema": _SCHEMA},
        **_enriched_columns(raw_fqn),
    }
    cls = type(f"ProductEnriched_{merchant_id}", (Base,), attrs)
    _enriched_model_cache[merchant_id] = cls
    return cls


# ---------------------------------------------------------------------------
# Stub classes for code that imports Price/Inventory/Cart/CartItem/Order.
# These tables don't exist in Supabase — stubs prevent import errors.
# ---------------------------------------------------------------------------

class Price:
    """Stub — Supabase stores price directly on products."""
    pass


class Inventory:
    """Stub — Supabase stores inventory directly on products."""
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
