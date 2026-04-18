"""
MerchantAgent — per-merchant retrieval abstraction.

Owns catalog scope and search delegation for a single merchant.
The registry in main.py maps merchant IDs → MerchantAgent instances.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.contract import Offer, StructuredQuery
from app.endpoints import search_products
from app.models import Product, make_enriched_model, make_product_model
from app.schemas import SearchProductsRequest

logger = logging.getLogger(__name__)

# Merchant id slug grammar. Narrow so it is always safe to splice into an
# identifier position in SQL once it has matched. Client-supplied slug only —
# callers never pass raw user input through these helpers; ingress validates
# at the endpoint before routing into the registry.
MERCHANT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def validate_merchant_id(merchant_id: str) -> str:
    if not isinstance(merchant_id, str) or not MERCHANT_ID_RE.fullmatch(merchant_id):
        raise ValueError(
            f"invalid merchant_id {merchant_id!r}: must match {MERCHANT_ID_RE.pattern}"
        )
    return merchant_id


def merchant_catalog_table(merchant_id: str) -> str:
    """Fully-qualified raw catalog table for this merchant."""
    return f"merchants.products_{validate_merchant_id(merchant_id)}"


def merchant_enriched_table(merchant_id: str) -> str:
    """Fully-qualified enriched catalog table for this merchant."""
    return f"merchants.products_enriched_{validate_merchant_id(merchant_id)}"

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
    to the shared retrieval stack (KG → vector → SQL).

    ``kg_strategy`` identifies the enrichment mix this agent's KG was built
    from — one ``MerchantAgent`` ↔ one ``(merchant_id, kg_strategy)`` pair,
    mirroring how products_enriched rows are keyed. The default of
    ``"default_v1"`` is a pin for the pre-multi-strategy era; merchants that
    run two strategies in parallel will need two MerchantAgent instances and
    two KG instances (contract rule 3 of #52).
    """

    def __init__(
        self,
        merchant_id: str,
        domain: str,
        kg_strategy: str = "default_v1",
    ) -> None:
        self.merchant_id = validate_merchant_id(merchant_id)
        self.domain = domain
        self.kg_strategy = kg_strategy
        self._catalog_table = merchant_catalog_table(self.merchant_id)
        self._enriched_table = merchant_enriched_table(self.merchant_id)
        # Per-merchant ORM models. Cached on the instance so downstream
        # retrieval paths that take a model argument don't need to re-enter
        # the factory.
        self._product_model = make_product_model(self.merchant_id)
        self._enriched_model = make_enriched_model(self.merchant_id)

    def catalog_table(self) -> str:
        """Return fully-qualified raw catalog table name for this merchant."""
        return self._catalog_table

    def enriched_table(self) -> str:
        """Return fully-qualified enriched catalog table name for this merchant."""
        return self._enriched_table

    @property
    def product_model(self):
        return self._product_model

    @property
    def enriched_model(self):
        return self._enriched_model

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    @classmethod
    def from_csv(
        cls,
        path: str,
        *,
        merchant_id: str,
        domain: str,
        product_type: str,
        strategy: str = "normalizer_v1",
        source: Optional[str] = None,
        col_map: Optional[Dict[str, str]] = None,
        normalize_limit: int = 1000,
        skip_enrichment: bool = False,
    ) -> "MerchantAgent":
        """Bootstrap a new merchant from a CSV file.

        Pipeline (synchronous for this PR — issue #42 tracks UI progress):
          1. Validate ``merchant_id`` against the slug grammar.
          2. Create per-merchant tables via ``create_merchant_catalog``.
          3. Load CSV rows into the merchant's raw table.
          4. Run ``CatalogNormalizer`` scoped to this merchant's tables.
          5. Register the agent in the in-memory merchant registry so
             ``/merchant/<id>/search`` routes here.

        Large catalogs will block; callers should budget accordingly. Async
        ingest with progress reporting is intentionally deferred — that work
        belongs with the #42 UI counterpart, not this PR.
        """
        merchant_id = validate_merchant_id(merchant_id)

        # Local imports to avoid load-time cycles. ``app.main`` imports
        # merchant_agent; ingestion.* imports from merchant_agent too.
        from app.database import SessionLocal
        from app.ingestion.schema import create_merchant_catalog
        from app.ingestion.csv_loader import load_csv_into_merchant
        from app.catalog_ingestion import CatalogNormalizer

        session = SessionLocal()
        engine = session.get_bind()
        session.close()

        raw_conn = engine.raw_connection()
        try:
            create_merchant_catalog(merchant_id, raw_conn)
        finally:
            raw_conn.close()

        agent = cls(merchant_id=merchant_id, domain=domain)

        db = SessionLocal()
        try:
            load_summary = load_csv_into_merchant(
                path,
                db=db,
                product_model=agent.product_model,
                merchant_id=merchant_id,
                product_type=product_type,
                source=source or f"csv:{merchant_id}",
                col_map=col_map,
            )
        finally:
            db.close()
        logger.info("from_csv_loaded merchant=%s summary=%s", merchant_id, load_summary)

        if not skip_enrichment:
            db = SessionLocal()
            try:
                normalizer = CatalogNormalizer()
                enrich_summary = normalizer.batch_normalize(
                    db,
                    limit=normalize_limit,
                    product_model=agent.product_model,
                    enriched_model=agent.enriched_model,
                    strategy=strategy,
                )
                logger.info(
                    "from_csv_enriched merchant=%s summary=%s",
                    merchant_id, enrich_summary,
                )
            finally:
                db.close()

        # In-memory registry only — on restart, the operator re-runs bootstrap
        # to re-register. Tables persist in Postgres so data is not lost.
        from app import main as app_main
        app_main.merchants[merchant_id] = agent
        logger.info("merchant_registered id=%s domain=%s", merchant_id, domain)
        return agent

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
        # Thread kg_strategy through filters so endpoints.search_products can
        # pick it up at the KG call site without a new positional arg.
        merged_filters["_kg_strategy"] = self.kg_strategy

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

        # Pull per-product KG scores out of the response envelope. When
        # KG didn't run (SQL-only hit, KG offline) or none of the returned
        # products have a score, we fall back to the pre-#52 positional
        # ranking and log once at INFO so the degraded path stays visible.
        kg_scores: Dict[str, Dict[str, Any]] = (
            (resp.data.scores or {}) if resp.data else {}
        )
        scored_totals = [
            kg_scores[p.product_id]["score"]
            for p in products
            if p.product_id in kg_scores
        ]
        if scored_totals:
            # Min-max normalize onto [0, 1] per request. Single-score batches
            # collapse to 1.0 rather than divide-by-zero.
            lo, hi = min(scored_totals), max(scored_totals)
            span = hi - lo if hi > lo else 0.0
        else:
            lo = hi = span = 0.0
            logger.info(
                "merchant_search_no_kg_scores merchant=%s n=%d — falling back "
                "to positional score (KG offline or SQL-only hit)",
                self.merchant_id, len(products),
            )

        offers: List[Offer] = []
        for i, p in enumerate(products):
            score_row = kg_scores.get(p.product_id)
            if score_row is not None:
                raw_total = float(score_row["score"])
                if span > 0:
                    normalized = (raw_total - lo) / span
                else:
                    normalized = 1.0
                breakdown_terms = {
                    k: float(v) for k, v in (score_row.get("breakdown") or {}).items()
                }
                breakdown_terms["raw"] = raw_total
                offers.append(Offer(
                    merchant_id=self.merchant_id,
                    product_id=p.product_id,
                    score=round(normalized, 4),
                    score_breakdown=breakdown_terms,
                    product=p,
                    rationale=p.reason or "",
                ))
            else:
                # Pre-#52 fallback: positional placeholder with empty breakdown.
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
        _Product = self._product_model
        if self.merchant_id == "default":
            from sqlalchemy import or_
            catalog_q = db.query(_Product).filter(
                or_(_Product.merchant_id.is_(None), _Product.merchant_id == "default")
            )
        else:
            catalog_q = db.query(_Product).filter(_Product.merchant_id == self.merchant_id)

        catalog_size = catalog_q.count()

        from sqlalchemy import func
        max_created = catalog_q.with_entities(func.max(_Product.created_at)).scalar()

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
