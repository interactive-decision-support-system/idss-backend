"""
Enhanced Caching System with Optimization

Improvements over basic cache.py:
1. Intelligent cache warming
2. Batch operations
3. Cache statistics
4. Smart invalidation
5. Compression for large objects
6. Query result caching

Usage:
    from app.enhanced_cache import EnhancedCache
    
    cache = EnhancedCache()
    
    # Cache product
    cache.set_product(product_id, product_data)
    
    # Cache search results
    cache.set_search_results(query, results)
    
    # Get statistics
    stats = cache.get_stats()
"""

import redis
import json
import time
import hashlib
from typing import Any, Optional, List, Dict
from datetime import datetime, timedelta
import zlib


class EnhancedCache:
    """
    Enhanced caching system with optimizations.
    
    Features:
    - Intelligent TTLs based on data type
    - Compression for large objects
    - Batch operations
    - Cache statistics
    - Smart invalidation
    """
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        """Initialize enhanced cache."""
        try:
            self.redis_client = redis.from_url(redis_url, decode_responses=False)
            self.redis_client.ping()
            self.available = True
        except Exception:
            self.redis_client = None
            self.available = False
        
        # Cache statistics
        self.stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "deletes": 0,
            "errors": 0
        }
        
        # TTL settings (in seconds)
        self.ttls = {
            "product_summary": 300,      # 5 minutes
            "product_detail": 600,       # 10 minutes
            "search_results": 180,       # 3 minutes
            "price": 60,                 # 1 minute (prices change frequently)
            "inventory": 30,             # 30 seconds (stock changes frequently)
            "category_list": 1800,       # 30 minutes (rarely changes)
            "popular_products": 600,     # 10 minutes
            "recommendations": 300,      # 5 minutes
        }
        
        # Compression threshold (bytes)
        self.compression_threshold = 1024  # 1KB
    
    def _compress(self, data: bytes) -> bytes:
        """Compress data if it exceeds threshold."""
        if len(data) > self.compression_threshold:
            return b"COMPRESSED:" + zlib.compress(data)
        return data
    
    def _decompress(self, data: bytes) -> bytes:
        """Decompress data if it was compressed."""
        if data.startswith(b"COMPRESSED:"):
            return zlib.decompress(data[11:])
        return data
    
    def _make_key(self, key_type: str, identifier: str) -> str:
        """Generate cache key."""
        return f"mcp:{key_type}:{identifier}"
    
    def _make_query_hash(self, query: str, filters: Dict[str, Any]) -> str:
        """Generate hash for search query."""
        query_str = f"{query}:{json.dumps(filters, sort_keys=True)}"
        return hashlib.md5(query_str.encode()).hexdigest()
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None
        """
        if not self.available:
            self.stats["misses"] += 1
            return None
        
        try:
            value = self.redis_client.get(key)
            if value is None:
                self.stats["misses"] += 1
                return None
            
            # Decompress if needed
            value = self._decompress(value)
            
            # Deserialize
            result = json.loads(value.decode('utf-8'))
            self.stats["hits"] += 1
            return result
            
        except Exception as e:
            self.stats["errors"] += 1
            return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds
            
        Returns:
            True if successful
        """
        if not self.available:
            return False
        
        try:
            # Serialize
            serialized = json.dumps(value).encode('utf-8')
            
            # Compress if large
            compressed = self._compress(serialized)
            
            # Store
            if ttl:
                self.redis_client.setex(key, ttl, compressed)
            else:
                self.redis_client.set(key, compressed)
            
            self.stats["sets"] += 1
            return True
            
        except Exception as e:
            self.stats["errors"] += 1
            return False
    
    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if not self.available:
            return False
        
        try:
            self.redis_client.delete(key)
            self.stats["deletes"] += 1
            return True
        except Exception:
            self.stats["errors"] += 1
            return False
    
    def set_product(
        self, 
        product_id: str, 
        product_data: Dict[str, Any],
        detail_level: str = "summary"
    ) -> bool:
        """
        Cache product data.
        
        Args:
            product_id: Product ID
            product_data: Product data
            detail_level: "summary" or "detail"
            
        Returns:
            True if successful
        """
        key_type = f"product_{detail_level}"
        key = self._make_key(key_type, product_id)
        ttl = self.ttls.get(f"product_{detail_level}", 300)
        
        return self.set(key, product_data, ttl)
    
    def get_product(
        self, 
        product_id: str,
        detail_level: str = "summary"
    ) -> Optional[Dict[str, Any]]:
        """Get cached product data."""
        key_type = f"product_{detail_level}"
        key = self._make_key(key_type, product_id)
        
        return self.get(key)
    
    def set_search_results(
        self,
        query: str,
        filters: Dict[str, Any],
        results: List[Dict[str, Any]]
    ) -> bool:
        """
        Cache search results.
        
        Args:
            query: Search query
            filters: Applied filters
            results: Search results
            
        Returns:
            True if successful
        """
        query_hash = self._make_query_hash(query, filters)
        key = self._make_key("search", query_hash)
        ttl = self.ttls["search_results"]
        
        cache_data = {
            "query": query,
            "filters": filters,
            "results": results,
            "cached_at": datetime.utcnow().isoformat()
        }
        
        return self.set(key, cache_data, ttl)
    
    def get_search_results(
        self,
        query: str,
        filters: Dict[str, Any]
    ) -> Optional[List[Dict[str, Any]]]:
        """Get cached search results."""
        query_hash = self._make_query_hash(query, filters)
        key = self._make_key("search", query_hash)
        
        cached = self.get(key)
        if cached:
            return cached.get("results")
        
        return None
    
    def set_price(self, product_id: str, price_cents: int) -> bool:
        """Cache product price (short TTL)."""
        key = self._make_key("price", product_id)
        ttl = self.ttls["price"]
        
        price_data = {
            "price_cents": price_cents,
            "cached_at": datetime.utcnow().isoformat()
        }
        
        return self.set(key, price_data, ttl)
    
    def get_price(self, product_id: str) -> Optional[int]:
        """Get cached price."""
        key = self._make_key("price", product_id)
        
        cached = self.get(key)
        if cached:
            return cached.get("price_cents")
        
        return None
    
    def set_inventory(self, product_id: str, available_qty: int) -> bool:
        """Cache inventory (very short TTL)."""
        key = self._make_key("inventory", product_id)
        ttl = self.ttls["inventory"]
        
        inventory_data = {
            "available_qty": available_qty,
            "cached_at": datetime.utcnow().isoformat()
        }
        
        return self.set(key, inventory_data, ttl)
    
    def get_inventory(self, product_id: str) -> Optional[int]:
        """Get cached inventory."""
        key = self._make_key("inventory", product_id)
        
        cached = self.get(key)
        if cached:
            return cached.get("available_qty")
        
        return None
    
    def invalidate_product(self, product_id: str) -> bool:
        """
        Invalidate all cached data for a product.
        
        Args:
            product_id: Product ID
            
        Returns:
            True if successful
        """
        keys = [
            self._make_key("product_summary", product_id),
            self._make_key("product_detail", product_id),
            self._make_key("price", product_id),
            self._make_key("inventory", product_id),
        ]
        
        if not self.available:
            return False
        
        try:
            self.redis_client.delete(*keys)
            self.stats["deletes"] += len(keys)
            return True
        except Exception:
            self.stats["errors"] += 1
            return False
    
    def warm_cache(self, products: List[Dict[str, Any]]) -> int:
        """
        Warm cache with popular products.
        
        Args:
            products: List of products to cache
            
        Returns:
            Number of products cached
        """
        if not self.available:
            return 0
        
        cached_count = 0
        for product in products:
            product_id = product.get("product_id")
            if product_id:
                if self.set_product(product_id, product, "summary"):
                    cached_count += 1
        
        return cached_count
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dict with cache statistics
        """
        total_requests = self.stats["hits"] + self.stats["misses"]
        hit_rate = (self.stats["hits"] / total_requests * 100) if total_requests > 0 else 0
        
        stats = {
            **self.stats,
            "total_requests": total_requests,
            "hit_rate_percent": round(hit_rate, 2),
            "available": self.available
        }
        
        # Add Redis info if available
        if self.available:
            try:
                info = self.redis_client.info("stats")
                stats["redis_keys"] = self.redis_client.dbsize()
                stats["redis_memory_used"] = info.get("used_memory_human")
            except Exception:
                pass
        
        return stats
    
    def reset_stats(self):
        """Reset cache statistics."""
        self.stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "deletes": 0,
            "errors": 0
        }
    
    def flush_all(self) -> bool:
        """
        Flush all cached data (use with caution).
        
        Returns:
            True if successful
        """
        if not self.available:
            return False
        
        try:
            self.redis_client.flushdb()
            return True
        except Exception:
            return False


# Global cache instance
_cache_instance = None


def get_cache() -> EnhancedCache:
    """Get global cache instance."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = EnhancedCache()
    return _cache_instance


