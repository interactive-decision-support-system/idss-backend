-- Richer KG (§7): Reddit-style features for complex queries.
-- Run: psql <your_db> -f scripts/add_kg_features_column.sql
-- Columns: kg_features JSONB (good_for_ml, good_for_web_dev, battery_life_hours, etc.)

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'kg_features') THEN
        ALTER TABLE products ADD COLUMN kg_features JSONB;
        COMMENT ON COLUMN products.kg_features IS 'Reddit-style features: good_for_ml, good_for_gaming, battery_life_hours, etc.';
    END IF;
END $$;

-- Optional GIN index for JSONB key/value queries (e.g. kg_features @> '{"good_for_ml": true}')
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = 'ix_products_kg_features_gin') THEN
        CREATE INDEX ix_products_kg_features_gin ON products USING gin(kg_features);
    END IF;
EXCEPTION
    WHEN undefined_object THEN NULL;  -- Skip if kg_features is JSON not JSONB
END $$;
