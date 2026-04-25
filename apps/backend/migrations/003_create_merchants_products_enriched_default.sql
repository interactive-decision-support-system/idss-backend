-- =============================================================================
-- merchants.products_enriched_default — derived attributes per (product, strategy).
--
-- The raw `products` table is the golden source and is never mutated by
-- enrichment. All derived attributes (normalized_description, soft tags,
-- LLM-extracted specs, etc.) are written here under a named strategy so
-- multiple strategies can coexist for the same product (A/B, simulations,
-- per-merchant variants).
--
-- Each table owns its fields: enriched must not write keys that already
-- exist in raw `merchants.products_default.attributes`. Readers join the
-- two tables; the union is a disjoint set of keys, never a COALESCE.
-- =============================================================================
-- Usage: psql $DATABASE_URL -f apps/backend/migrations/003_create_merchants_products_enriched_default.sql
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS merchants;

CREATE TABLE IF NOT EXISTS merchants.products_enriched_default (
    product_id  UUID        NOT NULL REFERENCES merchants.products_default(id) ON DELETE CASCADE,
    strategy    TEXT        NOT NULL,
    attributes  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    model       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (product_id, strategy)
);

CREATE INDEX IF NOT EXISTS idx_products_enriched_default_strategy
    ON merchants.products_enriched_default(strategy);

COMMENT ON TABLE merchants.products_enriched_default IS
  'Derived attributes per (product, strategy). Raw products is immutable; enrichers write here.';