# Test function
def test_enhanced_cache():
    """Test enhanced cache functionality."""
    print("="*80)
    print("ENHANCED CACHE - TEST")
    print("="*80)
    
    cache = EnhancedCache()
    
    if not cache.available:
        print("[FAIL] Redis not available. Tests skipped.")
        return
    
    print("\n1. Testing basic operations:")
    
    # Set and get
    test_data = {"name": "Test Product", "price": 9999}
    cache.set("test:product:1", test_data, ttl=60)
    result = cache.get("test:product:1")
    status = "" if result == test_data else "[FAIL]"
    print(f"  {status} Set and Get: {result == test_data}")
    
    # Product caching
    product_data = {
        "product_id": "test-123",
        "name": "MacBook Pro",
        "price_cents": 249999
    }
    cache.set_product("test-123", product_data, "summary")
    retrieved = cache.get_product("test-123", "summary")
    status = "" if retrieved and retrieved["name"] == "MacBook Pro" else "[FAIL]"
    print(f"  {status} Product caching: {retrieved is not None}")
    
    # Search results caching
    search_results = [{"product_id": "1"}, {"product_id": "2"}]
    cache.set_search_results("laptop", {"category": "Electronics"}, search_results)
    cached_results = cache.get_search_results("laptop", {"category": "Electronics"})
    status = "" if cached_results == search_results else "[FAIL]"
    print(f"  {status} Search caching: {cached_results == search_results}")
    
    print("\n2. Testing compression:")
    large_data = {"data": "x" * 2000}  # > 1KB
    key = "test:large"
    cache.set(key, large_data)
    retrieved_large = cache.get(key)
    status = "" if retrieved_large == large_data else "[FAIL]"
    print(f"  {status} Large data compression: {retrieved_large == large_data}")
    
    print("\n3. Testing invalidation:")
    cache.invalidate_product("test-123")
    after_invalidation = cache.get_product("test-123", "summary")
    status = "" if after_invalidation is None else "[FAIL]"
    print(f"  {status} Product invalidation: {after_invalidation is None}")
    
    print("\n4. Cache statistics:")
    stats = cache.get_stats()
    print(f"  Total requests: {stats['total_requests']}")
    print(f"  Hits: {stats['hits']}")
    print(f"  Misses: {stats['misses']}")
    print(f"  Hit rate: {stats['hit_rate_percent']}%")
    print(f"  Redis keys: {stats.get('redis_keys', 'N/A')}")
    
    print("\n" + "="*80)


if __name__ == "__main__":
    test_enhanced_cache()
