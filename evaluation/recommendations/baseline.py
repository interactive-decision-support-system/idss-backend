"""
Baseline: out-of-the-box LLM with DB schema and user query.

Two modes:
1. run_baseline / baseline_recommendations_with_products: LLM sees schema + samples,
   returns product_ids (may hallucinate IDs). Legacy.
2. baseline_ucp_recs_supabase_only: Same model as .env (OPENAI_MODEL). Extract filters
   from query via LLM, then search only the Supabase product store. All recs come from
   the database (no invented IDs). Used for UCP->recs baseline evaluation.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_MCP = os.path.join(_REPO_ROOT, "mcp-server")
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)

from openai import OpenAI

# Same model as .env (OPENAI_MODEL); used for filter extraction and legacy baseline.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


# ---------------------------------------------------------------------------
# UCP->recs baseline: Supabase-only (like agent_response baseline)
# ---------------------------------------------------------------------------

def _extract_baseline_filters_ucp(user_query: str) -> Dict[str, Any]:
    """Extract search filters and _soft_preferences from the user query via LLM. Uses OPENAI_MODEL from .env."""
    client = OpenAI()
    completion = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract from the user's laptop request and reply with ONLY a JSON object (no markdown):\n"
                    "(1) price_max_dollars: number or null (max budget in dollars)\n"
                    "(2) brand: string or null (e.g. HP, Dell, Lenovo, Apple)\n"
                    "(3) use_case: one of 'gaming', 'ml', 'creative', 'web_dev', 'school', 'business', 'general' or null\n"
                    "(4) liked_features: list of strings (specific features they want, e.g. touchscreen, backlit keyboard) or null\n"
                    "Example: {\"price_max_dollars\": 800, \"brand\": \"HP\", \"use_case\": \"school\", \"liked_features\": [\"touchscreen\"]}"
                ),
            },
            {"role": "user", "content": user_query},
        ],
        max_completion_tokens=120,
    )
    raw = (completion.choices[0].message.content or "").strip()
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        out = json.loads(raw)
        price_max_dollars = out.get("price_max_dollars")
        brand = out.get("brand")
        filters: Dict[str, Any] = {"category": "Electronics", "product_type": "laptop"}
        if isinstance(price_max_dollars, (int, float)) and price_max_dollars > 0:
            filters["price_max_cents"] = int(round(price_max_dollars * 100))
        if isinstance(brand, str) and brand.strip():
            filters["brand"] = brand.strip()
        use_case = out.get("use_case")
        liked_features = out.get("liked_features")
        soft: Dict[str, Any] = {"use_case": use_case if isinstance(use_case, str) and use_case.strip() else "general"}
        if liked_features is not None:
            soft["liked_features"] = liked_features if isinstance(liked_features, list) else [liked_features] if liked_features else []
        else:
            soft["liked_features"] = []
        filters["_soft_preferences"] = soft
        return filters
    except (json.JSONDecodeError, TypeError):
        return {
            "category": "Electronics",
            "product_type": "laptop",
            "_soft_preferences": {"use_case": "general", "liked_features": []},
        }


def _normalize_product_for_scoring(p: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure product dict has price_cents and shape expected by score_recommendations."""
    out = dict(p)
    if "price_cents" not in out and "price" in out:
        try:
            out["price_cents"] = int(round(float(out["price"]) * 100))
        except (TypeError, ValueError):
            pass
    return out


