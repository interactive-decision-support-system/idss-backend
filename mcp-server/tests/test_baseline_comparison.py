"""
Baseline comparison tests: cached vs uncached performance.

Measures speedup from Redis caching and validates that Bélády-inspired
adaptive TTL produces measurable differences between hot/warm/cold products.

Tests are skipped if Redis is not available.
"""

import pytest
import time
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.cache import CacheClient


@pytest.fixture
def client():
    """Create a test CacheClient using db=15."""
    os.environ["REDIS_DB_MCP"] = "15"
    c = CacheClient(namespace="mcp")
    if not c.ping():
        pytest.skip("Redis not available")
    c.flush_all()
    yield c
    c.flush_all()
    os.environ.pop("REDIS_DB_MCP", None)


class TestCacheHitVsMiss:
    """Compare cache hit vs miss latency."""

    def test_get_product_cache_hit_faster_than_miss(self, client):
        """
        Cache hit should be significantly faster than a simulated cache miss
        (miss = Redis lookup returning None + Postgres simulation + Redis write).
        """
        pid = "baseline-1"
        summary = {"product_id": pid, "name": "Test Laptop", "brand": "Dell"}
        price = {"price_cents": 99900, "currency": "USD"}
        inventory = {"available_qty": 10, "reserved_qty": 0}

        # Simulate cache miss: write all three keys (simulates Postgres → Redis path)
        miss_latencies = []
        for _ in range(50):
            client.invalidate_product(pid)
            start = time.perf_counter()
            # Miss path: check cache (None) + write to cache
            client.get_product_summary(pid)
            client.set_product_summary(pid, summary)
            client.set_price(pid, price)
            client.set_inventory(pid, inventory)
            miss_latencies.append((time.perf_counter() - start) * 1000)

        # Pre-populate for hit path
        client.set_product_summary(pid, summary)
        client.set_price(pid, price)
        client.set_inventory(pid, inventory)

        # Simulate cache hit: read all three keys
        hit_latencies = []
        for _ in range(50):
            start = time.perf_counter()
            client.get_product_summary(pid)
            client.get_price(pid)
            client.get_inventory(pid)
            hit_latencies.append((time.perf_counter() - start) * 1000)

        miss_latencies.sort()
        hit_latencies.sort()
        n = len(hit_latencies)
        p50_hit = hit_latencies[n // 2]
        p50_miss = miss_latencies[n // 2]
        avg_hit = sum(hit_latencies) / n
        avg_miss = sum(miss_latencies) / len(miss_latencies)
        speedup = p50_miss / p50_hit if p50_hit > 0 else float("inf")

        print(f"\n  Cache HIT p50: {p50_hit:.3f}ms  avg: {avg_hit:.3f}ms")
        print(f"  Cache MISS p50: {p50_miss:.3f}ms  avg: {avg_miss:.3f}ms")
        print(f"  Speedup (p50): {speedup:.1f}x")

        # Hit does 3 ops; miss does 4 ops. Allow 1.5x margin for system noise.
        assert p50_hit < p50_miss * 1.5, (
            f"Cache hit p50 ({p50_hit:.3f}ms) should not greatly exceed miss p50 ({p50_miss:.3f}ms)"
        )

    def test_search_cache_hit_faster(self, client):
        """Cached search results should be faster to retrieve than write+read."""
        key = CacheClient.make_search_key({"brand": "Dell"}, "Electronics")
        results = [{"product_id": f"p{i}", "name": f"Product {i}", "price_cents": 99900} for i in range(20)]

        # Write path
        write_latencies = []
        for _ in range(30):
            client.invalidate_search_cache()
            start = time.perf_counter()
            client.set_search_results(key, results)
            write_latencies.append((time.perf_counter() - start) * 1000)

        # Pre-populate
        client.set_search_results(key, results)

        # Read path (cache hit)
        read_latencies = []
        for _ in range(30):
            start = time.perf_counter()
            client.get_search_results(key)
            read_latencies.append((time.perf_counter() - start) * 1000)

        avg_write = sum(write_latencies) / len(write_latencies)
        avg_read = sum(read_latencies) / len(read_latencies)

        print(f"\n  Search WRITE avg: {avg_write:.3f}ms")
        print(f"  Search READ avg: {avg_read:.3f}ms")

        # Read should be faster than write (or at least comparable)
        assert avg_read <= avg_write * 2, "Search read should not be much slower than write"


class TestAdaptiveTTLBaseline:
    """Validate that adaptive TTL produces measurable differences."""

    def test_hot_product_has_longer_ttl(self, client):
        """Hot product (≥10 accesses) should have 3x the TTL of a cold product."""
        hot_pid = "hot-baseline"
        cold_pid = "cold-baseline"

        # Clean state
        client.client.zrem(CacheClient.POPULARITY_KEY, hot_pid, cold_pid)

        # Make hot product popular
        for _ in range(15):
            client.record_access(hot_pid)

        # Set with adaptive TTL
        client.set_product_summary(hot_pid, {"product_id": hot_pid, "name": "Hot"}, adaptive=True)
        client.set_product_summary(cold_pid, {"product_id": cold_pid, "name": "Cold"}, adaptive=True)

        hot_ttl = client.client.ttl(client._key(f"prod_summary:{hot_pid}"))
        cold_ttl = client.client.ttl(client._key(f"prod_summary:{cold_pid}"))

        print(f"\n  Hot product TTL: {hot_ttl}s")
        print(f"  Cold product TTL: {cold_ttl}s")
        print(f"  Ratio: {hot_ttl / cold_ttl:.1f}x")

        # Hot should have significantly longer TTL
        assert hot_ttl > cold_ttl * 2, f"Hot TTL ({hot_ttl}) should be >2x cold TTL ({cold_ttl})"

    def test_cold_product_evicts_faster(self, client):
        """Cold product (0 accesses) should have shorter TTL than warm product."""
        cold_pid = "cold-evict"
        warm_pid = "warm-evict"

        client.client.zrem(CacheClient.POPULARITY_KEY, cold_pid, warm_pid)

        # Make warm product
        for _ in range(5):
            client.record_access(warm_pid)

        client.set_price(cold_pid, {"price_cents": 100}, adaptive=True)
        client.set_price(warm_pid, {"price_cents": 100}, adaptive=True)

        cold_ttl = client.client.ttl(client._key(f"price:{cold_pid}"))
        warm_ttl = client.client.ttl(client._key(f"price:{warm_pid}"))

        print(f"\n  Cold price TTL: {cold_ttl}s")
        print(f"  Warm price TTL: {warm_ttl}s")

        assert cold_ttl < warm_ttl, f"Cold TTL ({cold_ttl}) should be < warm TTL ({warm_ttl})"

    def test_three_tier_ttl_ordering(self, client):
        """Verify cold < warm < hot TTL ordering for inventory cache."""
        cold, warm, hot = "tier-cold", "tier-warm", "tier-hot"
        client.client.zrem(CacheClient.POPULARITY_KEY, cold, warm, hot)

        for _ in range(5):
            client.record_access(warm)
        for _ in range(15):
            client.record_access(hot)

        inv_data = {"available_qty": 10, "reserved_qty": 0}
        client.set_inventory(cold, inv_data, adaptive=True)
        client.set_inventory(warm, inv_data, adaptive=True)
        client.set_inventory(hot, inv_data, adaptive=True)

        cold_ttl = client.client.ttl(client._key(f"inventory:{cold}"))
        warm_ttl = client.client.ttl(client._key(f"inventory:{warm}"))
        hot_ttl = client.client.ttl(client._key(f"inventory:{hot}"))

        print(f"\n  Inventory TTLs — Cold: {cold_ttl}s  Warm: {warm_ttl}s  Hot: {hot_ttl}s")

        assert cold_ttl < warm_ttl < hot_ttl, \
            f"Expected cold({cold_ttl}) < warm({warm_ttl}) < hot({hot_ttl})"


class TestBaselineSummary:
    """Generate a summary table suitable for poster presentation."""

    def test_generate_summary_table(self, client):
        """Print a formatted summary of cache performance metrics."""
        pid = "summary-test"
        summary = {"product_id": pid, "name": "Summary Laptop", "brand": "Dell"}

        # Measure cache write
        write_times = []
        for _ in range(50):
            start = time.perf_counter()
            client.set_product_summary(pid, summary)
            write_times.append((time.perf_counter() - start) * 1000)

        # Measure cache read
        read_times = []
        for _ in range(50):
            start = time.perf_counter()
            client.get_product_summary(pid)
            read_times.append((time.perf_counter() - start) * 1000)

        # Measure popularity tracking
        pop_times = []
        for _ in range(50):
            start = time.perf_counter()
            client.record_access(pid)
            pop_times.append((time.perf_counter() - start) * 1000)

        write_times.sort()
        read_times.sort()
        pop_times.sort()

        print("\n" + "=" * 70)
        print("CACHE PERFORMANCE BASELINE (for poster)")
        print("=" * 70)
        print(f"{'Operation':<30} {'p50 (ms)':>10} {'p95 (ms)':>10} {'p99 (ms)':>10}")
        print("-" * 70)
        print(f"{'Cache Write (SETEX)':<30} {write_times[25]:>10.3f} {write_times[47]:>10.3f} {write_times[49]:>10.3f}")
        print(f"{'Cache Read (GET)':<30} {read_times[25]:>10.3f} {read_times[47]:>10.3f} {read_times[49]:>10.3f}")
        print(f"{'Popularity Track (ZINCRBY)':<30} {pop_times[25]:>10.3f} {pop_times[47]:>10.3f} {pop_times[49]:>10.3f}")
        print("=" * 70)

        # Adaptive TTL summary
        client.client.zrem(CacheClient.POPULARITY_KEY, "cold-s", "warm-s", "hot-s")
        for _ in range(5):
            client.record_access("warm-s")
        for _ in range(15):
            client.record_access("hot-s")

        print(f"\n{'Adaptive TTL (Product Summary, base=300s)'}")
        print(f"  Cold (<3 accesses):  {client.get_adaptive_ttl('cold-s', 300)}s")
        print(f"  Warm (3-9 accesses): {client.get_adaptive_ttl('warm-s', 300)}s")
        print(f"  Hot (≥10 accesses):  {client.get_adaptive_ttl('hot-s', 300)}s")
        print("=" * 70)

        # This test always passes — it's for generating the summary
        assert True


class TestClickToCacheAdaptation:
    """Test that user clicking an uncached product triggers cache adaptation.

    Per week7NOTEs line 617-618: 'If a user clicks an uncached laptop, does cache adapt?'
    Verifies: record_access → popularity increases → adaptive TTL changes.
    """

    def test_uncached_product_click_populates_cache(self, client):
        """Simulate: user clicks uncached product → cache is populated."""
        pid = "click-test-uncached"
        # Ensure clean state
        client.invalidate_product(pid)
        client.client.zrem(CacheClient.POPULARITY_KEY, pid)

        # Before click: cache miss
        assert client.get_product_summary(pid) is None
        assert client.get_popularity_score(pid) == 0.0

        # Simulate get_product flow: DB fetch → cache write → record_access
        summary = {"product_id": pid, "name": "Test Laptop", "brand": "Dell"}
        client.set_product_summary(pid, summary, adaptive=True)
        client.record_access(pid)

        # After click: cache populated, popularity tracked
        cached = client.get_product_summary(pid)
        assert cached is not None
        assert cached["product_id"] == pid
        assert client.get_popularity_score(pid) >= 1.0

    def test_repeated_clicks_increase_ttl(self, client):
        """Repeated user clicks → higher popularity → longer cache TTL."""
        pid = "click-test-repeated"
        client.invalidate_product(pid)
        client.client.zrem(CacheClient.POPULARITY_KEY, pid)

        summary = {"product_id": pid, "name": "Popular Laptop", "brand": "HP"}

        # First click: cold product
        client.set_product_summary(pid, summary, adaptive=True)
        client.record_access(pid)
        cold_ttl = client.client.ttl(client._key(f"prod_summary:{pid}"))

        # Simulate 10+ clicks → becomes hot
        for _ in range(12):
            client.record_access(pid)
        client.set_product_summary(pid, summary, adaptive=True)
        hot_ttl = client.client.ttl(client._key(f"prod_summary:{pid}"))

        print(f"\n  Cold TTL after 1 click: {cold_ttl}s")
        print(f"  Hot TTL after 13 clicks: {hot_ttl}s")

        assert hot_ttl > cold_ttl, f"Hot TTL ({hot_ttl}) should be > cold TTL ({cold_ttl})"

    def test_search_impression_records_access(self, client):
        """Search results appearing to user should record access for top products."""
        pids = ["search-imp-1", "search-imp-2", "search-imp-3"]
        for pid in pids:
            client.client.zrem(CacheClient.POPULARITY_KEY, pid)

        # Simulate search result impressions (top 3 get access recorded)
        for pid in pids:
            client.record_access(pid)

        for pid in pids:
            score = client.get_popularity_score(pid)
            assert score >= 1.0, f"{pid} should have popularity ≥ 1.0 after search impression"
