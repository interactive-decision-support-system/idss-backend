"""Phase 2: parser_v1 — extracts and coerces specs from unstructured fields."""

from __future__ import annotations

from uuid import uuid4

import pytest

from merchant_agent.enrichment import registry
from merchant_agent.enrichment.agents.parser import ParserAgent, _coerce_specs
from merchant_agent.enrichment.tools.llm_client import LLMResponse
from merchant_agent.enrichment.types import ProductInput


@pytest.fixture(autouse=True)
def _clean():
    registry._reset_for_tests()
    registry.register(ParserAgent)
    yield
    registry._reset_for_tests()


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, **kw):
        return LLMResponse(
            text="",
            model="gpt-4o-mini",
            input_tokens=10,
            output_tokens=20,
            cost_usd=0.0,
            latency_ms=1,
            parsed_json=self.payload,
        )


def _product():
    return ProductInput(
        product_id=uuid4(),
        title="Vitamix 5200 Blender, 1380 watts, 64oz container",
        category="Small Appliances",
        brand="Vitamix",
        description="High-performance blender with 1380W motor and 1.9L container.",
    )


def test_parser_extracts_specs_with_unit_keys():
    llm = _FakeLLM(
        {
            "parsed_specs": {"wattage_w": 1380, "capacity_l": 1.9},
            "parsed_source_fields": {"wattage_w": "title", "capacity_l": "description"},
        }
    )
    result = ParserAgent(llm=llm).run(_product())
    assert result.success is True
    attrs = result.output.attributes
    assert attrs["parsed_specs"] == {"wattage_w": 1380, "capacity_l": 1.9}
    assert attrs["parsed_source_fields"] == {"wattage_w": "title", "capacity_l": "description"}
    assert "parsed_at" in attrs


def test_parser_drops_non_scalar_specs():
    coerced = _coerce_specs({"a": 1, "b": "x", "c": [1, 2], "d": {"k": "v"}, 3: "ignored"})
    assert coerced == {"a": 1, "b": "x"}


def test_parser_handles_empty_response():
    llm = _FakeLLM({})
    result = ParserAgent(llm=llm).run(_product())
    assert result.success is True
    assert result.output.attributes["parsed_specs"] == {}
    assert result.output.attributes["parsed_source_fields"] == {}
