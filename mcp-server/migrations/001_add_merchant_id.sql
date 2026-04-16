-- Add merchant_id column to products (nullable so existing rows keep working)
ALTER TABLE products ADD COLUMN IF NOT EXISTS merchant_id TEXT;

-- Index for per-merchant lookups
CREATE INDEX IF NOT EXISTS idx_products_merchant_id ON products (merchant_id);
