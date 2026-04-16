-- Create the `merchants` schema for per-merchant catalog isolation (interim).
--
-- Design: each merchant's catalog lives in its own pair of tables inside a
-- dedicated schema. The `public` schema stays frozen as the reference.
--
-- See CLAUDE.md "Merchant agent is generic; each uploaded catalog is isolated".
-- Issue #38: long-term target is one schema per merchant (this design is tentative).
-- Issue #39: CSV upload flow that bootstraps new merchants.
--
-- This migration is idempotent. public.* is never modified.

BEGIN;

CREATE SCHEMA IF NOT EXISTS merchants;

-- --------------------------------------------------------------------------
-- merchants.products_default  — raw catalog mirror for the default merchant
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merchants.products_default (
  LIKE public.products INCLUDING ALL
);

-- One-shot snapshot: copy only if the target is empty. ON CONFLICT would
-- silently back-fill rows added to public.products between re-runs; we want
-- strict snapshot semantics so a re-run is a no-op once initialised.
-- Data copy runs before adding merchant_id so SELECT * column counts line up.
INSERT INTO merchants.products_default
SELECT * FROM public.products
WHERE NOT EXISTS (SELECT 1 FROM merchants.products_default);

-- Per-merchant scoping column. Only present on the merchants schema — never
-- added to public.products (see CLAUDE.md: public is the golden catalog).
ALTER TABLE merchants.products_default
  ADD COLUMN IF NOT EXISTS merchant_id TEXT;

UPDATE merchants.products_default
SET merchant_id = 'default'
WHERE merchant_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_merchants_products_default_merchant_id
  ON merchants.products_default (merchant_id);

-- --------------------------------------------------------------------------
-- merchants.products_enriched_default  — enrichment mirror (if source exists)
-- --------------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'products_enriched'
  ) THEN
    EXECUTE
      'CREATE TABLE IF NOT EXISTS merchants.products_enriched_default (
         LIKE public.products_enriched INCLUDING ALL
       )';

    -- Snapshot semantics: only populate when the target is empty.
    EXECUTE
      'INSERT INTO merchants.products_enriched_default
       SELECT e.* FROM public.products_enriched e
       WHERE EXISTS (
         SELECT 1 FROM merchants.products_default p WHERE p.id = e.product_id
       )
       AND NOT EXISTS (SELECT 1 FROM merchants.products_enriched_default)';

    -- LIKE ... INCLUDING ALL does NOT copy foreign keys — recreate within schema.
    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint
      WHERE conname = 'fk_products_enriched_default_product'
    ) THEN
      EXECUTE
        'ALTER TABLE merchants.products_enriched_default
           ADD CONSTRAINT fk_products_enriched_default_product
           FOREIGN KEY (product_id)
           REFERENCES merchants.products_default (id)
           ON DELETE CASCADE';
    END IF;
  END IF;
END $$;

COMMIT;
