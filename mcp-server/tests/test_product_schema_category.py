"""
Regression tests for #45 — ProductSchema category casing.

Before #45, ``ProductSchema.to_product_row`` emitted capitalised category
values ("Electronics", "Books", "Vehicles") while the agent's slot-to-filter
translation produces lowercase ones ("electronics"). The SQL filter in
``endpoints.search_products`` compares ``Product.category == filters["category"]``
case-sensitively, so every search against a freshly-onboarded merchant
returned zero rows.

These tests lock in the lowercase contract at the schema layer and verify
the end-to-end search round-trip agrees.
"""
from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

from app.product_schema import ProductSchema


# ---------------------------------------------------------------------------
# Unit: _TYPE_TO_CATEGORY emits lowercase
# ---------------------------------------------------------------------------

class TestProductSchemaCategoryLowercase:
    """ProductSchema.to_product_row must emit lowercase category values."""

    @pytest.mark.parametrize("product_type,expected_category", [
        ("laptop", "electronics"),
        ("phone", "electronics"),
        ("tablet", "electronics"),
        ("camera", "electronics"),
        ("book", "books"),
        ("vehicle", "vehicles"),
        ("car", "vehicles"),
        ("truck", "vehicles"),
        ("suv", "vehicles"),
    ])
    def test_known_product_type_maps_to_lowercase_category(
        self, product_type: str, expected_category: str,
    ):
        schema = ProductSchema(title="x", product_type=product_type)
        row = schema.to_product_row()
        assert row["category"] == expected_category, (
            f"product_type={product_type!r} → category={row['category']!r}, "
            f"expected {expected_category!r}. Capitalised values break the "
            f"case-sensitive SQL filter (#45)."
        )

    def test_unknown_product_type_maps_to_other_lowercase(self):
        """Unknown product_types fall back to 'other' — also lowercase."""
        schema = ProductSchema(title="x", product_type="widget")
        row = schema.to_product_row()
        assert row["category"] == "other"

    def test_category_is_never_capitalised(self):
        """Belt-and-braces: every known product_type emits a lowercase string."""
        for pt in (
            "laptop", "phone", "tablet", "camera", "book",
            "vehicle", "car", "truck", "suv", "widget",
        ):
            row = ProductSchema(title="x", product_type=pt).to_product_row()
            assert row["category"] == row["category"].lower()


# ---------------------------------------------------------------------------
# Unit: slot→filter translation (chat_endpoint._domain_to_category)
# ---------------------------------------------------------------------------
#
# Regression: a query for product_type="laptop" must still route to
# category="electronics" on the agent side (not "Electronics").
# ---------------------------------------------------------------------------

def _import_domain_to_category():
    """chat_endpoint lives in the sibling ``agent/`` package; patch sys.path.

    chat_endpoint itself does ``from agent.X import ...``, so the repo root
    (parent of ``agent/``) must be on sys.path, not ``agent/`` itself.
    """
    import os
    import sys
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../.."),
    )
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    # Ensure OpenAI-reliant imports don't choke at import time
    os.environ.setdefault("OPENAI_API_KEY", "test-dummy-key-for-unit-tests")
    try:
        from agent.chat_endpoint import _domain_to_category  # type: ignore
        return _domain_to_category
    except Exception as exc:
        pytest.skip(f"chat_endpoint not importable from this test path: {exc}")


class TestDomainToCategoryLowercase:
    """``_domain_to_category`` must agree with the schema on casing."""

    def test_laptops_domain_routes_to_lowercase_electronics(self):
        _domain_to_category = _import_domain_to_category()
        assert _domain_to_category("laptops") == "electronics"

    def test_unknown_domain_defaults_to_lowercase_electronics(self):
        _domain_to_category = _import_domain_to_category()
        assert _domain_to_category(None) == "electronics"
        assert _domain_to_category("unknown") == "electronics"


# ---------------------------------------------------------------------------
# Integration: ProductSchema → DB insert → search finds the row
# ---------------------------------------------------------------------------
#
# Requires a live Postgres (same gate as test_endpoints.py). Skipped when
# DATABASE_URL is unreachable — see conftest._POSTGRES_REQUIRED_FILES.
# ---------------------------------------------------------------------------

def _postgres_reachable() -> bool:
    url = os.getenv("DATABASE_URL")
    if not url:
        return False
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(url, connect_args={"connect_timeout": 3})
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _postgres_reachable(),
    reason="Postgres not reachable — set DATABASE_URL to run DB integration test",
)
class TestCategoryRoundTrip:
    """End-to-end: insert via ProductSchema, filter by lowercase category."""

    @pytest.fixture
    def inserted_product_id(self) -> Iterator[uuid.UUID]:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database import DATABASE_URL
        from app.models import Product

        engine = create_engine(
            os.getenv("DATABASE_URL") or DATABASE_URL, pool_pre_ping=True,
        )
        Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        pid = uuid.uuid4()
        db = Session()
        try:
            schema = ProductSchema(
                title="Issue45 Test Laptop",
                product_type="laptop",
                price=999.99,
                brand="RegressionCo",
            )
            row = schema.to_product_row(product_id=str(pid))
            db.add(Product(
                product_id=pid,
                name=row["title"],
                category=row["category"],
                product_type=row["product_type"],
                brand=row["brand"],
                price_value=row["price"],
                image_url=row["imageurl"],
                rating=row["rating"],
                rating_count=row["rating_count"],
                source=row["source"],
                link=row["link"],
                ref_id=row["ref_id"],
                attributes=row["attributes"],
            ))
            db.commit()

            yield pid
        finally:
            db.query(Product).filter(Product.product_id == pid).delete(
                synchronize_session=False,
            )
            db.commit()
            db.close()
            engine.dispose()

    def test_row_has_lowercase_category_in_db(self, inserted_product_id):
        """The row written by ProductSchema stores category='electronics'."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database import DATABASE_URL
        from app.models import Product

        engine = create_engine(
            os.getenv("DATABASE_URL") or DATABASE_URL, pool_pre_ping=True,
        )
        Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = Session()
        try:
            stored = db.query(Product).filter(
                Product.product_id == inserted_product_id,
            ).one()
            assert stored.category == "electronics"
        finally:
            db.close()
            engine.dispose()

    def test_lowercase_category_filter_finds_the_row(self, inserted_product_id):
        """Filtering by category='electronics' returns the ProductSchema row."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database import DATABASE_URL
        from app.models import Product

        engine = create_engine(
            os.getenv("DATABASE_URL") or DATABASE_URL, pool_pre_ping=True,
        )
        Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = Session()
        try:
            hits = db.query(Product).filter(
                Product.category == "electronics",
                Product.product_id == inserted_product_id,
            ).all()
            assert len(hits) == 1, (
                "Case-sensitive filter category='electronics' did not match a "
                "row inserted via ProductSchema(product_type='laptop'). "
                "Either the schema reintroduced uppercase or the DB column "
                "was not populated from row['category']."
            )
        finally:
            db.close()
            engine.dispose()
