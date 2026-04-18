"""Contract drift test for the KG projection.

The Cypher reader in ``app.kg_service._build_cypher_query`` references
:Product node properties (``p.brand``, ``p.good_for_ml``, etc.). Every one
of those must be producible by either:

  1. An identity field from the raw ``products`` row (``IDENTITY_FIELDS``),
  2. A known raw-attribute key (``registry.KNOWN_RAW_ATTRIBUTE_KEYS``),
  3. A flattening rule for a registered strategy (exact match via
     ``FLATTENING_RULES`` output, or pattern match via ``KEY_PATTERNS``), or
  4. A system-owned property (``READER_SYSTEM_PROPERTIES``).

If a reader reference is unsatisfied, the scorer silently falls to 0 — this
is the class of bug that #51 was. The test is offline (no Neo4j required).
"""

from __future__ import annotations

import re

import pytest

from app.enrichment.registry import KNOWN_RAW_ATTRIBUTE_KEYS
from app.kg_projection import (
    FLATTENING_RULES,
    IDENTITY_FIELDS,
    KEY_PATTERNS,
    READER_SYSTEM_PROPERTIES,
    RESERVED_BOOL_FEATURES,
    cypher_referenced_properties,
)


def _static_produceable_keys() -> set[str]:
    """Keys we can enumerate up front: identity fields, known raw attributes,
    system-owned reader properties, reserved boolean features awaiting #61,
    plus any fixed keys emitted by a rule (we probe each rule with a shaped
    dummy input)."""
    keys: set[str] = (
        set(IDENTITY_FIELDS)
        | set(KNOWN_RAW_ATTRIBUTE_KEYS)
        | set(READER_SYSTEM_PROPERTIES)
        | set(RESERVED_BOOL_FEATURES)
    )
    # Probe each rule with a shaped dummy so any *static* keys the rule
    # always emits get included. Open-vocab rules return nothing here — they
    # must be matched via KEY_PATTERNS.
    for _strategy, rule in FLATTENING_RULES.items():
        try:
            keys.update(rule({}))
        except Exception:  # noqa: BLE001 — empty-input rule is best-effort
            pass
    return keys


def _matches_pattern(prop: str) -> bool:
    return any(pat.regex.match(prop) for pat in KEY_PATTERNS)


def test_cypher_referenced_properties_are_producible():
    """Every ``p.<name>`` the Cypher reader touches must be producible via
    identity fields, known raw attrs, a flattening rule, or a pattern."""
    referenced = cypher_referenced_properties()
    assert referenced, (
        "cypher_referenced_properties() returned empty — the static scan "
        "should find at least p.category / p.brand / p.price in the reader."
    )
    static_ok = _static_produceable_keys()
    unsatisfied = {
        prop
        for prop in referenced
        if prop not in static_ok and not _matches_pattern(prop)
    }
    assert not unsatisfied, (
        "Cypher reader references properties with no producer. Either add "
        "a flattening rule / identity field, or extend KEY_PATTERNS if the "
        "keys are open-vocab. Unsatisfied: " + repr(sorted(unsatisfied))
    )


def test_drift_detected_when_reader_references_unknown_property():
    """Negative test: inject a fake ``p.nonexistent_prop`` reference and
    confirm the drift check flags it."""
    bogus_source = """
    def _build_cypher_query(self, *args, **kwargs):
        return "MATCH (p:Product) WHERE p.nonexistent_prop = 1 RETURN p"
    """
    refs = cypher_referenced_properties(source=bogus_source)
    assert "nonexistent_prop" in refs
    static_ok = _static_produceable_keys()
    unsatisfied = {
        prop
        for prop in refs
        if prop not in static_ok and not _matches_pattern(prop)
    }
    assert "nonexistent_prop" in unsatisfied, (
        "Drift helper should flag p.nonexistent_prop as unsatisfied; "
        f"instead got unsatisfied={sorted(unsatisfied)}"
    )


def test_flattening_rules_emit_declared_shape():
    """soft_tagger_v1 rule turns good_for_tags dict into top-level props."""
    rule = FLATTENING_RULES["soft_tagger_v1"]
    out = rule({"good_for_tags": {"good_for_ml": 0.9, "good_for_gaming": 0.3}})
    assert out == {"good_for_ml": 0.9, "good_for_gaming": 0.3}
    # Non-tag keys are dropped
    out_filtered = rule({"good_for_tags": {"not_a_good_for": 1.0, "good_for_ml": 0.5}})
    assert out_filtered == {"good_for_ml": 0.5}
    # Bad shape → empty
    assert rule({"good_for_tags": "not a dict"}) == {}
    assert rule({}) == {}


def test_parser_rule_flattens_scalar_specs():
    """parser_v1 rule flattens parsed_specs scalars onto the node."""
    rule = FLATTENING_RULES["parser_v1"]
    out = rule(
        {
            "parsed_specs": {
                "ram_gb": 16,
                "battery_life_hours": 10,
                "dropped_nested": {"not": "scalar"},
                "dropped_list": [1, 2, 3],
                "kept_string": "foo",
            }
        }
    )
    assert out == {"ram_gb": 16, "battery_life_hours": 10, "kept_string": "foo"}
    assert rule({}) == {}


def test_good_for_pattern_covers_open_vocabulary():
    """KEY_PATTERNS should match any snake_case ``good_for_*`` key."""
    good = ["good_for_ml", "good_for_gaming", "good_for_baby_food", "good_for_linux"]
    for key in good:
        assert _matches_pattern(key), f"expected pattern match for {key!r}"
    # Non-good_for_ keys do not match a pattern — drift detection relies on
    # KNOWN_RAW_ATTRIBUTE_KEYS for parser-produced specs.
    assert not _matches_pattern("ram_gb")
    assert not _matches_pattern("nonexistent_prop")
    # Uppercase fails (Cypher props are lowercase snake_case by convention)
    assert not _matches_pattern("GOOD_FOR_ML")
