"""KG projection — flatten raw ``products`` + ``products_enriched`` rows
onto :Product node properties consumed by the Cypher reader in kg_service.

This module is the single place where the KG *writer* and *reader* agree on
the property set. Reader calls in ``kg_service._build_cypher_query`` reference
properties like ``p.good_for_ml``, ``p.battery_life_hours``, ``p.brand``;
those must be producible here (via identity fields or a flattening rule) or
the scorer silently falls to 0 (the original #51 bug).

Contract, verbatim from issue #52:
  - Direction is one-way: products_enriched → KG. Builders don't re-derive
    features from raw text.
  - The Cypher scorer defines the property set.
  - Per-(merchant_id, strategy) keying.
  - Identity fields come from raw; derived fields come only from enriched.

Design notes
------------
The projection is not a hardcoded property list — the merchant agent is
catalog-generic (laptop today, fashion or groceries tomorrow) and
``soft_tagger_v1`` has an open tag vocabulary. Property names (``good_for_ml``,
``calories_kcal``, …) are discovered from enriched rows at build time.

The contract drift test in ``tests/test_kg_contract.py`` verifies the *shape*
contract: every property name the reader references must either be an
identity field, a known raw attribute key, or producible by some strategy's
flattening rule. New catalog domain ⇒ add a flattening rule, not a projection
list edit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

# Keep the soft-tag threshold here, not in the Cypher string: it's a property
# of how we interpret LLM confidence scores, and the scorer reads it as a
# parameter so we can calibrate without code churn.
#
# NOTE (issue #60): LLM confidence scores from soft_tagger_v1 are not
# calibrated. 0.5 is a placeholder; revisit after #60 lands.
TAG_CONFIDENCE_THRESHOLD: float = 0.5


# ---------------------------------------------------------------------------
# Identity fields — copied straight from raw ``products`` onto :Product.
# Derived fields must NOT appear here; those come via flattening rules.
# ---------------------------------------------------------------------------

# These are logical field names — the writer is responsible for pulling the
# matching column off the ORM row, but the Cypher reader sees them by these
# names on the node.
IDENTITY_FIELDS: frozenset[str] = frozenset(
    {
        "product_id",
        "name",
        "brand",
        "category",
        "subcategory",  # derived at read time in some schemas; kept identity-side
        "price",
        "description",
        "image_url",
        "available",
        "source",
        "product_type",
    }
)


# ---------------------------------------------------------------------------
# Flattening rules — one per registered enrichment strategy.
#
# A rule consumes the strategy's ``attributes`` dict (shape owned by the
# strategy) and emits a flat {node_prop: value} sub-dict that gets SET on
# the :Product node. Rules are intentionally small and strategy-local so
# they co-evolve with the strategy prompt.
# ---------------------------------------------------------------------------


# Pattern for open-vocabulary keys produced by rules. The contract drift test
# uses these to match reader-referenced properties that have no static name.
@dataclass(frozen=True)
class _KeyPattern:
    strategy: str
    regex: re.Pattern[str]
    description: str


# Known open-vocabulary patterns. Each entry mirrors the prefix/shape the
# corresponding flattening rule below can emit.
#
# Only truly open-vocab shapes go here — parser_v1 emits snake_case scalars
# but the drift test treats those as "producible" only when they appear in
# KNOWN_RAW_ATTRIBUTE_KEYS (the registry's shared attribute vocabulary).
# A parser_v1 wildcard would swallow every new reader reference and defeat
# the whole point of drift detection.
KEY_PATTERNS: tuple[_KeyPattern, ...] = (
    _KeyPattern(
        strategy="soft_tagger_v1",
        regex=re.compile(r"^good_for_[a-z0-9_]+$"),
        description="Open-vocab confidence (0.0–1.0) per soft tag.",
    ),
)


def _rule_soft_tagger(attrs: Mapping[str, Any]) -> Dict[str, Any]:
    """soft_tagger_v1 emits ``{"good_for_tags": {tag: float}}``.

    Flatten into top-level ``good_for_<...>`` node properties so the Cypher
    scorer can reference them directly. Values stay float (not thresholded to
    bool) so the scorer can adjust the threshold without a rebuild.
    """
    out: Dict[str, Any] = {}
    tags = attrs.get("good_for_tags")
    if not isinstance(tags, dict):
        return out
    for tag, conf in tags.items():
        if not isinstance(tag, str) or not tag.startswith("good_for_"):
            continue
        try:
            out[tag] = float(conf)
        except (TypeError, ValueError):
            continue
    return out


def _rule_parser(attrs: Mapping[str, Any]) -> Dict[str, Any]:
    """parser_v1 emits ``{"parsed_specs": {key: scalar}}``.

    Flatten each (key, value) onto the node. Non-scalar values (dict, list)
    are dropped — Neo4j node properties are scalars or arrays of scalars.
    """
    out: Dict[str, Any] = {}
    specs = attrs.get("parsed_specs")
    if not isinstance(specs, dict):
        return out
    for key, val in specs.items():
        if not isinstance(key, str):
            continue
        if isinstance(val, (str, int, float, bool)) or val is None:
            out[key] = val
    return out


# Rule registry: strategy name → callable. Adding a new strategy that
# contributes node properties = add its entry here + (optionally) a pattern
# above if the keys are open-vocab.
FLATTENING_RULES: Dict[str, Callable[[Mapping[str, Any]], Dict[str, Any]]] = {
    "soft_tagger_v1": _rule_soft_tagger,
    "parser_v1": _rule_parser,
    # taxonomy_v1, specialist_v1: no rule yet. The Cypher reader doesn't
    # reference any fields from them today — add a rule here when it does.
}


# ---------------------------------------------------------------------------
# Projection entry point
# ---------------------------------------------------------------------------


def project(
    raw_identity: Mapping[str, Any],
    enriched_by_strategy: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Build the full :Product node property dict.

    Args:
        raw_identity: identity fields pulled from the raw ``products`` row.
            Keys should be a subset of ``IDENTITY_FIELDS``; unknown keys are
            dropped so a schema change in ``products`` doesn't silently leak
            derived-looking attributes into the KG.
        enriched_by_strategy: ``{strategy: attributes_dict}`` as produced by
            ``enriched_reader.fetch_enriched`` / ``hydrate_batch``. Strategies
            without a flattening rule are silently skipped.

    Returns:
        Flat dict ready to splice into ``SET n.$k = $v`` in Cypher.
    """
    props: Dict[str, Any] = {}
    for key in IDENTITY_FIELDS:
        if key in raw_identity:
            props[key] = raw_identity[key]
    for strategy, attrs in enriched_by_strategy.items():
        rule = FLATTENING_RULES.get(strategy)
        if rule is None or not isinstance(attrs, Mapping):
            continue
        props.update(rule(attrs))
    return props


