"""Phase 4: every enrichment strategy's output combines cleanly with raw.

This is the contract handshake with PR #41 — combine_raw_and_enriched must
accept any single strategy's output dict alongside the raw products.attributes
JSONB without raising. Runs purely in-memory; no DB needed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from merchant_agent.enriched_reader import combine_raw_and_enriched
from merchant_agent.enrichment import registry
from merchant_agent.enrichment.agents.parser import ParserAgent
from merchant_agent.enrichment.agents.soft_tagger import SoftTaggerAgent
from merchant_agent.enrichment.agents.specialist import SpecialistAgent
from merchant_agent.enrichment.agents.taxonomy import TaxonomyAgent
from merchant_agent.enrichment.agents.validator import make_audit_output
from merchant_agent.enrichment.agents.web_scraper import WebScraperAgent
from merchant_agent.enrichment.tools.llm_client import LLMResponse
from merchant_agent.enrichment.types import ProductInput, StrategyOutput


@pytest.fixture(autouse=True)
def _reg():
    registry._reset_for_tests()
    for cls in (TaxonomyAgent, ParserAgent, SpecialistAgent, WebScraperAgent, SoftTaggerAgent):
        registry.register(cls)
    yield
    registry._reset_for_tests()


# Realistic raw attributes the merchant_agent would see today.
_RAW = {
    "description": "ThinkPad X1 Carbon Gen 11. 14-inch ultraportable, 16GB RAM.",
    "color": "black",
    "ram_gb": 16,
    "storage_gb": 512,
    "cpu": "i7-1365U",
    "tags": ["business", "ultraportable"],
}


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


def _product():
    return ProductInput(
        product_id=uuid4(),
        title="ThinkPad X1 Carbon Gen 11",
        category="Electronics",
        brand="Lenovo",
        description=_RAW["description"],
        raw_attributes=_RAW.copy(),
    )


def test_taxonomy_output_combines_with_raw():
    out = TaxonomyAgent(
        llm=_FakeLLM({"product_type": "laptop", "confidence": 0.9})
    ).run(_product()).output
    combined = combine_raw_and_enriched(_RAW, out.attributes)
    assert combined["product_type"] == "laptop"
    assert combined["ram_gb"] == 16


def test_parser_output_combines_with_raw():
    out = ParserAgent(
        llm=_FakeLLM(
            {"parsed_specs": {"battery_life_hours": 16}, "parsed_source_fields": {"battery_life_hours": "title"}}
        )
    ).run(_product()).output
    combined = combine_raw_and_enriched(_RAW, out.attributes)
    assert combined["parsed_specs"]["battery_life_hours"] == 16
    assert combined["description"]  # raw preserved


def test_specialist_output_combines_with_raw():
    out = SpecialistAgent(
        llm=_FakeLLM(
            {
                "specialist_capabilities": ["business-class build"],
                "specialist_use_case_fit": {"business": 0.9},
                "specialist_audience": {"professionals": "lightweight"},
                "specialist_buyer_questions": ["What's the warranty?"],
            }
        )
    ).run(_product(), context={"taxonomy": {"product_type": "laptop"}}).output
    combined = combine_raw_and_enriched(_RAW, out.attributes)
    assert combined["specialist_use_case_fit"] == {"business": 0.9}


def test_soft_tagger_output_combines_with_raw():
    out = SoftTaggerAgent(
        llm=_FakeLLM({"good_for_tags": {"good_for_business": 0.9, "good_for_travel": 0.7}})
    ).run(_product()).output
    combined = combine_raw_and_enriched(_RAW, out.attributes)
    assert combined["good_for_tags"]["good_for_business"] == 0.9
    assert combined["color"] == "black"  # raw preserved


def test_validator_audit_combines_with_raw():
    out = make_audit_output(product_id=uuid4(), verdicts={})
    combined = combine_raw_and_enriched(_RAW, out.attributes)
    assert "validated_strategies" in combined
    assert combined["ram_gb"] == 16


def test_normalizer_v1_existing_strategy_still_combines_with_raw():
    # Phase 4 must not break the existing surface from PR #41.
    enriched = {
        "normalized_description": "Compact business ultraportable with i7 and 16GB.",
        "normalized_at": datetime.now(timezone.utc).isoformat(),
    }
    combined = combine_raw_and_enriched(_RAW, enriched)
    assert combined["normalized_description"].startswith("Compact")
    assert combined["description"] == _RAW["description"]
