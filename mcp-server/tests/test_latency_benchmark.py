"""
Latency benchmarking tests for Redis cache operations.

Measures p50, p95, p99 latencies for cache read/write, search key generation,
popularity tracking, and adaptive TTL computation.

Tests are skipped if Redis is not available.
"""

import pytest
import time
import statistics
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


def _measure(func, iterations=100):
    """Run func N times, return latency stats in ms."""
    latencies = []
    for _ in range(iterations):
        start = time.perf_counter()
        func()
        latencies.append((time.perf_counter() - start) * 1000)
    latencies.sort()
    n = len(latencies)
    return {
        "min": round(latencies[0], 4),
        "p50": round(latencies[n // 2], 4),
        "p95": round(latencies[int(n * 0.95)], 4),
        "p99": round(latencies[int(n * 0.99)], 4),
        "max": round(latencies[-1], 4),
        "mean": round(statistics.mean(latencies), 4),
    }


class TestCacheLatency:
    """Benchmark Redis cache operation latencies."""

    def test_cache_read_latency_under_5ms(self, client):
        """Redis GET should complete in <5ms (p95)."""
        # Pre-populate
        client.set_product_summary("bench-read", {"product_id": "bench-read", "name": "Test"})
        stats = _measure(lambda: client.get_product_summary("bench-read"))
        print(f"\n  Cache READ: p50={stats['p50']}ms  p95={stats['p95']}ms  p99={stats['p99']}ms")
        assert stats["p95"] < 5.0, f"Cache read p95 too slow: {stats['p95']}ms"

    def test_cache_write_latency_under_5ms(self, client):
        """Redis SETEX should complete in <5ms (p95)."""
        data = {"product_id": "bench-write", "name": "Test Product", "brand": "TestBrand"}
        stats = _measure(lambda: client.set_product_summary("bench-write", data))
        print(f"\n  Cache WRITE: p50={stats['p50']}ms  p95={stats['p95']}ms  p99={stats['p99']}ms")
        assert stats["p95"] < 5.0, f"Cache write p95 too slow: {stats['p95']}ms"

    def test_cache_miss_latency(self, client):
        """Cache miss (key doesn't exist) should be fast."""
        stats = _measure(lambda: client.get_product_summary("nonexistent-bench"))
        print(f"\n  Cache MISS: p50={stats['p50']}ms  p95={stats['p95']}ms  p99={stats['p99']}ms")
        assert stats["p95"] < 5.0

    def test_search_key_generation_latency(self, client):
        """Hash computation for search keys should be <1ms."""
        filters = {"brand": "Dell", "category": "Electronics", "min_price": 500, "max_price": 2000}
        stats = _measure(
            lambda: CacheClient.make_search_key(filters, "Electronics", page=1, limit=20),
            iterations=1000,
        )
        print(f"\n  Search key gen: p50={stats['p50']}ms  p95={stats['p95']}ms  p99={stats['p99']}ms")
        assert stats["p95"] < 1.0, f"Search key generation p95 too slow: {stats['p95']}ms"

    def test_search_cache_round_trip(self, client):
        """Search result write + read round-trip latency."""
        key = CacheClient.make_search_key({"brand": "Dell"}, "Electronics")
        results = [{"product_id": f"p{i}", "name": f"Product {i}"} for i in range(20)]

        def round_trip():
            client.set_search_results(key, results)
            client.get_search_results(key)

        stats = _measure(round_trip, iterations=50)
        print(f"\n  Search round-trip: p50={stats['p50']}ms  p95={stats['p95']}ms  p99={stats['p99']}ms")
        assert stats["p95"] < 10.0


class TestPopularityLatency:
    """Benchmark popularity tracking operation latencies."""

    def test_record_access_latency(self, client):
        """ZINCRBY should complete in <5ms (p95)."""
        stats = _measure(lambda: client.record_access("bench-pop"))
        print(f"\n  record_access (ZINCRBY): p50={stats['p50']}ms  p95={stats['p95']}ms  p99={stats['p99']}ms")
        assert stats["p95"] < 5.0

    def test_get_popularity_score_latency(self, client):
        """ZSCORE should complete in <5ms (p95)."""
        client.record_access("bench-score")
        stats = _measure(lambda: client.get_popularity_score("bench-score"))
        print(f"\n  get_popularity_score (ZSCORE): p50={stats['p50']}ms  p95={stats['p95']}ms  p99={stats['p99']}ms")
        assert stats["p95"] < 5.0

    def test_adaptive_ttl_latency(self, client):
        """get_adaptive_ttl (ZSCORE + math) should complete in <5ms (p95)."""
        client.record_access("bench-ttl")
        stats = _measure(lambda: client.get_adaptive_ttl("bench-ttl", 300))
        print(f"\n  get_adaptive_ttl: p50={stats['p50']}ms  p95={stats['p95']}ms  p99={stats['p99']}ms")
        assert stats["p95"] < 5.0

    def test_get_top_products_latency(self, client):
        """ZREVRANGE top-50 should complete in <10ms (p95)."""
        # Populate 100 products
        for i in range(100):
            client.client.zincrby(CacheClient.POPULARITY_KEY, i, f"top-bench-{i}")
        stats = _measure(lambda: client.get_top_products(50), iterations=50)
        print(f"\n  get_top_products(50): p50={stats['p50']}ms  p95={stats['p95']}ms  p99={stats['p99']}ms")
        assert stats["p95"] < 10.0


class TestAdaptiveTTLWithWrite:
    """Benchmark adaptive TTL write operations (set with adaptive=True)."""

    def test_adaptive_write_latency(self, client):
        """set_product_summary with adaptive=True should be <10ms (p95)."""
        pid = "bench-adaptive"
        for _ in range(5):
            client.record_access(pid)
        data = {"product_id": pid, "name": "Adaptive Bench"}
        stats = _measure(lambda: client.set_product_summary(pid, data, adaptive=True))
        print(f"\n  Adaptive write: p50={stats['p50']}ms  p95={stats['p95']}ms  p99={stats['p99']}ms")
        assert stats["p95"] < 10.0, f"Adaptive write p95 too slow: {stats['p95']}ms"
