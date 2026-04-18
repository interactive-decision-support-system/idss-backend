"""
Q3D — KG Integration Wiring Tests
===================================

Issue #27: "kg_service.py never called" — these tests verify that the KG
service IS wired into the agent's ecommerce search path and behaves correctly.

Three things are tested:

  A. KG search_candidates() IS invoked from _search_ecommerce_products()
     when the KG is available.  (Proves wiring.)

  B. When KG is unavailable, _search_ecommerce_products() still returns
     results via the SQL fallback.  (Proves graceful degradation.)

  C. KnowledgeGraphService(password=None) is a safe no-op: is_available()
     returns False and all query methods return empty containers.
     (Proves the offline stub contract documented in kg_service.py.)

Tests that require a live Neo4j connection are marked
@pytest.mark.skip — remove the mark if you have Neo4j running locally.
"""
import os
import sys
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "mcp-server"))


# ---------------------------------------------------------------------------
# Part C: offline stub contract (no Neo4j needed — pure unit test)
# ---------------------------------------------------------------------------

class TestKGOfflineStub:
    """
    KnowledgeGraphService constructed with password=None must be a safe
    no-op.  None of its query methods should raise exceptions.
    """

    def _make_offline_svc(self):
        from app.kg_service import KnowledgeGraphService
        return KnowledgeGraphService(password=None)

    def test_is_available_returns_false_when_no_password(self):
        """is_available() must be False when Neo4j password is absent."""
        svc = self._make_offline_svc()
        assert svc.is_available() is False, (
            "KG with no password must report unavailable so callers use SQL fallback"
        )

    def test_search_candidates_returns_empty_list_offline(self):
        """search_candidates() must return ([], {}, {}) when KG is offline."""
        svc = self._make_offline_svc()
        ids, scores, explanation = svc.search_candidates("gaming laptop under $800", {})
        assert ids == [], f"Expected empty id list, got {ids}"
        assert scores == {}, f"Expected empty scores dict, got {scores}"
        assert isinstance(explanation, dict), "Explanation must be a dict"

    def test_get_similar_products_returns_empty_offline(self):
        """get_similar_products() must return [] when KG is offline."""
        svc = self._make_offline_svc()
        result = svc.get_similar_products("fake-product-id", limit=5)
        assert result == [], f"Expected [], got {result}"

    def test_get_better_than_returns_empty_offline(self):
        """get_better_than() must return [] when KG is offline."""
        svc = self._make_offline_svc()
        result = svc.get_better_than("fake-product-id", limit=5)
        assert result == [], f"Expected [], got {result}"

    def test_get_compatible_components_returns_empty_offline(self):
        """get_compatible_components() must return [] when KG is offline."""
        svc = self._make_offline_svc()
        result = svc.get_compatible_components("fake-product-id", component_type="RAM")
        assert result == [], f"Expected [], got {result}"

    def test_close_does_not_raise_when_driver_is_none(self):
        """close() must be a no-op (not raise) when driver was never created."""
        svc = self._make_offline_svc()
        # Should not raise even with self.driver = None
        svc.close()


# ---------------------------------------------------------------------------
# Part A: KG IS called from _search_ecommerce_products (wiring proof)
# ---------------------------------------------------------------------------

