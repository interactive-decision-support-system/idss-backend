"""Phase 2: taxonomy_v1 — assigns product_type with confidence."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.enrichment import registry
from app.enrichment.agents.taxonomy import TaxonomyAgent
from app.enrichment.tools.llm_client import LLMResponse
from app.enrichment.types import ProductInput


@pytest.fixture(autouse=True)
def _registry_clean():
    registry._reset_for_tests()
    # Re-register the agent under test (registration ran at import time but reset wiped it).
    registry.register(TaxonomyAgent)
    yield
    registry._reset_for_tests()


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.last_call = None

    def complete(self, **kw):
        self.last_call = kw
        return LLMResponse(
            text="",
            model=kw.get("model") or "gpt-4o-mini",
            input_tokens=10,
            output_tokens=20,
            cost_usd=0.0001,
            latency_ms=5,
            parsed_json=self.payload,
        )


def _product():
    return ProductInput(
        product_id=uuid4(),
        title="Acer Chromebook 314",
        category="Electronics",
        brand="Acer",
        description="14-inch Chromebook with Intel Celeron, 4GB RAM, 64GB eMMC.",
        raw_attributes={"description": "14-inch Chromebook with Intel Celeron, 4GB RAM, 64GB eMMC."},
    )


def test_taxonomy_returns_product_type_and_confidence():
    llm = _FakeLLM(
        {
            "product_type": "laptop",
            "taxonomy_path": ["electronics", "computers", "laptop"],
            "confidence": 0.92,
        }
    )
    agent = TaxonomyAgent(llm=llm)
    result = agent.run(_product())
    assert result.success is True
    assert result.output.attributes == {
        "product_type": "laptop",
        "taxonomy_path": ["electronics", "computers", "laptop"],
        "product_type_confidence": 0.92,
    }
    assert result.cost_usd == 0.0001


def test_taxonomy_falls_back_to_unknown_on_empty_response():
    llm = _FakeLLM({})
    agent = TaxonomyAgent(llm=llm)
    result = agent.run(_product())
    assert result.success is True
    assert result.output.attributes["product_type"] == "unknown"
    assert result.output.attributes["product_type_confidence"] == 0.0


def test_taxonomy_uses_json_mode():
    llm = _FakeLLM({"product_type": "laptop", "taxonomy_path": [], "confidence": 0.5})
    TaxonomyAgent(llm=llm).run(_product())
    assert llm.last_call["json_mode"] is True
