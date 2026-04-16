"""soft_tagger_v1 — generates good_for_* tags with confidence scores.

Vocabulary is generated, not hard-coded: the tagger sees taxonomy + parser
+ specialist outputs (passed through context) and emits whatever good_for_*
labels the data supports. This keeps it useful across electronics product
types without baking in laptop-specific tags.

Output is shaped as a sub-dict so downstream code reads
`good_for_tags['good_for_gaming']` (float 0..1).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.enrichment import registry
from app.enrichment.base import BaseEnrichmentAgent
from app.enrichment.tools.llm_client import LLMClient, default_model
from app.enrichment.types import ProductInput, StrategyOutput


_SYSTEM = (
    "You generate soft 'good_for_*' tags for an e-commerce product. Return JSON with one key:\n"
    "  good_for_tags    object {tag_key: confidence 0.0-1.0}\n"
    "Tag key rules:\n"
    "  - snake_case prefixed with 'good_for_' (e.g. good_for_gaming, good_for_baby_food).\n"
    "  - Tag vocabulary is open: pick whatever the product genuinely supports.\n"
    "  - Only emit tags backed by evidence in the input. Lower confidence = "
    "weaker evidence; omit tags below 0.2.\n"
    "  - Cap to ~10 tags. Quality > quantity."
)


@registry.register
class SoftTaggerAgent(BaseEnrichmentAgent):
    STRATEGY = "soft_tagger_v1"
    OUTPUT_KEYS = frozenset({"good_for_tags", "soft_tags_at"})
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, llm: LLMClient | None = None) -> None:
        super().__init__()
        self._llm = llm or LLMClient()

    def _invoke(self, product: ProductInput, context: dict[str, Any]) -> StrategyOutput:
        user = _format_user(product, context)
        resp = self._llm.complete(
            system=_SYSTEM,
            user=user,
            model=context.get("model") or default_model(),
            json_mode=True,
            max_tokens=400,
            temperature=0.2,
        )
        context["_last_cost_usd"] = resp.cost_usd
        data = resp.parsed_json or {}
        tags = _coerce_tags(data.get("good_for_tags"))
        attrs = {
            "good_for_tags": tags,
            "soft_tags_at": datetime.now(timezone.utc).isoformat(),
        }
        return StrategyOutput(
            product_id=product.product_id,
            strategy=self.STRATEGY,
            model=resp.model,
            attributes=attrs,
        )


def _coerce_tags(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k.startswith("good_for_"):
            continue
        try:
            score = float(v)
        except (TypeError, ValueError):
            continue
        if 0.0 <= score <= 1.0:
            out[k] = score
    return out


def _format_user(p: ProductInput, context: dict[str, Any]) -> str:
    payload = {
        "title": p.title,
        "category": p.category,
        "brand": p.brand,
        "description": (p.description or "")[:1000],
        "raw_attributes": p.raw_attributes,
        "taxonomy": context.get("taxonomy") or {},
        "parsed_specs": (context.get("parsed") or {}).get("parsed_specs", {}),
        "specialist_use_case_fit": (context.get("specialist") or {}).get(
            "specialist_use_case_fit", {}
        ),
    }
    return "Tag this product:\n" + json.dumps(payload, ensure_ascii=False)
