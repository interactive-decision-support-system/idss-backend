"""composer_v1 — single writer of the canonical enriched row (issue #83).

Today's enrichment pipeline lets every strategy (taxonomy, parser, specialist,
scraper, soft_tagger) write its own (product_id, strategy, attributes) row;
reads pivot by strategy at the inspector's combine step. That means:

  - hallucinated or ungrounded values still land in the table
  - two strategies can claim overlapping keys with no tie-break
  - verbose narrative output (specialist_capabilities, specialist_audience)
    sits alongside factual output with no separation

The composer is the structural fix: it is the **only** agent allowed to
emit canonical catalog fields. Every other strategy still writes its own
row — but downstream readers prefer composer output when present.

v1 scope (this commit)
----------------------
  - Reads every upstream strategy's output from the runner's ``context``
    dict (populated in orchestration/runner.py: ``ctx[_short(strategy)] =
    output.attributes``).
  - Runs one LLM call (gpt-5 by default — see ``composer_model()``) with
    the full findings + raw row + policy:
        * no-hallucination: drop values not grounded in raw / parsed /
          scraped evidence
        * overlap resolution: when two strategies claim the same key,
          pick one (prefer parser > scraper > specialist > soft_tagger >
          taxonomy, or apply the LLM's judgment)
        * echo suppression: drop values identical to an already-present
          raw field
        * specialist-question reframe: treat specialist_buyer_questions
          as a planning artifact — it is NOT a catalog field, so the
          composer ignores it as output but may surface decisions keyed
          by it in ``composer_decisions``
  - Writes:
        composed_fields       flat dict {key: value} the composer decided
                              belongs on the canonical row
        composer_decisions    list[{key, chosen_value, source_strategy,
                              reason, dropped_alternatives}] — audit log
                              for the cell-lineage UX in #81
        composed_at           ISO timestamp

v2+ (follow-ups, deliberately out of scope)
-------------------------------------------
  - Retire the per-strategy rows into a ``products_findings_<m>`` table
    and promote composer output into a flat-schema catalog. Tracked in
    #83's "Findings surface shape" open question.
  - Closed-loop refinement (composer kicks agents back with ungrounded
    questions). Tracked in #83's "Out of scope" list.
  - Confidence-weighted merges (today we prefer a single source per key).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.enrichment import registry
from app.enrichment.base import BaseEnrichmentAgent
from app.enrichment.tools.llm_client import LLMClient, composer_model
from app.enrichment.types import ProductInput, StrategyOutput

logger = logging.getLogger(__name__)


# Context keys the runner populates for each upstream strategy.
# Keeping this list explicit (rather than scanning ctx) means the composer's
# prompt has a stable shape — the LLM sees the same findings schema every
# call — and an unknown upstream strategy won't silently leak into the prompt.
_UPSTREAM_CONTEXT_KEYS: tuple[str, ...] = (
    "taxonomy",
    "parsed",
    "specialist",
    "scraped",
    "soft_tagger",
)


_SYSTEM = (
    "You are the composer agent: the single writer of one product's canonical "
    "catalog row. You read the raw product, the findings emitted by every "
    "upstream agent, and decide which keys belong on the canonical row.\n"
    "\n"
    "Policy:\n"
    "  1. No hallucination. Only include a key if its value is grounded in "
    "the raw row, the parsed specs, or the scraped findings. If the "
    "specialist asked a buyer question (specialist_buyer_questions) whose "
    "answer is not in any of those sources, DO NOT fabricate it — omit it.\n"
    "  2. Overlap resolution. When two agents claim the same key, pick one "
    "source (prefer parser > scraper > specialist > soft_tagger > taxonomy) "
    "OR merge explicitly. Record the chosen source in composer_decisions.\n"
    "  3. Echo suppression. If a finding is identical to a field already on "
    "the raw row (same value, same key), drop it.\n"
    "  4. Type discipline. Scalars stay scalars; never wrap a number in an "
    "object. For spec dicts (e.g. parsed_specs), flatten their inner keys "
    "up into composed_fields one level.\n"
    "  5. Narrative text (specialist_capabilities, specialist_audience) does "
    "NOT belong on the canonical row. Drop it here — it lives elsewhere.\n"
    "\n"
    "Return JSON with two keys:\n"
    "  composed_fields     object {key: value} — the canonical row content\n"
    "  composer_decisions  list of objects {key, chosen_value, "
    "source_strategy, reason, dropped_alternatives} — one entry per key "
    "you considered (kept or dropped). Use dropped_alternatives=[] when "
    "there was no conflict."
)


@registry.register
class ComposerAgent(BaseEnrichmentAgent):
    STRATEGY = "composer_v1"
    OUTPUT_KEYS = frozenset({"composed_fields", "composer_decisions", "composed_at"})
    DEFAULT_MODEL = "gpt-5"

    def __init__(self, llm: LLMClient | None = None) -> None:
        super().__init__()
        self._llm = llm or LLMClient()

    def _invoke(self, product: ProductInput, context: dict[str, Any]) -> StrategyOutput:
        findings = _gather_findings(context)
        now_iso = datetime.now(timezone.utc).isoformat()

        # If nothing upstream ran successfully, the composer has no material
        # to work with. Emit empty composed_fields so the row is still written
        # (useful as a marker that the composer was invoked) and skip the LLM
        # call entirely — spending a gpt-5 call on an empty prompt is waste.
        if not findings:
            return StrategyOutput(
                product_id=product.product_id,
                strategy=self.STRATEGY,
                model=None,
                attributes={
                    "composed_fields": {},
                    "composer_decisions": [],
                    "composed_at": now_iso,
                },
                notes="no_upstream_findings",
            )

        user = _format_user(product, findings)
        resp = self._llm.complete(
            system=_SYSTEM,
            user=user,
            model=context.get("composer_model") or context.get("model") or composer_model(),
            json_mode=True,
            max_tokens=1200,
            temperature=0.1,
        )
        context["_last_cost_usd"] = resp.cost_usd
        data = resp.parsed_json or {}

        composed = _coerce_composed_fields(data.get("composed_fields"))
        decisions = _coerce_decisions(data.get("composer_decisions"))

        # Belt-and-braces policy enforcement: drop anything the LLM emitted
        # that matches a raw-attribute value (echo) or that is structurally
        # a narrative key we said to strip. Keeping this in Python means a
        # chatty model can't silently violate the contract.
        composed = _strip_echoes(composed, product)
        composed = _strip_narrative_keys(composed)

        return StrategyOutput(
            product_id=product.product_id,
            strategy=self.STRATEGY,
            model=resp.model,
            attributes={
                "composed_fields": composed,
                "composer_decisions": decisions,
                "composed_at": now_iso,
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Narrative / planning-artifact keys the issue explicitly calls out as NOT
# belonging on the canonical catalog row. The composer may receive them from
# upstream (specialist emits them today) but must never surface them.
_NARRATIVE_KEYS: frozenset[str] = frozenset(
    {
        "specialist_capabilities",
        "specialist_audience",
        "specialist_buyer_questions",
    }
)


def _gather_findings(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pick up each upstream strategy's attributes dict from the runner ctx.

    Returns a dict keyed by upstream strategy short-name (same keys the
    runner populates). Skips keys with no data so the prompt isn't noisy.
    """
    findings: dict[str, dict[str, Any]] = {}
    for key in _UPSTREAM_CONTEXT_KEYS:
        val = context.get(key)
        if isinstance(val, dict) and val:
            findings[key] = val
    return findings


