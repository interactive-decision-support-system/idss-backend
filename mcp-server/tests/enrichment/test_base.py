"""Phase 1 scaffold: BaseEnrichmentAgent.run() wraps invocation correctly."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.enrichment import registry, tracing
from app.enrichment.base import BaseEnrichmentAgent
from app.enrichment.types import ProductInput, StrategyOutput


@pytest.fixture(autouse=True)
def _clean_registry():
    registry._reset_for_tests()
    tracing._reset_for_tests()
    yield
    registry._reset_for_tests()
    tracing._reset_for_tests()


class _GoodAgent(BaseEnrichmentAgent):
    STRATEGY = "test_good_v1"
    OUTPUT_KEYS = frozenset({"test_good_alpha", "test_good_beta"})

    def _invoke(self, product, context):
        return StrategyOutput(
            product_id=product.product_id,
            strategy=self.STRATEGY,
            attributes={"test_good_alpha": 1, "test_good_beta": "ok"},
            model="gpt-4o-mini",
        )


class _RaisesAgent(BaseEnrichmentAgent):
    STRATEGY = "test_raises_v1"
    OUTPUT_KEYS = frozenset({"test_raises_x"})

    def _invoke(self, product, context):
        raise RuntimeError("boom")


class _UndeclaredKeyAgent(BaseEnrichmentAgent):
    STRATEGY = "test_undeclared_v1"
    OUTPUT_KEYS = frozenset({"test_undeclared_legit"})

    def _invoke(self, product, context):
        return StrategyOutput(
            product_id=product.product_id,
            strategy=self.STRATEGY,
            attributes={"test_undeclared_other": 1},  # not in OUTPUT_KEYS
        )


class _PidMismatchAgent(BaseEnrichmentAgent):
    STRATEGY = "test_pidmis_v1"
    OUTPUT_KEYS = frozenset({"test_pidmis_x"})

    def _invoke(self, product, context):
        return StrategyOutput(
            product_id=uuid4(),  # wrong
            strategy=self.STRATEGY,
            attributes={"test_pidmis_x": 1},
        )


def _product():
    return ProductInput(
        product_id=uuid4(),
        title="Test product",
        category="electronics",
        raw_attributes={"description": "raw desc"},
    )


def test_run_success_returns_agent_result():
    registry.register(_GoodAgent)
    p = _product()
    r = _GoodAgent().run(p)
    assert r.success is True
    assert r.output is not None
    assert r.output.attributes == {"test_good_alpha": 1, "test_good_beta": "ok"}
    assert r.strategy == "test_good_v1"
    assert r.product_id == p.product_id
    assert r.latency_ms >= 0
    assert r.trace_id  # noop tracer still issues an id


def test_run_catches_exceptions_and_returns_failure():
    registry.register(_RaisesAgent)
    r = _RaisesAgent().run(_product())
    assert r.success is False
    assert r.output is None
    assert r.error and "boom" in r.error
    assert r.strategy == "test_raises_v1"


def test_run_rejects_undeclared_keys():
    registry.register(_UndeclaredKeyAgent)
    r = _UndeclaredKeyAgent().run(_product())
    assert r.success is False
    assert r.error and "OUTPUT_KEYS" in r.error


def test_run_rejects_product_id_mismatch():
    registry.register(_PidMismatchAgent)
    r = _PidMismatchAgent().run(_product())
    assert r.success is False
    assert r.error and "product_id" in r.error


def test_noop_tracer_is_used_when_langfuse_unset(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    tracing._reset_for_tests()
    t = tracing.get_tracer()
    assert t.enabled is False
    with t.span(name="x") as span:
        span.update(foo="bar")
    t.flush()


def test_base_class_requires_strategy_and_output_keys():
    class _Empty(BaseEnrichmentAgent):
        pass

    with pytest.raises(TypeError, match="STRATEGY"):
        _Empty()

    class _NoKeys(BaseEnrichmentAgent):
        STRATEGY = "x_v1"

    with pytest.raises(TypeError, match="OUTPUT_KEYS"):
        _NoKeys()
