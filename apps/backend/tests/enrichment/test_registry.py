"""Phase 1 scaffold: registry enforces the disjoint-keys invariant."""

from __future__ import annotations

import pytest

from merchant_agent.enrichment import registry
from merchant_agent.enrichment.base import BaseEnrichmentAgent


@pytest.fixture(autouse=True)
def _clean_registry():
    registry._reset_for_tests()
    yield
    registry._reset_for_tests()


def _make_agent(strategy: str, output_keys: frozenset[str]):
    cls = type(
        f"Agent_{strategy}",
        (BaseEnrichmentAgent,),
        {"STRATEGY": strategy, "OUTPUT_KEYS": output_keys},
    )
    return cls


def test_register_succeeds_for_disjoint_keys():
    cls = _make_agent("test_disjoint_v1", frozenset({"test_disjoint_x", "test_disjoint_y"}))
    registry.register(cls)
    assert "test_disjoint_v1" in registry.list_strategies()


def test_register_rejects_overlap_with_raw_attributes():
    cls = _make_agent("bad_v1", frozenset({"description"}))  # raw key
    with pytest.raises(registry.StrategyKeyCollision, match="raw attributes"):
        registry.register(cls)


def test_register_rejects_overlap_with_external_strategy():
    # normalizer_v1 (external) writes 'normalized_description'
    cls = _make_agent("clash_v1", frozenset({"normalized_description"}))
    with pytest.raises(registry.StrategyKeyCollision, match="normalizer_v1"):
        registry.register(cls)


def test_register_rejects_overlap_between_two_in_module_strategies():
    a = _make_agent("a_v1", frozenset({"foo_x"}))
    b = _make_agent("b_v1", frozenset({"foo_x"}))
    registry.register(a)
    with pytest.raises(registry.StrategyKeyCollision, match="a_v1"):
        registry.register(b)


def test_register_rejects_missing_strategy_attr():
    cls = type("NoStrat", (BaseEnrichmentAgent,), {"OUTPUT_KEYS": frozenset({"x"})})
    with pytest.raises(registry.StrategyKeyCollision, match="STRATEGY"):
        registry.register(cls)


def test_register_rejects_missing_output_keys():
    cls = type("NoKeys", (BaseEnrichmentAgent,), {"STRATEGY": "x_v1"})
    with pytest.raises(registry.StrategyKeyCollision, match="OUTPUT_KEYS"):
        registry.register(cls)


def test_all_known_keys_includes_external():
    keys = registry.all_known_keys()
    assert "normalizer_v1" in keys
    assert "normalized_description" in keys["normalizer_v1"]
