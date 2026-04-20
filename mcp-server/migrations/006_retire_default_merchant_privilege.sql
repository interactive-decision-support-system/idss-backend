-- =============================================================================
-- 006_retire_default_merchant_privilege.sql
--
-- Retire the "default merchant is structurally privileged" pattern.
--
-- Target model: the source is archival, merchants are independent, no merchant
-- is privileged. A raw reference pool exists (``merchants.raw_products_default``)
-- but it is NOT a merchant's catalog — it is not registered in
-- ``merchants.registry`` and no code path reads it to serve user queries.
-- Every merchant — including what is today called "default" — is structurally
-- identical: ``merchants.products_<id>`` created via ``create_merchant_catalog``,
-- populated either by CSV upload or by sampling from raw.
--
-- What this migration does:
--   1. Renames ``merchants.products_default`` → ``merchants.raw_products_default``.
--      The full unfiltered ~51k-row snapshot becomes the platform-operator
--      archive. Postgres follows the rename for all dependent objects (FKs,
--      indexes, the PK); references that used to point at the table's OID still
--      resolve.
--   2. Creates a new ``merchants.products_default`` as a real, independent
--      table with the same schema as raw (via LIKE INCLUDING ALL).
--   3. Populates the new ``products_default`` with a quality-filtered sample
--      from raw (~34k rows). The filter drops rows identified in the
--      2026-04-19 catalog audit as unfit for serving user queries:
--        * ``product_type IS NULL``        (~14 scraper-gap rows)
--        * ``product_type = 'book'``       (16 Open Library rows, wrong vertical)
--        * ``price IS NULL``               (~17,440 mostly-laptop rows)
--        * ``price = 0``                   (2 router rows)
--        * ``price = 99999``               (placeholder sentinels)
--        * ``price >= 500000``             (obvious outliers, e.g. $950k ipad)
--   4. Adds table COMMENTs pinning the new semantics for future readers.
--
-- What this migration does NOT do:
--   * Rename the ``merchant_id`` string. ``'default'`` remains as the
--     merchant_id of the catalog served by the ``/chat`` endpoint. Renaming
--     to e.g. ``'public_scrape_v1'`` is a cosmetic follow-up that touches
--     ~40 code sites and is intentionally out of scope here.
--   * Touch ``merchants.products_enriched_default``. Enriched rows are keyed
--     by raw UUID and remain valid across the rename — the FK follows the
--     underlying table's OID, so it now references ``raw_products_default(id)``.
--     This is correct: enrichment identity is ground truth, not a sample.
--   * Touch ``merchants.registry``. The existing ``merchant_id='default'``
--     row continues to point at ``merchants.products_default``, which still
--     exists (now as a sampled table rather than the raw snapshot itself).
--
-- Structural consequences unlocked by this migration (implemented in the
-- same PR but in code, not SQL):
--   * ``schema.py`` can clone new merchants from ``raw_products_default``
--     rather than from ``products_default``. Dropping the default merchant
--     no longer breaks new-merchant provisioning.
--   * The "refuse to drop 'default'" guards in ``drop_merchant_catalog`` and
--     the admin DELETE route can be removed. Default is dropable like any
--     other merchant (still gated on ``ALLOW_MERCHANT_DROP=1``).
--   * ``SupabaseProductStore``'s ``!= 'default'`` rejection can be generalized
--     to accept any merchant via ``merchant_catalog_table(mid)``.
--
-- Idempotent. Safe to re-run.
-- =============================================================================
-- Usage: psql $DATABASE_URL -f mcp-server/migrations/006_retire_default_merchant_privilege.sql
-- =============================================================================

BEGIN;

-- Step 1: rename the existing table to become the archive.
-- IF EXISTS guards a re-run where the rename has already happened.
ALTER TABLE IF EXISTS merchants.products_default
  RENAME TO raw_products_default;

ALTER INDEX IF EXISTS merchants.idx_merchants_products_default_merchant_id
  RENAME TO idx_merchants_raw_products_default_merchant_id;

-- Step 2: create the new products_default table from raw's schema.
-- LIKE INCLUDING ALL copies columns, types, defaults, PK, indexes, NOT NULLs.
-- It does NOT copy foreign keys — none exist from raw to anywhere, so that's fine.
CREATE TABLE IF NOT EXISTS merchants.products_default
  (LIKE merchants.raw_products_default INCLUDING ALL);

-- Step 3: populate products_default with the filtered sample, only if empty.
-- The NOT EXISTS guard makes this a one-shot snapshot — re-running the migration
-- does not double-insert rows or silently backfill new raw rows into the sample.
-- To resample after raw changes, TRUNCATE products_default and re-run.
INSERT INTO merchants.products_default
SELECT * FROM merchants.raw_products_default
WHERE product_type IS NOT NULL
  AND product_type <> 'book'
  AND price IS NOT NULL
  AND price > 0
  AND price < 500000
  AND price <> 99999
  AND NOT EXISTS (SELECT 1 FROM merchants.products_default);

-- Step 4: table-level documentation.
COMMENT ON TABLE merchants.raw_products_default IS
  'Platform-operator archive: full, immutable snapshot of public.products at '
  'ingest time. NOT a merchant''s catalog — absent from merchants.registry, '
  'not read by any user-serving code path. Used as the clone template for '
  'new-merchant schema and as the sampling source for synthetic storefronts.';

COMMENT ON TABLE merchants.products_default IS
  'Catalog for merchant_id=''default''. A quality-filtered materialized sample '
  'of raw_products_default (see migration 006 for filter rationale). One '
  'sample among potentially many — benchmark storefronts will follow the same '
  'pattern. No structural privilege over other merchants.';

COMMIT;
