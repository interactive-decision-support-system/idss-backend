"""
Modular evaluation runners.

(a) Query -> UCP only: from golden user_query, run agent (filter-producing part), compare produced filters to expected_ucp.
(b) UCP -> recs only: from golden expected_ucp, call search (ucp_search / search_products), score recs (hard + soft).
(c) Query -> recs (full): from golden user_query, run full chat flow; score recs with hard + soft.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_MCP = os.path.join(_REPO_ROOT, "mcp-server")
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)

from evaluation.recommendations.schema import GoldenItem, load_golden_dataset
from evaluation.recommendations.scoring import (
    hard_constraint_pass_rate,
    mean_soft_similarity,
    score_recommendations,
)


def _normalize_filters(f: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize filter dict for comparison (e.g. list vs single value)."""
    out = {}
    for k, v in f.items():
        if k.startswith("_"):
            continue
        if v is None:
            continue
        if isinstance(v, list) and len(v) == 1:
            out[k] = v[0]
        else:
            out[k] = v
    return out


def _ucp_match_rate(produced: Dict[str, Any], expected: Dict[str, Any]) -> float:
    """
    Key-wise match: for each key in expected, produced has same value (or equivalent).
    Returns fraction of expected keys that match.
    """
    exp = _normalize_filters(expected)
    prod = _normalize_filters(produced)
    if not exp:
        return 1.0
    matches = 0
    for k, v in exp.items():
        pv = prod.get(k)
        if v is None and pv is None:
            matches += 1
        elif v is None:
            continue
        elif isinstance(v, (list, tuple)) and isinstance(pv, (list, tuple)):
            matches += 1 if set(str(x) for x in v) == set(str(x) for x in pv) else 0
        elif str(v).lower() == str(pv).lower():
            matches += 1
        elif v == pv:
            matches += 1
    return matches / len(exp)


# ---------------------------------------------------------------------------
# (a) Query -> UCP only
# ---------------------------------------------------------------------------

def run_query_to_ucp(golden: GoldenItem) -> Dict[str, Any]:
    """
    From golden user_query, run the agent (single message, k=0 to skip interview)
    and compare produced get_search_filters() to expected_ucp.
    """
    from agent.universal_agent import UniversalAgent

    agent = UniversalAgent(session_id="eval-ucp", max_questions=0)
    agent.domain = "laptops"
    agent.process_message(golden.user_query)
    produced = agent.get_search_filters()
    # Ensure category/product_type for laptops
    produced.setdefault("category", "Electronics")
    produced.setdefault("product_type", "laptop")
    match_rate = _ucp_match_rate(produced, golden.expected_ucp)
    return {
        "query_id": golden.query_id,
        "query": golden.user_query,
        "expected_ucp_match": match_rate,
        "produced_ucp": produced,
        "expected_ucp": golden.expected_ucp,
    }


# ---------------------------------------------------------------------------
# (b) UCP -> recs only
# ---------------------------------------------------------------------------

async def run_ucp_to_recs(golden: GoldenItem, limit: int = 10) -> Dict[str, Any]:
    """
    From golden expected_ucp, call MCP search_products (or product store directly),
    get recommendations, score with hard + soft.
    """
    from app.endpoints import search_products
    from app.schemas import SearchProductsRequest
    from app.database import SessionLocal

    filters = dict(golden.expected_ucp)
    filters.setdefault("category", "Electronics")
    filters.setdefault("product_type", "laptop")
    req = SearchProductsRequest(query="", filters=filters, limit=limit)
    db = SessionLocal()
    try:
        resp = await search_products(req, db)
    finally:
        db.close()

    if not resp.data or not resp.data.products:
        return {
            "query_id": golden.query_id,
            "query": golden.user_query,
            "hard_pass_rate": 0.0,
            "mean_soft_similarity": 0.0,
            "recommended_ids": [],
            "recommended_count": 0,
        }

    products = []
    for s in resp.data.products:
        p = {
            "product_id": s.product_id,
            "id": s.product_id,
            "name": s.name,
            "price_cents": s.price_cents,
            "category": s.category,
            "brand": s.brand,
            "product_type": s.product_type,
            "attributes": (s.metadata or {}),
        }
        products.append(p)
    ids = [p["product_id"] for p in products]

    hard_pass, mean_soft = score_recommendations(
        products,
        ids,
        golden.expected_ucp,
        golden.ground_truth_product_ids,
        model_name="all-mpnet-base-v2",
    )
    return {
        "query_id": golden.query_id,
        "query": golden.user_query,
        "hard_pass_rate": hard_pass,
        "mean_soft_similarity": mean_soft,
        "recommended_ids": ids,
        "recommended_count": len(ids),
    }


