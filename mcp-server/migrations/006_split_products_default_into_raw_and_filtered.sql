-- =============================================================================
-- 006_split_products_default_into_raw_and_filtered.sql
--
-- Introduce the raw / cleaned split in the default merchant catalog.
--
-- Before:
--   merchants.products_default           — full snapshot of public.products
--                                          (51,277 rows; includes NULL-price
--                                          laptops, Open Library "book" rows,
--                                          $99,999 sentinel prices, and rows
--                                          with NULL product_type).
--
-- After:
--   merchants.raw_products_default       — renamed original table. Immutable
--                                          full copy, kept for reproducibility
--                                          (storefront sampling, enrichment
--                                          benchmarks, data-quality audits).
--   merchants.products_default           — VIEW over raw with quality filters.
--                                          This is what the application reads.
--
-- Filter rationale (2026-04-19 catalog audit):
--   * product_type IS NOT NULL           drops ~14 rows the scraper never tagged.
--   * product_type <> 'book'             drops 16 Open Library rows (wrong vertical).
--   * price IS NOT NULL                  drops ~17,440 mostly-laptop rows with no
--                                          scraped price — a priceless storefront
--                                          item is not a storefront item.
--   * price > 0                          drops 2 router rows priced at 0.
--   * price < 500000 AND price <> 99999  drops sentinel placeholders (~65 rows
--                                          across smartphone, tv, smartwatch,
--                                          tablet, phone, ipad) and the 950125
--                                          ipad outlier.
--
-- Post-filter row count: 33,755 (verified 2026-04-19).
--
-- Why a VIEW, not a materialized table:
--   * Raw is a one-shot snapshot (migration 002 semantics); the cleaned
--     projection has no independent lifecycle.
--   * No drift risk — filtered state is always derivable.
--   * The FK merchants.products_enriched_default.product_id
--     → merchants.products_default(id) follows the rename automatically,
--     so enriched rows remain keyed against the RAW UUIDs. That is what we
--     want: enrichment identity is ground truth, not the filtered projection.
--     (Consequence: if a product is later removed by the filter, its enriched
--     rows simply become unreachable through the view. Not a correctness bug.)
--
-- Idempotent. Safe to re-run.
-- =============================================================================
-- Usage: psql $DATABASE_URL -f mcp-server/migrations/006_split_products_default_into_raw_and_filtered.sql
-- =============================================================================

BEGIN;

-- Step 1: rename the underlying table. FKs, indexes, and the PK follow
-- automatically. Guarded with IF EXISTS so a re-run after manual rollback
-- (where products_default was recreated as a view) is a no-op on this line.
ALTER TABLE IF EXISTS merchants.products_default
  RENAME TO raw_products_default;

ALTER INDEX IF EXISTS merchants.idx_merchants_products_default_merchant_id
  RENAME TO idx_merchants_raw_products_default_merchant_id;

-- Step 2: (re)create the filtered view at the original name. The DROP handles
-- the re-run case where a stale view already sits at merchants.products_default.
DROP VIEW IF EXISTS merchants.products_default;

CREATE VIEW merchants.products_default AS
SELECT *
FROM merchants.raw_products_default
WHERE product_type IS NOT NULL
  AND product_type <> 'book'
  AND price IS NOT NULL
  AND price > 0
  AND price < 500000
  AND price <> 99999;

COMMENT ON VIEW merchants.products_default IS
  'Quality-filtered projection of raw_products_default. Application reads go through this view. See migration 006 for filter rationale.';

COMMENT ON TABLE merchants.raw_products_default IS
  'Full, immutable snapshot of public.products for the default merchant. Preserved for benchmarking and reproducibility; not read by the application directly — reads go through the products_default view.';

COMMIT;
