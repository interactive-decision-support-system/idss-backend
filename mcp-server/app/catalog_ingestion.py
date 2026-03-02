"""
Catalog Ingestion / Normalization Layer
=======================================
Uses an LLM to rewrite product descriptions into a consistent, concise style
and stores the result in attributes['normalized_description'].

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

logger = logging.getLogger(__name__)

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
    ) -> Dict[str, int]:
        """
        Process up to `limit` products from the DB.

        Args:
            db:       SQLAlchemy session.
            limit:    Maximum number of products to process.
            dry_run:  If True, print results but do not write to DB.
            force:    If True, reprocess products that already have normalized_description.

        Returns:
            {"normalized": N, "skipped": N, "failed": N}
        """
        from app.models import Product  # local import to avoid circular deps

        products = db.query(Product).limit(limit * 3).all()  # fetch extra so we can skip

        normalized_count = 0
        skipped_count = 0
        failed_count = 0
        processed = 0

        for product in products:
            if processed >= limit:
                break

            attrs: Dict[str, Any] = product.attributes or {}

            # Skip if already normalized and not forcing
            if not force and "normalized_description" in attrs:
                skipped_count += 1
                continue

            processed += 1
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
                new_attrs = dict(attrs)
                new_attrs["normalized_description"] = normalized
                new_attrs["normalized_at"] = datetime.now(timezone.utc).isoformat()
                product.attributes = new_attrs
                db.add(product)

            normalized_count += 1

        if not dry_run and normalized_count > 0:
            db.commit()
            logger.info(
                "batch_normalize_done",
                extra={"normalized": normalized_count, "skipped": skipped_count, "failed": failed_count},
            )

        return {"normalized": normalized_count, "skipped": skipped_count, "failed": failed_count}
