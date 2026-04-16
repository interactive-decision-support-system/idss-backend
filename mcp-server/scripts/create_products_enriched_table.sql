-- =============================================================================
-- products_enriched — derived attributes produced by enrichment strategies.
--
-- The raw `products` table is the golden source and is never mutated by
-- enrichment. All derived attributes (normalized_description, soft tags,
-- LLM-extracted specs, etc.) are written here under a named strategy so
-- multiple strategies can coexist for the same product (A/B, simulations,
-- per-merchant variants).
--
-- Readers merge raw + enriched at query time; enriched keys win.
-- =============================================================================
-- Usage: psql $DATABASE_URL -f mcp-server/scripts/create_products_enriched_table.sql
-- =============================================================================

CREATE TABLE IF NOT EXISTS products_enriched (
    product_id  UUID        NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    strategy    TEXT        NOT NULL,
    attributes  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    model       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (product_id, strategy)
);

CREATE INDEX IF NOT EXISTS idx_products_enriched_strategy
    ON products_enriched(strategy);

COMMENT ON TABLE products_enriched IS
  'Derived attributes per (product, strategy). Raw products table is immutable; enrichers write here.';
