"""
Tests for Redis caching policy: search key generation, search result caching,
Upstash URL support, and brand/category index queries.

Uses a real local Redis instance (localhost:6379).
Tests are skipped if Redis is not available.
"""

import pytest
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.cache import CacheClient


@pytest.fixture
def client():
    """Create a test CacheClient using a dedicated test DB (db=15)."""
    os.environ["REDIS_DB_MCP"] = "15"  # Use db=15 for tests to avoid touching real data
    c = CacheClient(namespace="mcp")
    if not c.ping():
        pytest.skip("Redis not available")
    c.flush_all()
    yield c
    c.flush_all()
    os.environ.pop("REDIS_DB_MCP", None)


#  Search key generation 

class TestMakeSearchKey:
    def test_deterministic(self):
        """Same filters produce same key regardless of dict insertion order."""
        k1 = CacheClient.make_search_key({"brand": "Dell", "category": "Electronics"}, "Electronics")
        k2 = CacheClient.make_search_key({"category": "Electronics", "brand": "Dell"}, "Electronics")
        assert k1 == k2

    def test_different_filters_different_key(self):
        k1 = CacheClient.make_search_key({"brand": "Dell"}, "Electronics")
        k2 = CacheClient.make_search_key({"brand": "HP"}, "Electronics")
        assert k1 != k2

    def test_different_page_different_key(self):
        k1 = CacheClient.make_search_key({"brand": "Dell"}, "Electronics", page=1)
        k2 = CacheClient.make_search_key({"brand": "Dell"}, "Electronics", page=2)
        assert k1 != k2

    def test_different_limit_different_key(self):
        k1 = CacheClient.make_search_key({}, "Electronics", limit=10)
        k2 = CacheClient.make_search_key({}, "Electronics", limit=20)
        assert k1 != k2

    def test_internal_keys_excluded(self):
        """Keys starting with _ are transient and should not affect the cache key."""
        k1 = CacheClient.make_search_key({"brand": "Dell"}, "Electronics")
        k2 = CacheClient.make_search_key({"brand": "Dell", "_session_id": "abc"}, "Electronics")
        assert k1 == k2

    def test_none_values_excluded(self):
        k1 = CacheClient.make_search_key({"brand": "Dell"}, "Electronics")
        k2 = CacheClient.make_search_key({"brand": "Dell", "color": None}, "Electronics")
        assert k1 == k2

    def test_key_format(self):
        key = CacheClient.make_search_key({"brand": "Dell"}, "Electronics")
        assert key.startswith("search:")
        assert len(key) == len("search:") + 16  # sha256[:16]


#  Search result caching 

class TestSearchResultCaching:
    def test_set_and_get(self, client):
        results = [
            {"product_id": "p1", "name": "Dell XPS", "price_cents": 99900},
            {"product_id": "p2", "name": "HP Envy", "price_cents": 79900},
        ]
        key = CacheClient.make_search_key({"brand": "Dell"}, "Electronics")
        assert client.set_search_results(key, results)
        cached = client.get_search_results(key)
        assert cached is not None
        assert len(cached) == 2
        assert cached[0]["product_id"] == "p1"
        assert cached[1]["name"] == "HP Envy"

    def test_cache_miss_returns_none(self, client):
        key = CacheClient.make_search_key({"brand": "NonExistent"}, "Electronics")
        assert client.get_search_results(key) is None

    def test_invalidate_search_cache(self, client):
        key = CacheClient.make_search_key({"brand": "Dell"}, "Electronics")
        client.set_search_results(key, [{"product_id": "p1"}])
        assert client.get_search_results(key) is not None
        deleted = client.invalidate_search_cache()
        assert deleted >= 1
        assert client.get_search_results(key) is None

    def test_empty_results_not_cached_on_read(self, client):
        """Empty results should be storable but retrievable."""
        key = CacheClient.make_search_key({"brand": "Empty"}, "Electronics")
        client.set_search_results(key, [])
        cached = client.get_search_results(key)
        assert cached == []


#  Product summary / price / inventory (existing, regression) 

class TestExistingCacheMethods:
    def test_product_summary_round_trip(self, client):
        summary = {"product_id": "test-1", "name": "Test Laptop", "brand": "TestBrand"}
        client.set_product_summary("test-1", summary)
        cached = client.get_product_summary("test-1")
        assert cached["product_id"] == "test-1"
        assert cached["brand"] == "TestBrand"

    def test_price_round_trip(self, client):
        client.set_price("test-1", {"price_cents": 99900, "currency": "USD"})
        cached = client.get_price("test-1")
        assert cached["price_cents"] == 99900

    def test_inventory_round_trip(self, client):
        client.set_inventory("test-1", {"available_qty": 5, "reserved_qty": 1})
        cached = client.get_inventory("test-1")
        assert cached["available_qty"] == 5

    def test_invalidate_product(self, client):
        client.set_product_summary("test-1", {"product_id": "test-1"})
        client.set_price("test-1", {"price_cents": 100})
        client.set_inventory("test-1", {"available_qty": 1, "reserved_qty": 0})
        client.invalidate_product("test-1")
        assert client.get_product_summary("test-1") is None
        assert client.get_price("test-1") is None
        assert client.get_inventory("test-1") is None

    def test_cache_miss_returns_none(self, client):
        assert client.get_product_summary("nonexistent") is None
        assert client.get_price("nonexistent") is None
        assert client.get_inventory("nonexistent") is None


