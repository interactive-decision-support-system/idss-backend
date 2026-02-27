"""
Baseline: out-of-the-box LLM with DB schema and user query.

Loads product table schema + short description (or sample rows), prompts the LLM
to return recommended product IDs (or names resolved to IDs). Same scoring as main system.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_MCP = os.path.join(_REPO_ROOT, "mcp-server")
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)

from openai import OpenAI


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
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
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
