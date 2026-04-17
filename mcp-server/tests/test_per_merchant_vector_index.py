"""
Per-(merchant_id, strategy) vector index tests — issue #56.

Covers:
  1. Two merchants with disjoint catalogs: vector candidates from one never
     appear in the other's search results.
  2. Default merchant post-migration: the new layout returns the same top-k
     set as the legacy layout for the same product set (retrieval parity).
  3. ``get_vector_store(merchant, strategy)`` is idempotent / cached per-pair.

These tests exercise the real SentenceTransformer encoder and a real FAISS
index, matching the style of ``test_vector_search.py``. They do not require
Postgres.
"""

import importlib.util
import pickle
import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _load_migration_module():
    """Import the migration script by path (it lives outside the app package)."""
    script_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "migrate_vector_index_to_per_merchant.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migrate_vector_index_to_per_merchant", script_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module

try:
    import faiss  # noqa: F401
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

from app.vector_search import (
    UniversalEmbeddingStore,
    get_vector_store,
    merchant_index_dir,
    merchant_index_path,
    merchant_ids_path,
    reset_vector_store_cache,
)


ACME_PRODUCTS = [
    {"product_id": "ACME-001", "name": "Dell XPS 15 Laptop",
     "description": "Powerful laptop for video editing", "category": "electronics", "brand": "Dell"},
    {"product_id": "ACME-002", "name": "iPhone 15 Pro",
     "description": "Premium smartphone with great camera", "category": "electronics", "brand": "Apple"},
    {"product_id": "ACME-003", "name": "Sony WH-1000XM5 Headphones",
     "description": "Wireless noise-cancelling over-ear headphones", "category": "electronics", "brand": "Sony"},
]

WIDGETS_PRODUCTS = [
    {"product_id": "WID-001", "name": "Stainless Steel Water Bottle",
     "description": "Insulated reusable water bottle", "category": "lifestyle", "brand": "EcoWare"},
    {"product_id": "WID-002", "name": "Yoga Mat",
     "description": "Non-slip eco-friendly yoga mat", "category": "fitness", "brand": "ZenFit"},
    {"product_id": "WID-003", "name": "Cast Iron Skillet",
     "description": "Pre-seasoned 12 inch cast iron cookware", "category": "kitchen", "brand": "IronChef"},
]


@pytest.fixture
def clean_merchant_dirs(tmp_path, monkeypatch):
    """Redirect the per-merchant data root into a tmp dir so test state is isolated.

    Each test gets its own ``data/merchants/`` root, and the store cache is
    reset before and after the test so no prior instance pins the old path.
    """
    from app import vector_search

    fake_root = tmp_path / "data" / "merchants"
    fake_root.mkdir(parents=True)
    monkeypatch.setattr(vector_search, "_MERCHANT_DATA_ROOT", fake_root)

    reset_vector_store_cache()
    yield fake_root
    reset_vector_store_cache()


@pytest.mark.skipif(not FAISS_AVAILABLE, reason="faiss not installed")
class TestPerMerchantIsolation:
    """Two merchants, two disjoint catalogs — no cross-merchant leakage."""

    def test_search_never_returns_other_merchants_products(self, clean_merchant_dirs):
        acme = UniversalEmbeddingStore(merchant_id="acme", strategy="normalizer_v1")
        widgets = UniversalEmbeddingStore(merchant_id="widgets", strategy="normalizer_v1")

        acme.build_index(ACME_PRODUCTS, save_index=False)
        widgets.build_index(WIDGETS_PRODUCTS, save_index=False)

        acme_ids = {p["product_id"] for p in ACME_PRODUCTS}
        widgets_ids = {p["product_id"] for p in WIDGETS_PRODUCTS}

        # A query that matches items on both sides semantically.
        for query in ("laptop", "yoga", "cookware", "smartphone"):
            acme_hits, _ = acme.search(query, k=10)
            widgets_hits, _ = widgets.search(query, k=10)

            assert acme_hits, f"acme search for {query!r} returned nothing"
            assert widgets_hits, f"widgets search for {query!r} returned nothing"
            assert set(acme_hits).issubset(acme_ids), (
                f"acme leaked: {set(acme_hits) - acme_ids}"
            )
            assert set(widgets_hits).issubset(widgets_ids), (
                f"widgets leaked: {set(widgets_hits) - widgets_ids}"
            )

    def test_indices_are_written_to_distinct_directories(self, clean_merchant_dirs):
        acme_path = merchant_index_path("acme", "normalizer_v1")
        widgets_path = merchant_index_path("widgets", "normalizer_v1")
        assert acme_path != widgets_path
        # ../../<merchant>/<strategy>/faiss.bin -> merchants root via three parents
        assert acme_path.parent.parent.parent == widgets_path.parent.parent.parent

        acme = UniversalEmbeddingStore(merchant_id="acme", strategy="normalizer_v1")
        acme.build_index(ACME_PRODUCTS, save_index=True)
        assert acme_path.exists()
        assert merchant_ids_path("acme", "normalizer_v1").exists()
        # widgets side must remain untouched
        assert not widgets_path.exists()


