-- =============================================================================
-- Product embeddings table for evaluation pipeline (laptops).
-- Stores precomputed embeddings (e.g. SentenceTransformer all-mpnet-base-v2).
-- Use real[] for embedding; if pgvector is enabled later, replace with vector(768).
-- =============================================================================
-- Usage: psql $DATABASE_URL -f mcp-server/scripts/create_product_embeddings_table.sql
-- =============================================================================

-- Optional: enable pgvector if available (uncomment if your Postgres has the extension)
-- CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS product_embeddings (
    product_id     UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    embedding      real[] NOT NULL,
    model_name     text NOT NULL DEFAULT 'all-mpnet-base-v2',
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (product_id, model_name)
);

CREATE INDEX IF NOT EXISTS idx_product_embeddings_model ON product_embeddings(model_name);
COMMENT ON TABLE product_embeddings IS 'Precomputed product embeddings for laptops (evaluation and vector search).';
