"""
Supabase cart and checkout for signed-in users.

Cart table schema:
  id uuid PK default gen_random_uuid()
  user_id uuid not null FK auth.users(id) ON DELETE CASCADE
  product_id text not null
  product_snapshot jsonb not null
  quantity integer not null default 1
  created_at timestamptz default now()
  UNIQUE (user_id, product_id)

Checkout: for each cart row, decrement products.inventory by quantity; then delete cart rows for user.
Prefers DATABASE_URL (SQLAlchemy, bypasses RLS) over SUPABASE_KEY (REST API, blocked by RLS).
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mcp.supabase_cart")

_cart_client: Optional[Any] = None


def get_supabase_cart_client() -> Optional[Any]:
    """Return the singleton cart client (SQLAlchemy preferred, REST fallback), or None."""
    global _cart_client
    if _cart_client is not None:
        return _cart_client
    # Prefer DATABASE_URL — bypasses RLS on the cart table
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        try:
            _cart_client = _SQLAlchemyCartClient(db_url)
            logger.info("Using SQLAlchemy cart client via DATABASE_URL")
            return _cart_client
        except Exception as e:
            logger.warning("SQLAlchemy cart client init failed: %s", e)
    # Fall back to REST API (may fail if RLS blocks anon key)
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        logger.debug("SUPABASE_URL or key not set — Supabase cart unavailable")
        return None
    try:
        _cart_client = SupabaseCartClient(url, key)
        return _cart_client
    except Exception as e:
        logger.warning("Supabase cart client init failed: %s", e)
        return None


class SupabaseCartClient:
    """
    Cart and checkout against Supabase: cart table and products.inventory.
    All methods take user_id (UUID string) for the signed-in user.
    """

    def __init__(self, base_url: str, key: str) -> None:
        import httpx
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            timeout=30.0,
        )

    def get_cart(self, user_id: str) -> List[Dict[str, Any]]:
        """Return cart rows for user. Each item: id, product_id, product_snapshot, quantity."""
        logger.info("supabase_cart: method=get_cart user_id=%s", user_id)
        try:
            resp = self._client.get(
                "/rest/v1/cart",
                params={"user_id": f"eq.{user_id}", "order": "created_at.asc"},
            )
            resp.raise_for_status()
            rows = resp.json()
            result = [r for r in rows] if isinstance(rows, list) else []
            logger.info("supabase_cart: method=get_cart user_id=%s result=success row_count=%s", user_id, len(result))
            return result
        except Exception as e:
            logger.error("supabase_cart: method=get_cart user_id=%s result=error error=%s", user_id, e)
            return []

    def add_to_cart(
        self,
        user_id: str,
        product_id: str,
        product_snapshot: Dict[str, Any],
        quantity: int = 1,
    ) -> tuple[bool, Optional[str]]:
        """
        Upsert cart row: add or increment quantity.
        Returns (success, error_message).
        Validates product exists and has sufficient inventory when adding new row.
        """
        logger.info("supabase_cart: method=add_to_cart user_id=%s product_id=%s quantity=%s", user_id, product_id, quantity)
        try:
            existing = self._client.get(
                "/rest/v1/cart",
                params={"user_id": f"eq.{user_id}", "product_id": f"eq.{product_id}"},
            )
            existing.raise_for_status()
            rows = existing.json()
            if isinstance(rows, list) and len(rows) > 0:
                row = rows[0]
                new_qty = (row.get("quantity") or 0) + quantity
                return self._update_quantity(user_id, product_id, new_qty)
            # New row: validate product & check inventory.
            # If a product_snapshot is provided, trust it (products table has RLS that
            # blocks the anon key from reading rows via the REST API).  Only hit the
            # REST API when no snapshot is available.
            if product_snapshot:
                inv = product_snapshot.get("inventory")
            else:
                prod = self._get_product(product_id)
                if prod is None:
                    return False, "Product not found"
                inv = prod.get("inventory")
            if inv is not None:
                try:
                    if int(inv) < quantity:
                        return False, "Insufficient inventory"
                except (TypeError, ValueError):
                    pass
            payload = {
                "user_id": user_id,
                "product_id": product_id,
                "product_snapshot": product_snapshot,
                "quantity": quantity,
            }
            resp = self._client.post(
                "/rest/v1/cart",
                json=payload,
                headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            )
            if resp.status_code in (200, 201):
                logger.info("supabase_cart: method=add_to_cart user_id=%s product_id=%s result=success", user_id, product_id)
                return True, None
            # Conflict: row exists (e.g. race), try increment
            if resp.status_code == 409 or "duplicate" in (resp.text or "").lower():
                return self.add_to_cart(user_id, product_id, product_snapshot, quantity)
            return False, resp.text or f"HTTP {resp.status_code}"
        except Exception as e:
            logger.error("supabase_cart: method=add_to_cart user_id=%s product_id=%s result=error error=%s", user_id, product_id, e)
            return False, str(e)

    def _get_product(self, product_id: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._client.get(
                "/rest/v1/products",
                params={"id": f"eq.{product_id}", "limit": "1"},
            )
            resp.raise_for_status()
            rows = resp.json()
            return rows[0] if isinstance(rows, list) and rows else None
        except Exception:
            return None

    def remove_from_cart(self, user_id: str, product_id: str) -> tuple[bool, Optional[str]]:
        logger.info("supabase_cart: method=remove_from_cart user_id=%s product_id=%s", user_id, product_id)
        try:
            resp = self._client.delete(
                "/rest/v1/cart",
                params={"user_id": f"eq.{user_id}", "product_id": f"eq.{product_id}"},
            )
            resp.raise_for_status()
            logger.info("supabase_cart: method=remove_from_cart user_id=%s product_id=%s result=success", user_id, product_id)
            return True, None
        except Exception as e:
            logger.error("supabase_cart: method=remove_from_cart user_id=%s product_id=%s result=error error=%s", user_id, product_id, e)
            return False, str(e)

    def _update_quantity(
        self, user_id: str, product_id: str, quantity: int
    ) -> tuple[bool, Optional[str]]:
        logger.info("supabase_cart: method=update_quantity user_id=%s product_id=%s quantity=%s", user_id, product_id, quantity)
        if quantity <= 0:
            return self.remove_from_cart(user_id, product_id)
        try:
            resp = self._client.patch(
                "/rest/v1/cart",
                params={"user_id": f"eq.{user_id}", "product_id": f"eq.{product_id}"},
                json={"quantity": quantity},
            )
            resp.raise_for_status()
            logger.info("supabase_cart: method=update_quantity user_id=%s product_id=%s result=success", user_id, product_id)
            return True, None
        except Exception as e:
            logger.error("supabase_cart: method=update_quantity user_id=%s product_id=%s result=error error=%s", user_id, product_id, e)
            return False, str(e)

    def update_quantity(
        self, user_id: str, product_id: str, quantity: int
    ) -> tuple[bool, Optional[str]]:
        """Set cart item quantity (0 = remove)."""
        return self._update_quantity(user_id, product_id, quantity)

    def checkout(
        self, user_id: str
    ) -> tuple[bool, Optional[str], Optional[str], Optional[List[str]]]:
        """
        Checkout: decrement products.inventory for each cart item, then clear cart.
        Returns (success, order_id, error_message, sold_out_ids).
        """
        logger.info("supabase_cart: method=checkout user_id=%s", user_id)
        cart_rows = self.get_cart(user_id)
        if not cart_rows:
            logger.info("supabase_cart: method=checkout user_id=%s result=error error=Cart is empty", user_id)
            return False, None, "Cart is empty", None

        sold_out: List[str] = []
        for row in cart_rows:
            pid = row.get("product_id")
            qty = int(row.get("quantity") or 0)
            if not pid or qty <= 0:
                continue
            prod = self._get_product(pid)
            if prod is None:
                sold_out.append(pid)
                continue
            inv = prod.get("inventory")
            if inv is not None:
                available = int(inv)
                if available < qty:
                    sold_out.append(pid)
        if sold_out:
            logger.info("supabase_cart: method=checkout user_id=%s result=error error=out_of_stock sold_out=%s", user_id, sold_out)
            return False, None, "Some items are out of stock", sold_out

        for row in cart_rows:
            pid = row.get("product_id")
            qty = int(row.get("quantity") or 0)
            if not pid or qty <= 0:
                continue
            ok, err = self._decrement_inventory(pid, qty)
            if not ok:
                logger.error("supabase_cart: method=checkout user_id=%s result=error error=decrement_inventory_failed product_id=%s err=%s", user_id, pid, err)
                return False, None, "Failed to update inventory", None

        try:
            resp = self._client.delete(
                "/rest/v1/cart",
                params={"user_id": f"eq.{user_id}"},
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error("supabase_cart: method=checkout user_id=%s result=error error=clear_cart_failed err=%s", user_id, e)
            return False, None, str(e), None

        order_id = f"order-{uuid.uuid4().hex[:12]}"
        logger.info("supabase_cart: method=checkout user_id=%s result=success order_id=%s", user_id, order_id)
        return True, order_id, None, None

    def _decrement_inventory(self, product_id: str, by: int) -> tuple[bool, Optional[str]]:
        logger.info("supabase_cart: method=_decrement_inventory product_id=%s by=%s", product_id, by)
        try:
            resp = self._client.get(
                "/rest/v1/products",
                params={"id": f"eq.{product_id}", "select": "inventory", "limit": "1"},
            )
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list) or not rows:
                return False, "Product not found"
            current = rows[0].get("inventory")
            if current is None:
                return True, None
            new_val = max(0, int(current) - by)
            patch = self._client.patch(
                "/rest/v1/products",
                params={"id": f"eq.{product_id}"},
                json={"inventory": new_val},
            )
            patch.raise_for_status()
            return True, None
        except Exception as e:
            return False, str(e)


# ---------------------------------------------------------------------------
# SQLAlchemy cart client — uses DATABASE_URL, bypasses RLS
# ---------------------------------------------------------------------------

class _SQLAlchemyCartClient:
    """
    Cart and checkout via direct Postgres connection (DATABASE_URL).
    Bypasses Supabase RLS so the backend service role can read/write cart rows.
    Same public interface as SupabaseCartClient.
    """

    def __init__(self, db_url: str) -> None:
        try:
            from sqlalchemy import create_engine
        except ImportError:
            raise RuntimeError("sqlalchemy not installed")
        self._engine = create_engine(
            db_url,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=2,
            connect_args={"connect_timeout": 15},
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_cart(self, user_id: str) -> List[Dict[str, Any]]:
        logger.info("sqla_cart: method=get_cart user_id=%s", user_id)
        try:
            from sqlalchemy import text as sa_text
            import json as _json
            with self._engine.connect() as conn:
                rows = conn.execute(
                    sa_text(
                        "SELECT id, product_id, product_snapshot, quantity "
                        "FROM cart WHERE user_id = :uid ORDER BY created_at ASC"
                    ),
                    {"uid": user_id},
                ).fetchall()
            result = []
            for r in rows:
                snapshot = r[2]
                if isinstance(snapshot, str):
                    try:
                        snapshot = _json.loads(snapshot)
                    except Exception:
                        pass
                result.append({
                    "id": str(r[0]),
                    "product_id": r[1],
                    "product_snapshot": snapshot,
                    "quantity": r[3],
                })
            logger.info("sqla_cart: method=get_cart user_id=%s row_count=%s", user_id, len(result))
            return result
        except Exception as e:
            logger.error("sqla_cart: method=get_cart user_id=%s error=%s", user_id, e)
            return []

    def add_to_cart(
        self,
        user_id: str,
        product_id: str,
        product_snapshot: Dict[str, Any],
        quantity: int = 1,
    ) -> tuple[bool, Optional[str]]:
        logger.info("sqla_cart: method=add_to_cart user_id=%s product_id=%s qty=%s", user_id, product_id, quantity)
        try:
            from sqlalchemy import text as sa_text
            import json as _json
            snapshot_json = _json.dumps(product_snapshot)
            with self._engine.begin() as conn:
                # Check inventory from snapshot
                inv = product_snapshot.get("inventory") if product_snapshot else None
                if inv is not None:
                    try:
                        if int(inv) < quantity:
                            return False, "Insufficient inventory"
                    except (TypeError, ValueError):
                        pass
                # Upsert: insert or increment quantity on conflict
                conn.execute(
                    sa_text(
                        "INSERT INTO cart (user_id, product_id, product_snapshot, quantity) "
                        "VALUES (:uid, :pid, CAST(:snap AS jsonb), :qty) "
                        "ON CONFLICT (user_id, product_id) "
                        "DO UPDATE SET quantity = cart.quantity + :qty, "
                        "product_snapshot = EXCLUDED.product_snapshot"
                    ),
                    {"uid": user_id, "pid": product_id, "snap": snapshot_json, "qty": quantity},
                )
            logger.info("sqla_cart: method=add_to_cart user_id=%s product_id=%s result=success", user_id, product_id)
            return True, None
        except Exception as e:
            logger.error("sqla_cart: method=add_to_cart user_id=%s product_id=%s error=%s", user_id, product_id, e)
            return False, str(e)

    def remove_from_cart(self, user_id: str, product_id: str) -> tuple[bool, Optional[str]]:
        logger.info("sqla_cart: method=remove_from_cart user_id=%s product_id=%s", user_id, product_id)
        try:
            from sqlalchemy import text as sa_text
            with self._engine.begin() as conn:
                conn.execute(
                    sa_text("DELETE FROM cart WHERE user_id = :uid AND product_id = :pid"),
                    {"uid": user_id, "pid": product_id},
                )
            logger.info("sqla_cart: method=remove_from_cart user_id=%s product_id=%s result=success", user_id, product_id)
            return True, None
        except Exception as e:
            logger.error("sqla_cart: method=remove_from_cart user_id=%s product_id=%s error=%s", user_id, product_id, e)
            return False, str(e)

    def update_quantity(self, user_id: str, product_id: str, quantity: int) -> tuple[bool, Optional[str]]:
        """Set cart item quantity (0 = remove)."""
        logger.info("sqla_cart: method=update_quantity user_id=%s product_id=%s qty=%s", user_id, product_id, quantity)
        if quantity <= 0:
            return self.remove_from_cart(user_id, product_id)
        try:
            from sqlalchemy import text as sa_text
            with self._engine.begin() as conn:
                conn.execute(
                    sa_text(
                        "UPDATE cart SET quantity = :qty "
                        "WHERE user_id = :uid AND product_id = :pid"
                    ),
                    {"uid": user_id, "pid": product_id, "qty": quantity},
                )
            logger.info("sqla_cart: method=update_quantity user_id=%s product_id=%s result=success", user_id, product_id)
            return True, None
        except Exception as e:
            logger.error("sqla_cart: method=update_quantity user_id=%s product_id=%s error=%s", user_id, product_id, e)
            return False, str(e)

    def checkout(
        self, user_id: str
    ) -> tuple[bool, Optional[str], Optional[str], Optional[List[str]]]:
        """Checkout: decrement products.inventory for each cart item, then clear cart."""
        logger.info("sqla_cart: method=checkout user_id=%s", user_id)
        cart_rows = self.get_cart(user_id)
        if not cart_rows:
            return False, None, "Cart is empty", None

        try:
            from sqlalchemy import text as sa_text
            sold_out: List[str] = []
            with self._engine.begin() as conn:
                for row in cart_rows:
                    pid = row.get("product_id")
                    qty = int(row.get("quantity") or 0)
                    if not pid or qty <= 0:
                        continue
                    # Check current inventory
                    result = conn.execute(
                        sa_text("SELECT inventory FROM products WHERE id::text = :pid LIMIT 1"),
                        {"pid": pid},
                    ).fetchone()
                    if result is None:
                        sold_out.append(pid)
                        continue
                    inv = result[0]
                    if inv is not None and int(inv) < qty:
                        sold_out.append(pid)

                if sold_out:
                    logger.info("sqla_cart: method=checkout user_id=%s out_of_stock=%s", user_id, sold_out)
                    return False, None, "Some items are out of stock", sold_out

                # Decrement inventory and clear cart
                for row in cart_rows:
                    pid = row.get("product_id")
                    qty = int(row.get("quantity") or 0)
                    if not pid or qty <= 0:
                        continue
                    conn.execute(
                        sa_text(
                            "UPDATE products SET inventory = GREATEST(0, inventory - :qty) "
                            "WHERE id::text = :pid"
                        ),
                        {"pid": pid, "qty": qty},
                    )
                conn.execute(
                    sa_text("DELETE FROM cart WHERE user_id = :uid"),
                    {"uid": user_id},
                )

            order_id = f"order-{uuid.uuid4().hex[:12]}"
            logger.info("sqla_cart: method=checkout user_id=%s result=success order_id=%s", user_id, order_id)
            return True, order_id, None, None
        except Exception as e:
            logger.error("sqla_cart: method=checkout user_id=%s error=%s", user_id, e)
            return False, None, str(e), None
