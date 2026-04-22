"""composer_v1 — single writer of the canonical enriched row (issues #83, #88).

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

v1 scope
--------
  - Reads every upstream strategy's output from the runner's ``context``
    dict (populated in orchestration/runner.py: ``ctx[_short(strategy)] =
    output.attributes``).
  - Also reads ``ctx["_validator_notes"]`` so the composer knows which
    strategies the validator rejected (e.g. ram_gb out of bounds) and can
    reason about the resulting gap rather than silently ignore it.
  - Runs one LLM call (gpt-5 by default — see ``composer_model()``) with
    the full findings + raw row + policy:
        * no-hallucination: drop values not grounded in raw / parsed /
          scraped evidence
        * per-key precedence (see _PRECEDENCE_NOTE below): taxonomy owns
          product_type; parser/scraper own specs (scraper wins on tie
          because it's from the manufacturer page); soft_tagger owns
          good_for_* tags; specialist contributes only structured
          use-case-fit, never prose
        * echo applies to FINDINGS not the canonical row: a grounded
          value still belongs in composed_fields even if raw_attributes
          also has it — downstream readers should be able to read the
          canonical row without re-joining raw
        * type discipline: scalars stay scalar; flatten parsed_specs /
          scraped_specs one level up; drop narrative keys (via the
          registry's self-declared NARRATIVE_KEYS, not a hardcoded list)
  - Deterministic fallback: if the LLM errors or returns unparseable
    JSON, the composer still writes a row built from upstream findings
    (flatten parsed/scraped specs, union soft_tags, take product_type
    from taxonomy) with ``notes='deterministic_fallback'``. One product
    never loses its canonical row to a transient API blip.
  - Writes:
        composed_fields       flat dict {key: value} the composer decided
                              belongs on the canonical row
        composer_decisions    list[ComposerDecision] — audit log for the
                              cell-lineage UX in #81
        composed_at           ISO timestamp

1:1 enforcement policy (issue #88)
------------------------------------
Every key in ``composed_fields`` MUST have a matching entry in
``composer_decisions``. The composer instructs the LLM to comply, but compliance
is not guaranteed. After the LLM returns, ``_reconcile_composer_output`` enforces
this **leniently with synthesis**: any composed key that has no decision gets a
synthesized ``ComposerDecision`` marked ``source_kind=PARAMETRIC`` and
``reason='decision_synthesized_from_composed_fields'``. The row ships in full;
``attributes['incomplete_decisions'] = True`` flags the gap for the inspector.

source_kind provenance taxonomy (vision bullet 3 / #88)
--------------------------------------------------------
``ComposerDecision.source_kind`` (a ``SourceKind`` enum) classifies each
composed field by how its value was obtained:

  RAW_PARSE            — parser_v1 extracted it from raw catalog text
  SCRAPE               — scraper_v1 crawled the manufacturer page
  PARAMETRIC           — LLM world knowledge (specialist_v1, taxonomy_v1,
                         soft_tagger_v1, composer alone, or echoes from raw)
  DETERMINISTIC_FALLBACK — rule-based pass-through (no LLM inference)

The reconciler infers ``source_kind`` from ``source_strategy`` via
``_STRATEGY_TO_SOURCE_KIND``. If ``source_strategy`` is unknown, the field
defaults to ``PARAMETRIC`` and a warning is logged so the gap is visible.

Live validation caveat
-----------------------
The live mocklaptops enrichment run currently returns uniformly empty composer
output (task #12 investigates root cause). All validation for this PR is
therefore synthetic-fixture-only — see ``tests/enrichment/test_composer.py``.

# _TODO(closed_loop) — #85 attach points
# ---------------------------------------
# This composer is a single-shot LLM synthesizer. #85 (agentic pipeline
# gaps) calls out that real agentic behavior needs feedback loops. When
# we graduate to composer_v2, the hooks land in specific places below:
#
#   - _gather_findings(): point where a "request more evidence" edge
#     would fire back at scraper/parser for keys that are specialist-
#     asked-about but unground (#85 points 1, 12)
#   - post-LLM composed block: point where self-critique + revise loop
#     would run ("compose -> critique -> maybe re-compose") (#85 pt 4, 8)
#   - low-confidence merges: point where human escalation hook would
#     surface a clarification request (#85 pt 11)
#
# None of these land in this PR — but noting the attach points so the
# next iteration doesn't have to rediscover them.

v2+ (follow-ups, deliberately out of scope)
-------------------------------------------
  - Retire the per-strategy rows into a ``products_findings_<m>`` table
    and promote composer output into a flat-schema catalog. Tracked in
    #83's "Findings surface shape" open question.
  - Closed-loop refinement (see attach points above) (#85).
  - Confidence-weighted merges across duplicate sources.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.enrichment import registry
from app.enrichment.base import BaseEnrichmentAgent
from app.enrichment.tools.llm_client import LLMClient, composer_model
from app.enrichment.types import ComposerDecision, ProductInput, SourceKind, StrategyOutput

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


# Map the short context keys the runner uses back to the full strategy
# labels (taxonomy -> taxonomy_v1, etc). Only used for annotating the
# prompt / fallback decisions so the audit log is readable.
_SHORT_TO_STRATEGY: dict[str, str] = {
    "taxonomy": "taxonomy_v1",
    "parsed": "parser_v1",
    "specialist": "specialist_v1",
    "scraped": "scraper_v1",
    "soft_tagger": "soft_tagger_v1",
}


# Maps each known source_strategy to its SourceKind provenance bucket.
# Used by _reconcile_composer_output to populate source_kind on each
# ComposerDecision without requiring the LLM to do so (it often omits it).
# Unknown source_strategy values fall back to PARAMETRIC with a warning.
#
# Mapping rationale:
#   parser_v1      — LLM extraction from raw text → RAW_PARSE
#   scraper_v1     — crawled manufacturer page → SCRAPE
#   specialist_v1  — LLM world knowledge, no direct evidence → PARAMETRIC
#   taxonomy_v1    — LLM classifier, no raw evidence → PARAMETRIC
#   soft_tagger_v1 — LLM soft-tag inference → PARAMETRIC
#   composer_v1    — composer acting alone (no upstream contributed) → PARAMETRIC
#   echoes_raw     — literal pass-through reason from echo discipline → RAW_PARSE
#                    (the value came from raw_attributes, which is raw catalog input)
_STRATEGY_TO_SOURCE_KIND: dict[str, SourceKind] = {
    "parser_v1": SourceKind.RAW_PARSE,
    "scraper_v1": SourceKind.SCRAPE,
    "specialist_v1": SourceKind.PARAMETRIC,
    "taxonomy_v1": SourceKind.PARAMETRIC,
    "soft_tagger_v1": SourceKind.PARAMETRIC,
    "composer_v1": SourceKind.PARAMETRIC,
    "echoes_raw": SourceKind.RAW_PARSE,
}


# Per-key precedence rationale baked into the prompt. The reviewer (issue
# #83 review, item 2) flagged that a flat "parser > scraper > specialist >
# soft_tagger > taxonomy" ordering is wrong — taxonomy IS the canonical
# source for product_type; scraper (manufacturer page) usually beats parser
# (LLM extraction over possibly noisy text) for specs. The prompt spells
# this out rather than hard-coding it in Python so the LLM can still use
# judgment on confidence conflicts.
_PRECEDENCE_NOTE = (
    "Per-key precedence:\n"
    "  - product_type / taxonomy_path -> taxonomy_v1 (canonical classifier)\n"
    "  - numeric/categorical specs (ram_gb, weight_kg, etc.) -> scraper_v1 "
    "beats parser_v1 when both are present (scraper reads the manufacturer "
    "page, parser is LLM extraction); parser_v1 beats all others\n"
    "  - good_for_* tags -> soft_tagger_v1\n"
    "  - specialist_use_case_fit (structured {use_case: confidence}) may "
    "be included; specialist narrative is never canonical\n"
    "If two sources conflict, pick one and record the dropped alternative "
    "in composer_decisions[*].dropped_alternatives."
)


_SYSTEM = (
    "You are the composer agent: the single writer of one product's canonical "
    "catalog row. You read the raw product, the findings emitted by every "
    "upstream agent that actually ran, and decide which keys belong on the "
    "canonical row.\n"
    "\n"
    "Policy:\n"
    "  1. No hallucination. Only include a key if its value is grounded in "
    "the raw row, the parsed specs, or the scraped findings. If a buyer "
    "question (specialist_buyer_questions) has no grounded answer in those "
    "sources, DO NOT fabricate one — omit the key.\n"
    "  2. Overlap resolution. Apply the per-key precedence below. Record "
    "the chosen source in composer_decisions; put the dropped value in "
    "dropped_alternatives.\n"
    "  3. Echo discipline. A grounded value STILL belongs on the canonical "
    "row even if raw_attributes already has it — downstream readers must "
    "not have to re-join raw to reconstruct canonical state. Echo only "
    "matters for measuring agent value-add, and goes in composer_decisions"
    "[*].reason='echoes_raw', not into dropped keys.\n"
    "  4. Type discipline. Scalars stay scalars; never wrap a number in an "
    "object. For spec dicts (parsed_specs, scraped_specs), flatten their "
    "inner keys up into composed_fields one level.\n"
    "  5. Narrative text (prose bullets, audience blurbs, buyer questions) "
    "does NOT belong on the canonical row — the Python post-check will "
    "drop any narrative key regardless.\n"
    "  6. Only reference strategies listed in the 'available_findings' "
    "section of the user prompt. If a strategy didn't run, do not invent "
    "a source_strategy for it.\n"
    "  7. 1:1 requirement. For every key you emit in `composed_fields`, "
    "you MUST emit a matching entry in `composer_decisions` with `key` "
    "equal to that field name. Do not ship a composed value without a "
    "decision — the Python post-check will synthesize missing decisions "
    "and flag the gap, but a missing decision breaks cell lineage in the "
    "inspector.\n"
    "\n"
    + _PRECEDENCE_NOTE
    + "\n\n"
    "Return JSON with two keys:\n"
    "  composed_fields     object {key: value} — the canonical row content\n"
    "  composer_decisions  list of objects {key, chosen_value, "
    "source_strategy, reason, dropped_alternatives}. Every key in "
    "composed_fields MUST have a matching decision whose chosen_value "
    "equals the composed_fields value; the Python post-check will drop "
    "any decision that lies about its own key."
)


@registry.register
class ComposerAgent(BaseEnrichmentAgent):
    STRATEGY = "composer_v1"
    OUTPUT_KEYS = frozenset({
        "composed_fields", "composer_decisions", "composed_at",
        # incomplete_decisions is set to True when the reconciler had to
        # synthesize decisions for keys the LLM omitted from composer_decisions.
        # Presence flags the gap for the inspector (#88).
        "incomplete_decisions",
    })
    DEFAULT_MODEL = "gpt-5"

    def __init__(self, llm: LLMClient | None = None) -> None:
        super().__init__()
        self._llm = llm or LLMClient()

    def _invoke(self, product: ProductInput, context: dict[str, Any]) -> StrategyOutput:
        findings = _gather_findings(context)
        validator_notes = _gather_validator_notes(context)
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

        user = _format_user(product, findings, validator_notes)

        # LLM call + parse. If anything goes sideways (network blip, JSON
        # truncation, schema lies), fall back deterministically — one product
        # never loses its canonical row to a transient error.
        try:
            resp = self._llm.complete(
                system=_SYSTEM,
                user=user,
                model=(
                    context.get("composer_model")
                    or context.get("model")
                    or composer_model()
                ),
                json_mode=True,
                max_tokens=2500,
                temperature=0.1,
            )
            context["_last_cost_usd"] = resp.cost_usd
            data = resp.parsed_json or {}
            composed = _coerce_composed_fields(data.get("composed_fields"))
            decisions = _coerce_decisions(data.get("composer_decisions"))
        except Exception as exc:  # noqa: BLE001 - any LLM-side failure lands here
            logger.warning(
                "composer_llm_failed_falling_back",
                extra={"product_id": str(product.product_id), "error": str(exc)},
            )
            composed, decisions = _deterministic_fallback(findings)
            composed = registry_strip_narrative(composed)
            composed, decisions = _cross_check_decisions(composed, decisions)
            decisions, notes_payload = _reconcile_composer_output(composed, decisions)
            return StrategyOutput(
                product_id=product.product_id,
                strategy=self.STRATEGY,
                model=None,
                attributes={
                    "composed_fields": composed,
                    "composer_decisions": decisions,
                    "composed_at": now_iso,
                    **notes_payload,
                },
                notes="deterministic_fallback",
            )

        # Python post-check: strip narrative keys regardless of what the LLM
        # did (belt-and-braces), and drop decisions that lie about their own
        # key (chosen_value != composed_fields[key]).
        composed = registry_strip_narrative(composed)
        composed, decisions = _cross_check_decisions(composed, decisions)

        # 1:1 enforcement (issue #88): synthesize missing decisions and infer
        # source_kind for all existing decisions.
        decisions, notes_payload = _reconcile_composer_output(composed, decisions)

        return StrategyOutput(
            product_id=product.product_id,
            strategy=self.STRATEGY,
            model=resp.model,
            attributes={
                "composed_fields": composed,
                "composer_decisions": decisions,
                "composed_at": now_iso,
                **notes_payload,
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _gather_validator_notes(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the list the runner stashes under ``_validator_notes`` — one
    entry per rejected/failed upstream result. Always returns a list."""
    notes = context.get("_validator_notes")
    if not isinstance(notes, list):
        return []
    return [n for n in notes if isinstance(n, dict)]


