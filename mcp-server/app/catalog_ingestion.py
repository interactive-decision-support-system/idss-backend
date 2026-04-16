"""
Catalog Ingestion / Normalization Layer
=======================================
Uses an LLM to rewrite product descriptions into a consistent, concise style
and stores the result in products_enriched under strategy='normalizer_v1'.

The raw `products` table is the golden source and is never mutated here.
Derived attributes land in products_enriched keyed by (product_id, strategy)
so multiple enrichment strategies can coexist for A/B and merchant simulations.

Normalization philosophy:
  - 1-2 sentences, ≤ 30 words each
  - Present tense, feature-focused (no marketing hype)
  - Leads with the most important spec or use-case
  - Never opens with the product name verbatim
  - If description is missing, infers from title + specs

Usage:
  from app.catalog_ingestion import CatalogNormalizer
  normalizer = CatalogNormalizer()
  result = normalizer.batch_normalize(db, limit=100, dry_run=False)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert

logger = logging.getLogger(__name__)

# Strategy label for this enricher's output. Fine-grained: description normalization
# is its own strategy, independent of other enrichers (soft tags, LLM spec extract, etc.).
STRATEGY = "normalizer_v1"
MODEL = "gpt-4o-mini"

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a product catalog editor for an e-commerce platform.\n"
    "Rewrite the product description in exactly 1-2 sentences (max 30 words total).\n\n"
    "Style rules:\n"
    "- Present tense, feature-focused — highlight the 2-3 most important specs or benefits\n"
    "- Do NOT open with the exact product name or brand as the first word\n"
    "- No filler phrases ('This amazing product…', 'Great for…')\n"
    "- No marketing hyperbole (best-in-class, revolutionary, etc.)\n"
    "- If description is missing, infer from the title and specs provided\n"
    "- Output ONLY the rewritten description — no quotes, no prefix, nothing else"
)

# Keys extracted from attributes JSONB to give the LLM spec context
_SPEC_KEYS = (
    "ram_gb", "storage_gb", "processor", "cpu", "gpu", "gpu_model", "gpu_vendor",
    "display_size", "screen_size", "battery_life", "os", "operating_system",
    "resolution", "refresh_rate", "weight_kg", "weight_lbs",
    "author", "genre", "pages", "year",
    "engine", "mileage", "fuel_type", "transmission",
)


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

class CatalogNormalizer:
    """
    Normalizes product descriptions using gpt-4o-mini.

    Falls back gracefully if OpenAI quota is exhausted or unavailable:
    normalize_product() returns None and batch_normalize() logs the failure
    without crashing.
    """

    def __init__(self, openai_client=None):
        if openai_client is None:
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            except ImportError:
                self.client = None
                logger.warning("openai package not installed — CatalogNormalizer will be a no-op")
        else:
            self.client = openai_client

    # ------------------------------------------------------------------
    # Single-product normalization
    # ------------------------------------------------------------------

    def normalize_product(self, product) -> Optional[str]:
        """
        Call LLM to normalize the description for one Product ORM object.

        Returns the normalized description string, or None on failure
        (quota exhausted, network error, missing client, etc.).
        """
        if self.client is None:
            return None

        attrs: Dict[str, Any] = product.attributes or {}

        # Build a terse context block for the LLM
        lines = [f"Product: {product.name or '(unnamed)'}"]
        if product.brand:
            lines.append(f"Brand: {product.brand}")
        if product.category:
            lines.append(f"Category: {product.category}")

        existing_desc = attrs.get("description") or ""
        if existing_desc:
            lines.append(f"Current description: {existing_desc[:300]}")

        # Pull numeric/text specs from JSONB attributes
        specs = {k: attrs[k] for k in _SPEC_KEYS if k in attrs and attrs[k]}
        if specs:
            lines.append(f"Specs: {json.dumps(specs, ensure_ascii=False)}")

        context = "\n".join(lines)

        try:
            resp = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                max_tokens=80,
                temperature=0.3,
            )
            normalized = resp.choices[0].message.content.strip().strip('"').strip("'")
            return normalized if normalized else None
        except Exception as exc:
            logger.warning(
                "catalog_normalize_failed",
                extra={"product_id": str(getattr(product, "product_id", "?")), "error": str(exc)},
            )
            return None

    # ------------------------------------------------------------------
    # Batch normalization
    # ------------------------------------------------------------------

    def batch_normalize(
        self,
        db,
        *,
        limit: int = 100,
        dry_run: bool = False,
        force: bool = False,
        product_model=None,
        enriched_model=None,
        strategy: str = STRATEGY,
    ) -> Dict[str, int]:
        """
        Process up to `limit` products and write normalized output to the
        per-merchant enriched table under ``strategy``.

        The raw products table is NEVER mutated by this method.

        Args:
            db:              SQLAlchemy session.
            limit:           Maximum number of products to process.
            dry_run:         If True, print results but do not write to DB.
            force:           If True, re-normalize products that already have
                             a row under this strategy (UPSERT overwrites).
            product_model:   Optional per-merchant Product ORM class from
                             ``make_product_model(merchant_id)``. Defaults to
                             the module-level ``Product`` (default merchant).
            enriched_model:  Same idea for ``ProductEnriched``.
            strategy:        Strategy label written to the enriched row.
                             Normalizer output is versioned by this label so
                             A/B comparisons are just different labels.

        Returns:
            {"normalized": N, "skipped": N, "failed": N}
        """
        from app.models import Product, ProductEnriched  # local import to avoid circular deps
        product_model = product_model or Product
        enriched_model = enriched_model or ProductEnriched

        # Find products that don't yet have a row under this strategy (unless forcing).
        q = db.query(product_model)
        if not force:
            already_done = (
                db.query(enriched_model.product_id)
                .filter(enriched_model.strategy == strategy)
            )
            q = q.filter(~product_model.product_id.in_(already_done))

        products = q.limit(limit).all()

        normalized_count = 0
        skipped_count = 0
        failed_count = 0

        for product in products:
            normalized = self.normalize_product(product)

            if normalized is None:
                failed_count += 1
                logger.warning(
                    "batch_normalize_skip",
                    extra={"product_id": str(product.product_id), "reason": "LLM returned None"},
                )
                continue

            if dry_run:
                print(
                    f"[DRY RUN] {str(product.product_id)[:8]} "
                    f"{(product.name or '')[:45]!r:46s} → {normalized[:80]!r}"
                )
            else:
                payload = {
                    "normalized_description": normalized,
                    "normalized_at": datetime.now(timezone.utc).isoformat(),
                }
                stmt = pg_insert(enriched_model).values(
                    product_id=product.product_id,
                    strategy=strategy,
                    attributes=payload,
                    model=MODEL,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["product_id", "strategy"],
                    set_={
                        "attributes": payload,
                        "model": MODEL,
                        "updated_at": datetime.now(timezone.utc),
                    },
                )
                db.execute(stmt)

            normalized_count += 1

        if not dry_run and normalized_count > 0:
            db.commit()
            logger.info(
                "batch_normalize_done",
                extra={
                    "strategy": strategy,
                    "normalized": normalized_count,
                    "skipped": skipped_count,
                    "failed": failed_count,
                },
            )

        return {"normalized": normalized_count, "skipped": skipped_count, "failed": failed_count}