# ---------------------------------------------------------------------------
# Drift helper — enumerates properties the Cypher reader references.
# ---------------------------------------------------------------------------


# Reader-side properties that exist on every node by construction and do not
# need to be produced by a rule. Kept short and explicit so changes are
# visible in review.
READER_SYSTEM_PROPERTIES: frozenset[str] = frozenset({"created_at"})


# Boolean features the reader filters on (``p.repairable = true``,
# ``p.refurbished = true``) but which no registered enrichment strategy
# currently emits. The legacy ``backfill_kg_features.py`` used regex
# heuristics against title/description; that script is being retired under
# #52 and whether its logic migrates into a new strategy is tracked in #61.
#
# These stay in the producible set so drift detection doesn't false-positive
# while #61 is open. Remove when a strategy starts emitting them.
RESERVED_BOOL_FEATURES: frozenset[str] = frozenset({"repairable", "refurbished"})


_CYPHER_PROP_RE = re.compile(r"\bp\.([a-z_][a-z0-9_]*)\b")


def cypher_referenced_properties(source: Optional[str] = None) -> set[str]:
    """Statically scan ``kg_service._build_cypher_query`` for every ``p.<name>``
    the reader touches.

    This is a best-effort regex over the function source, not a Cypher parser
    — false positives (variable names that happen to match) are fine, false
    negatives are not. The drift test compares the returned set against the
    union of identity fields, raw-attribute keys, and flattening-rule output.

    Args:
        source: override for the scanned source (used by the negative test
            that monkeypatches ``_build_cypher_query`` to inject a bogus ref).
    """
    if source is None:
        # Import lazily: this module is imported by kg_service indirectly.
        import inspect

        from app.kg_service import KnowledgeGraphService

        source = inspect.getsource(KnowledgeGraphService._build_cypher_query)
    return set(_CYPHER_PROP_RE.findall(source))


__all__ = [
    "TAG_CONFIDENCE_THRESHOLD",
    "IDENTITY_FIELDS",
    "KEY_PATTERNS",
    "FLATTENING_RULES",
    "READER_SYSTEM_PROPERTIES",
    "RESERVED_BOOL_FEATURES",
    "project",
    "cypher_referenced_properties",
]
