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


def upsert_registry_row(
    *,
    merchant_id: str,
    domain: str,
    strategy: str,
    kg_strategy: str,
) -> None:
    """Persist this merchant into merchants.registry (issue #53).

    UPSERT semantics: re-running ``from_csv`` with the same ``merchant_id``
    refreshes ``domain`` / ``strategy`` / ``kg_strategy`` and bumps
    ``updated_at``; it does not error. This is the only idempotency
    guarantee in the registry PR — CSV row-level dedupe is unrelated.
    """
    from sqlalchemy import text

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        db.execute(
            text(
                """
                INSERT INTO merchants.registry
                    (merchant_id, domain, strategy, kg_strategy)
                VALUES
                    (:mid, :domain, :strategy, :kg_strategy)
                ON CONFLICT (merchant_id) DO UPDATE SET
                    domain      = EXCLUDED.domain,
                    strategy    = EXCLUDED.strategy,
                    kg_strategy = EXCLUDED.kg_strategy,
                    updated_at  = NOW()
                """
            ),
            {
                "mid": validate_merchant_id(merchant_id),
                "domain": domain,
                "strategy": strategy,
                "kg_strategy": kg_strategy,
            },
        )
        db.commit()
    finally:
        db.close()

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
        strategy: str = "normalizer_v1",
        kg_strategy: str = "default_v1",
        *,
        catalog: Optional["Catalog"] = None,
    ) -> None:
        """Construct an agent bound to a (merchant_id, catalog) pair.

        The bare ``MerchantAgent(mid, domain)`` form derives the catalog from
        the slug *without* probing Postgres — fine for the lifespan bootstrap
        and unit tests that own their own fixtures, but it leaves
        ``MerchantAgent('ghost', 'books')`` succeeding and producing a broken
        agent. For the request hot path use ``MerchantAgent.open(...)`` (or
        pass an already-verified ``catalog=`` from ``open_catalog``) so a
        missing-table deploy skew fails loudly here instead of at first query.
        """
        self.merchant_id = validate_merchant_id(merchant_id)
        self.domain = domain
        # One MerchantAgent instance <=> one (merchant_id, strategy) pair.
        # KG and vector store are keyed by that pair; two enrichment
        # strategies in parallel = two agents. See issues #52, #56.
        self.strategy = strategy
        self.kg_strategy = kg_strategy

        # Lazy import to break the cycle: app.catalog imports from this
        # module (validate_merchant_id, merchant_catalog_table, ...), so a
        # top-level import here would deadlock at startup.
        from app.catalog import Catalog

        if catalog is None:
            catalog = Catalog.for_merchant(self.merchant_id)
        elif catalog.merchant_id != self.merchant_id:
            # Defence against a caller wiring the wrong merchant's tables
            # into a fresh agent — silent mismatch would write into the
            # wrong tenant's catalog.
            raise ValueError(
                f"catalog.merchant_id={catalog.merchant_id!r} does not match "
                f"merchant_id={self.merchant_id!r}"
            )
        self.catalog = catalog

    @classmethod
    def open(
        cls,
        merchant_id: str,
        db: Session,
        *,
        domain: str,
        strategy: str = "normalizer_v1",
        kg_strategy: str = "default_v1",
    ) -> "MerchantAgent":
        """Construct an agent after verifying the catalog tables exist.

        Use from request handlers / hydration paths. Raises
        ``app.catalog.CatalogNotFound`` when the merchant's raw table is
        missing — the registry pointed at a slug whose DDL was never run.
        """
        from app.catalog import open_catalog

        catalog = open_catalog(merchant_id, db)
        return cls(
            merchant_id=merchant_id,
            domain=domain,
            strategy=strategy,
            kg_strategy=kg_strategy,
            catalog=catalog,
        )

    # --- Catalog accessors ---------------------------------------------
    # Read-through to the underlying Catalog. Kept on the agent so the
    # search / enrichment paths don't need to thread a separate Catalog
    # parameter — the agent already travels with every request.

    def catalog_table(self) -> str:
        """Fully-qualified raw catalog table name for this merchant."""
        return self.catalog.raw_table

    def enriched_table(self) -> str:
        """Fully-qualified enriched catalog table name for this merchant."""
        return self.catalog.enriched_table

    @property
    def product_model(self):
        return self.catalog.product_model

    @property
    def enriched_model(self):
        return self.catalog.enriched_model

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
        kg_strategy: str = "default_v1",
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
          5. UPSERT the merchant into ``merchants.registry`` (the durable
             source of truth — see issue #53).

        The returned agent is *not* installed into ``app.main.merchants``.
        That cache is owned by the route handler, which writes it after
        ``from_csv`` returns successfully (issue #69 — keeping ingest free of
        process-global mutation lets tests, scripts, and any future async
        ingest worker call this without poking at FastAPI module state).

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

        agent = cls(
            merchant_id=merchant_id,
            domain=domain,
            strategy=strategy,
            kg_strategy=kg_strategy,
        )

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

            # Build the per-(merchant, strategy) FAISS index off the freshly
            # enriched catalog. Failure here is non-fatal — search still
            # works without vector retrieval — but we log it loudly so the
            # ingest UI can surface the degraded state. See issue #56.
            try:
                agent.refresh_vector_index()
            except Exception as exc:
                logger.warning(
                    "from_csv_vector_index_failed merchant=%s err=%s",
                    merchant_id, exc,
                )

        # Durable registration. merchants.registry is the source of truth;
        # other workers / post-restart processes populate the in-process
        # cache via lazy hydration. See issue #53.
        upsert_registry_row(
            merchant_id=merchant_id,
            domain=domain,
            strategy=strategy,
            kg_strategy=kg_strategy,
        )

        # No write into app.main.merchants here — that cache belongs to the
        # route handler, which knows whether the caller is a request that
        # should serve the agent next (POST /merchant) or a script that
        # shouldn't (offline backfills). Issue #69.
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
        # Route vector retrieval to this agent's (merchant, strategy) index.
        # endpoints.search_products reads this to pick the right FAISS file.
        merged_filters["strategy"] = self.strategy

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
    # Vector index build / refresh
    # ------------------------------------------------------------------

    def refresh_vector_index(self) -> int:
        """Rebuild this merchant's FAISS index from its current catalog.

        Reads raw rows from the merchant's per-tenant ``Product`` table and
        hydrates the normalized description from its enriched sidecar (so the
        embedding matches what search will see at read time). The resulting
        index is written under ``data/merchants/<merchant_id>/<strategy>/``
        and hot-swapped in the in-process store cache.

        Returns the number of products indexed.
        """
        from app.database import SessionLocal
        from app.vector_search import get_vector_store

        _Product = self.product_model
        _Enriched = self.enriched_model

        db = SessionLocal()
        try:
            enriched_by_id: Dict[str, str] = {
                row.product_id: (row.attributes or {}).get("normalized_description") or ""
                for row in db.query(_Enriched).filter(_Enriched.strategy == self.strategy).all()
            }
            raw_products = db.query(_Product).all()

            products_for_index: List[Dict[str, Any]] = []
            for p in raw_products:
                description = enriched_by_id.get(p.product_id) or (
                    (getattr(p, "attributes", None) or {}).get("description", "") if getattr(p, "attributes", None) else ""
                )
                products_for_index.append({
                    "product_id": p.product_id,
                    "name": p.name,
                    "description": description,
                    "category": getattr(p, "category", None) or "",
                    "brand": getattr(p, "brand", None) or "",
                    "product_type": getattr(p, "product_type", None) or "",
                    "metadata": getattr(p, "attributes", None) or {},
                })
        finally:
            db.close()

        store = get_vector_store(self.merchant_id, self.strategy)
        store.build_index(products_for_index, save_index=True)
        logger.info(
            "vector_index_built merchant=%s strategy=%s count=%d path=%s",
            self.merchant_id, self.strategy, len(products_for_index), store.index_path,
        )
        return len(products_for_index)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self, db: Session) -> dict:
        # The per-merchant Product model is already bound to
        # merchants.products_<id> — no merchant_id filter needed. The old
        # ``.filter(Product.merchant_id == ...)`` was vestigial from the
        # shared-table era and would silently return 0 rows on any merchant
        # whose CSV import didn't populate the merchant_id column.
        _Product = self.product_model
        catalog_q = db.query(_Product)

        catalog_size = catalog_q.count()

        from sqlalchemy import func
        max_created = catalog_q.with_entities(func.max(_Product.created_at)).scalar()

        # Vector index mtime — read from this merchant's own path, not the
        # global one. A missing file means the index was never built for
        # this (merchant, strategy) pair and search will fall back to
        # keyword-only retrieval.
        vector_index_mtime = None
        try:
            from app.vector_search import merchant_index_path
            idx_path = merchant_index_path(self.merchant_id, self.strategy)
            if idx_path.exists():
                vector_index_mtime = idx_path.stat().st_mtime
        except Exception:
            pass

        return {
            "merchant_id": self.merchant_id,
            "strategy": self.strategy,
            "catalog_size": catalog_size,
            "kg_last_update": max_created.isoformat() if max_created else None,
            "vector_index_mtime": vector_index_mtime,
        }
