"""assessor_v1 — catalog-level profiler.

Reads a sample of products and produces an AssessorOutput: domain mix, column
density, sparse JSONB keys, discovered product types, and a recommended
strategy plan. Output is per-catalog (not per-product) so it lives in JSON,
not in products_enriched.

Today the assessor uses the LLM only to summarize discovered product types
(cheap utility-tier call — gpt-5-nano by default, see
``tools.llm_client.utility_model``); the counting work is deterministic
Python. Future revisions may route strategy-planning reasoning through the
composer-tier model, at which point this docstring should be updated.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from app.enrichment.tools.llm_client import LLMClient, utility_model
from app.enrichment.types import AssessorOutput, ProductInput

logger = logging.getLogger(__name__)


# gpt-5-mini is a reasoning model; even simple list-of-labels output
# requires ~500-1000 tokens of invisible CoT. Budget covers reasoning
# + up to 12 product-type labels + margin. See Task 12 / ENRICHMENT_MODEL_DIAGNOSIS.md.
_MAX_COMPLETION_TOKENS = 2000


# Strategies the assessor can recommend. Kept here so the assessor can reason
# about them without circular imports of the agent classes themselves.
# composer_v1 always runs (single writer of the canonical row — #83); the
# orchestrator appends it last regardless of assessor filtering.
# soft_tagger_v1 is opt-in only — default-off per issue #115 point 2
# (label explosion + 67% of generation spend for a signal with no confirmed
# downstream consumer). Pass it explicitly in recommended_strategies to enable.
_AVAILABLE_STRATEGIES = (
    "taxonomy_v1",
    "parser_v1",
    "specialist_v1",
    "scraper_v1",
    "composer_v1",
)


_SYSTEM = (
    "You read a sample of e-commerce products and identify the product types "
    "present. Return JSON with one key:\n"
    "  discovered_product_types  list of short snake_case product-type labels "
    "(e.g. ['laptop','blender','smart_bulb']). Up to 12. No duplicates.\n"
    "Never invent — only label what's clearly present."
)


class Assessor:
    """Not registered with the per-product registry — runs once per catalog."""

    STRATEGY = "assessor_v1"

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm or LLMClient()

    def assess(self, products: list[ProductInput], *, model: str | None = None) -> AssessorOutput:
        size = len(products)
        if size == 0:
            return AssessorOutput(catalog_size=0, recommended_strategies=list(_AVAILABLE_STRATEGIES))

        # Domain distribution from the raw 'category' field.
        cat_counts: Counter[str] = Counter()
        for p in products:
            cat_counts[p.category or "unknown"] += 1
        domain_dist = {k: v / size for k, v in cat_counts.items()}

        # Column density: fraction of rows whose top-level field is non-null.
        column_density = {
            "title": _density(products, lambda p: p.title),
            "brand": _density(products, lambda p: p.brand),
            "description": _density(products, lambda p: p.description),
            "category": _density(products, lambda p: p.category),
            "price": _density(products, lambda p: p.price),
        }

        # JSONB attribute key density.
        key_counts: Counter[str] = Counter()
        for p in products:
            key_counts.update((p.raw_attributes or {}).keys())
        sparse = sorted(k for k, c in key_counts.items() if c / size < 0.5)

        discovered = self._discover_product_types(products, model=model)
        recommended = list(_AVAILABLE_STRATEGIES)

        return AssessorOutput(
            catalog_size=size,
            domain_distribution=domain_dist,
            column_density=column_density,
            sparse_attribute_keys=sparse,
            discovered_product_types=discovered,
            recommended_strategies=recommended,
            notes=f"sampled={size}; sparse_keys={len(sparse)}",
        )

    # ------------------------------------------------------------------

    def _discover_product_types(
        self, products: list[ProductInput], *, model: str | None = None
    ) -> list[str]:
        if not products:
            return []
        try:
            sample = [
                {"title": p.title, "category": p.category, "brand": p.brand}
                for p in products[:30]
            ]
            resp = self._llm.complete(
                system=_SYSTEM,
                user="Sample:\n" + json.dumps(sample, ensure_ascii=False),
                model=model or utility_model(),
                json_mode=True,
                max_tokens=_MAX_COMPLETION_TOKENS,
                temperature=0.1,
            )
            data = resp.parsed_json or {}
            seen: list[str] = []
            for x in data.get("discovered_product_types") or []:
                s = str(x).strip().lower().replace(" ", "_")
                if s and s not in seen:
                    seen.append(s)
            return seen[:12]
        except Exception as exc:  # noqa: BLE001 - assessor must always return something
            logger.warning("assessor_discover_failed: %s", exc)
            return []


def _density(products: list[ProductInput], getter) -> float:
    if not products:
        return 0.0
    filled = sum(1 for p in products if getter(p) not in (None, ""))
    return filled / len(products)


def serialize(assessment: AssessorOutput) -> str:
    """Stable JSON serialization for on-disk artifacts."""
    return json.dumps(assessment.model_dump(mode="json"), ensure_ascii=False, indent=2)