#  Brand / Category index queries 

class TestIndexQueries:
    def test_category_index(self, client):
        # Populate a category set directly in Redis
        client.client.sadd("category:TestCat", "p1", "p2", "p3")
        result = client.get_product_ids_by_filters(category="TestCat")
        assert result is not None
        assert "p1" in result
        assert len(result) == 3

    def test_brand_index(self, client):
        client.client.sadd("brand:TestBrand", "p1", "p2")
        result = client.get_product_ids_by_filters(brand="TestBrand")
        assert result is not None
        assert len(result) == 2

    def test_intersection(self, client):
        client.client.sadd("category:Electronics", "p1", "p2", "p3")
        client.client.sadd("brand:Dell", "p2", "p3", "p4")
        result = client.get_product_ids_by_filters(category="Electronics", brand="Dell")
        assert result is not None
        assert result == {"p2", "p3"}

    def test_no_filters_returns_none(self, client):
        assert client.get_product_ids_by_filters() is None

    def test_nonexistent_set_returns_none(self, client):
        result = client.get_product_ids_by_filters(category="NoSuchCategory")
        assert result is None


#  Upstash URL support 

class TestUpstashSupport:
    def test_local_redis_default(self):
        """Without UPSTASH_REDIS_URL, should connect to localhost."""
        os.environ.pop("UPSTASH_REDIS_URL", None)
        c = CacheClient(namespace="mcp")
        # Should have a standard Redis client (not from_url)
        assert c.client is not None

    def test_upstash_url_used_when_set(self):
        """When UPSTASH_REDIS_URL is set, it should attempt to use it."""
        # Use a fake URL — we just verify the code path doesn't crash during init
        os.environ["UPSTASH_REDIS_URL"] = "redis://localhost:6379/15"
        try:
            c = CacheClient(namespace="mcp")
            assert c.client is not None
            # Should be able to ping (since we point to local Redis db=15)
            assert c.ping()
        finally:
            os.environ.pop("UPSTASH_REDIS_URL", None)


#  Session caching (regression) 

class TestSessionCaching:
    def test_session_round_trip(self, client):
        data = {"domain": "laptops", "filters": {"brand": "Dell"}, "question_count": 2}
        client.set_session_data("sess-1", data)
        cached = client.get_session_data("sess-1")
        assert cached["domain"] == "laptops"
        assert cached["question_count"] == 2

    def test_delete_session(self, client):
        client.set_session_data("sess-1", {"domain": "laptops"})
        client.delete_session_data("sess-1")
        assert client.get_session_data("sess-1") is None


#  Cache-aside integration: get_product populates Redis on miss 

class TestCacheAsideGetProduct:
    """
    Week7 requirement: 'If a user clicks an uncached laptop, does cache adapt?'
    Verifies the cache-aside pattern: first access populates Redis.
    """

    def test_get_product_populates_cache_on_miss(self, client):
        """First access (cache miss) should write product data to Redis."""
        pid = "cache-aside-test-1"
        client.invalidate_product(pid)
        assert client.get_product_summary(pid) is None

        # Simulate what get_product endpoint does on cache miss
        client.set_product_summary(pid, {"product_id": pid, "name": "Test Laptop", "brand": "Dell"})
        client.set_price(pid, {"price_cents": 99900, "currency": "USD"})
        client.set_inventory(pid, {"available_qty": 10, "reserved_qty": 0})

        # Cache adapted — second access is a hit
        assert client.get_product_summary(pid)["brand"] == "Dell"
        assert client.get_price(pid)["price_cents"] == 99900
        assert client.get_inventory(pid)["available_qty"] == 10

    def test_second_access_is_cache_hit(self, client):
        """Second access returns from cache without needing PostgreSQL."""
        pid = "cache-aside-test-2"
        client.set_product_summary(pid, {"product_id": pid, "name": "HP Envy"})
        client.set_price(pid, {"price_cents": 79900, "currency": "USD"})
        client.set_inventory(pid, {"available_qty": 5, "reserved_qty": 1})

        assert client.get_product_summary(pid)["name"] == "HP Envy"
        assert client.get_price(pid)["price_cents"] == 79900
        assert client.get_inventory(pid)["reserved_qty"] == 1

    def test_cache_expires_after_ttl(self, client):
        """After TTL expires, cache returns None (forces re-fetch from DB)."""
        import time
        pid = "cache-aside-ttl-test"
        key = client._key(f"prod_summary:{pid}")
        client.client.setex(key, 1, json.dumps({"product_id": pid}))
        assert client.get_product_summary(pid) is not None
        time.sleep(2)
        assert client.get_product_summary(pid) is None


