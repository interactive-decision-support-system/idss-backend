"""
IDSS Redis Caching Policy — defines what gets cached, TTLs, and invalidation rules.

This module documents the caching strategy. It is imported by cache.py for
TTL constants and by endpoints.py for cache-key generation.

Architecture:
  PostgreSQL → source of truth (products, prices, inventory)
  Redis      → read-through cache (hot data only, TTL-based expiry)
  Neo4j      → knowledge graph (relationships, not cached)

All cache entries expire automatically via TTL. No manual invalidation
is needed for normal operation. Invalidation methods exist for:
  - Product updates (price change, stock change) → invalidate_product()
  - Bulk data refreshes → invalidate_search_cache() or flush_all()
"""

# ────────────────────────────────────────────────────────────────────────────
# Cache Policy Table
# ────────────────────────────────────────────────────────────────────────────
#
# Data Type        | Key Pattern               | TTL     | Rationale
# -----------------+---------------------------+---------+--------------------------------
# Product summary  | mcp:prod_summary:{id}     | 5 min   | Rarely changes; acceptable staleness
# Price            | mcp:price:{id}            | 60 sec  | Prices may change; short TTL
# Inventory        | mcp:inventory:{id}        | 30 sec  | Most volatile; shortest TTL
# Search results   | mcp:search:{sha256[:16]}  | 5 min   | Same query = same results (within TTL)
# Agent session    | mcp:session:{session_id}  | 1 hour  | Conversation context; long-lived
# Brand index      | brand:{name}              | None    | Persistent set; refreshed by populate script
# Category index   | category:{name}           | None    | Persistent set; refreshed by populate script
#
# ────────────────────────────────────────────────────────────────────────────
# Consistency Expectations
# ────────────────────────────────────────────────────────────────────────────
#
# - Price/inventory may be stale by up to their TTL (60s / 30s).
#   This is acceptable for browsing; checkout always reads from Postgres.
# - Search results may be up to 5 min stale. A product added to Postgres
#   won't appear in cached search results until the cache entry expires.
# - Brand/category indexes are only refreshed when populate_all_databases.py
#   is re-run. New products won't appear in these indexes until then.
#
# ────────────────────────────────────────────────────────────────────────────
# Cache Invalidation Strategy
# ────────────────────────────────────────────────────────────────────────────
#
# Primary: TTL-based (automatic expiry, no coordination needed)
# Secondary: Explicit invalidation for known mutations:
#   - checkout/cart operations → invalidate_product(product_id)
#   - admin price updates → invalidate_product(product_id)
#   - bulk data refresh → invalidate_search_cache() + flush_all()
#
# We do NOT invalidate search cache on individual product changes because:
#   1. Search results contain multiple products — invalidating one doesn't help
#   2. TTL-based expiry (5 min) is acceptable for search freshness
#   3. Avoids complexity of tracking which search keys contain which products

# ────────────────────────────────────────────────────────────────────────────
# Bélády-Inspired Adaptive TTL Policy
# ────────────────────────────────────────────────────────────────────────────
#
# Classic Bélády's algorithm (optimal page replacement) evicts the item that
# will not be needed for the longest time in the future. Since we cannot
# predict the future, we approximate using access frequency as a proxy:
# items accessed more often are likely to be accessed again sooner.
#
# Implementation: Redis sorted set "mcp:popularity:access_count"
#   - Each get_product call increments the product's score via ZINCRBY
#   - O(log N) updates, O(1) score lookups
#
# Access Tier | Threshold  | TTL Multiplier | Product Summary | Price  | Inventory | Search
# -----------+-----------+----------------+-----------------+--------+-----------+--------
# Hot        | ≥10 hits  | 3x             | 15 min          | 3 min  | 90 sec    | 15 min
# Warm       | 3-9 hits  | 1x (base)      | 5 min           | 1 min  | 30 sec    | 5 min
# Cold       | <3 hits   | 0.5x           | 2.5 min         | 30 sec | 15 sec    | 2.5 min
#
# Rationale:
#   - Hot products (popular items users browse repeatedly) stay cached longer,
#     reducing Postgres load and improving response latency for the most
#     common requests.
#   - Cold products (rarely viewed) are evicted sooner, freeing cache memory
#     for items more likely to be requested again.
#   - This approximates Bélády's optimal by keeping items with shorter
#     predicted "time until next access" (high frequency → short interval).
#
# Reference: Bélády, L.A. (1966). "A study of replacement algorithms for
#   a virtual-storage computer." IBM Systems Journal, 5(2), 78-101.
#

# TTL constants (seconds) — used by cache.py via env vars
DEFAULT_TTL_PRODUCT_SUMMARY = 300   # 5 minutes
DEFAULT_TTL_PRICE = 60              # 1 minute
DEFAULT_TTL_INVENTORY = 30          # 30 seconds
DEFAULT_TTL_SEARCH = 300            # 5 minutes
DEFAULT_TTL_SESSION = 3600          # 1 hour

# Adaptive TTL tier thresholds
POPULARITY_HOT_THRESHOLD = 10      # ≥10 accesses → hot tier
POPULARITY_WARM_THRESHOLD = 3      # ≥3 accesses → warm tier
TTL_MULTIPLIER_HOT = 3.0           # Hot products get 3x base TTL
TTL_MULTIPLIER_WARM = 1.0          # Warm products get standard TTL
TTL_MULTIPLIER_COLD = 0.5          # Cold products get 0.5x base TTL
TTL_MINIMUM_SECONDS = 10           # Floor — never cache for less than 10s
