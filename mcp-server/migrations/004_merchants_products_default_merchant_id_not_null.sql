-- =============================================================================
-- Backfill + enforce NOT NULL on merchants.products_default.merchant_id.
--
-- Issue #55 — remove default-merchant special-casing.
--
-- Migration 002 added the merchant_id column as nullable and did a one-time
-- UPDATE NULL → 'default'. Application code has, until now, still treated
-- NULL and 'default' as equivalent when scoping queries on the default
-- merchant (see merchant_agent.health() and endpoints.search_products).
--
-- Once this migration has run, every row in merchants.products_default has
-- merchant_id = 'default' and the column is NOT NULL — so the code collapse
-- that ships in the same PR is safe (there are no legacy NULL rows left to
-- match against). The UPDATE is repeated here defensively; if migration 002
-- has already been applied on a given database it is a no-op.
--
-- Scope note: merchants.products_enriched_default is intentionally not
-- touched. Enriched rows are keyed by (product_id, strategy) and scoped by
-- table name + FK to the raw table — they have no merchant_id column and
-- do not need one for this change (see CLAUDE.md: each enrichment table
-- owns its own fields).
-- =============================================================================
-- Usage: psql $DATABASE_URL -f mcp-server/migrations/004_merchants_products_default_merchant_id_not_null.sql
-- =============================================================================

BEGIN;

UPDATE merchants.products_default
SET merchant_id = 'default'
WHERE merchant_id IS NULL;

ALTER TABLE merchants.products_default
  ALTER COLUMN merchant_id SET NOT NULL;

COMMIT;