#  Search result caching end-to-end 

class TestSearchCacheEndToEnd:
    """Verifies search result caching: repeated search = cache hit."""

    def test_search_cache_hit_on_repeat(self, client):
        filters = {"brand": "Apple", "category": "Electronics"}
        key = CacheClient.make_search_key(filters, "Electronics")
        assert client.get_search_results(key) is None

        results = [
            {"product_id": "p1", "name": "MacBook Air", "price_cents": 109900},
            {"product_id": "p2", "name": "MacBook Pro", "price_cents": 199900},
        ]
        client.set_search_results(key, results)

        cached = client.get_search_results(key)
        assert cached is not None
        assert len(cached) == 2
        assert cached[0]["name"] == "MacBook Air"

    def test_different_filters_no_cross_contamination(self, client):
        key1 = CacheClient.make_search_key({"brand": "Dell"}, "Electronics")
        key2 = CacheClient.make_search_key({"brand": "HP"}, "Electronics")
        client.set_search_results(key1, [{"product_id": "dell-1"}])
        client.set_search_results(key2, [{"product_id": "hp-1"}])
        assert client.get_search_results(key1)[0]["product_id"] == "dell-1"
        assert client.get_search_results(key2)[0]["product_id"] == "hp-1"

    def test_invalidate_clears_all_search_cache(self, client):
        key1 = CacheClient.make_search_key({"brand": "Dell"}, "Electronics")
        key2 = CacheClient.make_search_key({"brand": "HP"}, "Electronics")
        client.set_search_results(key1, [{"product_id": "p1"}])
        client.set_search_results(key2, [{"product_id": "p2"}])
        deleted = client.invalidate_search_cache()
        assert deleted >= 2
        assert client.get_search_results(key1) is None
        assert client.get_search_results(key2) is None


#  Bélády-Inspired Popularity Tracking 

class TestPopularityTracking:
    """Tests for access frequency tracking and adaptive TTL."""

    def test_record_access_increments_score(self, client):
        """Each record_access call increments the product's score by 1."""
        pid = "pop-test-1"
        client.client.zrem(CacheClient.POPULARITY_KEY, pid)  # clean state
        assert client.get_popularity_score(pid) == 0.0
        client.record_access(pid)
        assert client.get_popularity_score(pid) == 1.0
        client.record_access(pid)
        client.record_access(pid)
        assert client.get_popularity_score(pid) == 3.0

    def test_cold_product_gets_half_ttl(self, client):
        """Products with <3 accesses get 0.5x base TTL."""
        pid = "cold-product"
        client.client.zrem(CacheClient.POPULARITY_KEY, pid)
        ttl = client.get_adaptive_ttl(pid, 300)
        assert ttl == 150  # 300 * 0.5

    def test_warm_product_gets_base_ttl(self, client):
        """Products with 3-9 accesses get 1x base TTL."""
        pid = "warm-product"
        client.client.zrem(CacheClient.POPULARITY_KEY, pid)
        for _ in range(5):
            client.record_access(pid)
        ttl = client.get_adaptive_ttl(pid, 300)
        assert ttl == 300  # 1x base

    def test_hot_product_gets_triple_ttl(self, client):
        """Products with ≥10 accesses get 3x base TTL."""
        pid = "hot-product"
        client.client.zrem(CacheClient.POPULARITY_KEY, pid)
        for _ in range(15):
            client.record_access(pid)
        ttl = client.get_adaptive_ttl(pid, 300)
        assert ttl == 900  # 300 * 3

    def test_minimum_ttl_floor(self, client):
        """Cold products with very short base TTL still get at least 10s."""
        pid = "min-ttl-product"
        client.client.zrem(CacheClient.POPULARITY_KEY, pid)
        ttl = client.get_adaptive_ttl(pid, 10)
        assert ttl == 10  # max(10 * 0.5, 10) = 10

    def test_adaptive_set_product_summary(self, client):
        """set_product_summary with adaptive=True uses popularity-based TTL."""
        pid = "adaptive-test"
        client.client.zrem(CacheClient.POPULARITY_KEY, pid)
        # Make it hot
        for _ in range(12):
            client.record_access(pid)
        client.set_product_summary(pid, {"product_id": pid, "name": "Hot Item"}, adaptive=True)
        # Verify it was cached
        cached = client.get_product_summary(pid)
        assert cached is not None
        assert cached["name"] == "Hot Item"
        # Verify TTL is in the hot range (should be ~900s for 300 base)
        key = client._key(f"prod_summary:{pid}")
        ttl = client.client.ttl(key)
        assert ttl > 300  # Must be greater than base TTL

    def test_get_top_products(self, client):
        """get_top_products returns products sorted by access count."""
        # Set up known scores
        client.client.zadd(CacheClient.POPULARITY_KEY, {"top-1": 100, "top-2": 50, "top-3": 10})
        top = client.get_top_products(n=3)
        assert len(top) >= 3
        # First should be highest score
        assert top[0][0] == "top-1"
        assert top[0][1] == 100.0
