"""Phase 3: FixedOrchestrator — runs every recommended strategy on every product."""

from __future__ import annotations

from uuid import uuid4

from merchant_agent.enrichment.orchestration.fixed import FixedOrchestrator
from merchant_agent.enrichment.types import AssessorOutput, ProductInput


def _product():
    return ProductInput(product_id=uuid4(), title="x", category="Electronics")


def test_fixed_orders_strategies_correctly():
    products = [_product(), _product()]
    assessment = AssessorOutput(
        catalog_size=2,
        recommended_strategies=["soft_tagger_v1", "parser_v1", "taxonomy_v1", "specialist_v1"],
    )
    plan = FixedOrchestrator().plan(products, assessment)
    for pid, strategies in plan.per_product_agents.items():
        # taxonomy must come before specialist (which depends on it)
        assert strategies.index("taxonomy_v1") < strategies.index("specialist_v1")
        # parser must come before soft_tagger
        assert strategies.index("parser_v1") < strategies.index("soft_tagger_v1")


def test_fixed_filters_to_recommended_only():
    products = [_product()]
    assessment = AssessorOutput(catalog_size=1, recommended_strategies=["taxonomy_v1"])
    plan = FixedOrchestrator().plan(products, assessment)
    assert plan.per_product_agents[products[0].product_id] == ["taxonomy_v1"]


def test_fixed_with_no_products_returns_empty_plan():
    plan = FixedOrchestrator().plan([], AssessorOutput(catalog_size=0))
    assert plan.per_product_agents == {}
