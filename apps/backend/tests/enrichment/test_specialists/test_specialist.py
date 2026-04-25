"""Phase 2: specialist_v1 — adapts prompt to product_type, shapes output."""

from __future__ import annotations

from uuid import uuid4

import pytest

from merchant_agent.enrichment import registry
from merchant_agent.enrichment.agents.specialist import SpecialistAgent
from merchant_agent.enrichment.tools.llm_client import LLMResponse
from merchant_agent.enrichment.types import ProductInput


@pytest.fixture(autouse=True)
def _clean():
    registry._reset_for_tests()
    registry.register(SpecialistAgent)
    yield
    registry._reset_for_tests()


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.last_system = None

    def complete(self, **kw):
        self.last_system = kw.get("system")
        return LLMResponse(
            text="",
            model="gpt-4o-mini",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            latency_ms=1,
            parsed_json=self.payload,
        )


def _product():
    return ProductInput(product_id=uuid4(), title="ThinkPad X1", category="Electronics")


def test_specialist_loads_laptop_prompt_when_taxonomy_is_laptop():
    llm = _FakeLLM(
        {
            "specialist_capabilities": ["Fast i7", "14-inch IPS"],
            "specialist_use_case_fit": {"business": 0.9, "gaming": 0.1},
            "specialist_audience": {"professionals": "lightweight with strong battery"},
            "specialist_buyer_questions": ["What's the battery life?"],
        }
    )
    agent = SpecialistAgent(llm=llm)
    result = agent.run(_product(), context={"taxonomy": {"product_type": "laptop"}})
    assert result.success is True
    # Prompt fragment should be pulled in
    assert "laptop" in (llm.last_system or "").lower() or "Workload fit" in (llm.last_system or "")
    attrs = result.output.attributes
    assert attrs["specialist_capabilities"] == ["Fast i7", "14-inch IPS"]
    assert attrs["specialist_use_case_fit"] == {"business": 0.9, "gaming": 0.1}


def test_specialist_falls_back_to_default_prompt_for_unknown_type():
    llm = _FakeLLM(
        {
            "specialist_capabilities": [],
            "specialist_use_case_fit": {},
            "specialist_audience": {},
            "specialist_buyer_questions": [],
        }
    )
    result = SpecialistAgent(llm=llm).run(_product(), context={"taxonomy": {"product_type": "zzz-missing"}})
    assert result.success is True  # falls back to _default.md


def test_specialist_coerces_malformed_llm_output():
    llm = _FakeLLM(
        {
            "specialist_capabilities": "not a list",
            "specialist_use_case_fit": {"x": "bad-float", "y": 0.5},
            "specialist_audience": [1, 2, 3],
            "specialist_buyer_questions": None,
        }
    )
    result = SpecialistAgent(llm=llm).run(_product(), context={"taxonomy": {"product_type": "laptop"}})
    assert result.success is True
    attrs = result.output.attributes
    assert attrs["specialist_capabilities"] == []
    assert attrs["specialist_use_case_fit"] == {"y": 0.5}
    assert attrs["specialist_audience"] == {}
    assert attrs["specialist_buyer_questions"] == []