def _format_user(product: ProductInput, findings: dict[str, dict[str, Any]]) -> str:
    raw = {
        "product_id": str(product.product_id),
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "description": (product.description or "")[:600],
        "price": float(product.price) if product.price is not None else None,
        "raw_attributes": product.raw_attributes or {},
    }
    payload = {"raw": raw, "findings": findings}
    return "Compose the canonical row for this product.\n" + json.dumps(
        payload, ensure_ascii=False, default=str
    )


def _coerce_composed_fields(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in value.items():
        key = str(k).strip()
        if not key:
            continue
        out[key] = v
    return out


def _coerce_decisions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    decisions: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        decisions.append(
            {
                "key": key,
                "chosen_value": entry.get("chosen_value"),
                "source_strategy": str(entry.get("source_strategy") or "") or None,
                "reason": str(entry.get("reason") or "") or None,
                "dropped_alternatives": (
                    entry.get("dropped_alternatives")
                    if isinstance(entry.get("dropped_alternatives"), list)
                    else []
                ),
            }
        )
    return decisions


def _strip_echoes(
    composed: dict[str, Any], product: ProductInput
) -> dict[str, Any]:
    raw_attrs = product.raw_attributes or {}
    identity: dict[str, Any] = {
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "description": product.description,
    }
    out: dict[str, Any] = {}
    for k, v in composed.items():
        if k in identity and identity[k] == v:
            continue
        if k in raw_attrs and raw_attrs[k] == v:
            continue
        out[k] = v
    return out


def _strip_narrative_keys(composed: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in composed.items() if k not in _NARRATIVE_KEYS}
