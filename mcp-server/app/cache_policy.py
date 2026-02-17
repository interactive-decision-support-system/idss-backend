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

# TTL constants (seconds) — used by cache.py via env vars
DEFAULT_TTL_PRODUCT_SUMMARY = 300   # 5 minutes
DEFAULT_TTL_PRICE = 60              # 1 minute
DEFAULT_TTL_INVENTORY = 30          # 30 seconds
DEFAULT_TTL_SEARCH = 300            # 5 minutes
DEFAULT_TTL_SESSION = 3600          # 1 hour
