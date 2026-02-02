-- Add color and scraped_from_url to products table
-- color: product color (e.g. Silver, Space Gray). Optional.
-- scraped_from_url: URL or domain we scraped from (e.g. mc-demo.mybigcommerce.com). Null for Seed.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'products' AND column_name = 'color'
    ) THEN
        ALTER TABLE products ADD COLUMN color VARCHAR(80);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'products' AND column_name = 'scraped_from_url'
    ) THEN
        ALTER TABLE products ADD COLUMN scraped_from_url VARCHAR(512);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = 'idx_products_scraped_from_url') THEN
        CREATE INDEX idx_products_scraped_from_url ON products(scraped_from_url);
    END IF;
END $$;
