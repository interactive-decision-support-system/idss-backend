"""taxonomy_v1 — assigns each product a product_type label.

Pivot for every other per-product agent. The specialist consults this output
to pick the right prompt fragment; soft_tagger uses it to choose tag vocabulary.
Falls back to product_type='unknown' with confidence=0.0 rather than guessing
when the LLM is unavailable or returns nonsense.
"""

from __future__ import annotations

import json
from typing import Any

from merchant_agent.enrichment import registry
from merchant_agent.enrichment.base import BaseEnrichmentAgent
from merchant_agent.enrichment.tools.llm_client import LLMClient, default_model
from merchant_agent.enrichment.types import ProductInput, StrategyOutput


# gpt-5-mini is a reasoning model; ~500-1500 tokens go to invisible CoT
# before any output. Budget covers reasoning + visible output + margin.
# See Task 12 / ENRICHMENT_MODEL_DIAGNOSIS.md.
_MAX_COMPLETION_TOKENS = 2000


_SYSTEM = (
    "You classify e-commerce products by product type. Return JSON with keys:\n"
    "  product_type     short noun (e.g. 'laptop', 'blender', 'headphones', 'smart-bulb', 'office-chair')\n"
    "  confidence       0.0–1.0 — your certainty\n"
    "If the input is too sparse to classify, return product_type='unknown' "
    "and confidence below 0.3. Never invent specs — only label."
)


@registry.register
class TaxonomyAgent(BaseEnrichmentAgent):
    STRATEGY = "taxonomy_v1"
    OUTPUT_KEYS = frozenset({"product_type", "product_type_confidence"})
    DEFAULT_MODEL = "gpt-5-mini"

    def __init__(self, llm: LLMClient | None = None) -> None:
        super().__init__()
        self._llm = llm or LLMClient()

    def _invoke(self, product: ProductInput, context: dict[str, Any]) -> StrategyOutput:
        user = _format_user(product)
        resp = self._llm.complete(
            system=_SYSTEM,
            user=user,
            model=context.get("model") or default_model(),
            json_mode=True,
            max_tokens=_MAX_COMPLETION_TOKENS,
            temperature=0.1,
        )
        context["_last_cost_usd"] = resp.cost_usd
        data = resp.parsed_json or {}
        attrs = {
            "product_type": str(data.get("product_type") or "unknown"),
            "product_type_confidence": float(data.get("confidence") or 0.0),
        }
        return StrategyOutput(
            product_id=product.product_id,
            strategy=self.STRATEGY,
            model=resp.model,
            attributes=attrs,
            confidence=attrs["product_type_confidence"],
        )


def _format_user(p: ProductInput) -> str:
    payload = {
        "title": p.title,
        "category": p.category,
        "brand": p.brand,
        "description": (p.description or "")[:400],
        "raw_attribute_keys": sorted((p.raw_attributes or {}).keys()),
    }
    return "Classify this product:\n" + json.dumps(payload, ensure_ascii=False)
