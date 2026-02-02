-- Add source column to products table
-- Stores where the product came from: "Amazon", "WooCommerce", "Shopify", "Temu", "BigCommerce", "Seed", etc.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'products' AND column_name = 'source'
    ) THEN
        ALTER TABLE products ADD COLUMN source VARCHAR(100);
        CREATE INDEX idx_products_source ON products(source);
    END IF;
END $$;
