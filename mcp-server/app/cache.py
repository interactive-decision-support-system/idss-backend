"""
Redis cache layer for hot data (price, inventory, product summaries, search results).

Redis is ONLY a cache, never the source of truth.
Postgres is always authoritative.

Cache keys follow a clear naming pattern:
- prod_summary:{product_id}   — individual product data (TTL 5 min)
- price:{product_id}          — price data (TTL 60s)
- inventory:{product_id}      — stock levels (TTL 30s)
- search:{hash}               — search result lists (TTL 5 min)
- session:{session_id}        — agent session blobs (TTL 1 hour)

Supports both local Redis and Upstash (cloud-hosted) via UPSTASH_REDIS_URL.
"""

import hashlib
import redis
import json
import os
from typing import Optional, Dict, Any, List, Set


class CacheClient:
    """
    Redis cache client with clear TTL management and cache-hit tracking.

    Supports separate namespaces for MCP vs Agent caching:
    - MCP cache: mcp:{key} (product data, prices, inventory, search results)
    - Agent cache: agent:{key} (sessions, conversations, context)

    Bélády-inspired adaptive TTL:
    - Tracks access frequency via Redis sorted set (ZINCRBY)
    - Hot products (≥10 accesses) get 3x TTL — predicted to be needed again soon
    - Warm products (3-9 accesses) get standard TTL
    - Cold products (<3 accesses) get 0.5x TTL — evicted sooner
    """

    POPULARITY_KEY = "mcp:popularity:access_count"

    def __init__(self, namespace: str = "mcp"):
        """
        Initialize Redis connection.

        Connection priority:
        1. UPSTASH_REDIS_URL (cloud-hosted, rediss:// TLS)
        2. REDIS_HOST + REDIS_PORT (local)

        Args:
            namespace: Cache namespace - "mcp" for MCP data, "agent" for agent data
        """
        self.namespace = namespace
        upstash_url = os.getenv("UPSTASH_REDIS_URL")

        if upstash_url:
            # Cloud-hosted Redis (Upstash) — uses rediss:// TLS URL
            self.client = redis.from_url(
                upstash_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
        else:
            # Local Redis
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", "6379"))

            # Separate Redis databases for MCP vs Agent
            # MCP uses db=0, Agent uses db=1
            if namespace == "agent":
                redis_db = int(os.getenv("REDIS_DB_AGENT", "1"))
            else:
                redis_db = int(os.getenv("REDIS_DB_MCP", "0"))

            self.client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )

        # TTL configuration (in seconds)
        self.ttl_product_summary = int(os.getenv("CACHE_TTL_PRODUCT_SUMMARY", "300"))  # 5 minutes
        self.ttl_price = int(os.getenv("CACHE_TTL_PRICE", "60"))  # 1 minute
        self.ttl_inventory = int(os.getenv("CACHE_TTL_INVENTORY", "30"))  # 30 seconds
        self.ttl_search = int(os.getenv("CACHE_TTL_SEARCH", "300"))  # 5 minutes

        # Agent-specific TTLs
        self.ttl_agent_session = int(os.getenv("CACHE_TTL_AGENT_SESSION", "3600"))  # 1 hour
        self.ttl_agent_context = int(os.getenv("CACHE_TTL_AGENT_CONTEXT", "1800"))  # 30 minutes

    def _key(self, key: str) -> str:
        """Prefix key with namespace."""
        return f"{self.namespace}:{key}"


    def ping(self) -> bool:
        """Check if Redis is reachable."""
        try:
            return self.client.ping()
        except Exception:
            return False


    # 
    # Bélády-Inspired Popularity Tracking
    # 

    def record_access(self, product_id: str) -> None:
        """Track product access frequency via ZINCRBY on a sorted set."""
        try:
            self.client.zincrby(self.POPULARITY_KEY, 1, product_id)
        except Exception:
            pass  # Non-critical — caching still works without popularity

    def get_popularity_score(self, product_id: str) -> float:
        """Get access count for a product. Returns 0 if unknown."""
        try:
            score = self.client.zscore(self.POPULARITY_KEY, product_id)
            return float(score) if score else 0.0
        except Exception:
            return 0.0

    def get_adaptive_ttl(self, product_id: str, base_ttl: int) -> int:
        """
        Bélády-inspired adaptive TTL based on access frequency.

        Hot products (≥10 accesses):  3x base TTL — keep longer
        Warm products (3-9 accesses): 1x base TTL — standard
        Cold products (<3 accesses):  0.5x base TTL — evict sooner
        """
        score = self.get_popularity_score(product_id)
        if score >= 10:
            return int(base_ttl * 3)
        elif score >= 3:
            return base_ttl
        else:
            return max(int(base_ttl * 0.5), 10)

    def get_top_products(self, n: int = 50) -> List:
        """Get top N most accessed products (for cache warming)."""
        try:
            return self.client.zrevrange(self.POPULARITY_KEY, 0, n - 1, withscores=True)
        except Exception:
            return []

    # 
    # Product Summary Cache
    # 

    def get_product_summary(self, product_id: str) -> Optional[Dict[str, Any]]:
        """Get cached product summary by ID. Returns None on miss."""
        key = self._key(f"prod_summary:{product_id}")
        try:
            cached = self.client.get(key)
            if cached:
                return json.loads(cached)
            return None
        except Exception as e:
            print(f"Cache read error for {key}: {e}")
            return None


    def set_product_summary(self, product_id: str, summary: Dict[str, Any], adaptive: bool = False) -> bool:
        """Cache a product summary (TTL: 5 min base, adaptive if enabled)."""
        key = self._key(f"prod_summary:{product_id}")
        ttl = self.get_adaptive_ttl(product_id, self.ttl_product_summary) if adaptive else self.ttl_product_summary
        try:
            self.client.setex(key, ttl, json.dumps(summary))
            return True
        except Exception as e:
            print(f"Cache write error for {key}: {e}")
            return False


    # 
    # Price Cache
    # 

    def get_price(self, product_id: str) -> Optional[Dict[str, Any]]:
        """Get cached price. Returns None on miss."""
        key = self._key(f"price:{product_id}")
        try:
            cached = self.client.get(key)
            if cached:
                return json.loads(cached)
            return None
        except Exception as e:
            print(f"Cache read error for {key}: {e}")
            return None


    def set_price(self, product_id: str, price_data: Dict[str, Any], adaptive: bool = False) -> bool:
        """Cache price (TTL: 60s base, adaptive if enabled)."""
        key = self._key(f"price:{product_id}")
        ttl = self.get_adaptive_ttl(product_id, self.ttl_price) if adaptive else self.ttl_price
        try:
            self.client.setex(key, ttl, json.dumps(price_data))
            return True
        except Exception as e:
            print(f"Cache write error for {key}: {e}")
            return False


    # 
    # Inventory Cache
    # 

    def get_inventory(self, product_id: str) -> Optional[Dict[str, Any]]:
        """Get cached inventory. Returns None on miss."""
        key = self._key(f"inventory:{product_id}")
        try:
            cached = self.client.get(key)
            if cached:
                return json.loads(cached)
            return None
        except Exception as e:
            print(f"Cache read error for {key}: {e}")
            return None


    def set_inventory(self, product_id: str, inventory_data: Dict[str, Any], adaptive: bool = False) -> bool:
        """Cache inventory (TTL: 30s base, adaptive if enabled)."""
        key = self._key(f"inventory:{product_id}")
        ttl = self.get_adaptive_ttl(product_id, self.ttl_inventory) if adaptive else self.ttl_inventory
        try:
            self.client.setex(key, ttl, json.dumps(inventory_data))
            return True
        except Exception as e:
            print(f"Cache write error for {key}: {e}")
            return False


    # 
    # Search Result Cache
    # 

    @staticmethod
    def make_search_key(filters: Dict[str, Any], category: str, page: int = 1, limit: int = 20) -> str:
        """
        Generate a deterministic cache key for a search query.

        Filters are sorted by key to ensure identical queries produce identical keys
        regardless of dict ordering.
        """
        # Remove internal/transient keys that shouldn't affect caching
        stable_filters = {
            k: v for k, v in sorted(filters.items())
            if not k.startswith("_") and v is not None
        }
        raw = json.dumps({"f": stable_filters, "c": category, "p": page, "l": limit}, sort_keys=True)
        return f"search:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"

    def get_search_results(self, cache_key: str) -> Optional[List[Dict[str, Any]]]:
        """Get cached search results. Returns None on miss."""
        key = self._key(cache_key)
        try:
            cached = self.client.get(key)
            if cached:
                return json.loads(cached)
            return None
        except Exception as e:
            print(f"Search cache read error for {key}: {e}")
            return None

    def set_search_results(self, cache_key: str, results: List[Dict[str, Any]], adaptive: bool = False) -> bool:
        """Cache search results. TTL adapts based on popularity of returned products when adaptive=True."""
        key = self._key(cache_key)
        ttl = self.ttl_search
        if adaptive and results:
            # Compute average popularity across result products
            try:
                scores = [self.get_popularity_score(r.get("product_id", "")) for r in results[:10]]
                avg_score = sum(scores) / len(scores) if scores else 0
                if avg_score >= 10:
                    ttl = int(self.ttl_search * 3)   # Hot search: 15 min
                elif avg_score >= 3:
                    ttl = self.ttl_search             # Warm: 5 min
                else:
                    ttl = max(int(self.ttl_search * 0.5), 30)  # Cold: 2.5 min (min 30s)
            except Exception:
                pass  # Fall back to default TTL
        try:
            self.client.setex(key, ttl, json.dumps(results))
            return True
        except Exception as e:
            print(f"Search cache write error for {key}: {e}")
            return False


    # 
    # Brand / Category Index Queries (uses existing Redis sets)
    # 

    def get_product_ids_by_filters(
        self, category: Optional[str] = None, brand: Optional[str] = None
    ) -> Optional[Set[str]]:
        """
        Fast set intersection on brand:{name} and category:{name} Redis sets.

        These sets are populated by populate_all_databases.py and contain
        product_id members. Returns None if Redis is unavailable or sets
        don't exist.
        """
        try:
            keys = []
            if category:
                keys.append(f"category:{category}")
            if brand:
                keys.append(f"brand:{brand}")
            if not keys:
                return None
            if len(keys) == 1:
                result = self.client.smembers(keys[0])
            else:
                result = self.client.sinter(*keys)
            return result if result else None
        except Exception as e:
            print(f"Index query error: {e}")
            return None


    # 
    # Cache Invalidation
    # 

    def invalidate_product(self, product_id: str) -> bool:
        """Invalidate all cached data for a product (summary, price, inventory)."""
        keys = [
            self._key(f"prod_summary:{product_id}"),
            self._key(f"price:{product_id}"),
            self._key(f"inventory:{product_id}")
        ]
        try:
            self.client.delete(*keys)
            return True
        except Exception as e:
            print(f"Cache invalidation error for {product_id}: {e}")
            return False

    def invalidate_search_cache(self) -> int:
        """Invalidate all cached search results. Returns count of keys deleted."""
        try:
            pattern = self._key("search:*")
            keys = list(self.client.scan_iter(match=pattern, count=100))
            if keys:
                return self.client.delete(*keys)
            return 0
        except Exception as e:
            print(f"Search cache invalidation error: {e}")
            return 0

    def flush_all(self) -> bool:
        """Flush entire cache. Use carefully — only for maintenance or bulk updates."""
        try:
            self.client.flushdb()
            return True
        except Exception as e:
            print(f"Cache flush error: {e}")
            return False

    # 
    # Session persistence (mcp:session:{session_id})
    # 

    def get_session_data(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load session blob from Redis. Returns None if missing or on error."""
        key = self._key(f"session:{session_id}")
        try:
            cached = self.client.get(key)
            if cached:
                return json.loads(cached)
            return None
        except Exception as e:
            print(f"Session read error for {key}: {e}")
            return None

    def set_session_data(self, session_id: str, data: Dict[str, Any], ttl_seconds: int = 3600) -> bool:
        """Persist session blob to Redis (default TTL 1 hour)."""
        key = self._key(f"session:{session_id}")
        try:
            self.client.setex(key, ttl_seconds, json.dumps(data))
            return True
        except Exception as e:
            print(f"Session write error for {key}: {e}")
            return False

    def delete_session_data(self, session_id: str) -> bool:
        """Remove session from Redis (on domain switch)."""
        key = self._key(f"session:{session_id}")
        try:
            self.client.delete(key)
            return True
        except Exception as e:
            print(f"Session delete error for {key}: {e}")
            return False


# Global cache client instances
# MCP cache: product data, prices, inventory, search results (db=0)
# Agent cache: sessions, conversations, context (db=1)
cache_client = CacheClient(namespace="mcp")
agent_cache_client = CacheClient(namespace="agent")
