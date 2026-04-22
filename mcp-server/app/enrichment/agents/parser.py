"""parser_v1 — extract structured specs from existing unstructured fields.

Reads title + description + raw_attributes (whose contents we don't trust to
be normalized), asks the LLM to produce a flat sub-dict of normalized specs,
records which source field each spec came from. Output schema is intentionally
open: the agent is told to extract whatever is genuinely present, not to fit
a fixed slot list.
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
    "You extract product specifications from unstructured text. Return JSON with keys:\n"
    "  parsed_specs        flat object of normalized specs you found "
    "(e.g. {'ram_gb': 16, 'wattage_w': 1200, 'capacity_l': 1.5})\n"
    "  parsed_source_fields object mapping each spec key to the source field "
    "you read it from ('title' | 'description' | 'raw_attributes')\n"
    "Rules:\n"
    "  - Use snake_case keys with the unit suffix when meaningful "
    "(_gb, _mb, _w, _hz, _l, _kg, _cm, _hours, _years).\n"
    "  - Numeric values must be numbers, not strings.\n"
    "  - Only extract specs you can directly justify from the input — never invent.\n"
    "  - Omit anything you're unsure about; an empty parsed_specs is acceptable.\n"
    "  - Do NOT include description, title, brand, or category as specs."
)


@registry.register
class ParserAgent(BaseEnrichmentAgent):
    STRATEGY = "parser_v1"
    OUTPUT_KEYS = frozenset({"parsed_specs", "parsed_at", "parsed_source_fields"})
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
            max_tokens=600,
            temperature=0.0,
        )
        context["_last_cost_usd"] = resp.cost_usd
        data = resp.parsed_json or {}
        specs = _coerce_specs(data.get("parsed_specs") or {})
        sources = data.get("parsed_source_fields") or {}
        if not isinstance(sources, dict):
            sources = {}
        attrs = {
            "parsed_specs": specs,
            "parsed_source_fields": {k: str(v) for k, v in sources.items()},
            "parsed_at": datetime.now(timezone.utc).isoformat(),
        }
        return StrategyOutput(
            product_id=product.product_id,
            strategy=self.STRATEGY,
            model=resp.model,
            attributes=attrs,
        )


def _coerce_specs(raw: Any) -> dict[str, Any]:
    """Defensive: drop anything that isn't a simple key→scalar pair."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
    return out


def _format_user(p: ProductInput) -> str:
    payload = {
        "title": p.title,
        "category": p.category,
        "brand": p.brand,
        "description": (p.description or "")[:1500],
        "raw_attributes": p.raw_attributes,
    }
    return "Extract specs from this product:\n" + json.dumps(payload, ensure_ascii=False)
