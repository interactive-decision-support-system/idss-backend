"""
Supabase REST client for the 'products' table (electronics, books, etc.).

Mirrors SupabaseVehicleStore pattern: uses httpx directly against the
PostgREST REST API rather than SQLAlchemy, so no DATABASE_URL is needed.

Table schema (Supabase):
  id          UUID  primary key
  title       text
  category    text   e.g. "Electronics", "Books"
  brand       text
  price       numeric  (dollars)
  imageurl    text
  product_type text  e.g. "laptop", "book"
  attributes  jsonb  { ram_gb, storage_gb, screen_size, battery_hours, ... }
  rating      numeric
  rating_count bigint
  inventory   bigint
  link        text   product URL
"""
from __future__ import annotations

import os
import random
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mcp.supabase_product_store")

# ---------------------------------------------------------------------------
# Singleton store (lazy-init)
# ---------------------------------------------------------------------------
_store_cache: Optional["SupabaseProductStore"] = None


def get_product_store() -> "SupabaseProductStore":
    global _store_cache
    if _store_cache is None:
        # Prefer DATABASE_URL (direct Postgres connection — bypasses RLS, correct numeric
        # JSONB comparison).  The REST API path (SupabaseProductStore) is only used when
        # DATABASE_URL is absent, because the anon key is blocked by RLS on the products
        # table even when SUPABASE_URL/SUPABASE_KEY are set.
        db_url = os.environ.get("DATABASE_URL", "")
        if db_url:
            logger.info("Using SQLAlchemy product store via DATABASE_URL")
            _store_cache = _SQLAlchemyProductStore()
        else:
            url = os.environ.get("SUPABASE_URL", "")
            key = os.environ.get("SUPABASE_KEY", "")
            if url and key:
                logger.info("DATABASE_URL absent — using Supabase REST API product store")
                _store_cache = SupabaseProductStore()
            else:
                logger.error("No product store available: set DATABASE_URL or SUPABASE_URL+SUPABASE_KEY")
                _store_cache = _SQLAlchemyProductStore()  # will error gracefully on queries
    return _store_cache


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class SupabaseProductStore:
    """
    Search the Supabase `products` table via its REST API.
    Supports brand, category, product_type, price range, and JSONB spec filters.
    """

    def __init__(self) -> None:
        import httpx
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
        if not url or not key:
            logger.warning("SUPABASE_URL or SUPABASE_KEY not set — product store unavailable")
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(base_url=url, headers=headers, timeout=30.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search_products(
        self,
        filters: Dict[str, Any],
        limit: int = 100,
        exclude_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return up to `limit` product rows matching the given filters.

        Applies progressive filter relaxation:
          1. Full filters (brand + specs + price)
          2. Drop spec filters if empty
          3. Drop price floor if still empty
          4. Drop brand if still empty
          5. Bare category/product_type only
        """
        steps = [
            dict(drop_specs=False, drop_price_min=False, drop_brand=False),
            dict(drop_specs=True,  drop_price_min=False, drop_brand=False),
            dict(drop_specs=True,  drop_price_min=True,  drop_brand=False),
            dict(drop_specs=True,  drop_price_min=False, drop_brand=True),
            dict(drop_specs=True,  drop_price_min=True,  drop_brand=True),
        ]
        for step in steps:
            rows = self._fetch(filters, limit=limit, exclude_ids=exclude_ids, **step)
            if rows:
                return rows
        return []

    def get_by_id(self, product_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single product by UUID."""
        try:
            resp = self._client.get(
                "/rest/v1/products",
                params={"id": f"eq.{product_id}", "limit": "1"},
            )
            resp.raise_for_status()
            rows = resp.json()
            return self._row_to_dict(rows[0]) if rows else None
        except Exception as e:
            logger.error(f"get_by_id failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch(
        self,
        filters: Dict[str, Any],
        limit: int,
        exclude_ids: Optional[List[str]],
        drop_specs: bool,
        drop_price_min: bool,
        drop_brand: bool,
    ) -> List[Dict[str, Any]]:
        """Build PostgREST params and execute the query."""
        # Parse price range — agent may send any of these keys:
        #   price_min_cents / price_max_cents  (integer cents)
        #   price_min / price_max              (dollar strings or floats)
        #   budget                             (e.g. '$1500' — treated as price_max)
        price_min = _parse_price(filters.get("price_min_cents"), filters.get("price_min"))
        price_max = _parse_price(filters.get("price_max_cents"), filters.get("price_max"))

        # 'budget' from the agent = upper price limit
        if price_max is None and filters.get("budget"):
            price_max = _parse_price(None, filters["budget"])

        # Quality floor: if only a max is given, don't return bottom-of-barrel items.
        # Enforce price_min >= price_max * 0.5 so we stay in a sensible quality range.
        if price_max is not None and (price_min is None or price_min < price_max * 0.5):
            price_min = price_max * 0.5

        # Default electronics floor to avoid $0 rows
        category = filters.get("category", "")
        if category.lower() == "electronics" and (price_min is None or price_min < 50.0):
            price_min = 50.0

        # Determine pool size & ordering
        pool_size = min(limit * 3, 300)
        has_price_range = price_min is not None or price_max is not None

        if has_price_range and not drop_price_min:
            # Stratified price sampling (4 bands)
            return self._stratified_fetch(
                filters, price_min, price_max,
                limit=limit,
                exclude_ids=exclude_ids,
                drop_specs=drop_specs,
                drop_brand=drop_brand,
            )

        # No-price-filter: fetch larger pool ordered by id (non-price-biased), then shuffle
        params: List[Tuple[str, str]] = []
        self._add_base_params(params, filters, exclude_ids,
                              price_min=None if drop_price_min else price_min,
                              price_max=price_max,
                              drop_specs=drop_specs,
                              drop_brand=drop_brand)
        params.append(("order", "id.asc"))
        params.append(("limit", str(pool_size)))

        rows = self._get("/rest/v1/products", params)
        random.shuffle(rows)
        return [self._row_to_dict(r) for r in rows[:limit]]

    def _stratified_fetch(
        self,
        filters: Dict[str, Any],
        price_min: Optional[float],
        price_max: Optional[float],
        limit: int,
        exclude_ids: Optional[List[str]],
        drop_specs: bool,
        drop_brand: bool,
    ) -> List[Dict[str, Any]]:
        """Split price range into 4 bands and sample equally from each."""
        lo = price_min or 0.0
        hi = price_max or 10_000.0
        n_strata = 4
        per_stratum = max(limit // n_strata, 5)
        step = (hi - lo) / n_strata
        seen_ids: set = set()
        payloads: List[Dict[str, Any]] = []

        for i in range(n_strata):
            band_lo = lo + i * step
            band_hi = lo + (i + 1) * step if i < n_strata - 1 else hi

            params: List[Tuple[str, str]] = []
            self._add_base_params(params, filters, exclude_ids,
                                  price_min=None, price_max=None,
                                  drop_specs=drop_specs,
                                  drop_brand=drop_brand)
            params.append(("price", f"gte.{band_lo:.2f}"))
            params.append(("price", f"lte.{band_hi:.2f}"))
            params.append(("order", "price.asc"))
            params.append(("limit", str(per_stratum)))

            for row in self._get("/rest/v1/products", params):
                pid = row.get("id")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    payloads.append(self._row_to_dict(row))

        random.shuffle(payloads)
        return payloads[:limit]

    def _add_base_params(
        self,
        params: List[Tuple[str, str]],
        filters: Dict[str, Any],
        exclude_ids: Optional[List[str]],
        price_min: Optional[float],
        price_max: Optional[float],
        drop_specs: bool,
        drop_brand: bool,
    ) -> None:
        """Append filter params in-place."""
        # Always filter to non-zero price
        params.append(("price", "gte.0.01"))

        category = filters.get("category")
        if category:
            # Supabase column is title-cased ('Electronics', 'Books') — normalise
            params.append(("category", f"ilike.{category}"))

        product_type = filters.get("product_type")
        if product_type:
            params.append(("product_type", f"eq.{product_type}"))

        brand = filters.get("brand")
        if brand and not drop_brand and str(brand).lower() not in ("no preference", "any", "", "null"):
            params.append(("brand", f"ilike.*{brand}*"))

        # Books: genre / subcategory filter (stored in attributes or as product_type sub-value)
        genre = filters.get("genre") or filters.get("subcategory")
        if genre and str(genre).lower() not in ("no preference", "any", ""):
            params.append(("attributes->>genre", f"ilike.*{genre}*"))

        if price_min is not None:
            params.append(("price", f"gte.{price_min:.2f}"))
        if price_max is not None:
            params.append(("price", f"lte.{price_max:.2f}"))

        if not drop_specs:
            min_ram = filters.get("min_ram_gb")
            if min_ram:
                params.append(("attributes->>ram_gb", f"gte.{min_ram}"))

            min_storage = filters.get("min_storage_gb")
            if min_storage:
                params.append(("attributes->>storage_gb", f"gte.{min_storage}"))

            min_screen = filters.get("min_screen_size") or filters.get("min_screen_inches")
            if min_screen:
                params.append(("attributes->>screen_size", f"gte.{min_screen}"))

            max_screen = filters.get("max_screen_size")
            if max_screen:
                params.append(("attributes->>screen_size", f"lte.{max_screen}"))

            min_battery = filters.get("min_battery_hours")
            if min_battery:
                params.append(("attributes->>battery_life_hours", f"gte.{min_battery}"))

            storage_type = filters.get("storage_type")
            if storage_type and str(storage_type).upper() in ("SSD", "HDD"):
                params.append(("attributes->>storage_type", f"eq.{storage_type.upper()}"))

            # good_for_* boolean filters (soft constraints — only applied when explicitly set)
            for flag in ("good_for_ml", "good_for_gaming", "good_for_creative", "good_for_web_dev"):
                if filters.get(flag):
                    params.append((f"attributes->>{flag}", "eq.true"))

        if exclude_ids:
            ids_str = ",".join(str(i) for i in exclude_ids)
            params.append(("id", f"not.in.({ids_str})"))

    def _get(self, path: str, params: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
        """Execute a GET request; returns empty list on error."""
        try:
            resp = self._client.get(path, params=params)
            if resp.status_code == 500:
                # Retry without JSONB spec filters (they may not be indexed)
                safe_params = [(k, v) for k, v in params if "->>" not in k]
                resp = self._client.get(path, params=safe_params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Supabase products query failed: {e}")
            return []

    @staticmethod
    def _row_to_dict(row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalise a Supabase products row to the flat dict that format_product() expects.
        """
        attrs = row.get("attributes") or {}
        price_raw = row.get("price")
        price_dollars = float(price_raw) if price_raw else 0.0
        link = row.get("link") or row.get("merchant_product_url")

        return {
            # Identity
            "id": str(row.get("id", "")),
            "product_id": str(row.get("id", "")),
            # Name — Supabase uses 'title' column
            "name": row.get("title") or row.get("name") or "Unknown Product",
            "title": row.get("title") or row.get("name") or "Unknown Product",
            # Pricing
            "price": int(price_dollars),
            # Images — Supabase uses 'imageurl' (no underscore)
            "image_url": row.get("imageurl") or row.get("image_url"),
            # Taxonomy — normalise to title case ("electronics" → "Electronics")
            "category": (row.get("category") or "").title() or None,
            "product_type": row.get("product_type"),
            "brand": row.get("brand"),
            # ---- Laptop specs (confirmed DB attribute keys) ----
            "processor": attrs.get("cpu"),          # DB key: 'cpu'
            "ram": _fmt_gb(attrs.get("ram_gb")),    # DB key: 'ram_gb'
            "storage": _fmt_gb(attrs.get("storage_gb")),  # DB key: 'storage_gb'
            "storage_type": attrs.get("storage_type"),    # DB key: 'storage_type' ('SSD'/'HDD')
            "screen_size": attrs.get("screen_size"),       # DB key: 'screen_size' (inches)
            "refresh_rate_hz": attrs.get("refresh_rate_hz"),
            "resolution": attrs.get("resolution"),
            "battery_life": _fmt_hours(attrs.get("battery_life_hours")),  # DB key: 'battery_life_hours'
            "gpu": attrs.get("gpu") or attrs.get("gpu_model"),
            "os": attrs.get("os") or attrs.get("operating_system"),
            "weight": attrs.get("weight"),
            "color": attrs.get("color"),
            # Social proof
            "rating": float(row["rating"]) if row.get("rating") else None,
            "rating_count": row.get("rating_count"),
            # Listing metadata
            "url": link,
            "listing_url": link,
            # Scrape origin — derived from product URL domain so the frontend can show "From: System76"
            "source": _extract_source(link) or row.get("brand"),
            "inventory": row.get("inventory"),
            # Full attributes blob for anything else
            "attributes": attrs,
            "description": attrs.get("description"),
        }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_gb(val: Any) -> Optional[str]:
    """Format a raw GB integer from the DB into a human-readable string."""
    if val is None:
        return None
    try:
        return f"{int(val)} GB"
    except (TypeError, ValueError):
        return str(val)

def _fmt_hours(val: Any) -> Optional[str]:
    """Format a raw battery hours integer from the DB into a human-readable string."""
    if val is None:
        return None
    try:
        return f"{int(val)} hrs"
    except (TypeError, ValueError):
        return str(val)


def _extract_source(url: Optional[str]) -> Optional[str]:
    """
    Derive a human-readable source label from a product URL.
    Examples:
      "https://system76.com/laptops/oryx" → "System76"
      "https://frame.work/products/..."   → "Framework"
      "https://www.lenovo.com/..."        → "Lenovo"
    Falls back to None if URL is missing or unparseable.
    """
    if not url:
        return None
    # Explicit overrides for domains that produce misleading names via generic parsing
    _DOMAIN_OVERRIDES: Dict[str, str] = {
        "frame.work": "Framework",
    }
    try:
        from urllib.parse import urlparse
        hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if not hostname:
            return None
        if hostname in _DOMAIN_OVERRIDES:
            return _DOMAIN_OVERRIDES[hostname]
        parts = hostname.split(".")
        # Standard TLDs → use second-to-last part
        if len(parts) >= 2 and parts[-1] in ("com", "net", "org", "io", "co", "store", "us", "uk", "work"):
            name = parts[-2]
        else:
            name = parts[0]
        return name.capitalize()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# SQLAlchemy fallback (used when SUPABASE_KEY is not set)
# Queries the same `products` table via DATABASE_URL with proper numeric
# JSONB casting — avoids PostgREST string-comparison bugs on attributes.
# ---------------------------------------------------------------------------

class _SQLAlchemyProductStore:
    """
    Fallback product store using SQLAlchemy + DATABASE_URL.
    Same public interface as SupabaseProductStore.
    JSONB spec filters (ram_gb, screen_size, etc.) are applied in Python
    after a price/category/brand fetch, which gives correct numeric comparison.
    """

    def __init__(self) -> None:
        try:
            from sqlalchemy import create_engine
        except ImportError:
            logger.error("sqlalchemy not installed — cannot use DATABASE_URL product store")
            self._engine = None
            return

        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            logger.error("DATABASE_URL not set — no product store available")
            self._engine = None
            return

        try:
            # pool_size=3 + max_overflow=2 keeps us within Supabase session-mode limits
            self._engine = create_engine(
                db_url,
                pool_pre_ping=True,
                pool_size=3,
                max_overflow=2,
                connect_args={"connect_timeout": 15},
            )
        except Exception as e:
            logger.error(f"SQLAlchemy engine creation failed: {e}")
            self._engine = None

    def search_products(
        self,
        filters: Dict[str, Any],
        limit: int = 100,
        exclude_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        import json as _json
        try:
            from sqlalchemy import text as sa_text
        except ImportError:
            logger.error("sqlalchemy not installed — cannot fallback to DATABASE_URL")
            return []

        if self._engine is None:
            logger.error("SQLAlchemy engine not available")
            return []

        # Parse prices
        price_min = _parse_price(filters.get("price_min_cents"), filters.get("price_min"))
        price_max = _parse_price(filters.get("price_max_cents"), filters.get("price_max"))
        if price_max is None and filters.get("budget"):
            price_max = _parse_price(None, filters["budget"])
        if price_max is not None and (price_min is None or price_min < price_max * 0.5):
            price_min = price_max * 0.5
        category = filters.get("category", "")
        if category.lower() == "electronics" and (price_min is None or price_min < 50.0):
            price_min = 50.0

        conditions = ["price > 0.01"]
        params: Dict[str, Any] = {}

        if category:
            conditions.append("LOWER(category) = LOWER(:category)")
            params["category"] = category

        product_type = filters.get("product_type")
        if product_type:
            conditions.append("product_type = :product_type")
            params["product_type"] = product_type

        brand = filters.get("brand")
        if brand and str(brand).lower() not in ("no preference", "any", ""):
            conditions.append("brand ILIKE :brand")
            params["brand"] = f"%{brand}%"

        genre = filters.get("genre") or filters.get("subcategory")
        if genre and str(genre).lower() not in ("no preference", "any", ""):
            conditions.append("attributes->>'genre' ILIKE :genre")
            params["genre"] = f"%{genre}%"

        if price_min is not None:
            conditions.append("price >= :price_min")
            params["price_min"] = price_min
        if price_max is not None:
            conditions.append("price <= :price_max")
            params["price_max"] = price_max

        if exclude_ids:
            conditions.append("id != ALL(:exclude_ids)")
            params["exclude_ids"] = list(exclude_ids)

        where = " AND ".join(conditions)
        # Fetch a larger pool so Python-side spec filtering has enough to work with
        fetch_limit = min(limit * 8, 800)
        sql = sa_text(f"SELECT * FROM products WHERE {where} ORDER BY RANDOM() LIMIT :fetch_limit")
        params["fetch_limit"] = fetch_limit

        try:
            with self._engine.connect() as conn:
                result = conn.execute(sql, params)
                rows = [dict(r._mapping) for r in result]
        except Exception as e:
            logger.error(f"SQLAlchemy products query failed: {e}")
            return []

        # Python-side JSONB spec filtering (correct numeric comparison)
        min_ram = filters.get("min_ram_gb")
        min_storage = filters.get("min_storage_gb")
        min_screen = filters.get("min_screen_size") or filters.get("min_screen_inches")
        max_screen = filters.get("max_screen_size")
        min_battery = filters.get("min_battery_hours")
        storage_type = filters.get("storage_type")
        good_for_flags = {k: filters.get(k) for k in (
            "good_for_ml", "good_for_gaming", "good_for_creative", "good_for_web_dev"
        ) if filters.get(k)}

        def _num(val, default=0):
            """Safely coerce a JSONB value (may arrive as str or int) to float."""
            if val is None:
                return default
            try:
                return float(val)
            except (TypeError, ValueError):
                return default

        filtered = []
        for row in rows:
            attrs = row.get("attributes") or {}
            if isinstance(attrs, str):
                try:
                    attrs = _json.loads(attrs)
                except Exception:
                    attrs = {}

            if min_ram and _num(attrs.get("ram_gb")) < int(min_ram):
                continue
            if min_storage and _num(attrs.get("storage_gb")) < int(min_storage):
                continue
            if min_screen and _num(attrs.get("screen_size")) < float(min_screen):
                continue
            if max_screen and _num(attrs.get("screen_size"), 999) > float(max_screen):
                continue
            if min_battery and _num(attrs.get("battery_life_hours")) < float(min_battery):
                continue
            if storage_type and str(attrs.get("storage_type", "")).upper() != storage_type.upper():
                continue
            if good_for_flags:
                if not all(attrs.get(flag) for flag in good_for_flags):
                    continue
            filtered.append(SupabaseProductStore._row_to_dict(row))

        # If spec filtering wiped everything, fall back to unfiltered (relaxation)
        if not filtered and rows:
            logger.info("SQLAlchemy spec filters returned 0 — returning unfiltered pool")
            filtered = [SupabaseProductStore._row_to_dict(r) for r in rows]

        random.shuffle(filtered)
        return filtered[:limit]

    def get_by_id(self, product_id: str) -> Optional[Dict[str, Any]]:
        if self._engine is None:
            return None
        try:
            from sqlalchemy import text as sa_text
            with self._engine.connect() as conn:
                result = conn.execute(sa_text("SELECT * FROM products WHERE id = :id LIMIT 1"), {"id": product_id})
                row = result.fetchone()
                return SupabaseProductStore._row_to_dict(dict(row._mapping)) if row else None
        except Exception as e:
            logger.error(f"get_by_id (SQLAlchemy) failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Price parsing helpers
# ---------------------------------------------------------------------------

def _parse_price(
    cents_val: Any,
    dollar_val: Any = None,
) -> Optional[float]:
    """Convert cents or dollar values from agent filters to float dollars."""
    if cents_val is not None:
        try:
            return float(cents_val) / 100
        except (TypeError, ValueError):
            pass
    if dollar_val is not None:
        try:
            # Handle strings like "$2000" or "2000"
            cleaned = str(dollar_val).replace("$", "").replace(",", "").strip()
            return float(cleaned)
        except (TypeError, ValueError):
            pass
    return None
