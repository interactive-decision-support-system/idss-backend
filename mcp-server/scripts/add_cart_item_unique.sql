-- Unique (cart_id, product_id) for upsert / idempotent AddToCart.
-- Run: psql mcp_ecommerce -f scripts/add_cart_item_unique.sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_cart_items_cart_product ON cart_items (cart_id, product_id);
