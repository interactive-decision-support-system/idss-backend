"""Live-Neo4j tests for the #52 KG contract.

Two scenarios:
  - KG ranking regression for #51: products with higher good_for_ml
    confidence rank above those without.
  - Per-pair KG isolation: queries scoped to one (merchant_id, strategy)
    pair don't leak nodes from another pair.

Both require a running Neo4j and seed data they own. Skip-gated on the
same NEO4J_ENV_READY flag used by test_kg_service.py — no harness, no
fancy fixture — we clean up our own seeded rows in a ``finally`` block.
"""

from __future__ import annotations

import os
import uuid

import pytest

from merchant_agent.kg_service import KnowledgeGraphService, NEO4J_AVAILABLE


NEO4J_ENV_READY = all(
    os.getenv(key) for key in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
)


@pytest.fixture
def kg_service():
    svc = KnowledgeGraphService(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD"),
    )
    yield svc
    svc.close()


def _seed_product(
    svc: KnowledgeGraphService,
    *,
    product_id: str,
    merchant_id: str,
    kg_strategy: str,
    category: str = "Electronics",
    good_for: dict[str, float] | None = None,
    extra_props: dict | None = None,
) -> None:
    """Create one :Product node with the given tenant scope and tag floats."""
    props = {
        "product_id": product_id,
        "merchant_id": merchant_id,
        "kg_strategy": kg_strategy,
        "category": category,
        "name": f"Seeded {product_id}",
        "description": "test seed",
        "price": 1000.0,
        "brand": "TestBrand",
    }
    if good_for:
        props.update(good_for)
    if extra_props:
        props.update(extra_props)
    with svc.driver.session() as session:
        session.run(
            "CREATE (p:Product) SET p = $props",
            props=props,
        )


def _cleanup_seeded(svc: KnowledgeGraphService, product_ids: list[str]) -> None:
    if not product_ids:
        return
    with svc.driver.session() as session:
        session.run(
            "MATCH (p:Product) WHERE p.product_id IN $ids DETACH DELETE p",
            ids=product_ids,
        )


@pytest.mark.skipif(not NEO4J_AVAILABLE, reason="neo4j driver not installed")
@pytest.mark.skipif(not NEO4J_ENV_READY, reason="Neo4j env vars not configured")
def test_good_for_ml_float_ranks_high_confidence_product_first(kg_service):
    """#51 regression: product with good_for_ml=0.9 must rank above one
    with good_for_ml=0.1 when the user filters for ML. Pre-#52 the
    scorer compared floats to literal ``true`` and both scored zero."""
    if not kg_service.is_available():
        pytest.skip("Neo4j not reachable")

    mid = "test_pair_ml"
    strat = f"strat_{uuid.uuid4().hex[:8]}"
    hi_pid = f"hi_{uuid.uuid4().hex[:8]}"
    lo_pid = f"lo_{uuid.uuid4().hex[:8]}"
    seeded = [hi_pid, lo_pid]

    try:
        _seed_product(
            kg_service,
            product_id=hi_pid, merchant_id=mid, kg_strategy=strat,
            good_for={"good_for_ml": 0.9},
        )
        _seed_product(
            kg_service,
            product_id=lo_pid, merchant_id=mid, kg_strategy=strat,
            good_for={"good_for_ml": 0.1},
        )

        ids, scores, _ = kg_service.search_candidates(
            query="ml laptop",
            filters={"category": "Electronics", "good_for_ml": True},
            limit=10,
            merchant_id=mid,
            kg_strategy=strat,
        )

        assert hi_pid in ids, f"high-confidence product missing from results: {ids}"
        assert lo_pid not in ids[: ids.index(hi_pid)], (
            f"low-confidence product outranked high-confidence; order={ids}"
        )
        if lo_pid in ids:
            assert scores[hi_pid]["score"] > scores[lo_pid]["score"], (
                f"hi score {scores[hi_pid]['score']} not > lo {scores[lo_pid]['score']}"
            )
    finally:
        _cleanup_seeded(kg_service, seeded)


@pytest.mark.skipif(not NEO4J_AVAILABLE, reason="neo4j driver not installed")
@pytest.mark.skipif(not NEO4J_ENV_READY, reason="Neo4j env vars not configured")
def test_per_pair_tenancy_isolates_results(kg_service):
    """A query scoped to (m1, strat_a) must not see nodes created under
    (m2, strat_b) — the contract rule 3 from #52."""
    if not kg_service.is_available():
        pytest.skip("Neo4j not reachable")

    mid_a = "m1_test"
    mid_b = "m2_test"
    strat_a = f"strat_a_{uuid.uuid4().hex[:6]}"
    strat_b = f"strat_b_{uuid.uuid4().hex[:6]}"
    pid_a = f"a_{uuid.uuid4().hex[:8]}"
    pid_b = f"b_{uuid.uuid4().hex[:8]}"
    seeded = [pid_a, pid_b]

    try:
        _seed_product(
            kg_service,
            product_id=pid_a, merchant_id=mid_a, kg_strategy=strat_a,
            good_for={"good_for_ml": 0.8},
        )
        _seed_product(
            kg_service,
            product_id=pid_b, merchant_id=mid_b, kg_strategy=strat_b,
            good_for={"good_for_ml": 0.8},
        )

        ids_a, _scores_a, _ = kg_service.search_candidates(
            query="laptop",
            filters={"category": "Electronics"},
            limit=50,
            merchant_id=mid_a,
            kg_strategy=strat_a,
        )
        assert pid_a in ids_a, f"own-tenant product missing: {ids_a}"
        assert pid_b not in ids_a, (
            f"tenant leak: pid_b ({mid_b},{strat_b}) in {mid_a}'s results {ids_a}"
        )

        # Mirror: scoping to (m2, strat_b) sees only m2's product.
        ids_b, _scores_b, _ = kg_service.search_candidates(
            query="laptop",
            filters={"category": "Electronics"},
            limit=50,
            merchant_id=mid_b,
            kg_strategy=strat_b,
        )
        assert pid_b in ids_b
        assert pid_a not in ids_b, (
            f"tenant leak: pid_a ({mid_a},{strat_a}) in {mid_b}'s results {ids_b}"
        )
    finally:
        _cleanup_seeded(kg_service, seeded)