class TestKGWiredIntoEcommerceSearchPath:
    """
    _search_ecommerce_products() must call get_kg_service().search_candidates()
    when the KG reports is_available() == True.

    We patch:
      - get_kg_service   → controlled mock so Neo4j is not needed
      - get_product_store→ returns a handful of dummy products so the function
                           doesn't actually hit Supabase
      - cache_client     → no-op so Redis is not needed
    """

    def _make_mock_kg(self, is_available=True, candidate_ids=None):
        """Return a mock KG service that reports availability and returns controlled IDs."""
        mock_kg = MagicMock()
        mock_kg.is_available.return_value = is_available
        # search_candidates returns (list_of_ids, scores_dict, explanation_dict)
        mock_kg.search_candidates.return_value = (candidate_ids or [], {}, {})
        return mock_kg

    def _make_dummy_products(self, n=3):
        """Return n minimal product dicts that satisfy format_product requirements."""
        return [
            {
                "id": f"prod-{i}",
                "title": f"Laptop {i}",
                "brand": "TestBrand",
                "price": 799.0 + i * 100,
                "category": "Electronics",
                "product_type": "laptop",
                "subcategory": "laptop",
                "source": "test",
                "image": None,
                "description": "",
                "specs": {},
                "quantity": 5,
            }
            for i in range(n)
        ]

    @pytest.mark.asyncio
    async def test_kg_search_candidates_called_when_kg_available(self):
        """
        _search_ecommerce_products must invoke kg.search_candidates() when KG
        is available.  This is the core wiring assertion for Q3D.
        """
        dummy_products = self._make_dummy_products(3)
        mock_kg = self._make_mock_kg(is_available=True, candidate_ids=["prod-0"])

        # Mock product store — synchronous search_products returning dummy rows
        mock_store = MagicMock()
        mock_store.search_products.return_value = dummy_products

        # Mock cache client — treat every get as a miss and every set as a no-op
        mock_cache = MagicMock()
        mock_cache.get_search_results.return_value = None
        mock_cache.set_search_results.return_value = None

        # The KG is imported locally inside _search_ecommerce_products via
        #   from app.kg_service import get_kg_service
        # so we must patch at the source module, not the call site.
        with patch("app.kg_service.get_kg_service", return_value=mock_kg), \
             patch("app.tools.supabase_product_store.get_product_store", return_value=mock_store), \
             patch("app.cache.cache_client", mock_cache):
            from agent.chat_endpoint import _search_ecommerce_products
            buckets, labels = await _search_ecommerce_products(
                filters={"price_max_cents": 100000},
                category="Electronics",
            )

        # Primary assertion: KG search was invoked
        assert mock_kg.search_candidates.called, (
            "get_kg_service().search_candidates() was never called — KG wiring is broken"
        )

    @pytest.mark.asyncio
    async def test_kg_unavailable_falls_through_to_sql_results(self):
        """
        When KG reports is_available() == False, _search_ecommerce_products
        must still return SQL-sourced results (graceful degradation).
        """
        dummy_products = self._make_dummy_products(3)
        mock_kg = self._make_mock_kg(is_available=False)

        mock_store = MagicMock()
        mock_store.search_products.return_value = dummy_products

        mock_cache = MagicMock()
        mock_cache.get_search_results.return_value = None
        mock_cache.set_search_results.return_value = None

        with patch("app.kg_service.get_kg_service", return_value=mock_kg), \
             patch("app.tools.supabase_product_store.get_product_store", return_value=mock_store), \
             patch("app.cache.cache_client", mock_cache):
            from agent.chat_endpoint import _search_ecommerce_products
            buckets, labels = await _search_ecommerce_products(
                filters={"price_max_cents": 100000},
                category="Electronics",
            )

        # KG search must NOT have been called
        mock_kg.search_candidates.assert_not_called()
        # But results must still be present (SQL fallback)
        all_products = [p for row in buckets for p in row]
        assert len(all_products) > 0, "Expected SQL fallback products, got empty result"

    @pytest.mark.asyncio
    async def test_kg_exception_does_not_crash_search(self):
        """
        If kg.search_candidates() raises an exception, _search_ecommerce_products
        must catch it silently and continue with SQL results.
        This validates the try/except around the KG call at chat_endpoint.py:4169.
        """
        dummy_products = self._make_dummy_products(3)

        mock_kg = MagicMock()
        mock_kg.is_available.return_value = True
        mock_kg.search_candidates.side_effect = ConnectionError("Neo4j down")

        mock_store = MagicMock()
        mock_store.search_products.return_value = dummy_products

        mock_cache = MagicMock()
        mock_cache.get_search_results.return_value = None
        mock_cache.set_search_results.return_value = None

        with patch("app.kg_service.get_kg_service", return_value=mock_kg), \
             patch("app.tools.supabase_product_store.get_product_store", return_value=mock_store), \
             patch("app.cache.cache_client", mock_cache):
            from agent.chat_endpoint import _search_ecommerce_products
            # Must not raise — KG failure is non-fatal per design
            buckets, labels = await _search_ecommerce_products(
                filters={"price_max_cents": 100000},
                category="Electronics",
            )

        all_products = [p for row in buckets for p in row]
        assert len(all_products) > 0, (
            "SQL results must be returned even when KG raises an exception"
        )


# ---------------------------------------------------------------------------
# Part B: get_kg_service() singleton returns the same instance each call
# ---------------------------------------------------------------------------

class TestKGSingleton:
    """
    get_kg_service() caches the instance at module level.
    Two successive calls must return the same object.
    This is important for performance — we don't want a new Neo4j connection
    per request.
    """

    def test_get_kg_service_returns_singleton(self):
        """Two calls to get_kg_service() must return the SAME object."""
        from app.kg_service import get_kg_service
        svc_a = get_kg_service()
        svc_b = get_kg_service()
        assert svc_a is svc_b, (
            "get_kg_service() must return a singleton — a new instance per call "
            "would open a new Neo4j TCP connection each request."
        )
