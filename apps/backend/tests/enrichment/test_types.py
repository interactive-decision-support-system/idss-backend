"""Phase 1 scaffold: Pydantic types validate as expected."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from merchant_agent.enrichment.types import (
    AgentResult,
    AssessorOutput,
    CatalogSchema,
    OrchestratorPlan,
    ProductInput,
    ProductTypeSchema,
    ProposalAck,
    ProposalDecision,
    SlotSchema,
    StrategyOutput,
)


def test_product_input_minimal():
    pid = uuid4()
    p = ProductInput(product_id=pid)
    assert p.product_id == pid
    assert p.raw_attributes == {}


def test_strategy_output_defaults():
    pid = uuid4()
    out = StrategyOutput(product_id=pid, strategy="parser_v1")
    assert out.confidence == 1.0
    assert out.attributes == {}
    assert out.model is None


def test_agent_result_carries_strategy_and_pid():
    pid = uuid4()
    r = AgentResult(success=True, strategy="parser_v1", product_id=pid, latency_ms=12)
    assert r.success is True
    assert r.error is None


def test_catalog_schema_round_trip():
    schema = CatalogSchema(
        merchant_id="default",
        generated_at=datetime.now(timezone.utc),
        catalog_size=3,
        product_types=[
            ProductTypeSchema(
                product_type="laptop",
                sample_count=2,
                common_slots=[
                    SlotSchema(key="ram_gb", type="numeric", unit="GB", fill_rate=1.0),
                ],
            )
        ],
    )
    blob = schema.model_dump_json()
    again = CatalogSchema.model_validate_json(blob)
    assert again.product_types[0].common_slots[0].key == "ram_gb"


def test_assessor_output_optional_fields():
    a = AssessorOutput(catalog_size=10)
    assert a.recommended_strategies == []
    assert a.per_product_recommendations is None


def test_orchestrator_plan_default_empty():
    plan = OrchestratorPlan()
    assert plan.per_product_agents == {}


def test_proposal_ack_records_decisions():
    slot = SlotSchema(key="wattage", type="numeric", unit="W")
    ack = ProposalAck(
        merchant_id="default",
        proposal_id="abc",
        decisions=[ProposalDecision(slot=slot, decision="deferred", reason="test")],
    )
    assert ack.decisions[0].decision == "deferred"


def test_slot_schema_rejects_unknown_type():
    with pytest.raises(Exception):
        SlotSchema(key="x", type="weird")  # type: ignore[arg-type]
