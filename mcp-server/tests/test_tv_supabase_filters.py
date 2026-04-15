import pytest
import os

# Skip if no DATABASE_URL
pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set -- skipping Supabase integration tests",
)

def test_tv_search_returns_results():
    from app.tools.supabase_product_store import get_product_store
    store = get_product_store()
    results = store.search_products({"product_type": "tv", "category": "electronics"}, limit=10)
    assert len(results) > 0
    for r in results:
        assert r.get("product_type") == "tv"

def test_tv_panel_type_filter():
    from app.tools.supabase_product_store import get_product_store
    store = get_product_store()
    results = store.search_products(
        {"product_type": "tv", "category": "electronics", "panel_type": "OLED"},
        limit=10,
    )
    assert len(results) > 0
    for r in results:
        attrs = r.get("attributes") or {}
        assert "OLED" in str(attrs.get("panel_type", "")).upper()

def test_tv_brand_filter():
    from app.tools.supabase_product_store import get_product_store
    store = get_product_store()
    results = store.search_products(
        {"product_type": "tv", "category": "electronics", "brand": "Samsung"},
        limit=10,
    )
    assert len(results) > 0
    for r in results:
        assert "samsung" in (r.get("brand") or "").lower()

def test_tv_price_filter():
    from app.tools.supabase_product_store import get_product_store
    store = get_product_store()
    results = store.search_products(
        {"product_type": "tv", "category": "electronics", "price_max": 1000},
        limit=10,
    )
    assert len(results) > 0
    for r in results:
        assert float(r.get("price", 0)) <= 1000
