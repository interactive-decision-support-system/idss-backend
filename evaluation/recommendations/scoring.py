"""
Scoring for recommendation evaluation.

- Hard constraints: pass rate of recommended products vs expected_ucp (price_max, brand, product_type, etc.)
- Soft constraints: similarity between each recommended product's embedding and ground truth set
  (max or mean similarity; 1/(1+distance) as in vector_search.py)
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_MCP = os.path.join(_REPO_ROOT, "mcp-server")
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)

import numpy as np


def _product_price_cents(product: Dict[str, Any]) -> int:
    """Get product price in cents from store dict (price often in dollars) or detail."""
    price = product.get("price_cents")
    if price is not None:
        return int(price)
    price_d = product.get("price") or product.get("price_value") or 0
    try:
        return int(round(float(price_d) * 100))
    except (TypeError, ValueError):
        return 0


def _product_matches_hard_constraint(product: Dict[str, Any], key: str, value: Any) -> bool:
    """Check a single hard constraint for one product."""
    if value is None:
        return True
    if key == "price_max_cents":
        return _product_price_cents(product) <= int(value)
    if key == "price_min_cents":
        return _product_price_cents(product) >= int(value)
    if key == "brand":
        brand_val = value if isinstance(value, (list, tuple)) else [value]
        p_brand = (product.get("brand") or "").strip().lower()
        return any((b or "").strip().lower() in p_brand or p_brand in (b or "").strip().lower() for b in brand_val)
    if key == "product_type":
        pt_val = value if isinstance(value, (list, tuple)) else [value]
        p_pt = (product.get("product_type") or "").strip().lower()
        return any((t or "").strip().lower() == p_pt for t in pt_val)
    if key == "category":
        p_cat = (product.get("category") or "").strip().lower()
        v_cat = (value or "").strip().lower()
        return v_cat in p_cat or p_cat in v_cat
    if key == "min_ram_gb":
        attrs = product.get("attributes") or {}
        ram = attrs.get("ram_gb")
        try:
            return float(ram or 0) >= int(value)
        except (TypeError, ValueError):
            return False
    if key in ("min_screen_size", "max_screen_size"):
        attrs = product.get("attributes") or {}
        screen = attrs.get("screen_size")
        try:
            s = float(screen or 0)
            if key == "min_screen_size":
                return s >= float(value)
            return s <= float(value)
        except (TypeError, ValueError):
            return False
    if key in ("good_for_gaming", "good_for_ml", "good_for_creative", "good_for_web_dev"):
        attrs = product.get("attributes") or {}
        return bool(attrs.get(key))
    return True


# Keys we treat as hard constraints for scoring (expected_ucp keys that must hold on each rec)
HARD_KEYS = {
    "price_max_cents", "price_min_cents", "brand", "product_type", "category",
    "min_ram_gb", "min_screen_size", "max_screen_size",
    "good_for_gaming", "good_for_ml", "good_for_creative", "good_for_web_dev",
}


def filter_by_hard_constraints(
    products: List[Dict[str, Any]],
    expected_ucp: Dict[str, Any],
    hard_keys: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Return only products that satisfy all hard constraints in expected_ucp."""
    hard_keys = hard_keys or HARD_KEYS
    out = []
    for p in products:
        if all(_product_matches_hard_constraint(p, k, expected_ucp.get(k)) for k in hard_keys if k in expected_ucp):
            out.append(p)
    return out


def hard_constraint_pass_rate(
    recommended_products: List[Dict[str, Any]],
    expected_ucp: Dict[str, Any],
    hard_keys: Optional[set] = None,
) -> float:
    """
    Fraction of recommended products that satisfy all hard constraints from expected_ucp.
    """
    hard_keys = hard_keys or HARD_KEYS
    if not recommended_products:
        return 0.0
    passed = 0
    for p in recommended_products:
        if all(_product_matches_hard_constraint(p, k, expected_ucp.get(k)) for k in hard_keys if k in expected_ucp):
            passed += 1
    return passed / len(recommended_products)


def similarity_from_distance(distance: float) -> float:
    """Convert L2 distance to similarity (same as vector_search.py: 1/(1+distance))."""
    return 1.0 / (1.0 + float(distance))


