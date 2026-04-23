"""Regression tests for #111 — nested Langfuse tracing with trace-level tags.

Before #111 every span was a root-level ``generation()`` and tags passed to
``generation()`` were silently dropped by the Langfuse v2 SDK. These tests
exercise the fixed tracer against a mocked client and assert:

1. Exactly one ``client.trace(...)`` call per run_id, with all three tags
   (run / merchant / kg_strategy) passed at trace level.
2. Non-LLM spans call ``parent.span(...)``; names prefixed ``llm:`` call
   ``parent.generation(...)``.
3. Nested ``tracer.span(...)`` blocks parent onto the enclosing span, not
   onto the trace — so strategy spans inside a ``product:<id>`` span become
   its children (the fix's structural goal).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.enrichment import tracing
from app.enrichment.tracing import _LangfuseTracer, run_context


@pytest.fixture(autouse=True)
def _reset_tracer():
    tracing._reset_for_tests()
    yield
    tracing._reset_for_tests()


def _make_client() -> MagicMock:
    """Mock Langfuse v2 client: trace()/span()/generation() return handles
    that themselves expose span()/generation()/update()/end()."""
    client = MagicMock(name="langfuse_client")
    # trace() returns a handle with span()/generation()/update()/end()
    client.trace.return_value = MagicMock(name="trace")
    return client


def test_creates_one_trace_per_run_with_trace_level_tags():
    client = _make_client()
    tracer = _LangfuseTracer(client)

    with run_context(run_id="run-abc", merchant_id="acme", kg_strategy="default"):
        with tracer.span(name="parser_v1"):
            pass
        with tracer.span(name="specialist_v1"):
            pass

    assert client.trace.call_count == 1
    kwargs = client.trace.call_args.kwargs
    assert kwargs["id"] == "run-abc"
    assert "enrichment_run:acme:run-abc" in kwargs["name"]
    assert set(kwargs["tags"]) == {
        "run:run-abc",
        "merchant:acme",
        "kg_strategy:default",
    }


def test_non_llm_span_goes_to_parent_span_not_generation():
    client = _make_client()
    tracer = _LangfuseTracer(client)
    trace = client.trace.return_value

    with run_context(run_id="r", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="parser_v1"):
            pass

    trace.span.assert_called_once()
    trace.generation.assert_not_called()


def test_llm_prefixed_span_goes_to_parent_generation():
    client = _make_client()
    tracer = _LangfuseTracer(client)
    trace = client.trace.return_value

    with run_context(run_id="r", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="llm:gpt-5-mini"):
            pass

    trace.generation.assert_called_once()
    trace.span.assert_not_called()


def test_nested_span_parents_on_enclosing_span_not_trace():
    """The structural fix from #111: strategy spans inside a product span
    must become children of the product span, not siblings under the trace."""
    client = _make_client()
    tracer = _LangfuseTracer(client)
    trace = client.trace.return_value
    product_span = MagicMock(name="product_span")
    trace.span.return_value = product_span

    with run_context(run_id="r", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="product:p-1"):
            with tracer.span(name="parser_v1"):
                pass
            with tracer.span(name="llm:gpt-5-mini"):
                pass

    # The product span is created on the trace.
    trace.span.assert_called_once()
    # The two children are created on the product span, not on the trace.
    product_span.span.assert_called_once()
    product_span.generation.assert_called_once()


def test_trace_cache_survives_across_workers_same_run():
    """Same run_id → same trace. Different run_id → different trace."""
    client = _make_client()
    tracer = _LangfuseTracer(client)

    with run_context(run_id="r1", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="parser_v1"):
            pass
    with run_context(run_id="r1", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="specialist_v1"):
            pass
    with run_context(run_id="r2", merchant_id="m", kg_strategy="k"):
        with tracer.span(name="parser_v1"):
            pass

    assert client.trace.call_count == 2
    trace_ids = [c.kwargs["id"] for c in client.trace.call_args_list]
    assert trace_ids == ["r1", "r2"]


def test_no_run_context_falls_back_to_client_level():
    """Without a run_context the tracer should still work — no trace created,
    spans land on the client directly (legacy behavior)."""
    client = _make_client()
    tracer = _LangfuseTracer(client)

    with tracer.span(name="parser_v1"):
        pass
    with tracer.span(name="llm:gpt-5"):
        pass

    client.trace.assert_not_called()
    client.span.assert_called_once()
    client.generation.assert_called_once()
