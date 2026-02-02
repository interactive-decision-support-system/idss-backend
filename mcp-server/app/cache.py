"""
Redis cache layer for hot data (price, inventory, product summaries).

Redis is ONLY a cache, never the source of truth.
Postgres is always authoritative.

Cache keys follow a clear naming pattern:
- prod_summary:{product_id}
- price:{product_id}
- inventory:{product_id}
"""

import redis
import json
import os
from typing import Optional, Dict, Any
from datetime import timedelta


class CacheClient:
    """
    Redis cache client with clear TTL management and cache-hit tracking.
    
    Supports separate namespaces for MCP vs Agent caching:
    - MCP cache: mcp:{key} (product data, prices, inventory)
    - Agent cache: agent:{key} (sessions, conversations, context)
    """
    
    def __init__(self, namespace: str = "mcp"):
        """
        Initialize Redis connection.
        Uses environment variables for configuration.
        
        Args:
            namespace: Cache namespace - "mcp" for MCP data, "agent" for agent data
        """
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        
        # Separate Redis databases for MCP vs Agent
        # MCP uses db=0, Agent uses db=1
        if namespace == "agent":
            redis_db = int(os.getenv("REDIS_DB_AGENT", "1"))
        else:
            redis_db = int(os.getenv("REDIS_DB_MCP", "0"))
        
        self.namespace = namespace
        self.client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            decode_responses=True,  # Automatically decode bytes to strings
            socket_connect_timeout=2,
            socket_timeout=2
        )
        
        # TTL configuration (in seconds)
        # Product summaries change less frequently than price/inventory
        self.ttl_product_summary = int(os.getenv("CACHE_TTL_PRODUCT_SUMMARY", "300"))  # 5 minutes
        self.ttl_price = int(os.getenv("CACHE_TTL_PRICE", "60"))  # 1 minute
        self.ttl_inventory = int(os.getenv("CACHE_TTL_INVENTORY", "30"))  # 30 seconds
        
        # Agent-specific TTLs (longer for session data)
        self.ttl_agent_session = int(os.getenv("CACHE_TTL_AGENT_SESSION", "3600"))  # 1 hour
        self.ttl_agent_context = int(os.getenv("CACHE_TTL_AGENT_CONTEXT", "1800"))  # 30 minutes
    
    def _key(self, key: str) -> str:
        """Prefix key with namespace."""
        return f"{self.namespace}:{key}"
    
    
    def ping(self) -> bool:
        """
        Check if Redis is reachable.
        Returns True if healthy, False otherwise.
        """
        try:
            return self.client.ping()
        except Exception:
            return False
    
    
    # 
    # Product Summary Cache
    # 
    
    def get_product_summary(self, product_id: str) -> Optional[Dict[str, Any]]:
        """
        Get cached product summary by ID.
        Returns None if not in cache (cache miss).
        """
        key = self._key(f"prod_summary:{product_id}")
        try:
            cached = self.client.get(key)
            if cached:
                return json.loads(cached)
            return None
        except Exception as e:
            # If cache fails, just return None (treat as cache miss)
            # Don't let cache failures break the application
            print(f"Cache read error for {key}: {e}")
            return None
    
    
    def set_product_summary(self, product_id: str, summary: Dict[str, Any]) -> bool:
        """
        Cache a product summary with appropriate TTL.
        Returns True if successful, False otherwise.
        """
        key = self._key(f"prod_summary:{product_id}")
        try:
            self.client.setex(
                key,
                self.ttl_product_summary,
                json.dumps(summary)
            )
            return True
        except Exception as e:
            print(f"Cache write error for {key}: {e}")
            return False
    
    
    # 
    # Price Cache
    # 
    
    def get_price(self, product_id: str) -> Optional[Dict[str, Any]]:
        """
        Get cached price information.
        Returns None if not in cache.
        """
        key = self._key(f"price:{product_id}")
        try:
            cached = self.client.get(key)
            if cached:
                return json.loads(cached)
            return None
        except Exception as e:
            print(f"Cache read error for {key}: {e}")
            return None
    
    
    def set_price(self, product_id: str, price_data: Dict[str, Any]) -> bool:
        """
        Cache price information with short TTL.
        Price is critical and changes frequently, so we use a short TTL.
        """
        key = self._key(f"price:{product_id}")
        try:
            self.client.setex(
                key,
                self.ttl_price,
                json.dumps(price_data)
            )
            return True
        except Exception as e:
            print(f"Cache write error for {key}: {e}")
            return False
    
    
    # 
    # Inventory Cache
    # 
    
    def get_inventory(self, product_id: str) -> Optional[Dict[str, Any]]:
        """
        Get cached inventory information.
        Returns None if not in cache.
        """
        key = self._key(f"inventory:{product_id}")
        try:
            cached = self.client.get(key)
            if cached:
                return json.loads(cached)
            return None
        except Exception as e:
            print(f"Cache read error for {key}: {e}")
            return None
    
    
    def set_inventory(self, product_id: str, inventory_data: Dict[str, Any]) -> bool:
        """
        Cache inventory information with very short TTL.
        Inventory is the most volatile data, so we use the shortest TTL.
        """
        key = self._key(f"inventory:{product_id}")
        try:
            self.client.setex(
                key,
                self.ttl_inventory,
                json.dumps(inventory_data)
            )
            return True
        except Exception as e:
            print(f"Cache write error for {key}: {e}")
            return False
    
    
    # 
    # Cache Invalidation
    # 
    
    def invalidate_product(self, product_id: str) -> bool:
        """
        Invalidate all cached data for a product.
        Call this when product data changes in Postgres.
        """
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
    
    
    def flush_all(self) -> bool:
        """
        Flush entire cache.
        Use this carefully - only for maintenance or after bulk updates.
        """
        try:
            self.client.flushdb()
            return True
        except Exception as e:
            print(f"Cache flush error: {e}")
            return False

    # 
    # Session persistence (mcp:session:{session_id} per week4 / bigerrorjan29)
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
# MCP cache: product data, prices, inventory (db=0)
# Agent cache: sessions, conversations, context (db=1)
cache_client = CacheClient(namespace="mcp")
agent_cache_client = CacheClient(namespace="agent")