def get_embedding_for_product(
    product_id: str,
    product_dict: Optional[Dict[str, Any]] = None,
    model_name: str = "all-mpnet-base-v2",
) -> Optional[np.ndarray]:
    """
    Get embedding for a product: from product_embeddings table, or compute on the fly from product dict.
    Returns shape (1, dim) or (dim,) for convenience; caller can flatten.
    """
    from sqlalchemy import text
    from app.database import engine

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT embedding FROM product_embeddings WHERE product_id = :pid AND model_name = :model"),
            {"pid": product_id, "model": model_name},
        ).fetchone()
    if row and row[0] is not None:
        arr = np.array(row[0], dtype=np.float32)
        return arr.reshape(1, -1) if arr.ndim == 1 else arr

    if product_dict:
        from app.vector_search import get_vector_store
        store = get_vector_store()
        # encode_product expects name, description, category, brand, metadata (attributes), product_type
        p = dict(product_dict)
        if "metadata" not in p and "attributes" in p:
            p["metadata"] = p["attributes"]
        return store.encode_product(p)
    return None


def mean_soft_similarity(
    recommended_product_ids: List[str],
    ground_truth_product_ids: List[str],
    product_embeddings_map: Optional[Dict[str, np.ndarray]] = None,
    product_dicts_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    use_max: bool = False,
    model_name: str = "all-mpnet-base-v2",
) -> float:
    """
    For each recommended product, compute similarity to the ground truth set (max over golden items),
    then return mean over recommendations. Similarity = 1/(1+L2 distance).
    If embedding missing, compute on the fly from product dict and optionally backfill.
    """
    if not recommended_product_ids or not ground_truth_product_ids:
        return 0.0

    product_embeddings_map = product_embeddings_map or {}
    product_dicts_by_id = product_dicts_by_id or {}

    def get_emb(pid: str, as_row: bool = False) -> Optional[np.ndarray]:
        if pid in product_embeddings_map:
            e = product_embeddings_map[pid]
            return e.reshape(1, -1) if e.ndim == 1 else e
        e = get_embedding_for_product(pid, product_dicts_by_id.get(pid), model_name=model_name)
        if e is not None and pid not in product_embeddings_map and as_row:
            product_embeddings_map[pid] = e.reshape(-1)
        return e

    golden_embeddings = []
    for gid in ground_truth_product_ids:
        e = get_emb(gid, as_row=True)
        if e is not None:
            golden_embeddings.append(e.reshape(1, -1) if e.ndim == 1 else e)
    if not golden_embeddings:
        return 0.0
    gt_stack = np.vstack(golden_embeddings)

    sims = []
    for rid in recommended_product_ids:
        rec_emb = get_emb(rid, as_row=True)
        if rec_emb is None:
            sims.append(0.0)
            continue
        rec_emb = rec_emb.reshape(1, -1) if rec_emb.ndim == 1 else rec_emb
        distances = np.linalg.norm(gt_stack - rec_emb, axis=1)
        if use_max:
            sims.append(float(similarity_from_distance(np.min(distances))))
        else:
            sims.append(float(similarity_from_distance(np.min(distances))))  # per-rec: max sim to any golden
    return sum(sims) / len(recommended_product_ids) if sims else 0.0


def score_recommendations(
    recommended_products: List[Dict[str, Any]],
    recommended_ids: List[str],
    expected_ucp: Dict[str, Any],
    ground_truth_product_ids: List[str],
    product_embeddings_map: Optional[Dict[str, np.ndarray]] = None,
    model_name: str = "all-mpnet-base-v2",
) -> Tuple[float, float]:
    """
    Returns (hard_pass_rate, mean_soft_similarity).
    """
    hard_pass = hard_constraint_pass_rate(recommended_products, expected_ucp)
    by_id = {str(p.get("product_id") or p.get("id", "")): p for p in recommended_products}
    soft = mean_soft_similarity(
        recommended_ids,
        ground_truth_product_ids,
        product_embeddings_map=product_embeddings_map,
        product_dicts_by_id=by_id,
        model_name=model_name,
    )
    return hard_pass, soft
