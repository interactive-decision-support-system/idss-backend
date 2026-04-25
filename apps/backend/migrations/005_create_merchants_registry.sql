-- =============================================================================
-- merchants.registry — durable source of truth for merchant registrations.
--
-- Issue #53. Before this migration, merchant registrations lived only in
-- merchant_agent.main.merchants, an in-process Python dict populated by
-- MerchantAgent.from_csv. That dict has two well-known failure modes:
--
--   * Restarting the backend loses every non-default registration. The
--     catalog tables in merchants.* persist but the "this merchant exists
--     with these strategies" pointer is gone, so /merchant/<id>/* returns
--     404 until someone re-runs bootstrap.
--   * Under multi-worker deploys (uvicorn/gunicorn --workers >1) a
--     from_csv call on worker A writes to A's dict only. A subsequent
--     request landing on worker B 404s.
--
-- After this migration the dict becomes a cache and merchants.registry is
-- the source of truth. /merchant/{id}/* lazy-hydrates on cache miss.
--
-- Strategy columns
-- ----------------
-- Both `strategy` and `kg_strategy` are stored. Since #62,
-- MerchantAgent.__init__ takes both as distinct fields — they key
-- different subsystems:
--   * strategy     — enrichment rows (products_enriched.strategy) and
--                    per-merchant FAISS index path.
--   * kg_strategy  — Neo4j KG tenancy (one KG per (merchant, kg_strategy)).
-- Omitting either here would silently snap rehydrated agents back to
-- the default on the missing axis.
--
-- NOTE: the api_token column planned in #57 is intentionally NOT added
-- here. It will ship in its own later migration alongside the admin
-- auth surface so this PR stays scoped to "persist what already exists".
-- =============================================================================
-- Usage: psql $DATABASE_URL -f apps/backend/migrations/005_create_merchants_registry.sql
-- =============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS merchants;

CREATE TABLE IF NOT EXISTS merchants.registry (
    merchant_id  TEXT        PRIMARY KEY,
    domain       TEXT        NOT NULL,
    strategy     TEXT        NOT NULL DEFAULT 'normalizer_v1',
    kg_strategy  TEXT        NOT NULL DEFAULT 'default_v1',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE merchants.registry IS
  'Durable merchant registrations. App dict is a cache; this table is the source of truth. See issue #53.';

COMMIT;
