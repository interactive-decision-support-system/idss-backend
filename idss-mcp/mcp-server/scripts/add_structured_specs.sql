-- Add structured fields for hard-filtering (gaming PC / NVIDIA / device type).
-- Run: psql mcp_ecommerce -f scripts/add_structured_specs.sql

-- product_type: laptop, desktop_pc, gaming_laptop, book, etc.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'product_type') THEN
        ALTER TABLE products ADD COLUMN product_type VARCHAR(50);
        CREATE INDEX ix_products_product_type ON products(product_type);
    END IF;
END $$;

-- gpu_vendor: NVIDIA, AMD, Apple, Intel (for hard filter; NULL = unknown, must not pass when user requires NVIDIA)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'gpu_vendor') THEN
        ALTER TABLE products ADD COLUMN gpu_vendor VARCHAR(50);
        CREATE INDEX ix_products_gpu_vendor ON products(gpu_vendor);
    END IF;
END $$;

-- gpu_model: RTX 4070, etc.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'gpu_model') THEN
        ALTER TABLE products ADD COLUMN gpu_model VARCHAR(100);
    END IF;
END $$;

-- tags: e.g. {gaming} for GIN index
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'tags') THEN
        ALTER TABLE products ADD COLUMN tags TEXT[];
        CREATE INDEX ix_products_tags_gin ON products USING gin(tags);
    END IF;
END $$;

-- image_url, source_product_id (stable id per source for upsert)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'image_url') THEN
        ALTER TABLE products ADD COLUMN image_url VARCHAR(512);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'source_product_id') THEN
        ALTER TABLE products ADD COLUMN source_product_id VARCHAR(255);
    END IF;
END $$;

-- Uniqueness: one row per (source, source_product_id) when source_product_id is set (prevents duplicate scraped items)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = 'ux_products_source_source_product_id') THEN
        CREATE UNIQUE INDEX ux_products_source_source_product_id ON products(source, source_product_id) WHERE source_product_id IS NOT NULL AND source_product_id != '';
    END IF;
END $$;