@pytest.mark.skipif(not FAISS_AVAILABLE, reason="faiss not installed")
class TestDefaultMerchantMigrationParity:
    """After migration, (default, normalizer_v1) returns the same top-k set.

    We simulate the pre-migration state by writing a legacy-layout index pair
    into ``vector_indices/`` — bit-for-bit the same FAISS bytes a freshly
    built store would emit — then run the migration script and confirm that
    the new store loads the migrated files and produces identical top-k
    ordering for a fixed query set.
    """

    def _baseline_query_results(self, products, queries, k):
        """Build a reference index and capture top-k for each query."""
        baseline = UniversalEmbeddingStore(
            merchant_id="__baseline__",
            strategy="normalizer_v1",
            use_cache=False,
        )
        baseline.build_index(products, save_index=False)
        return {q: baseline.search(q, k=k)[0] for q in queries}

    def test_migration_preserves_topk(self, clean_merchant_dirs, tmp_path, monkeypatch):
        import faiss
        migrate_vector_index = _load_migration_module()

        # Arrange: build a real FAISS index + id-map for the "default" merchant
        # and drop it at the legacy global path the migration script scans.
        products = ACME_PRODUCTS + WIDGETS_PRODUCTS
        queries = ["laptop", "yoga", "headphones"]
        baseline_topk = self._baseline_query_results(products, queries, k=3)

        # Rebuild to serialize to disk under the legacy layout.
        seed_store = UniversalEmbeddingStore(
            merchant_id="__legacy_seed__",
            strategy="normalizer_v1",
            use_cache=False,
        )
        seed_store.build_index(products, save_index=False)

        # Point the migration script at our fake mcp-server root.
        fake_mcp_root = tmp_path / "mcp-server"
        legacy_dir = fake_mcp_root / "vector_indices"
        legacy_dir.mkdir(parents=True)
        legacy_idx = legacy_dir / "mcp_index_all_mpnet_base_v2_20250101_000000.index"
        legacy_ids = legacy_dir / "mcp_ids_all_mpnet_base_v2_20250101_000000.pkl"
        faiss.write_index(seed_store._index, str(legacy_idx))
        with open(legacy_ids, "wb") as f:
            pickle.dump(seed_store._product_ids, f)

        # Also redirect the new per-merchant data root to live under the same
        # fake mcp-server so the migration can write to ``<fake>/data/merchants/``.
        from app import vector_search as vs
        new_root = fake_mcp_root / "data" / "merchants"
        monkeypatch.setattr(vs, "_MERCHANT_DATA_ROOT", new_root)
        monkeypatch.setattr(migrate_vector_index, "_repo_mcp_root", lambda: fake_mcp_root)

        # Act: migrate + reset cache so the next get_vector_store picks up the
        # freshly-copied files.
        copied = migrate_vector_index.migrate(
            merchant_id="default",
            strategy="normalizer_v1",
            dry_run=False,
            delete_legacy=False,
        )
        assert copied >= 2
        reset_vector_store_cache()

        # Assert: the default merchant's new store returns the same top-k set
        # as the baseline index (retrieval parity across the migration).
        default_store = get_vector_store("default", "normalizer_v1")
        for q in queries:
            got, _ = default_store.search(q, k=3)
            assert got == baseline_topk[q], (
                f"top-k diverged for query={q!r}: got={got} baseline={baseline_topk[q]}"
            )


@pytest.mark.skipif(not FAISS_AVAILABLE, reason="faiss not installed")
class TestGetVectorStoreCaching:
    """get_vector_store(m, s) is idempotent per pair and distinct across pairs."""

    def test_same_pair_returns_same_instance(self, clean_merchant_dirs):
        a = get_vector_store("acme", "normalizer_v1")
        b = get_vector_store("acme", "normalizer_v1")
        assert a is b

    def test_different_strategies_distinct(self, clean_merchant_dirs):
        v1 = get_vector_store("acme", "normalizer_v1")
        v2 = get_vector_store("acme", "normalizer_v2")
        assert v1 is not v2

    def test_different_merchants_distinct(self, clean_merchant_dirs):
        acme = get_vector_store("acme", "normalizer_v1")
        widgets = get_vector_store("widgets", "normalizer_v1")
        assert acme is not widgets

    def test_reset_cache_drops_instances(self, clean_merchant_dirs):
        first = get_vector_store("acme", "normalizer_v1")
        reset_vector_store_cache()
        second = get_vector_store("acme", "normalizer_v1")
        assert first is not second
