#!/usr/bin/env python3
"""
Populate product_embeddings table with laptop product embeddings.

- Loads all laptop products (category=Electronics, product_type=laptop).
- Builds text from: name, description, category, brand, product_type, model, series, attributes.
- Uses same embedding model as mcp-server/app/vector_search.py (all-mpnet-base-v2).
- Upserts into product_embeddings (idempotent).

Usage:
  cd idss-backend && python -m evaluation.recommendations.populate_product_embeddings
  # Or with env: DATABASE_URL=postgresql://... python -m evaluation.recommendations.populate_product_embeddings
"""

import os
import sys

# Ensure mcp-server app and project root are on path
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_MCP = os.path.join(_REPO_ROOT, "mcp-server")
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)

import logging
from typing import Any, Dict, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_NAME = "all-mpnet-base-v2"


def build_product_text(product: Dict[str, Any]) -> str:
    """
    Build a single text representation from informative fields only.
    Includes: name, description, category, brand, product_type, model, series, attributes.
    Excludes: created_at, updated_at, and other non-informative fields.
    """
    parts = []
    if product.get("name"):
        parts.append(str(product["name"]))
    if product.get("description"):
        parts.append(str(product["description"]))
    if product.get("category"):
        parts.append(f"category: {product['category']}")
    if product.get("brand"):
        parts.append(f"brand: {product['brand']}")
    if product.get("product_type"):
        parts.append(f"product type: {product['product_type']}")
    if product.get("model"):
        parts.append(f"model: {product['model']}")
    if product.get("series"):
        parts.append(f"series: {product['series']}")
    attrs = product.get("attributes") or {}
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            if k in ("created_at", "updated_at"):
                continue
            if v is not None and isinstance(v, (str, int, float)):
                parts.append(f"{k}: {v}")
    return " ".join(parts)


def load_laptop_products(limit: int = 10000) -> List[Dict[str, Any]]:
    """Load all laptop products (Electronics + laptop). Uses direct SQL to get full set."""
    from sqlalchemy import text
    from app.database import engine
    from app.tools.supabase_product_store import SupabaseProductStore

    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT * FROM products
                WHERE LOWER(TRIM(category)) = 'electronics' AND LOWER(TRIM(product_type)) = 'laptop'
                AND price > 0.01
            """)
        )
        rows = result.fetchall()
    # Normalise to same dict shape as store (title -> name, id -> product_id, etc.)
    return [SupabaseProductStore._row_to_dict(dict(r._mapping)) for r in rows]


def get_encoder():
    """Lazy-load the same SentenceTransformer model as vector_search."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME)


def main() -> None:
    import numpy as np
    from sqlalchemy import text
    from app.database import engine

    logger.info("Loading laptop products...")
    products = load_laptop_products()
    if not products:
        logger.warning("No laptop products found. Ensure DB has products with category=Electronics, product_type=laptop.")
        return

    logger.info("Building text representations for %d products...", len(products))
    texts = []
    product_ids = []
    for p in products:
        pid = p.get("product_id") or p.get("id")
        if not pid:
            continue
        product_ids.append(str(pid))
        texts.append(build_product_text(p))

    logger.info("Encoding with %s...", MODEL_NAME)
    encoder = get_encoder()
    embeddings = encoder.encode(texts, convert_to_numpy=True, batch_size=32, show_progress_bar=True)
    embeddings = embeddings.astype(np.float32)

    # Upsert into product_embeddings (real[]; psycopg2 accepts list as array)
    logger.info("Upserting %d embeddings into product_embeddings...", len(product_ids))
    with engine.connect() as conn:
        for pid, emb in zip(product_ids, embeddings):
            arr = emb.tolist()
            conn.execute(
                text("""
                INSERT INTO product_embeddings (product_id, embedding, model_name)
                VALUES (:product_id::uuid, :embedding, :model_name)
                ON CONFLICT (product_id, model_name)
                DO UPDATE SET embedding = EXCLUDED.embedding, created_at = now()
                """),
                {"product_id": pid, "embedding": arr, "model_name": MODEL_NAME},
            )
        conn.commit()

    logger.info("Done. Upserted %d product embeddings.", len(product_ids))


if __name__ == "__main__":
    main()
