"""LLMOrchestrator: per product, asks an LLM which subset of strategies to run.

The LLM sees the assessor output + a per-product summary and returns a list of
strategy labels. taxonomy_v1 is force-included (the specialist needs it);
soft_tagger_v1 is force-included (cheap and high-value). Everything else is
the LLM's call.
"""

from __future__ import annotations

import json
import logging
from typing import Iterable

from app.enrichment.tools.llm_client import LLMClient, default_model
from app.enrichment.types import AssessorOutput, OrchestratorPlan, ProductInput

logger = logging.getLogger(__name__)


_SYSTEM = (
    "You decide which enrichment strategies to run on each product to maximize "
    "useful coverage at minimal cost. Return JSON with key:\n"
    "  per_product  list of objects {product_id, strategies}\n"
    "Available strategies and what they do:\n"
    "  parser_v1       extracts numeric/text specs from existing fields. CHEAP.\n"
    "  specialist_v1   adds capabilities + use-case fit + buyer questions. MEDIUM.\n"
    "  scraper_v1      fetches an external page (only useful if a manufacturer "
    "URL is present and the catalog is sparse). EXPENSIVE.\n"
    "Rules:\n"
    "  - Always include parser_v1 when the product has a description.\n"
    "  - Skip specialist_v1 when the title is too sparse to enrich meaningfully.\n"
    "  - Skip scraper_v1 unless raw_attributes already lists a merchant_product_url.\n"
    "  - Output exactly one entry per product_id you were given."
)


class LLMOrchestrator:
    # taxonomy and soft_tagger are not negotiable — taxonomy gates everything,
    # soft_tagger is cheap and the KG depends on it. The LLM only chooses among
    # the others.
    _FORCED = ("taxonomy_v1", "soft_tagger_v1")
    _CHOOSABLE = ("parser_v1", "specialist_v1", "scraper_v1")

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm or LLMClient()

    def plan(self, products: list[ProductInput], assessment: AssessorOutput) -> OrchestratorPlan:
        if not products:
            return OrchestratorPlan()

        choices = self._llm_choose(products, assessment)
        per: dict = {}
        for p in products:
            chosen = choices.get(str(p.product_id), [])
            chosen_clean = [s for s in chosen if s in self._CHOOSABLE]
            per[p.product_id] = list(_dedupe_in_order(self._FORCED + tuple(chosen_clean)))
        return OrchestratorPlan(per_product_agents=per)

    # ------------------------------------------------------------------

    def _llm_choose(
        self, products: list[ProductInput], assessment: AssessorOutput
    ) -> dict[str, list[str]]:
        try:
            user_payload = {
                "assessment": {
                    "catalog_size": assessment.catalog_size,
                    "discovered_product_types": assessment.discovered_product_types,
                    "column_density": assessment.column_density,
                    "sparse_attribute_keys": assessment.sparse_attribute_keys,
                },
                "products": [
                    {
                        "product_id": str(p.product_id),
                        "title": p.title,
                        "category": p.category,
                        "brand": p.brand,
                        "has_description": bool(p.description),
                        "raw_attribute_keys": sorted((p.raw_attributes or {}).keys()),
                    }
                    for p in products
                ],
            }
            resp = self._llm.complete(
                system=_SYSTEM,
                user=json.dumps(user_payload, ensure_ascii=False),
                model=default_model(large=True),
                json_mode=True,
                max_tokens=1500,
                temperature=0.1,
            )
            data = resp.parsed_json or {}
            out: dict[str, list[str]] = {}
            for entry in data.get("per_product") or []:
                if not isinstance(entry, dict):
                    continue
                pid = entry.get("product_id")
                strats = entry.get("strategies") or []
                if isinstance(pid, str) and isinstance(strats, list):
                    out[pid] = [str(s) for s in strats]
            return out
        except Exception as exc:  # noqa: BLE001 - never break a run on planning failure
            logger.warning("llm_orchestrator_planning_failed: %s — falling back to forced-only", exc)
            return {}


def _dedupe_in_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
