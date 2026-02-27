#!/usr/bin/env python3
"""
Populate ground_truth_product_ids for each item in the golden dataset.

For each item:
  1. Filter the database (laptop products) by expected_ucp hard constraints.
  2. Rank filtered products by similarity of user_query embedding to product embedding.
  3. Set ground_truth_product_ids to the top 10 product IDs.

Uses the same embedding model as product_embeddings (all-mpnet-base-v2). Product
embeddings are read from the product_embeddings table when present; otherwise
computed on the fly from product text (name, description, category, brand, etc.).

Usage:
  python -m evaluation.recommendations.populate_ground_truth [--golden path/to/golden_dataset.json] [--top 10] [--backup]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_MCP = os.path.join(_REPO_ROOT, "mcp-server")
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)

try:
    from dotenv import load_dotenv
    _env = os.path.join(_REPO_ROOT, ".env")
    if os.path.isfile(_env):
        load_dotenv(_env)
except ImportError:
    pass

import numpy as np

from evaluation.recommendations.schema import load_golden_dataset, GoldenItem
from evaluation.recommendations.scoring import (
    filter_by_hard_constraints,
    HARD_KEYS,
)
from evaluation.recommendations.populate_product_embeddings import (
    load_laptop_products,
    build_product_text,
    get_encoder,
    MODEL_NAME,
)


def load_product_embeddings_map(model_name: str = MODEL_NAME) -> dict[str, np.ndarray]:
    """Load all product_id -> embedding from product_embeddings table. Returns {} if table does not exist."""
    from sqlalchemy import text
    from app.database import engine
    from sqlalchemy.exc import ProgrammingError

    out = {}
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT product_id, embedding FROM product_embeddings WHERE model_name = :m"),
                {"m": model_name},
            )
            for row in result:
                pid = str(row[0])
                arr = np.array(row[1], dtype=np.float32)
                out[pid] = arr.reshape(-1) if arr.ndim > 1 else arr
    except ProgrammingError as e:
        if "does not exist" in str(e.orig) if getattr(e, "orig", None) else str(e):
            pass  # table not created yet; will compute on the fly
        else:
            raise
    return out


def similarity_from_distance(distance: float) -> float:
    """1 / (1 + L2 distance), same as vector_search / scoring."""
    return 1.0 / (1.0 + float(distance))


def top_k_by_query_similarity(
    products: list[dict],
    user_query: str,
    embeddings_map: dict[str, np.ndarray],
    encoder,
    product_text_fn,
    model_name: str = MODEL_NAME,
    k: int = 10,
) -> list[str]:
    """
    Rank products by similarity of user_query embedding to product embedding.
    Returns list of product_id strings (top k).
    """
    if not products:
        return []

    query_emb = encoder.encode([user_query], convert_to_numpy=True).astype(np.float32)
    query_emb = query_emb.reshape(-1)

    scored = []
    for p in products:
        pid = str(p.get("product_id") or p.get("id") or "")
        if not pid:
            continue
        if pid in embeddings_map:
            prod_emb = embeddings_map[pid]
        else:
            text = product_text_fn(p)
            prod_emb = encoder.encode([text], convert_to_numpy=True).astype(np.float32).reshape(-1)
        dist = np.linalg.norm(query_emb - prod_emb)
        sim = similarity_from_distance(dist)
        scored.append((pid, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [pid for pid, _ in scored[:k]]


def main() -> int:
    parser = argparse.ArgumentParser(description="Populate ground_truth_product_ids in golden dataset")
    parser.add_argument(
        "--golden",
        default=os.path.join(os.path.dirname(__file__), "golden_dataset.json"),
        help="Path to golden dataset JSON",
    )
    parser.add_argument("--top", type=int, default=10, help="Number of ground-truth product IDs per query")
    parser.add_argument("--backup", action="store_true", help="Write backup of golden file before overwriting")
    args = parser.parse_args()

    golden_path = Path(args.golden)
    if not golden_path.is_file():
        print(f"Error: {golden_path} not found", file=sys.stderr)
        return 1

    print("Loading golden dataset...")
    items = load_golden_dataset(str(golden_path))
    print("Loading laptop products...")
    all_products = load_laptop_products()
    if not all_products:
        print("No laptop products found. Run populate_product_embeddings first and ensure DB has laptops.", file=sys.stderr)
        return 1

    print("Loading product embeddings from DB...")
    embeddings_map = load_product_embeddings_map()
    print(f"  {len(embeddings_map)} embeddings loaded")

    print("Loading encoder for query/product text...")
    encoder = get_encoder()

    for g in items:
        filtered = filter_by_hard_constraints(all_products, g.expected_ucp, HARD_KEYS)
        top_ids = top_k_by_query_similarity(
            filtered,
            g.user_query,
            embeddings_map,
            encoder,
            build_product_text,
            model_name=MODEL_NAME,
            k=args.top,
        )
        g.ground_truth_product_ids = top_ids
        print(f"  {g.query_id}: {len(filtered)} passed hard constraints -> top {len(top_ids)} by similarity")

    # Serialize back to list of dicts
    out_data = [g.to_dict() for g in items]

    if args.backup:
        backup_path = golden_path.with_suffix(".json.bak")
        with open(golden_path, "r", encoding="utf-8") as f:
            backup_content = f.read()
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(backup_content)
        print(f"Backup written to {backup_path}")

    with open(golden_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2)

    print(f"Wrote {len(out_data)} items to {golden_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
