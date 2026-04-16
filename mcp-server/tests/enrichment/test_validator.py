"""Phase 2: validator_v1 — sanity-checks AgentResults before write."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.enrichment import registry
from app.enrichment.agents import validator  # noqa: F401 - module import side effect
from app.enrichment.agents.validator import (
    ValidationVerdict,
    make_audit_output,
    validate,
)
from app.enrichment.types import AgentResult, StrategyOutput


@pytest.fixture(autouse=True)
def _registry():
    registry._reset_for_tests()
    yield
    registry._reset_for_tests()


def _success(strategy: str, attrs: dict) -> AgentResult:
    pid = uuid4()
    return AgentResult(
        success=True,
        output=StrategyOutput(product_id=pid, strategy=strategy, attributes=attrs),
        strategy=strategy,
        product_id=pid,
        latency_ms=1,
    )


def test_passes_clean_taxonomy_output():
    r = _success(
        "taxonomy_v1",
        {"product_type": "laptop", "taxonomy_path": ["electronics"], "product_type_confidence": 0.9},
    )
    v = validate(r)
    assert v.passed is True
    assert v.reasons == []


def test_rejects_taxonomy_confidence_out_of_range():
    r = _success(
        "taxonomy_v1",
        {"product_type": "laptop", "taxonomy_path": [], "product_type_confidence": 1.7},
    )
    v = validate(r)
    assert v.passed is False
    assert any("taxonomy_confidence_out_of_range" in x for x in v.reasons)


def test_rejects_parser_specs_out_of_bounds():
    r = _success(
        "parser_v1",
        {
            "parsed_specs": {"ram_gb": 99999},  # absurd
            "parsed_source_fields": {},
            "parsed_at": "2026-04-16T00:00:00",
        },
    )
    v = validate(r)
    assert v.passed is False
    assert any("out_of_bounds" in x and "ram_gb" in x for x in v.reasons)


def test_rejects_soft_tagger_value_out_of_range():
    r = _success(
        "soft_tagger_v1",
        {"good_for_tags": {"good_for_gaming": 2.0}, "soft_tags_at": "x"},
    )
    v = validate(r)
    assert v.passed is False
    assert any("tag_out_of_range" in x for x in v.reasons)


def test_validator_audit_output_shape():
    pid = uuid4()
    out = make_audit_output(
        product_id=pid,
        verdicts={
            "parser_v1": ValidationVerdict(True),
            "taxonomy_v1": ValidationVerdict(False, ["x"], confidence=0.0),
        },
    )
    assert out.strategy == "validator_v1"
    assert out.product_id == pid
    assert "validated_strategies" in out.attributes
    assert out.attributes["validated_strategies"]["parser_v1"]["passed"] is True
    assert out.attributes["validated_strategies"]["taxonomy_v1"]["reasons"] == ["x"]


def test_failed_agent_result_is_invalid():
    r = AgentResult(success=False, output=None, strategy="x_v1", product_id=uuid4(), error="boom")
    v = validate(r)
    assert v.passed is False
