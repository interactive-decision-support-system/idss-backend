"""
MerchantAgent — per-merchant retrieval abstraction.

Owns catalog scope and search delegation for a single merchant.
The registry in main.py maps merchant IDs → MerchantAgent instances.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.contract import Offer, StructuredQuery
from app.endpoints import search_products
from app.models import Product
from app.schemas import SearchProductsRequest

logger = logging.getLogger(__name__)

# Translate agent slot vocabulary → KG scoring-flag vocabulary.
# Two naming conventions arrive depending on the upstream path:
#   - "use_case"  (singular, string)  — from agent chat interview
#   - "use_cases" (plural,   list)    — from MCP query parser
# The agent schema says "machine_learning"; the MCP parser says "ml".
_USE_CASE_FLAG_MAP = {
    "ml": "good_for_ml",
    "machine_learning": "good_for_ml",
    "gaming": "good_for_gaming",
    "web_dev": "good_for_web_dev",
    "creative": "good_for_creative",
    "linux": "good_for_linux",
}

# Soft-preference keys whose values carry useful text for KG substring matching.
_TEXT_HARVEST_SLOTS = ("subcategory", "brand", "genre", "style", "material", "color")


class MerchantAgent:
    """Per-merchant search agent. Scopes catalog by merchant_id and delegates
    to the shared retrieval stack (KG → vector → SQL)."""

    def __init__(
        self,
        merchant_id: str,
        domain: str,
    ) -> None:
        self.merchant_id = merchant_id
        self.domain = domain

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self, query: StructuredQuery, db: Session
    ) -> List[Offer]:
        merged_filters: Dict[str, Any] = {**query.hard_filters, **query.soft_preferences}
        if "category" not in merged_filters and query.domain:
            merged_filters["category"] = query.domain

        # --- Slot translation: use_case(s) → good_for_* flags --------
        _raw_uc = merged_filters.get("use_cases") or []
        if isinstance(_raw_uc, str):
            _raw_uc = [_raw_uc]
        _single_uc = merged_filters.get("use_case")
        if _single_uc and isinstance(_single_uc, str):
            _raw_uc.append(_single_uc)
        for _uc in _raw_uc:
            _flag = _USE_CASE_FLAG_MAP.get(str(_uc).lower().strip())
            if _flag:
                merged_filters[_flag] = True

        # --- Catalog scope --------------------------------------------
        merged_filters["merchant_id"] = self.merchant_id

        # --- Extract exclude_ids from user_context --------------------
        _ctx = query.user_context if isinstance(query.user_context, dict) else {}
        exclude_ids = list(_ctx.get("exclude_ids") or [])
        exclude_set = set(exclude_ids)

        # --- Harvest text-ish slots for KG substring matching ---------
        _parts: List[str] = []
        if _ctx.get("query"):
            _parts.append(str(_ctx["query"]))
        for _slot in _TEXT_HARVEST_SLOTS:
            _val = query.soft_preferences.get(_slot)
            if isinstance(_val, list):
                _parts.extend(str(v) for v in _val if v)
            elif isinstance(_val, str) and _val.strip().lower() not in ("", "no preference", "specific brand"):
                _parts.append(_val)
        text_query = " ".join(dict.fromkeys(p.strip() for p in _parts if p.strip())) or None

        # --- Build legacy request and call retrieval stack ------------
        over_fetch = min(query.top_k + len(exclude_ids), 100)
        legacy_req = SearchProductsRequest(
            query=text_query,
            filters=merged_filters,
            limit=over_fetch,
        )
        resp = await search_products(legacy_req, db)

        raw = resp.data.products if resp.data and resp.data.products else []
        products = [p for p in raw if p.product_id not in exclude_set][: query.top_k]
        n = max(len(products), 1)
        offers: List[Offer] = []
        for i, p in enumerate(products):
            offers.append(Offer(
                merchant_id=self.merchant_id,
                product_id=p.product_id,
                score=round(1.0 - (i / n), 4),
                score_breakdown={},
                product=p,
                rationale=p.reason or "",
            ))
        return offers

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self, db: Session) -> dict:
        if self.merchant_id == "default":
            from sqlalchemy import or_
            catalog_q = db.query(Product).filter(
                or_(Product.merchant_id.is_(None), Product.merchant_id == "default")
            )
        else:
            catalog_q = db.query(Product).filter(Product.merchant_id == self.merchant_id)

        catalog_size = catalog_q.count()

        from sqlalchemy import func
        max_created = catalog_q.with_entities(func.max(Product.created_at)).scalar()

        # Vector index mtime (best-effort)
        vector_index_mtime = None
        try:
            cache_dir = os.path.join(os.path.dirname(__file__), "..", "data", "vector_cache")
            idx_path = os.path.join(cache_dir, "faiss_index.bin")
            if os.path.exists(idx_path):
                vector_index_mtime = os.path.getmtime(idx_path)
        except Exception:
            pass

        return {
            "merchant_id": self.merchant_id,
            "catalog_size": catalog_size,
            "kg_last_update": max_created.isoformat() if max_created else None,
            "vector_index_mtime": vector_index_mtime,
        }
