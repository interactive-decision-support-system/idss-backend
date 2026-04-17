"""validator_v1 — gates AgentResults before they're upserted.

Two responsibilities:
  1. validate(result) -> ValidationVerdict — sanity-checks an agent output
     for hallucinations, schema conformance, and value bounds. Cheap rule-based
     checks; no LLM call.
  2. (optional) write an audit row under strategy='validator_v1' summarizing
     which strategies passed/failed for a product. Off by default — opt-in
     via the runner so the enriched table stays lean.

Validator is NOT registered as a per-product agent (it doesn't transform a
ProductInput into a StrategyOutput on its own). It declares its OUTPUT_KEYS
through the registry so the disjoint-keys check stays honest if the audit
row is later opted into.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.enrichment import registry
from app.enrichment.types import AgentResult, StrategyOutput


# validator_v1's key footprint is declared in registry.EXTERNAL_STRATEGY_KEYS so
# it stays claimed across _reset_for_tests(). Importing this module is enough.
assert "validator_v1" in registry.EXTERNAL_STRATEGY_KEYS


# ---------------------------------------------------------------------------
# Bounds — conservative, only flag obvious hallucinations.
# ---------------------------------------------------------------------------

_NUMERIC_BOUNDS: dict[str, tuple[float, float]] = {
    "ram_gb": (0.5, 1024),
    "storage_gb": (1, 100_000),
    "screen_size": (3, 100),
    "battery_life_hours": (0.5, 100),
    "refresh_rate_hz": (24, 480),
    "weight_kg": (0.05, 500),
    "wattage_w": (1, 50_000),
    "capacity_l": (0.05, 500),
    "megapixels": (0.5, 200),
    "year": (1900, 2100),
}


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


class ValidationVerdict:
    __slots__ = ("passed", "reasons", "confidence")

    def __init__(self, passed: bool, reasons: list[str] | None = None, confidence: float = 1.0):
        self.passed = passed
        self.reasons = reasons or []
        self.confidence = confidence

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "reasons": self.reasons, "confidence": self.confidence}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate(result: AgentResult) -> ValidationVerdict:
    if not result.success or result.output is None:
        return ValidationVerdict(False, ["agent_did_not_succeed"], confidence=0.0)
    output = result.output
    reasons: list[str] = []

    # Check declared OUTPUT_KEYS adherence (BaseEnrichmentAgent already does this,
    # but validator runs even if base check is bypassed).
    try:
        declared = registry.output_keys(result.strategy)
    except KeyError:
        declared = frozenset()
    if declared:
        unknown = set(output.attributes.keys()) - declared
        if unknown:
            reasons.append(f"undeclared_keys:{sorted(unknown)}")

    # Numeric bounds — drill into well-known sub-dicts where parser/specialist
    # may carry numeric specs.
    _check_numeric_bounds(output.attributes, reasons)

    # Spec-prefixed nested dicts (parsed_specs, scraped_specs, etc.)
    for sub_key in ("parsed_specs", "scraped_specs"):
        sub = output.attributes.get(sub_key)
        if isinstance(sub, dict):
            _check_numeric_bounds(sub, reasons, prefix=f"{sub_key}.")

    # Confidence sanity for taxonomy
    if result.strategy == "taxonomy_v1":
        conf = output.attributes.get("product_type_confidence")
        if conf is not None:
            try:
                cval = float(conf)
                if cval < 0 or cval > 1:
                    reasons.append(f"taxonomy_confidence_out_of_range:{cval}")
            except (TypeError, ValueError):
                reasons.append("taxonomy_confidence_not_numeric")

    # Soft tag confidence range
    if result.strategy == "soft_tagger_v1":
        tags = output.attributes.get("good_for_tags") or {}
        if isinstance(tags, dict):
            for k, v in tags.items():
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    reasons.append(f"tag_value_non_numeric:{k}")
                    continue
                if fv < 0 or fv > 1:
                    reasons.append(f"tag_out_of_range:{k}={fv}")

    return ValidationVerdict(passed=not reasons, reasons=reasons)


def _check_numeric_bounds(payload: dict[str, Any], reasons: list[str], *, prefix: str = "") -> None:
    for k, v in payload.items():
        bounds = _NUMERIC_BOUNDS.get(k)
        if bounds is None:
            # Try unit-suffix match: ram_gb / parsed_ram_gb both bound by 'ram_gb'
            for known, b in _NUMERIC_BOUNDS.items():
                if k.endswith("_" + known) or k == known:
                    bounds = b
                    break
        if bounds is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        lo, hi = bounds
        if fv < lo or fv > hi:
            reasons.append(f"out_of_bounds:{prefix}{k}={fv} (expected {lo}-{hi})")


def make_audit_output(
    *,
    product_id: UUID,
    verdicts: dict[str, ValidationVerdict],
) -> StrategyOutput:
    """Compose a validator_v1 row that summarizes which strategies passed for one product."""
    return StrategyOutput(
        product_id=product_id,
        strategy="validator_v1",
        model=None,
        attributes={
            "validated_strategies": {k: v.to_dict() for k, v in verdicts.items()},
            "validated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
