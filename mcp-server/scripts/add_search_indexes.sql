-- Composite indexes for search latency (category + filters).
-- Run: psql mcp_ecommerce -f scripts/add_search_indexes.sql
CREATE INDEX IF NOT EXISTS ix_products_category_product_type ON products (category, product_type);
CREATE INDEX IF NOT EXISTS ix_products_category_brand ON products (category, brand);
CREATE INDEX IF NOT EXISTS ix_products_category_color ON products (category, color);
CREATE INDEX IF NOT EXISTS ix_products_category_gpu_vendor ON products (category, gpu_vendor);
CREATE INDEX IF NOT EXISTS ix_prices_product_id_price_cents ON prices (product_id, price_cents);
