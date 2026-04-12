"""
Q2 — Cache Coherence / TTL Tests
=================================

Issue #27: Nino identified that cache_policy.py TTL mismatches could cause
stale data to be served. Specifically:

  Problem: LLM narratives are cached for 300s (product summary TTL), but
  price data only lasts 60s. If a flash sale drops the price from $999 to
  $799, the narrative still says "$999" for up to 240 extra seconds.

  Solution: NARRATIVE_TTL is now explicitly capped at DEFAULT_TTL_PRICE (60s).

This file tests the INVARIANTS — the relationships between TTL constants —
not the Redis mechanics (those are in test_cache_policy.py).

Most tests here do NOT require a live Redis connection: they verify that
the constants in cache_policy.py satisfy the coherence contract.

Redis-dependent tests are skipped when Redis is unavailable (same as
test_cache_policy.py).
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.cache_policy import (
    DEFAULT_TTL_PRODUCT_SUMMARY,
    DEFAULT_TTL_PRICE,
    DEFAULT_TTL_INVENTORY,
    DEFAULT_TTL_SEARCH,
    DEFAULT_TTL_NARRATIVE,
)
from app.cache import CacheClient


# ---------------------------------------------------------------------------
# Fixture — real Redis, skipped when not available (same pattern as test_cache_policy.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def redis_client():
    """Return a test CacheClient using db=15. Skip if Redis unavailable."""
    os.environ["REDIS_DB_MCP"] = "15"
    c = CacheClient(namespace="mcp")
    if not c.ping():
        pytest.skip("Redis not available")
    c.flush_all()
    yield c
    c.flush_all()
    os.environ.pop("REDIS_DB_MCP", None)


# ---------------------------------------------------------------------------
# Part A: TTL hierarchy invariants (no Redis needed — pure constant checks)
# ---------------------------------------------------------------------------

class TestTTLHierarchyInvariants:
    """
    The coherence contract specifies a strict TTL ordering:
    INVENTORY < PRICE < PRODUCT_SUMMARY

    These tests run without Redis — they verify the constants in cache_policy.py
    satisfy the contract as documented. If a developer changes a TTL constant
    and breaks the hierarchy, these tests fail immediately.
    """

    def test_inventory_ttl_shorter_than_price_ttl(self):
        """Inventory is most volatile: must expire before price data."""
        assert DEFAULT_TTL_INVENTORY < DEFAULT_TTL_PRICE, (
            f"Invariant violated: INVENTORY_TTL ({DEFAULT_TTL_INVENTORY}s) must be "
            f"< PRICE_TTL ({DEFAULT_TTL_PRICE}s). Stale inventory outlasting price "
            f"causes users to see 'in stock' when the item sold out."
        )

    def test_price_ttl_shorter_than_product_summary_ttl(self):
        """Price changes more often than product specs: price must expire first."""
        assert DEFAULT_TTL_PRICE < DEFAULT_TTL_PRODUCT_SUMMARY, (
            f"Invariant violated: PRICE_TTL ({DEFAULT_TTL_PRICE}s) must be "
            f"< PRODUCT_SUMMARY_TTL ({DEFAULT_TTL_PRODUCT_SUMMARY}s)."
        )

    def test_narrative_ttl_not_greater_than_price_ttl(self):
        """
        Narrative must NOT outlive price.

        LLM-generated narratives may quote a specific price ('priced at $999').
        If price drops to $799, the stale narrative creates misleading UX.
        Capping NARRATIVE_TTL at PRICE_TTL eliminates this class of inconsistency.
        """
        assert DEFAULT_TTL_NARRATIVE <= DEFAULT_TTL_PRICE, (
            f"Coherence violation: NARRATIVE_TTL ({DEFAULT_TTL_NARRATIVE}s) > "
            f"PRICE_TTL ({DEFAULT_TTL_PRICE}s). A stale narrative may quote a price "
            f"that is no longer accurate. Set NARRATIVE_TTL ≤ PRICE_TTL."
        )

    def test_all_ttls_positive(self):
        """Sanity: all TTLs must be positive — a TTL of 0 means no caching."""
        for name, val in [
            ("INVENTORY_TTL", DEFAULT_TTL_INVENTORY),
            ("PRICE_TTL", DEFAULT_TTL_PRICE),
            ("PRODUCT_SUMMARY_TTL", DEFAULT_TTL_PRODUCT_SUMMARY),
            ("NARRATIVE_TTL", DEFAULT_TTL_NARRATIVE),
            ("SEARCH_TTL", DEFAULT_TTL_SEARCH),
        ]:
            assert val > 0, f"{name} must be positive, got {val}"

    def test_narrative_ttl_equals_price_ttl(self):
        """Confirm NARRATIVE_TTL is set to exactly PRICE_TTL (the design decision)."""
        assert DEFAULT_TTL_NARRATIVE == DEFAULT_TTL_PRICE, (
            f"NARRATIVE_TTL ({DEFAULT_TTL_NARRATIVE}s) should equal PRICE_TTL "
            f"({DEFAULT_TTL_PRICE}s) per the cache coherence contract."
        )


# ---------------------------------------------------------------------------
# Part B: Invalidation atomicity (requires Redis)
# ---------------------------------------------------------------------------

class TestProductInvalidationAtomicity:
    """
    When a product is updated (price change, stock change), ALL related
    cache entries — summary, price, inventory — must be cleared together.

    If only price is invalidated but summary is not, the summary may
    include the old price in its narrative. Atomicity prevents this gap.
    """

    def test_invalidate_product_clears_summary_price_and_inventory(self, redis_client):
        """invalidate_product() must clear all three data layers in one call."""
        pid = "coherence-test-pid-001"

        # Populate all three layers
        redis_client.set_product_summary(pid, {
            "product_id": pid, "name": "Test Laptop",
            "narrative": "Priced at $999 — a great deal!"  # May become stale
        })
        redis_client.set_price(pid, {"price_cents": 99900, "currency": "USD"})
        redis_client.set_inventory(pid, {"available_qty": 10, "reserved_qty": 0})

        # Verify all three are cached before invalidation
        assert redis_client.get_product_summary(pid) is not None, "Summary should be cached"
        assert redis_client.get_price(pid) is not None, "Price should be cached"
        assert redis_client.get_inventory(pid) is not None, "Inventory should be cached"

        # Single invalidation call must clear all three atomically
        redis_client.invalidate_product(pid)

        # All three must be gone — a partial invalidation is a coherence bug
        assert redis_client.get_product_summary(pid) is None, (
            "Summary still cached after invalidation — narrative may quote stale price"
        )
        assert redis_client.get_price(pid) is None, "Price still cached after invalidation"
        assert redis_client.get_inventory(pid) is None, "Inventory still cached after invalidation"

    def test_invalidation_does_not_affect_other_products(self, redis_client):
        """Invalidating one product must not clear cache for other products."""
        pid_a = "coherence-pid-A"
        pid_b = "coherence-pid-B"

        redis_client.set_product_summary(pid_a, {"product_id": pid_a, "name": "Product A"})
        redis_client.set_price(pid_a, {"price_cents": 50000})
        redis_client.set_product_summary(pid_b, {"product_id": pid_b, "name": "Product B"})
        redis_client.set_price(pid_b, {"price_cents": 70000})

        # Invalidate only A
        redis_client.invalidate_product(pid_a)

        # A gone
        assert redis_client.get_product_summary(pid_a) is None
        assert redis_client.get_price(pid_a) is None

        # B unaffected
        assert redis_client.get_product_summary(pid_b) is not None, "Product B summary should survive"
        assert redis_client.get_price(pid_b) is not None, "Product B price should survive"


# ---------------------------------------------------------------------------
# Part C: Narrative TTL enforcement via Redis TTL inspection (requires Redis)
# ---------------------------------------------------------------------------

class TestNarrativeTTLEnforcement:
    """
    Verify that if NARRATIVE_TTL is used as the TTL when caching product
    summaries (which may contain narrative text), the Redis TTL is ≤ price TTL.

    This test documents the expected behavior. Enforcement depends on the
    caller (endpoints.py) using DEFAULT_TTL_NARRATIVE when setting product
    summaries that contain LLM-generated text.
    """

    def test_summary_cached_with_narrative_ttl_expires_before_price(self, redis_client):
        """
        A product summary cached with NARRATIVE_TTL should have a shorter TTL
        than a price entry cached with PRICE_TTL.

        Simulates the coherent pattern: narrative summary expires no later than price.
        """
        import json
        import time
        pid = "narrative-ttl-test-001"
        key_summary = redis_client._key(f"prod_summary:{pid}")
        key_price = redis_client._key(f"price:{pid}")

        # Cache summary with NARRATIVE_TTL
        redis_client.client.setex(
            key_summary,
            DEFAULT_TTL_NARRATIVE,
            json.dumps({"product_id": pid, "narrative": "Great laptop at $999"})
        )
        # Cache price with PRICE_TTL (should be same or longer)
        redis_client.client.setex(
            key_price,
            DEFAULT_TTL_PRICE,
            json.dumps({"price_cents": 99900})
        )

        ttl_summary = redis_client.client.ttl(key_summary)
        ttl_price = redis_client.client.ttl(key_price)

        assert ttl_summary <= ttl_price, (
            f"Narrative summary TTL ({ttl_summary}s) exceeds price TTL ({ttl_price}s). "
            f"Summary may serve stale price text after price expires."
        )

    def test_search_results_ttl_acceptable(self, redis_client):
        """
        Search result TTL (5 min) is acceptable because search results contain
        product_ids, not narrative text. Narrative staleness is a summary concern,
        not a search concern.

        This test documents that SEARCH_TTL can safely exceed PRICE_TTL.
        """
        # The invariant for search is less strict — just verify it's positive
        assert DEFAULT_TTL_SEARCH > 0
        # But document the relationship: search TTL > price TTL is intentional
        assert DEFAULT_TTL_SEARCH >= DEFAULT_TTL_PRICE, (
            "Search cache TTL should be at least as long as price TTL for efficiency"
        )
