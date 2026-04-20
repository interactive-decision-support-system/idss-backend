"""LLM feature extractor — turns raw shopper text into ExtractedFeatures.

Deliberately free-vocabulary: we do NOT give the LLM a closed list of
attributes, because the point of the whole prototype is to discover
which attributes shoppers spontaneously talk about. The normalization
(snake_case, singular, `good_for_*` prefix for soft tags, etc.) is
enforced via prompt, then validated + cleaned in Python.

The extractor does one LLM call per UserQuery. For corpora in the
hundreds that is cheap enough; if it ever has to scale we can batch
10 queries per call behind the same schema.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.enrichment.tools.llm_client import LLMClient, default_model
from app.feature_discovery.types import ExtractedFeatures, UserQuery

logger = logging.getLogger(__name__)


_SYSTEM = (
    "You read a shopper's message about a product and extract the features "
    "they are reasoning about. Return JSON with these keys, all list[str]:\n"
    "  mentioned_attributes  spec-like fields the shopper refers to, "
    "snake_case and singular (e.g. 'battery_life_hours', 'ram_gb', "
    "'weight_kg', 'refresh_rate_hz', 'has_touchscreen').\n"
    "  use_cases             what the shopper wants to do with it, snake_case "
    "(e.g. 'video_editing', 'college_notes', 'travel').\n"
    "  hard_constraints      must-meet filters, snake_case with comparator "
    "if applicable (e.g. 'max_price_usd_1500', 'min_ram_gb_16', "
    "'has_usb_c').\n"
    "  soft_preferences      nice-to-haves, snake_case (e.g. "
    "'brand_apple', 'color_silver', 'matte_screen').\n"
    "  implicit_concerns     qualitative worries not in a spec sheet, "
    "snake_case (e.g. 'runs_hot', 'fan_noise', 'linux_compatibility', "
    "'build_quality').\n"
    "Rules:\n"
    "  - Only emit items the text actually supports. Do not infer.\n"
    "  - Keys must be snake_case, ASCII, no spaces.\n"
    "  - Cap each list at 12 items. Quality over quantity.\n"
    "  - If a field is not present, return an empty list for that key."
)


_SNAKE_RE = re.compile(r"[^a-z0-9_]")


class FeatureExtractor:
    def __init__(self, llm: LLMClient | None = None, model: str | None = None) -> None:
        self._llm = llm or LLMClient()
        self._model = model

    def extract(self, query: UserQuery) -> ExtractedFeatures:
        model = self._model or default_model()
        user_msg = f"product_type: {query.product_type}\n---\n{query.text}"
        resp = self._llm.complete(
            system=_SYSTEM,
            user=user_msg,
            model=model,
            json_mode=True,
            max_tokens=500,
            temperature=0.1,
        )
        data = resp.parsed_json or _parse_json_lax(resp.text)
        return ExtractedFeatures(
            source_id=query.source_id,
            product_type=query.product_type,
            mentioned_attributes=_clean_list(data.get("mentioned_attributes")),
            use_cases=_clean_list(data.get("use_cases")),
            hard_constraints=_clean_list(data.get("hard_constraints")),
            soft_preferences=_clean_list(data.get("soft_preferences")),
            implicit_concerns=_clean_list(data.get("implicit_concerns")),
            model=model,
        )


def _clean_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        key = _SNAKE_RE.sub("", item.strip().lower().replace(" ", "_").replace("-", "_"))
        key = key.strip("_")
        if not key or key in seen:
            continue
        if len(key) > 80:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= 12:
            break
    return out


def _parse_json_lax(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return {}