# ---------------------------------------------------------------------------
# (c) Query -> recs (full)
# ---------------------------------------------------------------------------

async def run_full_system(golden: GoldenItem, n_rows: int = 2, n_per_row: int = 5) -> Dict[str, Any]:
    """
    From golden user_query, run full process_chat flow; extract recommendation product IDs,
    score with hard + soft (no expected_ucp in scoring for recs, but we can still compare UCP for analysis).
    """
    from agent.chat_endpoint import process_chat
    from agent.chat_endpoint import ChatRequest

    req = ChatRequest(
        message=golden.user_query,
        session_id=f"eval-full-{golden.query_id}",
        k=0,
        n_rows=n_rows,
        n_per_row=n_per_row,
    )
    resp = await process_chat(req)

    recommended_ids = []
    recommended_products = []
    if resp.recommendations:
        for row in resp.recommendations:
            for item in row:
                pid = item.get("product_id") or item.get("id")
                if pid and pid not in recommended_ids:
                    recommended_ids.append(pid)
                # Build minimal product dict for scoring (price_cents, brand, etc.)
                p = {
                    "product_id": pid,
                    "id": pid,
                    "name": item.get("name"),
                    "price_cents": item.get("price_cents") or (int((item.get("price") or 0) * 100) if item.get("price") else 0),
                    "category": item.get("category"),
                    "brand": item.get("brand"),
                    "product_type": item.get("product_type"),
                    "attributes": item.get("attributes") or item.get("metadata") or {},
                }
                if pid:
                    recommended_products.append(p)

    if not recommended_products:
        return {
            "query_id": golden.query_id,
            "query": golden.user_query,
            "hard_pass_rate": 0.0,
            "mean_soft_similarity": 0.0,
            "expected_ucp_match": _ucp_match_rate(resp.filters or {}, golden.expected_ucp),
            "recommended_ids": [],
            "recommended_count": 0,
        }

    hard_pass, mean_soft = score_recommendations(
        recommended_products,
        recommended_ids,
        golden.expected_ucp,
        golden.ground_truth_product_ids,
        model_name="all-mpnet-base-v2",
    )
    return {
        "query_id": golden.query_id,
        "query": golden.user_query,
        "hard_pass_rate": hard_pass,
        "mean_soft_similarity": mean_soft,
        "expected_ucp_match": _ucp_match_rate(resp.filters or {}, golden.expected_ucp),
        "recommended_ids": recommended_ids,
        "recommended_count": len(recommended_ids),
    }


# ---------------------------------------------------------------------------
# Baseline runner
# ---------------------------------------------------------------------------

def run_baseline_eval(golden: GoldenItem, limit: int = 10) -> Dict[str, Any]:
    """Run baseline LLM recommender; score with same hard + soft."""
    from app.tools.supabase_product_store import get_product_store
    from evaluation.recommendations.baseline import baseline_recommendations_with_products

    ids, products = baseline_recommendations_with_products(golden.user_query, get_product_store())
    if not products:
        return {
            "query_id": golden.query_id,
            "query": golden.user_query,
            "hard_pass_rate": 0.0,
            "mean_soft_similarity": 0.0,
            "recommended_ids": [],
            "recommended_count": 0,
        }
    hard_pass, mean_soft = score_recommendations(
        products,
        ids,
        golden.expected_ucp,
        golden.ground_truth_product_ids,
        model_name="all-mpnet-base-v2",
    )
    return {
        "query_id": golden.query_id,
        "query": golden.user_query,
        "hard_pass_rate": hard_pass,
        "mean_soft_similarity": mean_soft,
        "recommended_ids": ids,
        "recommended_count": len(ids),
    }