def baseline_ucp_recs_supabase_only(
    user_query: str,
    product_store: Any,
    limit: int = 10,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    UCP->recs baseline: extract filters from query with LLM (OPENAI_MODEL from .env),
    then return only products from the Supabase/product store. No schema/sample prompt;
    all recommendations come from search_products(filters).
    Returns (product_ids, product_dicts) for scoring.
    """
    filters = _extract_baseline_filters_ucp(user_query)
    products = product_store.search_products(filters, limit=limit)
    if not products:
        return [], []
    normalized = [_normalize_product_for_scoring(p) for p in products]
    ids = [str(p.get("product_id") or p.get("id", "")) for p in normalized]
    return ids, normalized


# ---------------------------------------------------------------------------
# Legacy baseline: schema + samples, LLM returns product_ids
# ---------------------------------------------------------------------------

SCHEMA_DESC = """
Table: products (laptops / electronics)
- id (UUID, primary key)
- title (text), category (text, e.g. Electronics), brand (text), product_type (text, e.g. laptop)
- price (numeric, dollars), attributes (JSONB: ram_gb, storage_gb, screen_size, cpu, gpu_vendor, etc.)
- rating, rating_count, source, link
"""


def get_sample_products_text(limit: int = 30) -> str:
    """Short description of products: a few sample rows (id, title, brand, price, key specs)."""
    from app.tools.supabase_product_store import get_product_store

    store = get_product_store()
    filters = {"category": "Electronics", "product_type": "laptop"}
    products = store.search_products(filters, limit=limit)
    lines = []
    for p in products[:limit]:
        pid = p.get("product_id") or p.get("id", "")
        name = p.get("name") or p.get("title", "")
        brand = p.get("brand", "")
        price = p.get("price", 0)
        attrs = p.get("attributes") or {}
        ram = attrs.get("ram_gb", "")
        screen = attrs.get("screen_size", "")
        lines.append(f"  id={pid} | {name} | brand={brand} | price=${price} | ram_gb={ram} | screen_size={screen}")
    return "\n".join(lines) if lines else "  (no products)"


BASELINE_PROMPT = """You are a product recommender. Given the database schema and a sample of products (laptops), recommend products that best match the user's query.

Schema:
{schema}

Sample products (id, title, brand, price, specs):
{samples}

User query: {query}

Respond with a JSON object containing exactly one key "product_ids" whose value is a list of product UUID strings (from the id column above). Recommend up to 10 products. Use only IDs that appear in the sample or that you infer are valid UUIDs. Output nothing else except this JSON."""


def run_baseline(user_query: str, limit: int = 10) -> List[str]:
    """
    Call LLM with schema + samples and user query; parse response for product_ids.
    Returns list of product ID strings (resolved from names if needed).
    """
    client = OpenAI()
    schema = SCHEMA_DESC
    samples = get_sample_products_text(limit=50)
    prompt = BASELINE_PROMPT.format(schema=schema, samples=samples, query=user_query)
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = (response.choices[0].message.content or "").strip()
    # Extract JSON (handle markdown code block)
    if "```" in content:
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```\s*$", "", content)
    try:
        data = json.loads(content)
        ids = data.get("product_ids") or []
        if isinstance(ids, str):
            ids = [ids]
        return [str(i).strip() for i in ids if i]
    except json.JSONDecodeError:
        # Fallback: find UUID-like strings
        uuid_re = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
        )
        return uuid_re.findall(content)


def baseline_recommendations_with_products(
    user_query: str,
    product_store,
) -> tuple[List[str], List[Dict[str, Any]]]:
    """
    Run baseline LLM, return (product_ids, product_dicts).
    Fetches full product dicts for the returned IDs so we can score.
    """
    ids = run_baseline(user_query)
    if not ids:
        return [], []
    # Resolve to product dicts (store may have get_by_ids)
    if hasattr(product_store, "get_by_ids"):
        products = product_store.get_by_ids(ids)
    else:
        products = []
        for pid in ids:
            p = product_store.get_by_id(pid) if hasattr(product_store, "get_by_id") else None
            if p:
                products.append(p)
    # Preserve order and filter to existing
    id_to_product = {(str(p.get("product_id") or p.get("id", ""))): p for p in products}
    ordered_ids = [i for i in ids if i in id_to_product]
    ordered_products = [id_to_product[i] for i in ordered_ids]
    return ordered_ids, ordered_products
