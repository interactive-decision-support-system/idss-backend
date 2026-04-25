"""Phase 2: assessor_v1 — catalog-level profiling."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from merchant_agent.enrichment.agents.assessor import Assessor, serialize
from merchant_agent.enrichment.tools.llm_client import LLMResponse
from merchant_agent.enrichment.types import ProductInput


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, **kw):
        return LLMResponse(
            text="",
            model=kw.get("model") or "gpt-4o",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            latency_ms=1,
            parsed_json=self.payload,
        )


def _laptop():
    return ProductInput(
        product_id=uuid4(),
        title="ThinkPad",
        brand="Lenovo",
        category="Electronics",
        description="business laptop",
        price=Decimal("999.00"),
        raw_attributes={"description": "business laptop", "ram_gb": 16},
    )


def _blender():
    return ProductInput(
        product_id=uuid4(),
        title="Vitamix 5200",
        brand="Vitamix",
        category="Small Appliances",
        description="powerful blender",
        raw_attributes={"description": "powerful blender", "wattage_w": 1380},
    )


def test_assessor_empty_catalog():
    a = Assessor(llm=_FakeLLM({"discovered_product_types": []}))
    out = a.assess([])
    assert out.catalog_size == 0
    assert "taxonomy_v1" in out.recommended_strategies


def test_assessor_counts_distribution_and_density():
    products = [_laptop(), _laptop(), _blender()]
    llm = _FakeLLM({"discovered_product_types": ["laptop", "blender"]})
    out = Assessor(llm=llm).assess(products)
    assert out.catalog_size == 3
    assert abs(out.domain_distribution["Electronics"] - 2 / 3) < 1e-6
    assert abs(out.domain_distribution["Small Appliances"] - 1 / 3) < 1e-6
    assert out.column_density["title"] == 1.0
    assert out.column_density["price"] < 1.0  # only laptop has price
    assert out.discovered_product_types == ["laptop", "blender"]


def test_assessor_handles_llm_failure_gracefully():
    class _BoomLLM:
        def complete(self, **kw):
            raise RuntimeError("network down")

    products = [_laptop()]
    out = Assessor(llm=_BoomLLM()).assess(products)
    assert out.discovered_product_types == []  # didn't crash


def test_serialize_round_trips():
    out = Assessor(llm=_FakeLLM({"discovered_product_types": ["x"]})).assess([_laptop()])
    blob = serialize(out)
    assert "x" in blob
    assert "catalog_size" in blob
