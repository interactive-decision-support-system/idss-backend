"""specialist_v1 — generic per-product-type expert agent.

Adapts its prompt to the product's product_type (read from the taxonomy_v1
output that lives in `context['taxonomy']`). One agent class for every
product type — adding kitchen-appliances, headphones, etc. is a single new
prompt fragment under specialist_prompts/<type>.md, no code change.

Output sub-dicts (top-level keys held disjoint vs raw and other strategies):
  specialist_capabilities        type-relevant capability bullets
  specialist_use_case_fit        {use_case_label: confidence float}
  specialist_audience            {audience_label: short_explanation}
  specialist_buyer_questions     list of frequent buyer questions for this product type
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.enrichment import registry
from app.enrichment.base import BaseEnrichmentAgent
from app.enrichment.tools.llm_client import LLMClient, default_model
from app.enrichment.types import ProductInput, StrategyOutput


_PROMPT_DIR = Path(__file__).resolve().parent / "specialist_prompts"
_DEFAULT_PROMPT_NAME = "_default.md"


_SYSTEM_PREFIX = (
    "You are a domain specialist enriching a product entry for an e-commerce "
    "catalog. Return JSON with keys:\n"
    "  specialist_capabilities    list[str] — concise capability statements "
    "tailored to the product type (no marketing fluff)\n"
    "  specialist_use_case_fit    object {use_case: confidence 0.0-1.0} — only "
    "the use cases this specific product genuinely fits\n"
    "  specialist_audience        object {audience: short_reason}\n"
    "  specialist_buyer_questions list[str] — questions a real buyer of this "
    "product type would ask before purchase\n"
    "Never invent specs the input does not support. Use the product-type "
    "guidance below to weight what matters most.\n"
    "PRODUCT TYPE GUIDANCE:\n"
)


def _load_prompt(product_type: str) -> str:
    candidate = _PROMPT_DIR / f"{product_type}.md"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8").strip()
    fallback = _PROMPT_DIR / _DEFAULT_PROMPT_NAME
    if fallback.exists():
        return fallback.read_text(encoding="utf-8").strip()
    return "(no specialist prompt configured — use general e-commerce knowledge)"


@registry.register
class SpecialistAgent(BaseEnrichmentAgent):
    STRATEGY = "specialist_v1"
    OUTPUT_KEYS = frozenset(
        {
            "specialist_capabilities",
            "specialist_use_case_fit",
            "specialist_audience",
            "specialist_buyer_questions",
        }
    )
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, llm: LLMClient | None = None) -> None:
        super().__init__()
        self._llm = llm or LLMClient()

    def _invoke(self, product: ProductInput, context: dict[str, Any]) -> StrategyOutput:
        product_type = _get_product_type(context)
        guidance = _load_prompt(product_type)
        system = _SYSTEM_PREFIX + guidance
        user = _format_user(product, product_type, context)
        resp = self._llm.complete(
            system=system,
            user=user,
            model=context.get("model") or default_model(),
            json_mode=True,
            max_tokens=900,
            temperature=0.2,
        )
        context["_last_cost_usd"] = resp.cost_usd
        data = resp.parsed_json or {}
        attrs = {
            "specialist_capabilities": _as_list_of_str(data.get("specialist_capabilities")),
            "specialist_use_case_fit": _as_str_to_float(data.get("specialist_use_case_fit")),
            "specialist_audience": _as_str_to_str(data.get("specialist_audience")),
            "specialist_buyer_questions": _as_list_of_str(data.get("specialist_buyer_questions")),
        }
        return StrategyOutput(
            product_id=product.product_id,
            strategy=self.STRATEGY,
            model=resp.model,
            attributes=attrs,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_product_type(context: dict[str, Any]) -> str:
    tax = context.get("taxonomy") or {}
    pt = tax.get("product_type") if isinstance(tax, dict) else None
    return str(pt) if pt else "unknown"


def _format_user(p: ProductInput, product_type: str, context: dict[str, Any]) -> str:
    payload = {
        "product_type_hint": product_type,
        "title": p.title,
        "category": p.category,
        "brand": p.brand,
        "description": (p.description or "")[:1500],
        "raw_attributes": p.raw_attributes,
        "parsed_specs": (context.get("parsed") or {}).get("parsed_specs", {}),
    }
    return "Enrich this product:\n" + json.dumps(payload, ensure_ascii=False)


def _as_list_of_str(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x) for x in v if x is not None]


def _as_str_to_float(v: Any) -> dict[str, float]:
    if not isinstance(v, dict):
        return {}
    out: dict[str, float] = {}
    for k, val in v.items():
        try:
            out[str(k)] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def _as_str_to_str(v: Any) -> dict[str, str]:
    if not isinstance(v, dict):
        return {}
    return {str(k): str(val) for k, val in v.items()}
