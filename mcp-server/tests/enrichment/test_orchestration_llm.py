"""Phase 3: LLMOrchestrator — forces taxonomy + soft_tagger, lets LLM choose the rest."""

from __future__ import annotations

from uuid import uuid4

from app.enrichment.orchestration.orchestrated import LLMOrchestrator
from app.enrichment.tools.llm_client import LLMResponse
from app.enrichment.types import AssessorOutput, ProductInput


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, **kw):
        return LLMResponse(
            text="",
            model="gpt-4o",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            latency_ms=1,
            parsed_json=self.payload,
        )


def test_llm_orchestrator_forces_taxonomy_and_soft_tagger():
    pid = uuid4()
    products = [ProductInput(product_id=pid, title="x")]
    payload = {"per_product": [{"product_id": str(pid), "strategies": ["parser_v1"]}]}
    plan = LLMOrchestrator(llm=_FakeLLM(payload)).plan(
        products, AssessorOutput(catalog_size=1)
    )
    chosen = plan.per_product_agents[pid]
    assert "taxonomy_v1" in chosen  # forced
    assert "soft_tagger_v1" in chosen  # forced
    assert "parser_v1" in chosen  # LLM picked
    assert "specialist_v1" not in chosen
    assert "scraper_v1" not in chosen


def test_llm_orchestrator_filters_unknown_strategies():
    pid = uuid4()
    products = [ProductInput(product_id=pid, title="x")]
    payload = {"per_product": [{"product_id": str(pid), "strategies": ["bogus_v1", "parser_v1"]}]}
    plan = LLMOrchestrator(llm=_FakeLLM(payload)).plan(
        products, AssessorOutput(catalog_size=1)
    )
    chosen = plan.per_product_agents[pid]
    assert "bogus_v1" not in chosen
    assert "parser_v1" in chosen


def test_llm_orchestrator_falls_back_to_forced_only_on_planning_failure():
    class _BoomLLM:
        def complete(self, **kw):
            raise RuntimeError("network down")

    pid = uuid4()
    products = [ProductInput(product_id=pid, title="x")]
    plan = LLMOrchestrator(llm=_BoomLLM()).plan(products, AssessorOutput(catalog_size=1))
    chosen = plan.per_product_agents[pid]
    # composer_v1 always trails — it is the single writer of the canonical
    # row (#83) and must run even when LLM planning fails.
    assert chosen == ["taxonomy_v1", "soft_tagger_v1", "composer_v1"]


def test_llm_orchestrator_empty_products():
    plan = LLMOrchestrator(llm=_FakeLLM({"per_product": []})).plan([], AssessorOutput(catalog_size=0))
    assert plan.per_product_agents == {}
