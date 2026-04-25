"""Phase 2: soft_tagger_v1 — emits only good_for_* keys, scores 0..1."""

from __future__ import annotations

from uuid import uuid4

import pytest

from merchant_agent.enrichment import registry
from merchant_agent.enrichment.agents.soft_tagger import SoftTaggerAgent, _coerce_tags
from merchant_agent.enrichment.tools.llm_client import LLMResponse
from merchant_agent.enrichment.types import ProductInput


@pytest.fixture(autouse=True)
def _clean():
    registry._reset_for_tests()
    registry.register(SoftTaggerAgent)
    yield
    registry._reset_for_tests()


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, **kw):
        return LLMResponse(
            text="",
            model="gpt-4o-mini",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            latency_ms=1,
            parsed_json=self.payload,
        )


def test_tags_only_retains_good_for_prefix():
    coerced = _coerce_tags(
        {
            "good_for_gaming": 0.9,
            "good_for_ml": 0.1,
            "something_else": 0.8,   # wrong prefix
            "good_for_bad": "not-a-float",
            "good_for_outofrange": 1.5,
        }
    )
    assert coerced == {"good_for_gaming": 0.9, "good_for_ml": 0.1}


def test_tagger_output_shape():
    llm = _FakeLLM({"good_for_tags": {"good_for_smoothies": 0.85}})
    result = SoftTaggerAgent(llm=llm).run(
        ProductInput(product_id=uuid4(), title="Vitamix 5200"),
        context={"taxonomy": {"product_type": "blender"}},
    )
    assert result.success is True
    assert result.output.attributes["good_for_tags"] == {"good_for_smoothies": 0.85}
    assert "soft_tags_at" in result.output.attributes