def _format_user(
    product: ProductInput,
    findings: dict[str, dict[str, Any]],
    validator_notes: list[dict[str, Any]],
) -> str:
    raw = {
        "product_id": str(product.product_id),
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "description": (product.description or "")[:600],
        "price": float(product.price) if product.price is not None else None,
        "raw_attributes": product.raw_attributes or {},
    }
    # Tell the LLM which strategies actually produced findings so it can't
    # invent a source_strategy for a strategy that didn't run (issue #83
    # review, item 8).
    available_findings = [_SHORT_TO_STRATEGY.get(k, k) for k in findings.keys()]
    payload = {
        "raw": raw,
        "available_findings": available_findings,
        "findings": findings,
        "validator_notes": validator_notes,
    }
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
    """Validate each decision against ComposerDecision; drop entries that
    don't parse so the inspector doesn't have to defend against ill-formed
    JSON at render time (issue #83 review, item 7)."""
    if not isinstance(value, list):
        return []
    decisions: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        try:
            parsed = ComposerDecision.model_validate(entry)
        except ValidationError as exc:
            logger.debug("composer_decision_invalid: %s (%s)", entry, exc)
            continue
        decisions.append(parsed.model_dump(mode="json"))
    return decisions


def _cross_check_decisions(
    composed: dict[str, Any], decisions: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Drop decisions whose chosen_value disagrees with composed_fields —
    catches the LLM lying about its own decisions, per #83 review item 7.
    Decisions with ``source_strategy`` outside the known upstream set are
    also dropped (can't lineage-render an unknown source)."""
    known_sources = frozenset(_STRATEGY_TO_SOURCE_KIND.keys()) | {None}
    kept: list[dict[str, Any]] = []
    for d in decisions:
        key = d.get("key")
        src = d.get("source_strategy")
        if src not in known_sources:
            logger.debug("composer_decision_unknown_source: %s", d)
            continue
        if key in composed and d.get("chosen_value") != composed[key]:
            logger.debug(
                "composer_decision_mismatch: key=%s decision=%r composed=%r",
                key,
                d.get("chosen_value"),
                composed[key],
            )
            continue
        kept.append(d)
    return composed, kept


def _reconcile_composer_output(
    composed_fields: dict[str, Any],
    composer_decisions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enforce 1:1 between composed_fields keys and composer_decisions entries.

    Policy: lenient with synthesis. For any composed key without a decision,
    synthesize a ComposerDecision marked source_kind=PARAMETRIC and
    reason='decision_synthesized_from_composed_fields'. The output ships in
    full but the gap is visible in the trace and surfaceable in the inspector.

    Also ensures source_kind is populated on every existing decision:
    inferred from source_strategy via _STRATEGY_TO_SOURCE_KIND. If the
    source_strategy is not in the mapping, defaults to PARAMETRIC and emits
    a warning.

    Returns (reconciled_decisions, notes_payload). notes_payload contains
    'incomplete_decisions': True if any synthesis happened.
    """
    # Index existing decisions by key for O(1) lookup.
    decision_by_key: dict[str, dict[str, Any]] = {
        d["key"]: d for d in composer_decisions if isinstance(d.get("key"), str)
    }

    reconciled: list[dict[str, Any]] = []
    synthesized_any = False

    for key, value in composed_fields.items():
        if key in decision_by_key:
            d = dict(decision_by_key[key])
        else:
            # Orphan composed key — synthesize a decision.
            logger.warning(
                "composer_missing_decision_synthesized",
                extra={"key": key},
            )
            d = {
                "key": key,
                "chosen_value": value,
                "source_strategy": None,
                "reason": "decision_synthesized_from_composed_fields",
                "dropped_alternatives": [],
            }
            synthesized_any = True

        # Always infer source_kind from source_strategy so the authoritative
        # mapping drives provenance (not the LLM's or pydantic's default).
        src = d.get("source_strategy")
        if src is not None and src not in _STRATEGY_TO_SOURCE_KIND:
            logger.warning(
                "composer_unknown_source_strategy_defaulting_to_parametric: "
                "strategy=%s key=%s",
                src,
                key,
            )
        kind = _STRATEGY_TO_SOURCE_KIND.get(src, SourceKind.PARAMETRIC)
        d["source_kind"] = kind.value

        reconciled.append(d)

    notes_payload: dict[str, Any] = {}
    if synthesized_any:
        notes_payload["incomplete_decisions"] = True

    return reconciled, notes_payload


def registry_strip_narrative(composed: dict[str, Any]) -> dict[str, Any]:
    """Drop narrative keys declared by any registered agent. Reads from the
    registry instead of a hardcoded list so new narrative-emitting agents
    are covered by self-declaration (issue #83 review, item 4)."""
    narrative = registry.narrative_keys()
    if not narrative:
        return composed
    return {k: v for k, v in composed.items() if k not in narrative}


def _deterministic_fallback(
    findings: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build composed_fields + decisions from findings, no LLM.

    Order matters: later writes win, so precedence goes low-to-high.
    soft_tags first (weakest claim to canonical), then taxonomy product_type,
    then parser specs, then scraper specs (manufacturer page beats parser).
    Narrative stripping happens in the caller — keep this function's shape
    simple.
    """
    composed: dict[str, Any] = {}
    decisions: list[dict[str, Any]] = []

    def _record(key: str, value: Any, source: str, reason: str) -> None:
        prev = composed.get(key)
        dropped = [prev] if key in composed and prev != value else []
        composed[key] = value
        decisions.append(
            {
                "key": key,
                "chosen_value": value,
                "source_strategy": source,
                "reason": reason,
                "dropped_alternatives": dropped,
            }
        )

    tags = (findings.get("soft_tagger") or {}).get("good_for_tags") or {}
    if isinstance(tags, dict):
        for k, v in tags.items():
            _record(str(k), v, "soft_tagger_v1", "fallback_from_soft_tagger")

    taxonomy = findings.get("taxonomy") or {}
    ptype = taxonomy.get("product_type")
    if ptype and ptype != "unknown":
        _record("product_type", ptype, "taxonomy_v1", "fallback_from_taxonomy")

    parsed_specs = (findings.get("parsed") or {}).get("parsed_specs") or {}
    if isinstance(parsed_specs, dict):
        for k, v in parsed_specs.items():
            _record(str(k), v, "parser_v1", "fallback_from_parser")

    scraped_specs = (findings.get("scraped") or {}).get("scraped_specs") or {}
    if isinstance(scraped_specs, dict):
        for k, v in scraped_specs.items():
            _record(str(k), v, "scraper_v1", "fallback_from_scraper")

    use_case_fit = (findings.get("specialist") or {}).get("specialist_use_case_fit") or {}
    if isinstance(use_case_fit, dict):
        _record(
            "specialist_use_case_fit",
            use_case_fit,
            "specialist_v1",
            "fallback_from_specialist_use_case_fit",
        )

    return composed, decisions
